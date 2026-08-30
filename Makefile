.PHONY: install-hooks test benchmark

## install-hooks: symlink scripts/pre-push (and any future hooks) into .git/hooks/
install-hooks:
	@bash scripts/install-hooks.sh

## test: run the full test suite
test:
	python -m pytest tests/ -q --tb=short

## benchmark: run the full benchmark suite
benchmark:
	python -m smartbench benchmark run --manifest benchmarks/real/manifest.yaml
