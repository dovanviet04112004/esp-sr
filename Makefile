.DEFAULT_GOAL := help

# Board B on the CH340 that usbipd hands to WSL (KEHOACH 2.1).
PORT ?= /dev/ttyUSB0
BOARD_USB_ID ?= 1a86:7523

# The ml environment: every ml/ target runs there, the CPU-only ones with no GPU visible.
ML := cd ml && uv run
ML_CPU := cd ml && CUDA_VISIBLE_DEVICES= uv run

# Firmware profiles (KEHOACH 4.5.8); the bench broker password rides in only when the builder holds it (KEHOACH 4.5.2).
SDKCONFIG_BASE := sdkconfig.defaults;sdkconfig.defaults.esp32s3;sdkconfig.afe
SECRETS := $(if $(wildcard firmware/sdkconfig.secrets),;sdkconfig.secrets)
IDF_DEV = idf.py -D SDKCONFIG_DEFAULTS="$(SDKCONFIG_BASE);sdkconfig.dev$(SECRETS)"
IDF_BENCH = idf.py -B build_bench -D SDKCONFIG=build_bench/sdkconfig \
  -D SDKCONFIG_DEFAULTS="$(SDKCONFIG_BASE);sdkconfig.bench$(SECRETS)"
FW_DEFAULTS := firmware/sdkconfig.defaults firmware/sdkconfig.defaults.esp32s3 firmware/sdkconfig.afe \
               firmware/CMakeLists.txt $(wildcard firmware/sdkconfig.secrets) \
               $(wildcard firmware/components/*/Kconfig) firmware/main/Kconfig.projbuild
# idf.py only adds options a generated sdkconfig lacks, so one older than its defaults, their list or a Kconfig
# default is dropped.
fresh_sdkconfig = if [ -f $(1) ] && [ -n "$$(find $(2) -newer $(1))" ]; then rm $(1); echo "$(1) regenerated"; fi
# What a test app's sdkconfig is generated from: the bench profile and the app's own defaults.
app_defaults = firmware/sdkconfig.defaults.esp32s3 firmware/sdkconfig.bench $(1)/sdkconfig.defaults $(1)/CMakeLists.txt

# Test apps on board B, run by pytest-embedded from the app's folder.
BENCH_APP := firmware/test_apps/bench_afe
PARITY_APP := firmware/test_apps/parity
ESPSR_APP := firmware/test_apps/espsr_compare
CAPTURE_APP := firmware/test_apps/capture
CALIB_APP := firmware/test_apps/calib
UNIT_APP := firmware/components/ai_engine/test_apps/unit
LISTEN_APP := firmware/components/svc_listen/test_apps/unit
ON_BOARD = --rootdir . --embedded-services esp,idf --target esp32s3 --port $(PORT) -p no:cacheprovider
UNIT_PARTITIONS := $(CURDIR)/firmware/test_apps/partitions_unit.csv
PARTTOOL = python $$IDF_PATH/components/partition_table/parttool.py --port $(PORT) --partition-table-file $(UNIT_PARTITIONS)
WRITE_PART = $(PARTTOOL) write_partition --partition-name
ERASE_PART = $(PARTTOOL) erase_partition --partition-name

##@ Help

.PHONY: help
help: ## List the targets by group
	@awk 'BEGIN {FS = ":.*## "} \
	  /^##@ / {printf "\n\033[1m%s\033[0m\n", substr($$0, 5)} \
	  /^[a-z0-9-]+:.*## / {printf "  \033[36m%-24s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

##@ Contracts and checks

.PHONY: gen check lint test ci-status
gen: ## Generate C headers and Python modules from contracts/
	python3 tools/gen_contracts.py

check: gen ## Regenerate and fail if the committed output drifted
	git diff --exit-code

lint: ## check_comments + check_layers + check_purity + ruff
	python3 tools/check_comments.py
	python3 tools/check_layers.py
	python3 tools/check_purity.py
	uvx ruff check tools $(wildcard ml) $(wildcard host)

test: ## Host-side tests for tools/, ml/ and host/
	python3 -m unittest discover -s tools/tests -t .
	@if [ -f ml/pyproject.toml ]; then cd ml && uv run pytest; fi
	@if [ -f host/pyproject.toml ]; then cd host && uv run pytest; fi

ci-status: ## Copy the GitHub Actions results of HEAD onto the same commit in Gitea
	python3 tools/ci_status.py --wait

##@ Golden and parity

HOST_BUILD := build/host
HOST_RUNS := dsp_spec/dsp_spec_host dsp_afe_host_off dsp_afe_host_on dsp_afe_host_product lang_vi/lang_vi_host \
             ai_engine/ai_engine_parity_host
# The judge imports pytest: the ml environment has it here, CI installs it and passes PARITY_PY=python.
PARITY_PY ?= $(if $(shell command -v uv 2>/dev/null),uv run --project ml python,python3)

.PHONY: golden parity-host parity-board
golden: ## Emit golden vectors into contracts/golden/
	cd ml && ./scripts/41_emit_golden.sh

parity-host: ## Every golden case through dsp_spec, dsp_afe (off, all on, the product's), lang_vi and kws on the host
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

parity-board: ## Every golden case on board B: the default chain build, then the build with the real modules on
	@$(call fresh_sdkconfig,$(PARITY_APP)/sdkconfig,$(call app_defaults,$(PARITY_APP)))
	@$(call fresh_sdkconfig,$(PARITY_APP)/build_modules/sdkconfig,$(call app_defaults,$(PARITY_APP)) firmware/sdkconfig.afe)
	cd $(PARITY_APP) && idf.py build
	cd $(PARITY_APP) && idf.py -B build_modules -D SDKCONFIG=build_modules/sdkconfig -D PARITY_PROFILE=modules build
	cd $(PARITY_APP) && pytest pytest_parity.py $(ON_BOARD) --build-dir build
	cd $(PARITY_APP) && pytest pytest_parity.py $(ON_BOARD) --build-dir build_modules

##@ Data: screening, spans, splits

.PHONY: screen screen-audit spans splits extract-pilot extract-recut
screen: ## Measure every clip of every corpus once, then list what the rules reject (E11-T16, data_screen.md)
	$(ML) python -m srpipe.core.screen measure && uv run python -m srpipe.core.screen judge

screen-audit: screen ## Hear clips in bins of each speech measure through PhoWhisper, to place the rules (data_screen.md)
	$(ML) python -m srpipe.core.screen audit

spans: ## Cut a corpus's long rows at word bounds into training spans, aligner in Docker: NAME=vimd (KEHOACH 1.2)
	$(ML) python -m srpipe.core.spans $(NAME)

splits: screen ## Cut the wake and command splits from screened clips into ml/data/splits (KEHOACH 1.3)
	$(ML) python -m srpipe.tasks.wake.data split && uv run python -m srpipe.tasks.command.ctc.data

extract-pilot: ## Fetch, cut and copy a sample of every hf_extract phrase to cache/listen/hf_extract, before the whole run
	$(ML) python -m srpipe.core.extract hf_extract fetch --pilot
	$(ML) python -m srpipe.core.extract hf_extract cut
	$(ML) python -m srpipe.core.extract hf_extract listen

extract-recut: ## Cut hf_extract's sentences on disk again under the current rule, then copy samples to listen to
	$(ML) python -m srpipe.core.extract hf_extract recut
	$(ML) python -m srpipe.core.extract hf_extract listen

##@ Front end on simulated scenes (docs/measurements/afe, pitch.md)

.PHONY: eval-vad eval-agc eval-ns eval-doa eval-pitch
eval-vad: ## Score vad against a bare energy threshold on labelled VIVOS test scenes
	$(ML) python -m srpipe.scenes.vad --workers 16

eval-agc: ## Score agc on VIVOS scenes at input levels -50 .. -10 dBFS through vad and agc
	$(ML) python -m srpipe.scenes.agc --workers 16

eval-ns: ## Score the ns floor on VIVOS scenes: noise and speech lost, SNR gained
	$(ML) python -m srpipe.scenes.ns --workers 16

eval-doa: ## Score doa on the labelled standard scenes by band, condition and region
	$(ML) python -m srpipe.scenes.spatial doa --workers 16

eval-pitch: ## Measure the pitch mirror against Kaldi itself on VIVOS test, on the CPU
	$(ML_CPU) python -m srpipe.metrics.pitch

##@ Speaker verification survey on the PC (KEHOACH 3.17, E11-T24)

.PHONY: eval-speaker
# The owner's command windows of Gate 3 and the command split's test speakers through the board simulation, every
# extractor of configs/scenes/speaker.yaml through its uv project under ml/spk_ref; embeddings stay in cache (KEHOACH 1.4).
eval-speaker: ## EER of the pretrained extractors, and the owner's windows kept at 1% of impostors passing
	$(ML) --extra train python -m srpipe.scenes.speaker

##@ Wake

.PHONY: wake-features wake-train eval-tts wake-synth
wake-features: ## Run each file of the wake split through the board simulation into processed/wake (E4-T8)
	$(ML) python -m srpipe.tasks.wake.data simulate

wake-train: ## Train the wake TCN on processed/wake/<split> on the GPU into ml/artifacts/wake/runs (E11-T11)
	$(ML) --extra train python -m srpipe.tasks.wake.train

eval-tts: screen ## Compare the desktop TTS engines on the wake word, heard back by PhoWhisper (tts_engines.md)
	$(ML) python -m srpipe.tasks.wake.synth pilot

wake-synth: screen ## Synthesise the wake positives and near misses, keep what the checker allows (E11-T7)
	$(ML) python -m srpipe.tasks.wake.synth positives && uv run python -m srpipe.tasks.wake.synth negatives \
	  && uv run python -m srpipe.tasks.wake.synth select

##@ Command: synthesis

.PHONY: command-synth-pilot command-synth command-synth-make
command-synth-pilot: screen ## Synthesise a few command clips and near misses to hear before the overnight run (E11-T7)
	$(ML) python -m srpipe.tasks.command.synth pilot

command-synth: screen ## Synthesise the command clips, near misses and halves, keep what the checker allows (E11-T7)
	$(ML) python -m srpipe.tasks.command.synth positives && uv run python -m srpipe.tasks.command.synth negatives \
	  && uv run python -m srpipe.tasks.command.synth select

command-synth-make: screen ## Make the command clips and near misses without the checker; command-synth hears them later
	$(ML) python -m srpipe.tasks.command.synth positives --make-only \
	  && uv run python -m srpipe.tasks.command.synth negatives --make-only

##@ Command: kws track (E11-T17)

.PHONY: kws-split kws-features kws-train
kws-split: screen ## Cut command_kws/v1 from the command TTS, real clips, ordinary speech and noise into ml/data/splits
	$(ML) python -m srpipe.tasks.command.kws.data split

kws-features: ## Run each file of command_kws/v1 through the board simulation with pitch into processed/command_kws
	$(ML) python -m srpipe.tasks.command.kws.data simulate

kws-train: ## Train the kws DS-CNN on processed/command_kws on the GPU into ml/artifacts/command_kws/runs
	$(ML) --extra train python -m srpipe.tasks.command.kws.train

##@ Command: ctc track (E11-T12, E11-T23)

.PHONY: ctc-features ctc-train ctc-watch ctc-tone-flip
# Simulates the split with pitch into processed/command, then cuts the board sessions with Gate 3's code, which needs
# torch; Ctrl-C pauses at once, and run again it goes on from the finished shards.
ctc-features: ## Run each file of the command split through the board simulation with pitch into processed/command
	$(ML) --extra train --extra swiftf0 python -m srpipe.tasks.command.ctc.data simulate

# SET overrides command_ctc.yaml, such as train.init.
ctc-train: ## Train the ctc net on the GPU into ml/artifacts/command_ctc/runs [RESUME=<run>] [SET="key=value ..."]
	$(ML) --extra train python -m srpipe.tasks.command.ctc.train $(if $(RESUME),--resume $(RESUME)) $(foreach s,$(SET),--set $(s))

# Gate 3 on each checkpoint and the owner check on every second; run again it goes on from the last entry.
ctc-watch: ## Score each checkpoint of a ctc run as its training writes it, into <run>/watch.yaml: RUN=<run>
	$(ML_CPU) --extra train --extra swiftf0 --extra praat python -m srpipe.tasks.command.ctc.watch $(RUN)

# val moves a checked sắc or nặng syllable of val onto the other tone by Praat, formants kept, its session simulated
# again; owner moves the first word of the owner's bật / tắt sessions; places reads tone right at the first and later
# syllables, pitch held or not. val and owner need Docker for the aligner. STEPS scores those checkpoints with owner or
# places; KALDI=1 hears through the board's Kaldi pitch.
ctc-tone-flip: ## The tone checks of KEHOACH 3.11 into <run>/tone_flip_<check>.yaml: CHECK=val|owner|places RUN=<run> [STEPS=2000,4000] [KALDI=1]
	$(ML_CPU) --extra train --extra swiftf0 --extra praat python -m srpipe.tasks.command.ctc.tone_flip $(or $(CHECK),val) $(RUN) $(if $(STEPS),--steps $(STEPS)) $(if $(KALDI),--kaldi-pitch)

##@ Command: ctc int8 ladder (KEHOACH 3.14)

# KALDI=1 hears a run on the board's Kaldi pitch, HOLD folds that pitch dim at its train mean; each ladder step then
# writes into <run>/int8_kaldi_<hold>/.
CTC_HEARD = $(if $(KALDI),--kaldi-pitch) $(if $(HOLD),--hold $(HOLD))
CTC_QUANT = $(ML_CPU) --extra train --extra espdl python -m srpipe.tasks.command.ctc.quant

.PHONY: ctc-ptq ctc-int16 ctc-qat ctc-thresholds ctc-deploy
ctc-ptq: ## Rungs 1 and 2: each calibration beside float: RUN=<run> [KALDI=1] [HOLD=voicing|f0|pitch] (E11-T12)
	$(CTC_QUANT) ptq $(RUN) $(CTC_HEARD)

ctc-int16: ## Rung 3: the convolutions ESP-PPQ ranks worst at 16 bits, on ctc-ptq's best: RUN=<run> [KALDI=1] [HOLD=]
	$(CTC_QUANT) int16 $(RUN) $(CTC_HEARD)

ctc-qat: ## Rung 4: QAT on the GPU from ctc-ptq's best calibration: RUN=<run> [KALDI=1] [HOLD=] (E11-T12)
	$(ML) --extra train --extra espdl python -m srpipe.tasks.command.ctc.quant qat $(RUN) $(CTC_HEARD)

ctc-thresholds: ## Choose delta1..3 of a ladder row on val_commands and val, as the chip decides: RUN=<run> ROW=<row> (E11-T13)
	$(CTC_QUANT) thresholds $(RUN) --row $(ROW) $(CTC_HEARD)

ctc-deploy: ## Export a ladder row and its thresholds into firmware/models/command and lock them: RUN=<run> ROW=<row> (E11-T19)
	$(CTC_QUANT) deploy $(RUN) --row $(ROW) $(CTC_HEARD)

##@ Command: rnnt track (E11-T20)

.PHONY: rnnt-ptq
rnnt-ptq: ## Rung 2: three graphs with each calibration, Gate 3 after int8 beside float, rows rnnt_*: RUN=<run>
	$(ML_CPU) --extra train --extra espdl python -m srpipe.tasks.command.rnnt.quant ptq $(RUN)

##@ Command: Gate 3 (E11-T13, E11-T17)

.PHONY: command-eval
# SET is a command set file from the repo root; KALDI=1 hears a pitch_source run through the board's Kaldi pitch;
# HOLD puts those pitch dims at the run's train mean.
command-eval: ## Score Gate 3 of a track on the board sessions: TRACK=kws|ctc|rnnt RUN=<run> [SET=] [KALDI=1] [HOLD=voicing|f0|pitch]
	$(ML) --extra train --extra swiftf0 python -m srpipe.tasks.command.eval $(TRACK) $(RUN) $(if $(SET),--commands $(abspath $(SET))) $(if $(KALDI),--kaldi-pitch) $(if $(HOLD),--hold $(HOLD))

##@ Noise suppression: ns candidates (E9-T3, E9-T4)

.PHONY: ns-data ns-pilot ns-smoke ns-train ns-pause ns-resume ns-eval ns-bench
ns-data: screen ## Split, pools and held val/test sets of the ns branch into data/splits/ns, interim and processed
	$(ML) python -m srpipe.tasks.ns.data clean && uv run python -m srpipe.tasks.ns.data split \
	  && uv run python -m srpipe.tasks.ns.data pool && uv run python -m srpipe.tasks.ns.data sets

ns-pilot: ## Write ns training examples and a DNSMOS report of the targets to cache/listen/ns/pilot, to hear first
	$(ML) python -m srpipe.tasks.ns.data pilot

ns-smoke: ## Train every ns candidate a few small steps on the CPU through the real loader, to time it
	$(ML_CPU) --extra train python -m srpipe.tasks.ns.train --smoke

# Ctrl-C or make ns-pause pauses after the step under way.
ns-train: ## Train RNNoise-16k and NSNet-16k S/M/L on identical batches on the GPU [RESUME=<run>]
	$(ML) --extra train python -m srpipe.tasks.ns.train $(if $(RESUME),--resume $(RESUME))

ns-pause: ## Pause the running ns training after the step under way; make ns-resume goes on
	@for f in ml/artifacts/ns/runs/*/train.pid; do \
	  pid=$$(cat $$f 2>/dev/null); \
	  if [ -n "$$pid" ] && grep -aq srpipe.tasks.ns.train /proc/$$pid/cmdline 2>/dev/null; then \
	    kill -INT $$pid && echo "$$(dirname $$f): pauses after the step under way"; exit 0; \
	  fi; \
	done; echo "no ns training is running"

ns-resume: ## Go on with the latest paused ns run from the state it saved
	$(ML) --extra train python -m srpipe.tasks.ns.train --resume

ns-eval: ## Score the last ns run against the OM-LSA floor on the held val and test sets into its eval/
	$(ML) --extra train python -m srpipe.tasks.ns.eval score --set val \
	  && uv run --extra train python -m srpipe.tasks.ns.eval score --set test

ns-bench: ## Score OM-LSA and an ns run's candidates on the afe bench's fan and music mixtures [RUN=<run>] [EPOCH=<e>]
	$(ML) --extra train python -m srpipe.tasks.ns.eval bench $(if $(RUN),--run $(RUN)) $(if $(EPOCH),--epoch $(EPOCH))

##@ Measurements

.PHONY: measure report
measure: ## Merge bench CSVs into docs/measurements/budget.md
	python3 -m tools.budget

report: measure ## Rebuild every number in docs/measurements/ with one command
	cd ml && ./scripts/60_eval_board.sh

##@ Firmware builds (KEHOACH 4.5.8)

.PHONY: fw-dev fw-bench fw-bench-flash fw-prod
fw-dev: ## Build the dev profile
	@$(call fresh_sdkconfig,firmware/sdkconfig,$(FW_DEFAULTS) firmware/sdkconfig.dev)
	cd firmware && $(IDF_DEV) build

fw-bench: ## Build the bench profile, the only one numbers are reported from
	@$(call fresh_sdkconfig,firmware/build_bench/sdkconfig,$(FW_DEFAULTS) firmware/sdkconfig.bench)
	cd firmware && $(IDF_BENCH) build

fw-bench-flash: fw-bench ## Flash only the bench app over the CH340; storage, set.json among it, stays as the board has it
	cd firmware && $(IDF_BENCH) -p $(PORT) app-flash

fw-prod: ## Build the prod profile in build_prod, from its own sdkconfig
	rm -f firmware/build_prod/sdkconfig
	cd firmware && idf.py -B build_prod -D SDKCONFIG=build_prod/sdkconfig \
	  -D SDKCONFIG_DEFAULTS="$(SDKCONFIG_BASE);sdkconfig.prod$(SECRETS)" build

##@ Board B: USB, flash, monitor

.PHONY: board-attach board-detach flash monitor models-flash capture-flash capture-radio-off-flash
# A USB port Windows has not shared yet asks once for admin.
board-attach: ## Attach board B's CH340 to WSL through usbipd and wait for its serial port (KEHOACH 2.1)
	@for i in $$(seq 10); do row=$$(usbipd.exe list | tr -d '\r' | grep " $(BOARD_USB_ID) ") && break; sleep 1; done; \
	case "$$row" in \
	  "") echo "no $(BOARD_USB_ID) on the USB of Windows: plug board B in"; exit 1;; \
	  *Attached) echo "board B is attached already";; \
	  *"Not shared") powershell.exe -NoProfile -Command \
	      "Start-Process usbipd -Verb RunAs -Wait -ArgumentList 'bind','--hardware-id','$(BOARD_USB_ID)'" \
	    && usbipd.exe attach --wsl --hardware-id $(BOARD_USB_ID);; \
	  *) usbipd.exe attach --wsl --hardware-id $(BOARD_USB_ID);; \
	esac
	@for i in $$(seq 10); do [ -e $(PORT) ] && { echo "board B on $(PORT)"; exit 0; }; sleep 1; done; \
	  echo "$(PORT) did not appear"; exit 1

board-detach: ## Hand board B's CH340 back to Windows
	usbipd.exe detach --hardware-id $(BOARD_USB_ID)
	@for i in $$(seq 10); do [ -e $(PORT) ] || exit 0; sleep 1; done; echo "$(PORT) is still there"; exit 1

flash: ## Flash and monitor the dev profile over the CH340
	@$(call fresh_sdkconfig,firmware/sdkconfig,$(FW_DEFAULTS) firmware/sdkconfig.dev)
	cd firmware && $(IDF_DEV) -p $(PORT) flash monitor

monitor: ## Open the serial monitor on the CH340
	cd firmware && idf.py -p $(PORT) monitor

models-flash: ## Pack every model of contracts/models.lock.json and write models_0 of board B (KEHOACH 4.5.6, 6.1)
	cd ml && PORT=$(PORT) ./scripts/50_pack_and_flash.sh

capture-flash: ## Flash test_apps/capture, the raw-only recorder (E5-T12)
	cd $(CAPTURE_APP) && idf.py -p $(PORT) flash

capture-radio-off-flash: ## Flash capture that records 60 s with the radio off, then sends: the Wi-Fi off floor (E2-T4)
	cd $(CAPTURE_APP) && idf.py -B build_radio_off -D SDKCONFIG=build_radio_off/sdkconfig \
	  -D CAPTURE_PROFILE=radio_off -p $(PORT) flash

##@ Board B: calibration (E2-T5, E2-T6)

.PHONY: calib-flash calib-estimate calib-write calib-shift
calib-flash: ## Flash test_apps/calib, the console that stores NVS calib/*
	cd $(CALIB_APP) && idf.py -p $(PORT) flash

calib-estimate: ## Estimate balance from frontal white noise sessions: SESSIONS="<dir> <dir> ..."
	cd host && uv run python -m srhost.calib estimate $(SESSIONS)

calib-write: ## Write a balance file to the board: CSV=docs/measurements/calib/<board>_balance.csv
	cd host && uv run python -m srhost.calib write ../$(CSV) --port $(PORT)

calib-shift: ## Store NVS calib/pcm_shift through test_apps/calib: SHIFT=13
	@test -n "$(SHIFT)" || { echo "usage: make calib-shift SHIFT=<8..16>"; exit 1; }
	cd host && uv run python -m srhost.calib shift $(SHIFT) --port $(PORT)

##@ Board B: test apps

# Where srpipe.scenes.compare prepare wrote the items, read from ml/ only when a recipe needs it.
COMPARE_ITEMS = $(shell $(ML) --quiet python -c "from srpipe.core.config import data_paths; print(data_paths()['interim'] / 'scenes' / 'afe_compare')")

.PHONY: bench-board espsr-compare ai-probe ai-unit ai-unit-rnnt listen-unit
bench-board: ## Run bench_afe on board B, keep its rows in docs/measurements/bench, then rebuild budget.md
	@$(call fresh_sdkconfig,$(BENCH_APP)/sdkconfig,$(call app_defaults,$(BENCH_APP)) firmware/sdkconfig.afe)
	cd $(BENCH_APP) && idf.py build
	cd $(BENCH_APP) && pytest pytest_bench_afe.py $(ON_BOARD) -s
	python3 -m tools.budget

espsr-compare: ## Run every board variant of KEHOACH 3.16 on board B into interim/scenes/afe_compare [ONLY="<item> ..."]
	@$(call fresh_sdkconfig,$(ESPSR_APP)/sdkconfig,$(call app_defaults,$(ESPSR_APP)))
	cd $(ESPSR_APP) && idf.py build
	cd $(ESPSR_APP) && ESPSR_COMPARE_ITEMS=$(COMPARE_ITEMS) ESPSR_COMPARE_ONLY="$(ONLY)" pytest pytest_espsr_compare.py \
	  $(ON_BOARD) -s

# CTC_RUN=<run> CTC_ROW=<row of its int8/ladder.yaml> streams that graph; CTC_KALDI=1 CTC_HOLD=voicing takes the row
# of its int8_kaldi_voicing/.
ai-probe: ## Export the probes of E11-T10 (TCN), E11-T17 (kws), E11-T12 (ctc) and E9-T10 (ns) into ai_engine/test_apps/unit
	$(ML_CPU) --extra train --extra espdl python -m srpipe.tasks.wake.quant probe
	$(ML_CPU) --extra train --extra espdl python -m srpipe.tasks.command.kws.quant probe
	$(ML_CPU) --extra train --extra espdl python -m srpipe.tasks.command.ctc.probe $(if $(CTC_RUN),--run $(CTC_RUN) --row $(CTC_ROW)) $(if $(CTC_KALDI),--kaldi-pitch) $(if $(CTC_HOLD),--hold $(CTC_HOLD))
	$(ML_CPU) --extra train --extra espdl python -m srpipe.tasks.ns.quant probe

# Both model slots end erased; with CTC_RUN, Gate 3 is counted on the chip's decisions, round by round as the voice
# partition holds them.
ai-unit: ai-probe ## Run the ai_engine suite on board B at bench's compiler settings [CTC_RUN= CTC_ROW=]
	@$(call fresh_sdkconfig,$(UNIT_APP)/sdkconfig,$(call app_defaults,$(UNIT_APP)))
	cd $(UNIT_APP) && idf.py build && \
	  $(WRITE_PART) models_0 --input main/probe/ctc_models.bin && \
	  $(WRITE_PART) models_1 --input main/probe/ns_models.bin && \
	  : > build/unit.log && skip=n && \
	  for gate in $$(ls main/probe/ctc_gate_*.bin 2>/dev/null | sort -V | sed 1d); do \
	    $(WRITE_PART) voice --input $$gate || exit 1; \
	    { pytest pytest_unit.py $(ON_BOARD) -s --skip-autoflash $$skip; echo $$? > build/unit.status; } 2>&1 \
	      | tee -a build/unit.log; \
	    [ $$(cat build/unit.status) -eq 0 ] || exit 1; skip=y; \
	  done && \
	  if [ -f main/probe/ctc_gate_0.bin ]; then \
	    $(WRITE_PART) voice --input main/probe/ctc_gate_0.bin; \
	  else \
	    $(ERASE_PART) voice; \
	  fi && \
	  { pytest pytest_unit.py $(ON_BOARD) -s --skip-autoflash $$skip; echo $$? > build/unit.status; } 2>&1 \
	    | tee -a build/unit.log; \
	  exit $$(cat build/unit.status)
	@if [ -f $(UNIT_APP)/main/probe/ctc_gate.json ]; then \
	  cd ml && uv run --extra train python -m srpipe.tasks.command.ctc.probe --gate-log ../$(UNIT_APP)/build/unit.log; \
	fi

# rnnt/probe.py's three graphs go to model slot 0, every rnnt call is checked against Python and timed; slot 0 ends
# erased.
ai-unit-rnnt: ## Run the rnnt build of the ai_engine suite on board B [RNNT_RUN=<run> RNNT_ROW=<rnnt_* row>] (E11-T20)
	$(ML_CPU) --extra train --extra espdl python -m srpipe.tasks.command.rnnt.probe \
	  $(if $(RNNT_RUN),--run $(RNNT_RUN) --row $(RNNT_ROW))
	@$(call fresh_sdkconfig,$(UNIT_APP)/build_rnnt/sdkconfig,$(call app_defaults,$(UNIT_APP)))
	cd $(UNIT_APP) && idf.py -B build_rnnt -D SDKCONFIG=build_rnnt/sdkconfig -D UNIT_PROFILE=rnnt build && \
	  $(WRITE_PART) models_0 --input main/probe/rnnt_models.bin && \
	  if [ -f main/probe/rnnt_gate.bin ]; then \
	    $(WRITE_PART) voice --input main/probe/rnnt_gate.bin; \
	  else \
	    $(ERASE_PART) voice; \
	  fi && \
	  { pytest pytest_unit.py $(ON_BOARD) --build-dir build_rnnt -s; echo $$? > build_rnnt/unit.status; } 2>&1 \
	    | tee build_rnnt/unit.log; \
	  exit $$(cat build_rnnt/unit.status)
	@if [ -f $(UNIT_APP)/main/probe/rnnt_gate.json ]; then \
	  cd ml && uv run --extra train python -m srpipe.tasks.command.rnnt.probe --gate-log ../$(UNIT_APP)/build_rnnt/unit.log; \
	fi

# The locked models go to model slot 0; each window is decided as Python decides its int8 simulation, field by field.
listen-unit: ## Run svc_listen on board B over every Gate 3 session, round by round (E11-T14)
	$(ML_CPU) --extra train --extra espdl python -m srpipe.tasks.command.ctc.probe --listen
	$(ML) python -m srpipe.export.pack_models artifacts/models.bin --lock
	@$(call fresh_sdkconfig,$(LISTEN_APP)/sdkconfig,$(call app_defaults,$(LISTEN_APP)))
	cd $(LISTEN_APP) && idf.py build
	$(WRITE_PART) models_0 --input ml/artifacts/models.bin
	@skip=n; for round in $$(ls $(LISTEN_APP)/main/probe/listen_*_voice.bin | sed 's/.*listen_\([0-9]*\)_voice.bin/\1/' | sort -n); do \
	  for part in models_1 voice; do \
	    $(WRITE_PART) $$part --input $(LISTEN_APP)/main/probe/listen_$${round}_$$part.bin || exit 1; \
	  done; \
	  { cd $(LISTEN_APP) && pytest pytest_unit.py $(ON_BOARD) -s --skip-autoflash $$skip; \
	    echo $$? > build/listen.status; cd $(CURDIR); } 2>&1 | tee $(LISTEN_APP)/build/listen_$$round.log; \
	  [ $$(cat $(LISTEN_APP)/build/listen.status) -eq 0 ] || exit 1; skip=y; \
	done

##@ Broker and host

.PHONY: broker-up broker-down host-live commands
broker-up: ## Start the bench MQTT broker (needs deploy/.env and deploy/emqx/users.csv)
	@test -f deploy/.env || { echo "copy deploy/.env.example to deploy/.env and fill it"; exit 1; }
	@test -f deploy/emqx/users.csv || { echo "copy deploy/emqx/users.csv.example to deploy/emqx/users.csv and fill it"; exit 1; }
	cd deploy && docker compose up -d --wait

broker-down: ## Stop the bench MQTT broker
	cd deploy && docker compose down

host-live: ## Show wake and command events live
	cd host && uv run python -m srhost.live

commands: ## Send a command set to one board, retained, and show its answer: DEVICE=<deviceId> SET=<file.json> [CLEAR=1]
	cd host && uv run python -m srhost.commands --device "$(DEVICE)" $(if $(CLEAR),--clear,"$(abspath $(SET))")

##@ Recording sessions (KEHOACH 4.6)

# make session reads where to listen and where raw/ lives from host/.env, like srhost.config.
STREAM_PORT = $(shell sed -n 's/^SRHOST_STREAM_PORT=//p' host/.env 2>/dev/null)
DATA_ROOT = $(shell sed -n 's/^SRPIPE_DATA_ROOT=//p' host/.env 2>/dev/null)
PCM_SHIFT ?= 16
PLAN_FW ?= capture@$(shell git describe --always --tags)

.PHONY: session session-plan
# The LAN cannot reach WSL in NAT mode; a port Docker Desktop publishes on Windows it can (KEHOACH 4.6).
# The board waits in its bootloader until the receiver listens, so a session starts at seq 0 of one boot.
session: ## Record a labelled session: ARGS="--kind probe --room home --fw <ver+sha> --pcm-shift 16"
	@test -f host/.env || { echo "copy host/.env.example to host/.env and fill it"; exit 1; }
	@docker rm -f sr-session >/dev/null 2>&1 || true
	python -m esptool --chip esp32s3 -p $(PORT) --after no-reset chip-id >/dev/null
	@( for i in $$(seq 60); do docker logs sr-session 2>&1 | grep -q recording && break; sleep 1; done; \
	   python -m esptool --chip esp32s3 -p $(PORT) run >/dev/null && echo "board released on $(PORT)" ) &
	docker run --rm $$([ -t 0 ] && echo -it) --name sr-session --user $$(id -u):$$(id -g) --env-file host/.env \
	  -e PYTHONPATH=/repo/host/src -p $(STREAM_PORT):$(STREAM_PORT) \
	  -v "$(CURDIR)":/repo -v "$(DATA_ROOT)":"$(DATA_ROOT)" \
	  python:3.12-slim python -m srhost.session $(ARGS)

session-plan: ## Record a script of host/plans in turn: PLAN=host/plans/<x>.tsv ROOM=<room> [SPK=spk_001 CONSENT=C001]
	@test -f "$(PLAN)" && test -n "$(ROOM)" || { echo "usage: make session-plan PLAN=host/plans/<x>.tsv ROOM=<room> [SPK= CONSENT=]"; exit 1; }
	@if grep -q '{spk}' "$(PLAN)" && [ -z "$(SPK)" -o -z "$(CONSENT)" ]; then echo "$(PLAN) has a speaker: give SPK and CONSENT"; exit 1; fi
	@n=0; grep -v '^#' "$(PLAN)" | sed -e 's/{spk}/$(SPK)/g' -e 's/{consent}/$(CONSENT)/g' | \
	while IFS='	' read -r say args; do \
	  n=$$((n + 1)); printf '\n[%d] %s\n    Enter: record, s: skip, q: stop > ' "$$n" "$$say"; read ans < /dev/tty; \
	  case "$$ans" in s) continue;; q) break;; esac; \
	  $(MAKE) --no-print-directory session \
	    ARGS="--room $(ROOM) --fw $(PLAN_FW) --pcm-shift $(PCM_SHIFT) $$args" < /dev/tty || exit 1; \
	done
