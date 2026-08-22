.PHONY: check gate gate-history shellcheck ruff test validate-manifests plugin-validate hooks smoke smoke-launchd

check: gate gate-history shellcheck ruff test validate-manifests plugin-validate

gate:
	bash tools/redaction-gate.sh

gate-history:
	bash tools/redaction-gate.sh --history

shellcheck:
	@if command -v shellcheck >/dev/null 2>&1; then \
		shellcheck $$(git ls-files '*.sh') .githooks/commit-msg .githooks/pre-push; \
	else \
		echo "skip: shellcheck not installed"; \
	fi

ruff:
	@if command -v ruff >/dev/null 2>&1; then \
		ruff check .; \
	else \
		echo "skip: ruff not installed"; \
	fi

test:
	python3 -m unittest discover -s tests

validate-manifests:
	python3 tools/validate-manifests.py

plugin-validate:
	@if command -v claude >/dev/null 2>&1; then \
		claude plugin validate . && claude plugin validate plugin/; \
	else \
		echo "skip: claude not installed"; \
	fi

hooks:
	git config core.hooksPath .githooks
	@echo "git hooks enabled (.githooks)"

smoke:
	bash tests/smoke.sh

smoke-launchd:
	bash tests/smoke-launchd.sh
