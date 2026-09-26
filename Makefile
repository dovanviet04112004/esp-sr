.DEFAULT_GOAL := help
.PHONY: help gen check lint test golden measure report ci-status \
        fw-dev fw-bench fw-prod flash monitor broker-up broker-down host-live

PORT ?= /dev/ttyUSB0
SDKCONFIG_BASE := sdkconfig.defaults;sdkconfig.defaults.esp32s3
# The bench broker password rides in only when the builder holds it (KEHOACH 4.5.2).
SECRETS := $(if $(wildcard firmware/sdkconfig.secrets),;sdkconfig.secrets)

help:
	@grep -E '^[a-z0-9-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	 awk 'BEGIN{FS=":.*?## "};{printf "  \033[36m%-12s\033[0m %s\n",$$1,$$2}'

# contracts
gen: ## Generate C headers and Python modules from contracts/
	python3 tools/gen_contracts.py

check: gen ## Regenerate and fail if the committed output drifted
	git diff --exit-code

# checks
lint: ## check_comments + check_layers + check_purity + ruff
	python3 tools/check_comments.py
	python3 tools/check_layers.py
	python3 tools/check_purity.py
	uvx ruff check tools $(wildcard ml) $(wildcard host)

test: ## Host-side tests for tools/, ml/ and host/
	python3 -m unittest discover -s tools/tests -t .
	@if [ -f ml/pyproject.toml ]; then cd ml && uv run pytest; fi
	@if [ -f host/pyproject.toml ]; then cd host && uv run pytest; fi

# ci
ci-status: ## Copy the GitHub Actions results of HEAD onto the same commit in Gitea
	python3 tools/ci_status.py --wait

# measurements
golden: ## Emit golden vectors into contracts/golden/
	cd ml && ./scripts/41_emit_golden.sh

measure: ## Merge bench CSVs into docs/measurements/budget.md
	python3 tools/budget.py

report: measure ## Rebuild every number in docs/measurements/ with one command
	cd ml && ./scripts/60_eval_board.sh

# firmware build profiles (KEHOACH 4.5.8)
fw-dev: ## Build the dev profile
	cd firmware && idf.py -D SDKCONFIG_DEFAULTS="$(SDKCONFIG_BASE);sdkconfig.dev$(SECRETS)" build

fw-bench: ## Build the bench profile, the only one numbers are reported from
	cd firmware && idf.py -B build_bench -D SDKCONFIG=build_bench/sdkconfig \
	  -D SDKCONFIG_DEFAULTS="$(SDKCONFIG_BASE);sdkconfig.bench$(SECRETS)" build

fw-prod: ## Build the prod profile in build_prod, from its own sdkconfig
	rm -f firmware/build_prod/sdkconfig
	cd firmware && idf.py -B build_prod -D SDKCONFIG=build_prod/sdkconfig \
	  -D SDKCONFIG_DEFAULTS="$(SDKCONFIG_BASE);sdkconfig.prod$(SECRETS)" build

flash: ## Flash and monitor the dev profile over the CH340 port
	cd firmware && idf.py -p $(PORT) -D SDKCONFIG_DEFAULTS="$(SDKCONFIG_BASE);sdkconfig.dev$(SECRETS)" flash monitor

monitor: ## Open the serial monitor on the CH340 port
	cd firmware && idf.py -p $(PORT) monitor

# broker and host
broker-up: ## Start the bench MQTT broker
	cd deploy && docker compose up -d

broker-down: ## Stop the bench MQTT broker
	cd deploy && docker compose down

host-live: ## Show wake and command events live
	cd host && uv run python -m srhost.live
