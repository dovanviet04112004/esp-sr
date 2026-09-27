.DEFAULT_GOAL := help
.PHONY: help gen check lint test golden measure report ci-status eval-vad eval-agc eval-ns parity-host \
        fw-dev fw-bench fw-prod flash monitor capture-flash broker-up broker-down host-live session

PORT ?= /dev/ttyUSB0
SDKCONFIG_BASE := sdkconfig.defaults;sdkconfig.defaults.esp32s3;sdkconfig.afe
# The bench broker password rides in only when the builder holds it (KEHOACH 4.5.2).
SECRETS := $(if $(wildcard firmware/sdkconfig.secrets),;sdkconfig.secrets)
# make session reads where to listen and where raw/ lives from host/.env, like srhost.config.
STREAM_PORT = $(shell sed -n 's/^SRHOST_STREAM_PORT=//p' host/.env 2>/dev/null)
DATA_ROOT = $(shell sed -n 's/^SRPIPE_DATA_ROOT=//p' host/.env 2>/dev/null)
# idf.py only adds options a generated sdkconfig lacks, so one older than its defaults or their list is dropped.
fresh_sdkconfig = if [ -f $(1) ] && [ -n "$$(find $(2) -newer $(1))" ]; then rm $(1); echo "$(1) regenerated"; fi
FW_DEFAULTS := firmware/sdkconfig.defaults firmware/sdkconfig.defaults.esp32s3 firmware/sdkconfig.afe \
               firmware/CMakeLists.txt $(wildcard firmware/sdkconfig.secrets)
BENCH_APP := firmware/test_apps/bench_afe
PARITY_APP := firmware/test_apps/parity
PARITY_DEFAULTS := firmware/sdkconfig.defaults.esp32s3 firmware/sdkconfig.bench $(PARITY_APP)/sdkconfig.defaults \
                   $(PARITY_APP)/CMakeLists.txt
HOST_BUILD := build/host
HOST_RUNS := dsp_spec/dsp_spec_host dsp_afe_host_off dsp_afe_host_on dsp_afe_host_product lang_vi/lang_vi_host
# The judge imports pytest: the ml environment has it here, CI installs it and passes PARITY_PY=python.
PARITY_PY ?= $(if $(shell command -v uv 2>/dev/null),uv run --project ml python,python3)

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
	@if [ -f host/pyproject.toml ]; then cd host && uv run --extra score pytest; fi

# ci
ci-status: ## Copy the GitHub Actions results of HEAD onto the same commit in Gitea
	python3 tools/ci_status.py --wait

# measurements
golden: ## Emit golden vectors into contracts/golden/
	cd ml && ./scripts/41_emit_golden.sh

eval-vad: ## Score vad against a bare energy threshold on labelled scenes of VIVOS test (docs/measurements/afe/vad.md)
	cd ml && uv run python -m srpipe.scenes.vad --workers 16

eval-agc: ## Score agc on VIVOS scenes at input levels -50 .. -10 dBFS through vad and agc (docs/measurements/afe/agc.md)
	cd ml && uv run python -m srpipe.scenes.agc --workers 16

eval-ns: ## Score the ns floor on VIVOS scenes: noise and speech lost, SNR gained (docs/measurements/afe/ns.md)
	cd ml && uv run python -m srpipe.scenes.ns --workers 16

measure: ## Merge bench CSVs into docs/measurements/budget.md
	python3 -m tools.budget

report: measure ## Rebuild every number in docs/measurements/ with one command
	cd ml && ./scripts/60_eval_board.sh

# firmware build profiles (KEHOACH 4.5.8)
fw-dev: ## Build the dev profile
	@$(call fresh_sdkconfig,firmware/sdkconfig,$(FW_DEFAULTS) firmware/sdkconfig.dev)
	cd firmware && idf.py -D SDKCONFIG_DEFAULTS="$(SDKCONFIG_BASE);sdkconfig.dev$(SECRETS)" build

fw-bench: ## Build the bench profile, the only one numbers are reported from
	@$(call fresh_sdkconfig,firmware/build_bench/sdkconfig,$(FW_DEFAULTS) firmware/sdkconfig.bench)
	cd firmware && idf.py -B build_bench -D SDKCONFIG=build_bench/sdkconfig \
	  -D SDKCONFIG_DEFAULTS="$(SDKCONFIG_BASE);sdkconfig.bench$(SECRETS)" build

fw-prod: ## Build the prod profile in build_prod, from its own sdkconfig
	rm -f firmware/build_prod/sdkconfig
	cd firmware && idf.py -B build_prod -D SDKCONFIG=build_prod/sdkconfig \
	  -D SDKCONFIG_DEFAULTS="$(SDKCONFIG_BASE);sdkconfig.prod$(SECRETS)" build

flash: ## Flash and monitor the dev profile over the CH340 port
	@$(call fresh_sdkconfig,firmware/sdkconfig,$(FW_DEFAULTS) firmware/sdkconfig.dev)
	cd firmware && idf.py -p $(PORT) -D SDKCONFIG_DEFAULTS="$(SDKCONFIG_BASE);sdkconfig.dev$(SECRETS)" flash monitor

monitor: ## Open the serial monitor on the CH340 port
	cd firmware && idf.py -p $(PORT) monitor

capture-flash: ## Flash test_apps/capture, the raw-only recorder (E5-T12)
	cd firmware/test_apps/capture && idf.py -p $(PORT) flash

capture-radio-off-flash: ## Flash capture that records 60 s with the radio off, then sends: the Wi-Fi off floor (E2-T4)
	cd firmware/test_apps/capture && idf.py -B build_radio_off -D SDKCONFIG=build_radio_off/sdkconfig \
	  -D CAPTURE_PROFILE=radio_off -p $(PORT) flash

bench-board: ## Run bench_afe on board B, keep its rows in docs/measurements/bench, then rebuild budget.md
	@$(call fresh_sdkconfig,$(BENCH_APP)/sdkconfig,firmware/sdkconfig.defaults.esp32s3 firmware/sdkconfig.bench firmware/sdkconfig.afe $(BENCH_APP)/sdkconfig.defaults $(BENCH_APP)/CMakeLists.txt)
	cd firmware/test_apps/bench_afe && idf.py build
	cd firmware/test_apps/bench_afe && pytest pytest_bench_afe.py --embedded-services esp,idf --target esp32s3 --port $(PORT) -s -p no:cacheprovider
	python3 -m tools.budget

parity-host: ## Run every golden case through dsp_spec, dsp_afe (modules off, all on, the product's) and lang_vi on the host
	cd firmware/components/dsp_afe/test_apps/host && cmake -S . -B $(CURDIR)/$(HOST_BUILD) -DCMAKE_BUILD_TYPE=Release
	cd $(HOST_BUILD) && cmake --build . -j
	cd firmware/components/lang_vi/test_apps/host && cmake -S . -B $(CURDIR)/$(HOST_BUILD)/lang_vi -DCMAKE_BUILD_TYPE=Release
	cd $(HOST_BUILD)/lang_vi && cmake --build . -j
	@: > $(HOST_BUILD)/parity.log; for run in $(HOST_RUNS); do \
	  $(HOST_BUILD)/$$run contracts/golden >> $(HOST_BUILD)/parity.log || { tail -n 30 $(HOST_BUILD)/parity.log; exit 1; }; \
	done; grep -h "^HOST [0-9]* failure" $(HOST_BUILD)/parity.log
	$(PARITY_PY) $(PARITY_APP)/pytest_parity.py $(HOST_BUILD)/parity.log

parity-board: ## Run every golden case on board B: the default chain build, then the build with the real modules on
	@$(call fresh_sdkconfig,$(PARITY_APP)/sdkconfig,$(PARITY_DEFAULTS))
	@$(call fresh_sdkconfig,$(PARITY_APP)/build_modules/sdkconfig,$(PARITY_DEFAULTS) firmware/sdkconfig.afe)
	cd firmware/test_apps/parity && idf.py build
	cd firmware/test_apps/parity && idf.py -B build_modules -D SDKCONFIG=build_modules/sdkconfig -D PARITY_PROFILE=modules build
	cd firmware/test_apps/parity && pytest pytest_parity.py --embedded-services esp,idf --target esp32s3 --port $(PORT) --build-dir build -p no:cacheprovider
	cd firmware/test_apps/parity && pytest pytest_parity.py --embedded-services esp,idf --target esp32s3 --port $(PORT) --build-dir build_modules -p no:cacheprovider

calib-estimate: ## Estimate balance from frontal white noise sessions: make calib-estimate SESSIONS="<dir> <dir> ..."
	cd host && uv run --extra score python -m srhost.calib estimate $(SESSIONS)

calib-flash: ## Flash test_apps/calib, the console that stores NVS calib/* (E2-T6)
	cd firmware/test_apps/calib && idf.py -p $(PORT) flash

calib-write: ## Write a balance file to the board: make calib-write CSV=docs/measurements/calib/<board>_balance.csv
	cd host && uv run --extra score python -m srhost.calib write ../$(CSV) --port $(PORT)

# broker and host
broker-up: ## Start the bench MQTT broker (needs deploy/.env and deploy/emqx/users.csv)
	@test -f deploy/.env || { echo "copy deploy/.env.example to deploy/.env and fill it"; exit 1; }
	@test -f deploy/emqx/users.csv || { echo "copy deploy/emqx/users.csv.example to deploy/emqx/users.csv and fill it"; exit 1; }
	cd deploy && docker compose up -d --wait

broker-down: ## Stop the bench MQTT broker
	cd deploy && docker compose down

host-live: ## Show wake and command events live
	cd host && uv run python -m srhost.live

# The LAN cannot reach WSL in NAT mode; a port Docker Desktop publishes on Windows it can (KEHOACH 4.6).
# The board waits in its bootloader until the receiver listens, so a session starts at seq 0 of one boot.
session: ## Record a labelled session: make session ARGS="--kind probe --room home --fw <ver+sha> --pcm-shift 16"
	@test -f host/.env || { echo "copy host/.env.example to host/.env and fill it"; exit 1; }
	@docker rm -f sr-session >/dev/null 2>&1 || true
	python -m esptool --chip esp32s3 -p $(PORT) --after no-reset chip-id >/dev/null
	@( for i in $$(seq 60); do docker logs sr-session 2>&1 | grep -q recording && break; sleep 1; done; \
	   python -m esptool --chip esp32s3 -p $(PORT) run >/dev/null && echo "board released on $(PORT)" ) &
	docker run --rm $$([ -t 0 ] && echo -it) --name sr-session --user $$(id -u):$$(id -g) --env-file host/.env \
	  -e PYTHONPATH=/repo/host/src -p $(STREAM_PORT):$(STREAM_PORT) \
	  -v "$(CURDIR)":/repo -v "$(DATA_ROOT)":"$(DATA_ROOT)" \
	  python:3.12-slim python -m srhost.session $(ARGS)
