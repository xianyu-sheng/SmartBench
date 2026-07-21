"""
CrossChecker — cross-validate debate proposals against code graph and source.

Takes proposals from the debate engine, verifies each claim against:
  1. Disk (file existence, line ranges)
  2. Code graph (function existence, call edges)
  3. Semantic search (RAG, if available)

Returns proposals annotated with verification data.
"""

import logging
import re
from typing import Any, Dict, List

from smartbench.graph.retriever import GraphRetriever
from smartbench.graph.schema import CodeGraph
from smartbench.verifier import VerificationResult, VerificationStatus
from smartbench.verifier.extractor import EvidenceExtractor
from smartbench.verifier.location import LocationVerifier

logger = logging.getLogger(__name__)


class CrossChecker:
    """
    Cross-validates debate proposals against actual project code.

    Each proposal is checked for:
      1. File existence (via LocationVerifier)
      2. Function existence in code graph
      3. Call chain accuracy
      4. Source code match (actual vs claimed)
    """

    def __init__(self, graph: CodeGraph, project_path: str,
                 graph_retriever: GraphRetriever,
                 hybrid_retriever=None):
        """
        Args:
            graph: The project's code graph
            project_path: Root directory
            graph_retriever: GraphRetriever instance
            hybrid_retriever: Optional HybridRetriever (for RAG verification)
        """
        self.graph = graph
        self.project_path = project_path
        self.graph_retriever = graph_retriever
        self.hybrid_retriever = hybrid_retriever
        self.loc_verifier = LocationVerifier(project_path)
        self.extractor = EvidenceExtractor(project_path, graph)

    # ── Public API ──────────────────────────────────────────────────────

    def verify_proposals(self, proposals: List[Dict]) -> List[Dict]:
        """
        Annotate each proposal with verification results.

        For each proposal:
          1. Parse "location" → (file, line)
          2. Verify file:line exists
          3. If function names found, verify in graph
          4. If call chains claimed, verify edges
          5. Extract actual code snippet
          6. Perform RAG cross-check if available

        Returns proposals with added "__verification" field.

        Args:
            proposals: List of proposal dicts from Proposer

        Returns:
            Proposals with "__verification" scores and details
        """
        verified = []
        for p in proposals:
            if not isinstance(p, dict):
                verified.append(p)
                continue

            # Extract claims
            claims = self._extract_claims(p)

            # Verify each claim
            results = []
            for claim in claims:
                vtype = claim.get("type", "")
                if vtype == "file_location":
                    result = self._verify_location_claim(claim)
                elif vtype == "call_chain":
                    result = self._verify_call_claim(claim)
                elif vtype == "code_pattern":
                    result = self._verify_pattern_claim(claim)
                else:
                    result = VerificationResult(
                        status=VerificationStatus.UNVERIFIABLE,
                        claim=str(claim),
                        detail="无法识别的声明类型",
                    )
                results.append(result)

            # Score the proposal
            score_data = self._score_proposal(p, results)
            p["__verification"] = score_data
            verified.append(p)

        return verified

    def verify_critique(self, critique: Dict,
                        proposals: List[Dict]) -> Dict:
        """
        Verify critique verdict claims against actual code.

        Checks whether concerns reference real code patterns,
        and whether "reject" verdicts cite valid reasons.

        Args:
            critique: Critique output dict
            proposals: Original proposals (with __verification)

        Returns:
            Critique dict with added "__verification" field
        """
        verdicts = critique.get("verdicts", [])
        verified_verdicts = []

        for v in verdicts:
            if not isinstance(v, dict):
                verified_verdicts.append(v)
                continue

            # Verify each concern against actual code
            concerns = v.get("concerns", [])
            verified_concerns = []
            for c in concerns:
                if not isinstance(c, str):
                    verified_concerns.append({"text": str(c)})
                    continue

                # Extract any file references from the concern text
                file_refs = self._extract_file_refs(c)
                loc_results = []
                for fr in file_refs:
                    result = self.loc_verifier.verify(fr)
                    loc_results.append(result.to_dict())

                verified_concerns.append({
                    "text": c,
                    "file_references": file_refs,
                    "location_checks": loc_results,
                    "all_files_exist": all(
                        r.get("status") != "hallucinated"
                        for r in loc_results
                    ) if loc_results else None,
                })

            v["__verified_concerns"] = verified_concerns
            verified_verdicts.append(v)

        critique["__verification"] = {
            "verdicts_checked": len(verified_verdicts),
            "validated": all(
                v.get("__verified_concerns") is not None
                for v in verified_verdicts
            ),
        }
        return critique

    # ── Claim extraction ────────────────────────────────────────────────

    def _extract_claims(self, proposal: Dict) -> List[Dict]:
        """
        Extract all verifiable claims from a proposal JSON.

        Looks for:
          - "location" field (file:line)
          - Function names in "problem" text
          - Call chains in "implementation_steps"
          - Evidence claims (new schema)
        """
        claims = []

        # 1. Explicit evidence claims (new schema)
        if "evidence_claims" in proposal:
            for ec in proposal["evidence_claims"]:
                if isinstance(ec, dict):
                    claims.append(ec)
            return claims  # If explicit claims exist, use only those

        # 2. Location field
        location = proposal.get("location", "")
        if location:
            claims.append({
                "type": "file_location",
                "target": location,
                "description": proposal.get("problem", ""),
            })

        # 3. Function names mentioned in problem
        problem = proposal.get("problem", "")
        func_names = self._extract_function_names(problem)
        for fn in func_names:
            claims.append({
                "type": "code_pattern",
                "target": fn,
                "description": problem,
            })

        # 4. Call chains in implementation steps
        steps = proposal.get("implementation_steps", [])
        for step in steps:
            chain = self._extract_call_chain(step)
            if chain:
                claims.append({
                    "type": "call_chain",
                    "target": " -> ".join(chain),
                    "description": step,
                })

        return claims

    # ── Claim verification ──────────────────────────────────────────────

    def _verify_location_claim(self, claim: Dict) -> VerificationResult:
        """Verify a file:line location claim."""
        target = claim.get("target", "")
        file_path, line = self._parse_location(target)

        result = self.loc_verifier.verify(file_path, line)

        # If path is hallucinated but semantic search finds a match, note it
        if result.status == VerificationStatus.HALLUCINATED and self.hybrid_retriever:
            context = claim.get("description", "")
            if context:
                rag_result = self.hybrid_retriever.retrieve_claim_evidence({
                    "context": context,
                })
                if rag_result and rag_result.get("semantic_matches"):
                    result.detail += (
                        " (向量检索找到相似代码: "
                        + rag_result["semantic_matches"][0]["file"]
                        + ")"
                    )

        return result

    def _verify_call_claim(self, claim: Dict) -> VerificationResult:
        """Verify a call chain claim."""
        target = claim.get("target", "")
        chain = [s.strip() for s in target.split("->")]

        chain_result = self.extractor.verify_call_chain(chain)
        accuracy = chain_result["accuracy"]

        if accuracy >= 1.0:
            return VerificationResult(
                status=VerificationStatus.VERIFIED,
                claim=target,
                confidence=1.0,
                detail=f"调用链已确认: {target}",
            )
        elif accuracy >= 0.5:
            return VerificationResult(
                status=VerificationStatus.PARTIAL,
                claim=target,
                confidence=accuracy,
                detail=(
                    f"部分验证: {len(chain_result['valid_edges'])}/{len(chain_result['valid_edges']) + len(chain_result['missing_edges'])} 条边存在"
                ),
            )
        else:
            return VerificationResult(
                status=VerificationStatus.HALLUCINATED,
                claim=target,
                confidence=0.0,
                detail=f"调用链不存在: 缺失 {chain_result['missing_edges']}",
            )

    def _verify_pattern_claim(self, claim: Dict) -> VerificationResult:
        """Verify a code pattern claim (e.g., 'missing error handling')."""
        target = claim.get("target", "")
        description = claim.get("description", "")

        # Check if the named function exists in graph
        found = self.graph.find_by_name(target)
        if found:
            node = found[0]
            return VerificationResult(
                status=VerificationStatus.VERIFIED,
                claim=target,
                resolved_file=node.file_path,
                resolved_line=node.line_start,
                actual_function=node.name,
                confidence=0.9,
                detail=f"函数 '{target}' 存在于 {node.file_path}:{node.line_start}",
            )

        # Try semantic search
        if self.hybrid_retriever:
            evidence = self.hybrid_retriever.retrieve_claim_evidence({
                "context": description or target,
            })
            if evidence and evidence.get("semantic_matches"):
                match = evidence["semantic_matches"][0]
                return VerificationResult(
                    status=VerificationStatus.PARTIAL,
                    claim=target,
                    resolved_file=match.get("file"),
                    resolved_line=match.get("line"),
                    confidence=match.get("score", 0.5),
                    detail=f"未直接找到 '{target}'，但语义匹配到 {match.get('file')}:{match.get('line')}",
                )

        return VerificationResult(
            status=VerificationStatus.HALLUCINATED,
            claim=target,
            confidence=0.0,
            detail=f"未找到命名实体 '{target}'",
        )

    # ── Scoring ─────────────────────────────────────────────────────────

    def _score_proposal(self, proposal: Dict,
                        results: List[VerificationResult]) -> Dict[str, Any]:
        """Compute aggregate verification score for a proposal."""
        if not results:
            return {
                "verification_score": 0.5,
                "verdict": "unverifiable",
                "flags": [],
                "verified_locations": [],
                "hallucinated_locations": [],
                "partial_locations": [],
                "detail": "无可验证声明",
            }

        verified_locs = []
        hallucinated_locs = []
        partial_locs = []
        flags = []

        total_weight = len(results)
        weighted_score = 0.0

        for r in results:
            if r.status == VerificationStatus.VERIFIED:
                weighted_score += r.confidence
                if r.resolved_file:
                    loc = f"{r.resolved_file}"
                    if r.resolved_line:
                        loc += f":{r.resolved_line}"
                    verified_locs.append(loc)
            elif r.status == VerificationStatus.PARTIAL:
                weighted_score += r.confidence * 0.7  # File exists, path corrected
                if r.resolved_file:
                    loc = f"{r.claimed_file or '?'} -> {r.resolved_file}"
                    if r.resolved_line:
                        loc += f":{r.resolved_line}"
                    partial_locs.append(loc)
            elif r.status == VerificationStatus.HALLUCINATED:
                weighted_score += 0.0
                if r.claimed_file:
                    hallucinated_locs.append(r.claimed_file)
                flags.append(f"[!] 证据缺失: {r.detail}")
            else:
                weighted_score += 0.3
                flags.append(f"[?] 无法验证: {r.detail}")

        score = weighted_score / max(total_weight, 1)

        # Determine verdict
        if score >= 0.8:
            verdict = "verified"
        elif score >= 0.4:
            verdict = "partial"
        else:
            verdict = "hallucinated"

        return {
            "verification_score": round(score, 2),
            "verdict": verdict,
            "flags": flags,
            "verified_locations": verified_locs,
            "hallucinated_locations": hallucinated_locs,
            "partial_locations": partial_locs,
            "detail": f"验证得分: {score:.0%} ({len(verified_locs)} 通过, {len(partial_locs)} 部分, {len(hallucinated_locs)} 不存在)",
            "claim_count": len(results),
        }

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _parse_location(location: str) -> tuple:
        """Parse 'file:line' or 'file:line1-line2' format."""
        file_path = location
        line = None

        # Match "file:123" or "file:123-145"
        match = re.search(r'^(.+?):(\d+)(?:-\d+)?$', location.strip())
        if match:
            file_path = match.group(1)
            line = int(match.group(2))

        return file_path, line

    @staticmethod
    def _extract_function_names(text: str) -> List[str]:
        """Extract potential function names from text."""
        if not text:
            return []
        # Match CamelCase and snake_case identifiers
        names = re.findall(r'\b([A-Z][a-zA-Z0-9]+|[a-z][a-zA-Z0-9_]{3,})\b', text)
        # Filter common words
        stop_words = {'the', 'and', 'for', 'with', 'that', 'this', 'from',
                       'when', 'then', 'than', 'which', 'would', 'could',
                       'should', 'there', 'their', 'about', 'after', 'before'}
        return [n for n in names if n.lower() not in stop_words][:5]

    @staticmethod
    def _extract_call_chain(text: str) -> List[str]:
        """Extract a call chain from a step description."""
        if not text:
            return []
        # Match A->B->C pattern
        match = re.search(
            r'(\w+(?:\s*->\s*\w+)+)', text
        )
        if match:
            return [s.strip() for s in match.group(1).split('->')]
        return []

    @staticmethod
    def _extract_file_refs(text: str) -> List[str]:
        """Extract file references from text."""
        if not text:
            return []
        # Match "file.ts:123" or "src/main.py"
        refs = re.findall(
            r'([\w./-]+\.[a-z]{2,4}(?::\d+)?)', text
        )
        return refs
