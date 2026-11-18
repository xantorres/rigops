# STRICT=1 (exactly 1) turns a missing optional tool from a skip into a failure; CI sets it.
STRICT ?=
SKIP = if [ "$(STRICT)" = 1 ]; then echo "fail: $(1) not installed (STRICT)" >&2; exit 1; else echo "skip: $(1) not installed"; fi

.PHONY: check gate gate-history shellcheck ruff test test-hooks validate-manifests plugin-validate hooks smoke smoke-launchd

check: gate gate-history shellcheck ruff test test-hooks smoke validate-manifests plugin-validate

gate:
	bash tools/redaction-gate.sh

gate-history:
	bash tools/redaction-gate.sh --history

shellcheck:
	@if command -v shellcheck >/dev/null 2>&1; then \
		shellcheck $$(git ls-files '*.sh') .githooks/commit-msg .githooks/pre-push; \
	else \
		$(call SKIP,shellcheck); \
	fi

ruff:
	@if command -v ruff >/dev/null 2>&1; then \
		ruff check .; \
	else \
		$(call SKIP,ruff); \
	fi

test:
	python3 -m unittest discover -s tests

test-hooks:
	bash tests/hooks/run.sh

validate-manifests:
	python3 tools/validate-manifests.py

plugin-validate:
	@if command -v claude >/dev/null 2>&1; then \
		claude plugin validate --strict . && claude plugin validate --strict plugin/; \
	else \
		$(call SKIP,claude); \
	fi

hooks:
	git config core.hooksPath .githooks
	@echo "git hooks enabled (.githooks)"

smoke:
	bash tests/smoke.sh

smoke-launchd:
	bash tests/smoke-launchd.sh
