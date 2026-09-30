.DEFAULT_GOAL := help
.PHONY: help gen check lint test golden measure report ci-status eval-vad eval-agc eval-ns eval-doa screen screen-audit splits wake-features wake-train eval-tts wake-synth command-synth-pilot command-synth parity-host \
        fw-dev fw-bench fw-prod flash monitor capture-flash broker-up broker-down host-live session session-plan

PORT ?= /dev/ttyUSB0
SDKCONFIG_BASE := sdkconfig.defaults;sdkconfig.defaults.esp32s3;sdkconfig.afe
# The bench broker password rides in only when the builder holds it (KEHOACH 4.5.2).
SECRETS := $(if $(wildcard firmware/sdkconfig.secrets),;sdkconfig.secrets)
# make session reads where to listen and where raw/ lives from host/.env, like srhost.config.
STREAM_PORT = $(shell sed -n 's/^SRHOST_STREAM_PORT=//p' host/.env 2>/dev/null)
DATA_ROOT = $(shell sed -n 's/^SRPIPE_DATA_ROOT=//p' host/.env 2>/dev/null)
PCM_SHIFT ?= 16
PLAN_FW ?= capture@$(shell git describe --always --tags)
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

eval-doa: ## Score doa on the labelled standard scenes by band, condition and region (docs/measurements/afe/doa.md)
	cd ml && uv run python -m srpipe.scenes.spatial doa --workers 16

screen: ## Measure every clip of every corpus once, then list what the rules reject (E11-T16, docs/measurements/data_screen.md)
	cd ml && uv run python -m srpipe.core.screen measure && uv run python -m srpipe.core.screen judge

screen-audit: screen ## Hear clips in bins of each speech measure through PhoWhisper, to place the rules (docs/measurements/data_screen.md)
	cd ml && uv run python -m srpipe.core.screen audit

splits: screen ## Cut the wake and command splits from screened clips into ml/data/splits (KEHOACH 1.3)
	cd ml && uv run python -m srpipe.tasks.wake.data split && uv run python -m srpipe.tasks.command.data

wake-features: ## Run each file of the wake split through the board simulation into processed/wake (E4-T8)
	cd ml && uv run python -m srpipe.tasks.wake.data simulate

wake-train: ## Train the wake TCN on processed/wake/<split> on the GPU into ml/artifacts/wake/runs (E11-T11)
	cd ml && uv run --extra train python -m srpipe.tasks.wake.train

eval-tts: screen ## Compare the desktop TTS engines on the wake word, every clip heard back by PhoWhisper (docs/measurements/tts_engines.md)
	cd ml && uv run python -m srpipe.tasks.wake.synth pilot

wake-synth: screen ## Synthesise the wake positives and near misses, keep what the checker allows (E11-T7)
	cd ml && uv run python -m srpipe.tasks.wake.synth positives && uv run python -m srpipe.tasks.wake.synth negatives \
	  && uv run python -m srpipe.tasks.wake.synth select

command-synth-pilot: screen ## Synthesise a few command clips and near misses to hear before the overnight run (E11-T7)
	cd ml && uv run python -m srpipe.tasks.command.synth pilot

command-synth: screen ## Synthesise the command clips, near misses and halves, keep what the checker allows (E11-T7)
	cd ml && uv run python -m srpipe.tasks.command.synth positives && uv run python -m srpipe.tasks.command.synth negatives \
	  && uv run python -m srpipe.tasks.command.synth select

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

calib-shift: ## Store NVS calib/pcm_shift through test_apps/calib: make calib-shift SHIFT=13 (E2-T5)
	@test -n "$(SHIFT)" || { echo "usage: make calib-shift SHIFT=<8..16>"; exit 1; }
	cd host && uv run --extra score python -m srhost.calib shift $(SHIFT) --port $(PORT)

ai-probe: ## Export the streaming TCN probe of E11-T10 into ai_engine/test_apps/unit (ml extras train and espdl)
	cd ml && uv run --extra train --extra espdl python -m srpipe.tasks.wake.quant probe

ai-unit: ai-probe ## Run the ai_engine suite on board B; model slot 1 is rewritten and left erased
	cd firmware/components/ai_engine/test_apps/unit && idf.py build && \
	  pytest pytest_unit.py --embedded-services esp,idf --target esp32s3 --port $(PORT) -s -p no:cacheprovider

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

session-plan: ## Record a script of host/plans in turn: make session-plan PLAN=host/plans/<x>.tsv ROOM=<room> [SPK=spk_001 CONSENT=C001]
	@test -f "$(PLAN)" && test -n "$(ROOM)" || { echo "usage: make session-plan PLAN=host/plans/<x>.tsv ROOM=<room> [SPK= CONSENT=]"; exit 1; }
	@if grep -q '{spk}' "$(PLAN)" && [ -z "$(SPK)" -o -z "$(CONSENT)" ]; then echo "$(PLAN) has a speaker: give SPK and CONSENT"; exit 1; fi
	@n=0; grep -v '^#' "$(PLAN)" | sed -e 's/{spk}/$(SPK)/g' -e 's/{consent}/$(CONSENT)/g' | \
	while IFS='	' read -r say args; do \
	  n=$$((n + 1)); printf '\n[%d] %s\n    Enter: record, s: skip, q: stop > ' "$$n" "$$say"; read ans < /dev/tty; \
	  case "$$ans" in s) continue;; q) break;; esac; \
	  $(MAKE) --no-print-directory session \
	    ARGS="--room $(ROOM) --fw $(PLAN_FW) --pcm-shift $(PCM_SHIFT) $$args" < /dev/tty || exit 1; \
	done
