.DEFAULT_GOAL := help
.PHONY: help gen check lint test golden measure report ci-status eval-vad eval-agc eval-ns eval-doa eval-pitch screen screen-audit spans splits wake-features wake-train eval-tts wake-synth extract-pilot extract-recut command-synth-pilot command-synth command-synth-make kws-split kws-features kws-train ctc-features ctc-train ctc-ptq ctc-int16 ctc-qat ctc-thresholds ctc-deploy rnnt-ptq ai-unit-rnnt listen-unit models-flash command-eval ns-data ns-pilot ns-smoke ns-train ns-eval espsr-compare parity-host \
        fw-dev fw-bench fw-prod flash monitor capture-flash broker-up broker-down host-live commands session session-plan

PORT ?= /dev/ttyUSB0
SDKCONFIG_BASE := sdkconfig.defaults;sdkconfig.defaults.esp32s3;sdkconfig.afe
# The bench broker password rides in only when the builder holds it (KEHOACH 4.5.2).
SECRETS := $(if $(wildcard firmware/sdkconfig.secrets),;sdkconfig.secrets)
# make session reads where to listen and where raw/ lives from host/.env, like srhost.config.
STREAM_PORT = $(shell sed -n 's/^SRHOST_STREAM_PORT=//p' host/.env 2>/dev/null)
DATA_ROOT = $(shell sed -n 's/^SRPIPE_DATA_ROOT=//p' host/.env 2>/dev/null)
PCM_SHIFT ?= 16
PLAN_FW ?= capture@$(shell git describe --always --tags)
# idf.py only adds options a generated sdkconfig lacks, so one older than its defaults, their list or a Kconfig
# default is dropped.
fresh_sdkconfig = if [ -f $(1) ] && [ -n "$$(find $(2) -newer $(1))" ]; then rm $(1); echo "$(1) regenerated"; fi
FW_DEFAULTS := firmware/sdkconfig.defaults firmware/sdkconfig.defaults.esp32s3 firmware/sdkconfig.afe \
               firmware/CMakeLists.txt $(wildcard firmware/sdkconfig.secrets) \
               $(wildcard firmware/components/*/Kconfig) firmware/main/Kconfig.projbuild
BENCH_APP := firmware/test_apps/bench_afe
UNIT_APP := firmware/components/ai_engine/test_apps/unit
PARITY_APP := firmware/test_apps/parity
LISTEN_APP := firmware/components/svc_listen/test_apps/unit
ESPSR_APP := firmware/test_apps/espsr_compare
# Where srpipe.scenes.compare prepare wrote the items, read from ml/ only when a recipe needs it.
COMPARE_ITEMS = $(shell cd ml && uv run --quiet python -c "from srpipe.core.config import data_paths; print(data_paths()['interim'] / 'scenes' / 'afe_compare')")
PARITY_DEFAULTS := firmware/sdkconfig.defaults.esp32s3 firmware/sdkconfig.bench $(PARITY_APP)/sdkconfig.defaults \
                   $(PARITY_APP)/CMakeLists.txt
HOST_BUILD := build/host
HOST_RUNS := dsp_spec/dsp_spec_host dsp_afe_host_off dsp_afe_host_on dsp_afe_host_product lang_vi/lang_vi_host \
             ai_engine/ai_engine_parity_host
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
	@if [ -f host/pyproject.toml ]; then cd host && uv run pytest; fi

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

eval-pitch: ## Measure the pitch mirror against Kaldi itself on VIVOS test, on the CPU (docs/measurements/pitch.md)
	cd ml && CUDA_VISIBLE_DEVICES= uv run python -m srpipe.metrics.pitch

screen: ## Measure every clip of every corpus once, then list what the rules reject (E11-T16, docs/measurements/data_screen.md)
	cd ml && uv run python -m srpipe.core.screen measure && uv run python -m srpipe.core.screen judge

screen-audit: screen ## Hear clips in bins of each speech measure through PhoWhisper, to place the rules (docs/measurements/data_screen.md)
	cd ml && uv run python -m srpipe.core.screen audit

spans: ## Cut a corpus's long rows at word bounds into spans a training window holds, aligner in Docker: make spans NAME=vimd (KEHOACH 1.2)
	cd ml && uv run python -m srpipe.core.spans $(NAME)

splits: screen ## Cut the wake and command splits from screened clips into ml/data/splits (KEHOACH 1.3)
	cd ml && uv run python -m srpipe.tasks.wake.data split && uv run python -m srpipe.tasks.command.ctc.data

wake-features: ## Run each file of the wake split through the board simulation into processed/wake (E4-T8)
	cd ml && uv run python -m srpipe.tasks.wake.data simulate

wake-train: ## Train the wake TCN on processed/wake/<split> on the GPU into ml/artifacts/wake/runs (E11-T11)
	cd ml && uv run --extra train python -m srpipe.tasks.wake.train

eval-tts: screen ## Compare the desktop TTS engines on the wake word, every clip heard back by PhoWhisper (docs/measurements/tts_engines.md)
	cd ml && uv run python -m srpipe.tasks.wake.synth pilot

wake-synth: screen ## Synthesise the wake positives and near misses, keep what the checker allows (E11-T7)
	cd ml && uv run python -m srpipe.tasks.wake.synth positives && uv run python -m srpipe.tasks.wake.synth negatives \
	  && uv run python -m srpipe.tasks.wake.synth select

extract-pilot: ## A sample of every phrase of hf_extract fetched, cut and copied to cache/listen/hf_extract, before the whole run
	cd ml && uv run python -m srpipe.core.extract hf_extract fetch --pilot
	cd ml && uv run python -m srpipe.core.extract hf_extract cut
	cd ml && uv run python -m srpipe.core.extract hf_extract listen

extract-recut: ## Cut hf_extract's sentences of the corpora on disk again under the current rule, then copy samples to listen to
	cd ml && uv run python -m srpipe.core.extract hf_extract recut
	cd ml && uv run python -m srpipe.core.extract hf_extract listen

command-synth-pilot: screen ## Synthesise a few command clips and near misses to hear before the overnight run (E11-T7)
	cd ml && uv run python -m srpipe.tasks.command.synth pilot

command-synth: screen ## Synthesise the command clips, near misses and halves, keep what the checker allows (E11-T7)
	cd ml && uv run python -m srpipe.tasks.command.synth positives && uv run python -m srpipe.tasks.command.synth negatives \
	  && uv run python -m srpipe.tasks.command.synth select

command-synth-make: screen ## Make the command clips and near misses without the checker; make command-synth hears them later (E11-T7)
	cd ml && uv run python -m srpipe.tasks.command.synth positives --make-only \
	  && uv run python -m srpipe.tasks.command.synth negatives --make-only

kws-split: screen ## Cut command_kws/v1 from the command TTS, real clips, ordinary speech and noise into ml/data/splits (E11-T17)
	cd ml && uv run python -m srpipe.tasks.command.kws.data split

kws-features: ## Run each file of command_kws/v1 through the board simulation with pitch into processed/command_kws (E11-T17)
	cd ml && uv run python -m srpipe.tasks.command.kws.data simulate

kws-train: ## Train the kws DS-CNN on processed/command_kws on the GPU into ml/artifacts/command_kws/runs (E11-T17)
	cd ml && uv run --extra train python -m srpipe.tasks.command.kws.train

ctc-features: ## Run each file of the command split through the board simulation with pitch into processed/command, then cut the board sessions with Gate 3's code, which needs torch; Ctrl-C pauses at once, run again it goes on from the finished shards (E11-T12)
	cd ml && uv run --extra train --extra swiftf0 python -m srpipe.tasks.command.ctc.data simulate

ctc-train: ## Train the ctc net on processed/command on the GPU into ml/artifacts/command_ctc/runs; RESUME=<run under ml/> goes on from its last checkpoint (E11-T12)
	cd ml && uv run --extra train python -m srpipe.tasks.command.ctc.train $(if $(RESUME),--resume $(RESUME))

ctc-ptq: ## Rungs 1 and 2 of KEHOACH 3.14 on a trained ctc run, a row of each calibration beside float: make ctc-ptq RUN=<run under ml/> (E11-T12)
	cd ml && CUDA_VISIBLE_DEVICES= uv run --extra train --extra espdl python -m srpipe.tasks.command.ctc.quant ptq $(RUN)

rnnt-ptq: ## Rung 2 of the rnnt track on a trained run: its three graphs with each calibration, Gate 3 after int8 beside float, rows rnnt_* in the run's ladder: make rnnt-ptq RUN=<run under ml/> (E11-T20)
	cd ml && CUDA_VISIBLE_DEVICES= uv run --extra train --extra espdl python -m srpipe.tasks.command.rnnt.quant ptq $(RUN)

ctc-int16: ## Rung 3: the convolutions ESP-PPQ ranks worst at 16 bits, on the best calibration of ctc-ptq: make ctc-int16 RUN=<run under ml/> (E11-T12)
	cd ml && CUDA_VISIBLE_DEVICES= uv run --extra train --extra espdl python -m srpipe.tasks.command.ctc.quant int16 $(RUN)

ctc-qat: ## Rung 4: QAT on the GPU from the best calibration of ctc-ptq: make ctc-qat RUN=<run under ml/> (E11-T12)
	cd ml && uv run --extra train --extra espdl python -m srpipe.tasks.command.ctc.quant qat $(RUN)

ctc-thresholds: ## Choose delta1 and delta2 of a ladder row on val_commands and val, decided as the chip decides: make ctc-thresholds RUN=<run under ml/> ROW=<row> (E11-T13)
	cd ml && CUDA_VISIBLE_DEVICES= uv run --extra train --extra espdl python -m srpipe.tasks.command.ctc.quant thresholds $(RUN) --row $(ROW)

ctc-deploy: ## Export a ladder row and its chosen delta1, delta2 into firmware/models/command and lock them: make ctc-deploy RUN=<run under ml/> ROW=<row> (E11-T19)
	cd ml && CUDA_VISIBLE_DEVICES= uv run --extra train --extra espdl python -m srpipe.tasks.command.ctc.quant deploy $(RUN) --row $(ROW)

models-flash: ## Pack every model of contracts/models.lock.json and write models_0 of board B (KEHOACH 4.5.6, 6.1)
	cd ml && PORT=$(PORT) ./scripts/50_pack_and_flash.sh

command-eval: ## Score Gate 3 of a command track on the board sessions: make command-eval TRACK=kws|ctc|rnnt RUN=<run under ml/> [SET=<command set file from the repo root>] (E11-T13, E11-T17)
	cd ml && uv run --extra train --extra swiftf0 python -m srpipe.tasks.command.eval $(TRACK) $(RUN) $(if $(SET),--commands $(abspath $(SET)))

ns-data: screen ## Split, pools and held val/test sets of the ns branch into data/splits/ns, interim and processed (E9-T3)
	cd ml && uv run python -m srpipe.tasks.ns.data clean && uv run python -m srpipe.tasks.ns.data split \
	  && uv run python -m srpipe.tasks.ns.data pool && uv run python -m srpipe.tasks.ns.data sets

ns-pilot: ## Write ns training examples and a DNSMOS report of the targets to cache/listen/ns/pilot, to hear first (E9-T3)
	cd ml && uv run python -m srpipe.tasks.ns.data pilot

ns-smoke: ## Train every ns candidate a few small steps on the CPU through the real loader, to time it (E9-T4)
	cd ml && CUDA_VISIBLE_DEVICES= uv run --extra train python -m srpipe.tasks.ns.train --smoke

ns-train: ## Train RNNoise-16k and NSNet-16k S/M/L on identical batches on the GPU; RESUME=<run under ml/> goes on from its last epoch (E9-T4)
	cd ml && uv run --extra train python -m srpipe.tasks.ns.train $(if $(RESUME),--resume $(RESUME))

ns-eval: ## Score the last ns run against the OM-LSA floor on the held val and test sets into its eval/ (E9-T4)
	cd ml && uv run --extra train python -m srpipe.tasks.ns.eval score --set val \
	  && uv run --extra train python -m srpipe.tasks.ns.eval score --set test

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
	cd firmware/test_apps/bench_afe && pytest pytest_bench_afe.py --rootdir . --embedded-services esp,idf --target esp32s3 --port $(PORT) -s -p no:cacheprovider
	python3 -m tools.budget

espsr-compare: ## Run every board variant of KEHOACH 3.16 on board B into interim/scenes/afe_compare; ONLY="<item> ..." limits it
	@$(call fresh_sdkconfig,$(ESPSR_APP)/sdkconfig,firmware/sdkconfig.defaults.esp32s3 firmware/sdkconfig.bench $(ESPSR_APP)/sdkconfig.defaults $(ESPSR_APP)/CMakeLists.txt)
	cd $(ESPSR_APP) && idf.py build
	cd $(ESPSR_APP) && ESPSR_COMPARE_ITEMS=$(COMPARE_ITEMS) ESPSR_COMPARE_ONLY="$(ONLY)" pytest pytest_espsr_compare.py \
	  --rootdir . --embedded-services esp,idf --target esp32s3 --port $(PORT) -s -p no:cacheprovider

parity-host: ## Run every golden case through dsp_spec, dsp_afe (modules off, all on, the product's), lang_vi and kws on the host
	cd firmware/components/dsp_afe/test_apps/host && cmake -S . -B $(CURDIR)/$(HOST_BUILD) -DCMAKE_BUILD_TYPE=Release
	cd $(HOST_BUILD) && cmake --build . -j
	cd firmware/components/lang_vi/test_apps/host && cmake -S . -B $(CURDIR)/$(HOST_BUILD)/lang_vi -DCMAKE_BUILD_TYPE=Release
	cd $(HOST_BUILD)/lang_vi && cmake --build . -j
	cd firmware/components/ai_engine/test_apps/host && cmake -S . -B $(CURDIR)/$(HOST_BUILD)/ai_engine \
	  -DCMAKE_BUILD_TYPE=Release -DAI_ENGINE_HOST_PARITY_ONLY=ON
	cd $(HOST_BUILD)/ai_engine && cmake --build . -j
	@: > $(HOST_BUILD)/parity.log; for run in $(HOST_RUNS); do \
	  $(HOST_BUILD)/$$run contracts/golden >> $(HOST_BUILD)/parity.log || { tail -n 30 $(HOST_BUILD)/parity.log; exit 1; }; \
	done; grep -h "^HOST [0-9]* failure" $(HOST_BUILD)/parity.log
	$(PARITY_PY) $(PARITY_APP)/pytest_parity.py $(HOST_BUILD)/parity.log

parity-board: ## Run every golden case on board B: the default chain build, then the build with the real modules on
	@$(call fresh_sdkconfig,$(PARITY_APP)/sdkconfig,$(PARITY_DEFAULTS))
	@$(call fresh_sdkconfig,$(PARITY_APP)/build_modules/sdkconfig,$(PARITY_DEFAULTS) firmware/sdkconfig.afe)
	cd firmware/test_apps/parity && idf.py build
	cd firmware/test_apps/parity && idf.py -B build_modules -D SDKCONFIG=build_modules/sdkconfig -D PARITY_PROFILE=modules build
	cd firmware/test_apps/parity && pytest pytest_parity.py --rootdir . --embedded-services esp,idf --target esp32s3 --port $(PORT) --build-dir build -p no:cacheprovider
	cd firmware/test_apps/parity && pytest pytest_parity.py --rootdir . --embedded-services esp,idf --target esp32s3 --port $(PORT) --build-dir build_modules -p no:cacheprovider

calib-estimate: ## Estimate balance from frontal white noise sessions: make calib-estimate SESSIONS="<dir> <dir> ..."
	cd host && uv run python -m srhost.calib estimate $(SESSIONS)

calib-flash: ## Flash test_apps/calib, the console that stores NVS calib/* (E2-T6)
	cd firmware/test_apps/calib && idf.py -p $(PORT) flash

calib-write: ## Write a balance file to the board: make calib-write CSV=docs/measurements/calib/<board>_balance.csv
	cd host && uv run python -m srhost.calib write ../$(CSV) --port $(PORT)

calib-shift: ## Store NVS calib/pcm_shift through test_apps/calib: make calib-shift SHIFT=13 (E2-T5)
	@test -n "$(SHIFT)" || { echo "usage: make calib-shift SHIFT=<8..16>"; exit 1; }
	cd host && uv run python -m srhost.calib shift $(SHIFT) --port $(PORT)

ai-probe: ## Export the probes of E11-T10 (TCN), E11-T17 (kws), E11-T12 (ctc; CTC_RUN=<run under ml/> CTC_ROW=<row of its int8/ladder.yaml> streams that graph) and E9-T10 (ns) into ai_engine/test_apps/unit
	cd ml && CUDA_VISIBLE_DEVICES= uv run --extra train --extra espdl python -m srpipe.tasks.wake.quant probe
	cd ml && CUDA_VISIBLE_DEVICES= uv run --extra train --extra espdl python -m srpipe.tasks.command.kws.quant probe
	cd ml && CUDA_VISIBLE_DEVICES= uv run --extra train --extra espdl python -m srpipe.tasks.command.ctc.probe $(if $(CTC_RUN),--run $(CTC_RUN) --row $(CTC_ROW))
	cd ml && CUDA_VISIBLE_DEVICES= uv run --extra train --extra espdl python -m srpipe.tasks.ns.quant probe

ai-unit: ai-probe ## Run the ai_engine suite on board B at bench's compiler settings; both model slots end erased; with CTC_RUN, Gate 3 counted on the chip's decisions
	@$(call fresh_sdkconfig,$(UNIT_APP)/sdkconfig,firmware/sdkconfig.defaults.esp32s3 firmware/sdkconfig.bench \
	  $(UNIT_APP)/sdkconfig.defaults $(UNIT_APP)/CMakeLists.txt)
	cd $(UNIT_APP) && idf.py build && \
	  python $$IDF_PATH/components/partition_table/parttool.py --port $(PORT) \
	    --partition-table-file ../../../../test_apps/partitions_unit.csv write_partition --partition-name models_0 \
	    --input main/probe/ctc_models.bin && \
	  python $$IDF_PATH/components/partition_table/parttool.py --port $(PORT) \
	    --partition-table-file ../../../../test_apps/partitions_unit.csv write_partition --partition-name models_1 \
	    --input main/probe/ns_models.bin && \
	  if [ -f main/probe/ctc_gate.bin ]; then \
	    python $$IDF_PATH/components/partition_table/parttool.py --port $(PORT) \
	      --partition-table-file ../../../../test_apps/partitions_unit.csv write_partition --partition-name voice \
	      --input main/probe/ctc_gate.bin; \
	  else \
	    python $$IDF_PATH/components/partition_table/parttool.py --port $(PORT) \
	      --partition-table-file ../../../../test_apps/partitions_unit.csv erase_partition --partition-name voice; \
	  fi && \
	  { pytest pytest_unit.py --rootdir . --embedded-services esp,idf --target esp32s3 --port $(PORT) -s \
	      -p no:cacheprovider; echo $$? > build/unit.status; } 2>&1 | tee build/unit.log; \
	  exit $$(cat build/unit.status)
	@if [ -f $(UNIT_APP)/main/probe/ctc_gate.json ]; then \
	  cd ml && uv run --extra train python -m srpipe.tasks.command.ctc.probe --gate-log ../$(UNIT_APP)/build/unit.log; \
	fi

ai-unit-rnnt: ## Run the rnnt build of the ai_engine suite on board B: rnnt/probe.py's three graphs in model slot 0, every rnnt call against Python, timed; RNNT_RUN=<run under ml/> RNNT_ROW=<rnnt_* row> runs that row and Gate 3 on the chip; slot 0 ends erased (E11-T20)
	cd ml && CUDA_VISIBLE_DEVICES= uv run --extra train --extra espdl python -m srpipe.tasks.command.rnnt.probe \
	  $(if $(RNNT_RUN),--run $(RNNT_RUN) --row $(RNNT_ROW))
	@$(call fresh_sdkconfig,$(UNIT_APP)/build_rnnt/sdkconfig,firmware/sdkconfig.defaults.esp32s3 firmware/sdkconfig.bench \
	  $(UNIT_APP)/sdkconfig.defaults $(UNIT_APP)/CMakeLists.txt)
	cd $(UNIT_APP) && idf.py -B build_rnnt -D SDKCONFIG=build_rnnt/sdkconfig -D UNIT_PROFILE=rnnt build && \
	  python $$IDF_PATH/components/partition_table/parttool.py --port $(PORT) \
	    --partition-table-file ../../../../test_apps/partitions_unit.csv write_partition --partition-name models_0 \
	    --input main/probe/rnnt_models.bin && \
	  if [ -f main/probe/rnnt_gate.bin ]; then \
	    python $$IDF_PATH/components/partition_table/parttool.py --port $(PORT) \
	      --partition-table-file ../../../../test_apps/partitions_unit.csv write_partition --partition-name voice \
	      --input main/probe/rnnt_gate.bin; \
	  else \
	    python $$IDF_PATH/components/partition_table/parttool.py --port $(PORT) \
	      --partition-table-file ../../../../test_apps/partitions_unit.csv erase_partition --partition-name voice; \
	  fi && \
	  { pytest pytest_unit.py --rootdir . --embedded-services esp,idf --target esp32s3 --port $(PORT) \
	      --build-dir build_rnnt -s -p no:cacheprovider; echo $$? > build_rnnt/unit.status; } 2>&1 | tee build_rnnt/unit.log; \
	  exit $$(cat build_rnnt/unit.status)
	@if [ -f $(UNIT_APP)/main/probe/rnnt_gate.json ]; then \
	  cd ml && uv run --extra train python -m srpipe.tasks.command.rnnt.probe --gate-log ../$(UNIT_APP)/build_rnnt/unit.log; \
	fi

listen-unit: ## Run svc_listen on board B over every Gate 3 session, round by round: the locked models in model slot 0, each window decided as Python decides its int8 simulation, field by field (E11-T14)
	cd ml && CUDA_VISIBLE_DEVICES= uv run --extra train --extra espdl python -m srpipe.tasks.command.ctc.probe --listen
	cd ml && uv run python -m srpipe.export.pack_models artifacts/models.bin --lock
	@$(call fresh_sdkconfig,$(LISTEN_APP)/sdkconfig,firmware/sdkconfig.defaults.esp32s3 firmware/sdkconfig.bench \
	  $(LISTEN_APP)/sdkconfig.defaults $(LISTEN_APP)/CMakeLists.txt)
	cd $(LISTEN_APP) && idf.py build
	python $$IDF_PATH/components/partition_table/parttool.py --port $(PORT) --partition-table-file firmware/test_apps/partitions_unit.csv \
	  write_partition --partition-name models_0 --input ml/artifacts/models.bin
	@skip=n; for round in $$(ls $(LISTEN_APP)/main/probe/listen_*_voice.bin | sed 's/.*listen_\([0-9]*\)_voice.bin/\1/' | sort -n); do \
	  for part in models_1 voice; do \
	    python $$IDF_PATH/components/partition_table/parttool.py --port $(PORT) --partition-table-file firmware/test_apps/partitions_unit.csv \
	      write_partition --partition-name $$part --input $(LISTEN_APP)/main/probe/listen_$${round}_$$part.bin || exit 1; \
	  done; \
	  { cd $(LISTEN_APP) && pytest pytest_unit.py --rootdir . --embedded-services esp,idf --target esp32s3 --port $(PORT) \
	      -s -p no:cacheprovider --skip-autoflash $$skip; echo $$? > build/listen.status; cd $(CURDIR); } 2>&1 \
	    | tee $(LISTEN_APP)/build/listen_$$round.log; \
	  [ $$(cat $(LISTEN_APP)/build/listen.status) -eq 0 ] || exit 1; skip=y; \
	done

# broker and host
broker-up: ## Start the bench MQTT broker (needs deploy/.env and deploy/emqx/users.csv)
	@test -f deploy/.env || { echo "copy deploy/.env.example to deploy/.env and fill it"; exit 1; }
	@test -f deploy/emqx/users.csv || { echo "copy deploy/emqx/users.csv.example to deploy/emqx/users.csv and fill it"; exit 1; }
	cd deploy && docker compose up -d --wait

broker-down: ## Stop the bench MQTT broker
	cd deploy && docker compose down

host-live: ## Show wake and command events live
	cd host && uv run python -m srhost.live

commands: ## Send a command set to one board, retained, and show its answer: make commands DEVICE=<deviceId> SET=<file.json> [CLEAR=1]
	cd host && uv run python -m srhost.commands --device "$(DEVICE)" $(if $(CLEAR),--clear,"$(abspath $(SET))")

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
