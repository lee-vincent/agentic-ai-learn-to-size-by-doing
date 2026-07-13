# Makefile — project-level convenience targets for the GPU/HPC sizing lab.
# (The per-experiment sweep CLI lives under experiment-cli/ and is separate from this file.)

.PHONY: help hooks-selftest hooks-enable destroy

help:
	@echo "Targets:"
	@echo "  make hooks-selftest   Behavioral test of the cost-guard hooks (uses a throwaway"
	@echo "                        state dir; does not touch your real .claude/state/)"
	@echo "  make hooks-enable     chmod +x the hook scripts (Claude Code's exec form needs it)"
	@echo "  make destroy          Canonical teardown: terraform destroy in infra/"

hooks-enable:
	chmod +x .claude/hooks/*.sh
	@echo "Hook scripts are now executable."

# Intentionally does NOT depend on hooks-enable: the self-test reports a missing exec bit as a
# WARN so you can see the real state of the repo. Run `make hooks-enable` to clear that warning.
hooks-selftest:
	@bash .claude/hooks/selftest.sh

# User-initiated teardown. Run from your own shell (not gated by cost-guard, which only governs
# commands Claude runs). Safe no-op message if infra/ hasn't been built yet.
destroy:
	@if [ -d infra ]; then \
		cd infra && terraform destroy ; \
	else \
		echo "infra/ not built yet — nothing to destroy." ; \
	fi
