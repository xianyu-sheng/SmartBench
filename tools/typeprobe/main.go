// Command typeprobe resolves Go symbol types at file:line via go/types.
//
// It is the "type checker" evidence provider for SmartBench's Go frontend:
// the surface provider (go.surface) only sees types declared in the analyzed
// repository, while this helper resolves the true type through the module
// dependency graph (e.g. SDK response types such as io.Reader).
//
// Protocol:
//
//	stdin:  JSON array of {"file": string, "line": int, "symbol": string}
//	stdout: JSON array of ProbeResult (same order as input)
//
// The helper intentionally accepts multiple queries so one go/packages load
// serves the whole project. Run `go build -buildvcs=false .` to produce the
// binary; SmartBench looks for it under tools/typeprobe/ or at the path in
// SMARTBENCH_GO_TYPEPROBE.
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"go/ast"
	"go/token"
	"go/types"
	"os"
	"path/filepath"
	"strings"

	"golang.org/x/tools/go/packages"
)

type Query struct {
	File   string `json:"file"`
	Line   int    `json:"line"`
	Symbol string `json:"symbol"`
}

type ProbeResult struct {
	Symbol         string   `json:"symbol"`
	DeclaredType   string   `json:"declared_type"`
	IsCloser       bool     `json:"implements_io_closer"`
	IsReadCloser   bool     `json:"implements_io_readcloser"`
	HasCloseMethod bool     `json:"has_close_method"`
	CloseMethods   []string `json:"close_methods"`
	ObjectKind     string   `json:"object_kind"`
	Error          string   `json:"error,omitempty"`
}

func main() {
	queries, err := readQueries()
	if err != nil {
		fatal(fmt.Sprintf("read stdin: %v", err))
	}
	if len(queries) == 0 {
		fmt.Println("[]")
		return
	}

	// Single package load for the whole batch. Directory = first query's file
	// parent walked up to the nearest go.mod.
	dir, err := moduleDir(queries[0].File)
	if err != nil {
		emitErrors(queries, "module dir: "+err.Error())
		return
	}

	cfg := &packages.Config{
		Mode: packages.NeedName | packages.NeedFiles | packages.NeedCompiledGoFiles |
			packages.NeedImports | packages.NeedTypes | packages.NeedSyntax | packages.NeedTypesInfo,
		Dir: dir,
		Env: os.Environ(),
	}
	pkgs, err := packages.Load(cfg, "./...")
	if err != nil {
		emitErrors(queries, "load: "+err.Error())
		return
	}
	packages.PrintErrors(pkgs) // tolerated; symbol lookup still attempted

	results := make([]ProbeResult, 0, len(queries))
	for _, q := range queries {
		results = append(results, resolve(pkgs, q))
	}
	writeResults(results)
}

func readQueries() ([]Query, error) {
	reader := bufio.NewReader(os.Stdin)
	data, err := reader.ReadBytes('\n')
	if err != nil && len(data) == 0 {
		return nil, err
	}
	var queries []Query
	if err := json.Unmarshal(data, &queries); err != nil {
		return nil, err
	}
	return queries, nil
}

func moduleDir(filePath string) (string, error) {
	abs, err := filepath.Abs(filePath)
	if err != nil {
		return "", err
	}
	dir := filepath.Dir(abs)
	for {
		if _, err := os.Stat(filepath.Join(dir, "go.mod")); err == nil {
			return dir, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", fmt.Errorf("no go.mod found above %s", filePath)
		}
		dir = parent
	}
}

func resolve(pkgs []*packages.Package, q Query) ProbeResult {
	res := ProbeResult{Symbol: q.Symbol}
	absFile, err := filepath.Abs(q.File)
	if err != nil {
		absFile = q.File
	}
	matchName := q.Symbol
	if i := strings.LastIndex(q.Symbol, "."); i >= 0 {
		matchName = q.Symbol[:i]
	}

	for _, pkg := range pkgs {
		if pkg.TypesInfo == nil || pkg.Fset == nil {
			continue
		}
		for _, f := range pkg.Syntax {
			fname := pkg.Fset.Position(f.Pos()).Filename
			if !sameFile(fname, absFile, q.File) {
				continue
			}
			pos := findIdentAtLine(pkg.Fset, f, q.Line, matchName)
			if pos == token.NoPos {
				pos = findFirstIdentAtLine(pkg.Fset, f, q.Line)
			}
			if pos == token.NoPos {
				res.Error = "no identifier at line"
				return res
			}
			// The first match on a line may be the left-hand side of a short
			// variable declaration (e.g. "resp, err := httpClient.Do(...)").
			// Prefer the occurrence whose resolved type is a usable object
			// (non-nil type, not a multi-value signature or error).
			if strings.Contains(q.Symbol, ".") {
				if sel := probeSelector(pkg.TypesInfo, f, pos, q.Symbol); sel != nil {
					return *sel
				}
			}
			if candidate := bestUseOrDef(pkg.TypesInfo, pkg.Fset, f, q.Line, matchName, pos); candidate != nil {
				candidate.Symbol = q.Symbol
				return *candidate
			}
			res.Error = "no type info at position"
			return res
		}
	}
	res.Error = "file not found in loaded packages"
	return res
}

// bestUseOrDef returns the resolved type for the target identifier on a line,
// scanning every occurrence of the name and preferring the occurrence with a
// usable object type.  Occurrences used as a selector receiver (“cmd.Run()“)
// are preferred over declarations, because the receiver of a CALL is what the
// resource-lifecycle analyzer cares about.
func bestUseOrDef(info *types.Info, fset *token.FileSet, f *ast.File, line int, name string, firstPos token.Pos) *ProbeResult {
	var receiverPos, defPos, otherPos token.Pos
	receiverPos, defPos, otherPos = token.NoPos, token.NoPos, token.NoPos

	ast.Inspect(f, func(n ast.Node) bool {
		if id, ok := n.(*ast.Ident); ok && id.Name == name {
			posLine := fset.Position(id.Pos()).Line
			if posLine != line {
				return true
			}
			if sel, ok := parentSelector(f, id); ok && sel.X == id {
				if receiverPos == token.NoPos {
					receiverPos = id.Pos()
				}
				return true
			}
			if _, isDef := info.Defs[id]; isDef && defPos == token.NoPos {
				defPos = id.Pos()
				return true
			}
			if otherPos == token.NoPos {
				otherPos = id.Pos()
			}
		}
		return true
	})

	for _, pos := range []token.Pos{receiverPos, defPos, otherPos, firstPos} {
		if pos == token.NoPos {
			continue
		}
		if candidate := candidateAt(info, pos); candidate != nil {
			return candidate
		}
	}
	return nil
}

// parentSelector returns the SelectorExpr that directly contains the ident,
// if the ident is the selector's X operand.
func parentSelector(f *ast.File, id *ast.Ident) (*ast.SelectorExpr, bool) {
	var found *ast.SelectorExpr
	ast.Inspect(f, func(n ast.Node) bool {
		if found != nil {
			return false
		}
		if sel, ok := n.(*ast.SelectorExpr); ok {
			if sel.X == id {
				found = sel
				return false
			}
		}
		return true
	})
	return found, found != nil
}

func candidateAt(info *types.Info, pos token.Pos) *ProbeResult {
	for id, obj := range info.Uses {
		if id.Pos() != pos || obj == nil {
			continue
		}
		// A package reference (e.g. "os" in "os.Open") is not a resource
		// object; reject it so the Python provider filters the query.
		if _, isPkg := obj.(*types.PkgName); isPkg {
			return nil
		}
		res := &ProbeResult{ObjectKind: "use"}
		analyzeCloser(obj.Type(), res)
		if usable(obj.Type()) {
			res.DeclaredType = obj.Type().String()
			return res
		}
	}
	for id, obj := range info.Defs {
		if id.Pos() != pos || obj == nil {
			continue
		}
		res := &ProbeResult{ObjectKind: "def"}
		analyzeCloser(obj.Type(), res)
		if usable(obj.Type()) {
			res.DeclaredType = obj.Type().String()
			return res
		}
	}
	for expr, tv := range info.Types {
		if expr.Pos() != pos || tv.Type == nil {
			continue
		}
		res := &ProbeResult{ObjectKind: "expr"}
		analyzeCloser(tv.Type, res)
		if usable(tv.Type) {
			res.DeclaredType = tv.Type.String()
			return res
		}
	}
	return nil
}

// usable rejects types that are not useful for resource-lifecycle evidence:
// error, multi-value signatures, or missing types.
func usable(t types.Type) bool {
	if t == nil {
		return false
	}
	if sig, ok := t.(*types.Signature); ok {
		results := sig.Results()
		if results != nil && results.Len() > 1 {
			return false
		}
		// A function value is not a resource; reject signatures entirely so
		// we never report a func as the receiver type.
		return false
	}
	if named, ok := t.(*types.Named); ok {
		if named.Obj() != nil && named.Obj().Name() == "error" && named.Obj().Pkg() == nil {
			return false
		}
	}
	if t.String() == "invalid type" {
		return false
	}
	return true
}

func probeSelector(info *types.Info, f *ast.File, xPos token.Pos, symbol string) *ProbeResult {
	want := symbol
	if i := strings.LastIndex(symbol, "."); i >= 0 {
		want = symbol[i+1:]
	}
	var found *ast.SelectorExpr
	ast.Inspect(f, func(n ast.Node) bool {
		if found != nil {
			return false
		}
		if sel, ok := n.(*ast.SelectorExpr); ok {
			if sel.X.Pos() == xPos && sel.Sel.Name == want {
				found = sel
				return false
			}
		}
		return true
	})
	if found == nil {
		return nil
	}
	tv, ok := info.Types[found]
	if !ok || tv.Type == nil {
		return nil
	}
	res := &ProbeResult{Symbol: symbol, ObjectKind: "selector", DeclaredType: tv.Type.String()}
	analyzeCloser(tv.Type, res)
	return res
}

func findIdentAtLine(fset *token.FileSet, f *ast.File, line int, name string) token.Pos {
	var found token.Pos = token.NoPos
	ast.Inspect(f, func(n ast.Node) bool {
		if found != token.NoPos {
			return false
		}
		if id, ok := n.(*ast.Ident); ok && id.Name == name {
			if fset.Position(id.Pos()).Line == line {
				found = id.Pos()
				return false
			}
		}
		return true
	})
	return found
}

func findFirstIdentAtLine(fset *token.FileSet, f *ast.File, line int) token.Pos {
	var found token.Pos = token.NoPos
	ast.Inspect(f, func(n ast.Node) bool {
		if found != token.NoPos {
			return false
		}
		if id, ok := n.(*ast.Ident); ok {
			if fset.Position(id.Pos()).Line == line {
				found = id.Pos()
				return false
			}
		}
		return true
	})
	return found
}

func sameFile(a, b, raw string) bool {
	// Exact path comparison first; the packages loader reports absolute
	// paths, while queries may use relative paths from the module root.
	if a == b {
		return true
	}
	absA, errA := filepath.Abs(a)
	absB, errB := filepath.Abs(b)
	absRaw, errRaw := filepath.Abs(raw)
	if errA == nil && errRaw == nil && absA == absRaw {
		return true
	}
	if errA == nil && errB == nil && absA == absB {
		return true
	}
	// No base-name fallback: "cmd/e2ebench/diff.go" and "internal/diff/diff.go"
	// must not be conflated when loading "./..." resolves multiple packages.
	return false
}

func analyzeCloser(t types.Type, res *ProbeResult) {
	var closeMethods []string
	hasClose := false
	for _, base := range []types.Type{t, types.NewPointer(t)} {
		ms := types.NewMethodSet(base)
		for i := 0; i < ms.Len(); i++ {
			sel := ms.At(i)
			if sel.Obj().Name() != "Close" {
				continue
			}
			hasClose = true
			pkg := sel.Obj().Pkg()
			pkgPath := ""
			if pkg != nil {
				pkgPath = pkg.Path()
			}
			closeMethods = append(closeMethods, fmt.Sprintf("%s.Close: %s", pkgPath, sel.Type().String()))
		}
	}
	res.HasCloseMethod = hasClose
	res.CloseMethods = closeMethods
	for _, m := range closeMethods {
		if strings.HasSuffix(m, "func() error") {
			res.IsCloser = true
		}
	}
	res.IsReadCloser = res.IsCloser && (strings.Contains(res.DeclaredType, "io.Reader") || strings.Contains(res.DeclaredType, "io.ReadCloser"))
}

func emitErrors(queries []Query, message string) {
	results := make([]ProbeResult, 0, len(queries))
	for _, q := range queries {
		results = append(results, ProbeResult{Symbol: q.Symbol, Error: message})
	}
	writeResults(results)
}

func writeResults(results []ProbeResult) {
	encoder := json.NewEncoder(os.Stdout)
	if err := encoder.Encode(results); err != nil {
		fmt.Fprintln(os.Stderr, "encode:", err)
	}
}

func fatal(message string) {
	fmt.Fprintln(os.Stderr, "typeprobe:", message)
	os.Exit(2)
}
