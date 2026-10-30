.DEFAULT_GOAL := help

# idf.py and the python that drives board B come from IDF's export.sh. In a terminal that has not sourced it, a goal
# that needs them runs make once more under it, about 5 s; IDF_LOADED stops a second round if export.sh fails.
IDF_EXPORT ?= $(HOME)/esp/esp-idf/export.sh
IDF_GOALS := ai-unit ai-unit-cores ai-unit-ctc ai-unit-pitch ai-unit-rnnt ai-unit-speaker ai-unit-wake bench-board bss calib-flash \
  capture-flash capture-radio-off-flash espsr-compare flash fw-bench fw-bench-flash fw-dev fw-prod listen-unit \
  models-flash monitor nvs-restore nvs-save parity-board product session session-plan storage-flash
ifneq ($(and $(filter $(IDF_GOALS),$(MAKECMDGOALS)),$(if $(IDF_LOADED)$(shell command -v idf.py),,no idf.py)),)
.PHONY: $(MAKECMDGOALS)
$(firstword $(MAKECMDGOALS)):
	@test -f "$(IDF_EXPORT)" || { echo "no $(IDF_EXPORT): point IDF_EXPORT at ESP-IDF's export.sh"; exit 1; }
	@echo "loading ESP-IDF for $(MAKECMDGOALS) ($(IDF_EXPORT))"
	@bash -c '. "$(IDF_EXPORT)" >/dev/null 2>&1 && exec "$(MAKE)" --no-print-directory IDF_LOADED=1 $(MAKECMDGOALS)'
ifneq ($(word 2,$(MAKECMDGOALS)),)
$(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS)):
	@:
endif
else

# Board B's CH340 stays with Windows, served to WSL over RFC 2217 by make board-attach (KEHOACH 2.1). It holds
# 921600 baud, as pytest-embedded flashes; esptool left alone writes a partition at 115200, ~11 KB/s.
BOARD_USB_ID ?= 1a86:7523
BRIDGE_HOST ?= $(shell ip route show default 2>/dev/null | awk '{print $$3; exit}')
BRIDGE_TCP_PORT ?= 4011
PORT ?= rfc2217://$(BRIDGE_HOST):$(BRIDGE_TCP_PORT)?ign_set_control
FLASH_BAUD ?= 921600
# Every Python a goal on board B starts loads tools/board_link first: RFC 2217 read timeouts stay on this side.
BOARD_GOALS := $(IDF_GOALS) wifi calib-write calib-shift
$(BOARD_GOALS): export PYTHONPATH := $(CURDIR)/tools/board_link$(if $(PYTHONPATH),:$(PYTHONPATH))
# idf.py takes its flash baud from here; on its command line -b would set the monitor's baud too.
$(BOARD_GOALS): export ESPBAUD := $(FLASH_BAUD)
# esptool writes an image only where its MD5 on board B differs (KEHOACH 2.1).
ONLY_CHANGED := --extra-args=--skip-flashed

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
# What a test app's sdkconfig is generated from: the bench profile, the app's own defaults, the components' Kconfig.
app_defaults = firmware/sdkconfig.defaults.esp32s3 firmware/sdkconfig.bench $(1)/sdkconfig.defaults $(1)/CMakeLists.txt \
  $(wildcard firmware/components/*/Kconfig)

# Test apps on board B, run by pytest-embedded from the app's folder.
BENCH_APP := firmware/test_apps/bench_afe
PARITY_APP := firmware/test_apps/parity
ESPSR_APP := firmware/test_apps/espsr_compare
CAPTURE_APP := firmware/test_apps/capture
CALIB_APP := firmware/test_apps/calib
BSS_LIVE_APP := firmware/test_apps/bss_live
UNIT_APP := firmware/components/ai_engine/test_apps/unit
LISTEN_APP := firmware/components/svc_listen/test_apps/unit
ON_BOARD = --rootdir . --confcutdir $(CURDIR)/firmware --embedded-services esp,idf --target esp32s3 --port $(PORT) \
  -p no:cacheprovider
UNIT_PARTITIONS := $(CURDIR)/firmware/test_apps/partitions_unit.csv
PARTTOOL = python $$IDF_PATH/components/partition_table/parttool.py --port $(PORT) --baud $(FLASH_BAUD) \
  --partition-table-file $(UNIT_PARTITIONS)
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

eval-bss: ## Score bss on board B's talkers and the standard scenes' talkers in turns, by RT60 and side (E8-T3)
	$(ML) python -m srpipe.scenes.spatial bss --workers 16

eval-pitch: ## Measure the pitch mirror against Kaldi itself on VIVOS test, on the CPU
	$(ML_CPU) python -m srpipe.metrics.pitch

##@ Speaker verification survey on the PC (KEHOACH 3.17, E11-T24)

.PHONY: eval-speaker
# The owner's command windows of Gate 3 and the command split's test speakers through the board simulation, every
# extractor of configs/scenes/speaker.yaml through its uv project under ml/spk_ref; embeddings stay in cache (KEHOACH 1.4).
eval-speaker: ## EER of the pretrained extractors, and the owner's windows kept at 1% of impostors passing
	$(ML) --extra train python -m srpipe.scenes.speaker

##@ Speaker: chip path, as command_ctc goes (KEHOACH 3.17, E11-T25)

SPEAKER_QUANT = $(ML_CPU) --extra train --extra espdl python -m srpipe.tasks.speaker.quant

.PHONY: speaker-import speaker-ptq speaker-int16 speaker-timing speaker-same-int8 speaker-deploy speaker-use \
  speaker-music speed
speaker-import: ## Import a pretrained ReDimNet2 of the survey as a run into ml/artifacts/speaker/runs: [EXTRACTOR=redimnet2_b0] (E11-T35)
	$(SPEAKER_QUANT) import $(if $(EXTRACTOR),--extractor $(EXTRACTOR))

# Each calibration of configs/models/speaker.yaml quantises for tens of minutes on the CPU: run it in tmux. WORKERS
# simulate the impostors' sessions, about 0.5 GB each.
speaker-ptq: ## Rungs 1 and 2: each calibration beside float on the survey's material, into <run>/int8/: RUN=<run> [WORKERS=8] [CALIB="mse kl"]
	$(SPEAKER_QUANT) ptq $(RUN) $(if $(WORKERS),--workers $(WORKERS)) $(if $(CALIB),--calibrations $(CALIB))

speaker-int16: ## Rung 3: the operations ESP-PPQ ranks worst at 16 bits, on speaker-ptq's best calibration: RUN=<run> [WORKERS=8]
	$(SPEAKER_QUANT) int16 $(RUN) $(if $(WORKERS),--workers $(WORKERS))

speaker-timing: ## Step 2.1 of KEHOACH 3.14's procedure: the timing row into <run>/int8/timing/: RUN=<run> [WORKERS=8]
	$(SPEAKER_QUANT) timing $(RUN) $(if $(WORKERS),--workers $(WORKERS))

speaker-same-int8: ## Step 2.4: b0 as quant.py builds it now and at AGAINST, quantised alike, same integers: RUN=<run> [AGAINST=HEAD] [CALIB=kl]
	$(SPEAKER_QUANT) same-int8 $(RUN) --against $(or $(AGAINST),HEAD) $(if $(CALIB),--calibration $(CALIB)) \
	  $(if $(WORKERS),--workers $(WORKERS))

# The survey's material, four conditions of it, through WORKERS processes of the int8 simulation: run it in tmux. The
# deploy stays in <run>/int8/<row>/deploy/, where speaker-use finds it.
speaker-deploy: ## Lock a ladder row into firmware/models/speaker with its threshold set on the chip's path: RUN=<run> ROW=<row> [WORKERS=6] (E11-T26)
	$(SPEAKER_QUANT) deploy $(RUN) --row $(ROW) $(if $(WORKERS),--workers $(WORKERS))

speaker-use: ## Put a kept deploy, a variant of configs/models/speaker.yaml, into firmware/models/speaker and the lock: SPEAKER=b0|b3 (ADR-0028)
	$(SPEAKER_QUANT) use $(SPEAKER)

# The survey's material quiet and at each music level, all through the chip's path: about four deploys' work, run it
# in tmux.
speaker-music: ## The locked row with music in the room: owner kept, strangers passing, into <run>/int8/<row>/music.yaml: RUN=<run> [WORKERS=6] (E11-T33)
	$(SPEAKER_QUANT) music $(RUN) $(if $(WORKERS),--workers $(WORKERS))

SPEED_LOG_speaker = $(UNIT_APP)/$(SPEAKER_BUILD)/unit.log
SPEED_ESPDL_speaker = ml/$(RUN)/int8/timing/speaker.espdl

# Phase 2 of KEHOACH 3.14's procedure in one chain, stopping at the first step that fails; source IDF's export.sh first,
# the board runs in it. A branch joins with <branch>-timing, <branch>-same-int8, ai-unit-<branch> and SPEED_LOG_<branch>.
speed: ## Same integers, timing row, board probe, profile report: BRANCH=<branch> RUN=<run> [AGAINST=HEAD] (E11-T30)
	$(MAKE) $(BRANCH)-same-int8 RUN=$(RUN) AGAINST=$(or $(AGAINST),HEAD)
	$(MAKE) $(BRANCH)-timing RUN=$(RUN)
	$(MAKE) ai-unit-$(BRANCH) RUN=$(RUN) ROW=timing
	$(MAKE) ai-profile LOG=$(SPEED_LOG_$(BRANCH)) ESPDL=$(SPEED_ESPDL_$(BRANCH))

##@ Pitch: SwiftF0 on the chip (KEHOACH 3.11)

PITCH_QUANT = $(ML_CPU) --extra train --extra espdl --extra swiftf0 python -m srpipe.tasks.pitch.quant
.PHONY: pitch-import pitch-ptq pitch-rebuild pitch-timing pitch-same-int8
pitch-import: ## swift-f0's pinned model.onnx as a run under ml/artifacts/pitch/runs, its rewrite held to swift-f0
	$(PITCH_QUANT) import

pitch-ptq: ## Rung 2: each calibration of configs/models/pitch.yaml against float into <run>/int8/: RUN=<run>
	$(PITCH_QUANT) ptq $(RUN)

pitch-rebuild: ## Files of the command build simulated afresh on a row's pitch, into processed/command/<build>_resim_<row>[_stream]: RUN=<run> ROW=<row>|float [FILES=val] [WORKERS=6] [STREAM=1]
	$(PITCH_QUANT) rebuild $(RUN) --row $(ROW) $(if $(FILES),--files $(FILES)) $(if $(WORKERS),--workers $(WORKERS)) \
	  $(if $(STREAM),--stream)

pitch-deploy: ## A row of the pitch ladder into firmware/models/pitch and models.lock.json, streaming deploy.step_frames a call: RUN=<run> ROW=<row> (E11-T37)
	$(PITCH_QUANT) deploy $(RUN) --row $(ROW)

pitch-timing: ## Step 2.1 of KEHOACH 3.14's procedure: the timing row into <run>/int8/timing/: RUN=<run>
	$(PITCH_QUANT) timing $(RUN)

pitch-same-int8: ## Step 2.4: model.py's network now and at AGAINST, quantised alike, same integers: RUN=<run> [AGAINST=HEAD] [CALIB=kl]
	$(PITCH_QUANT) same-int8 $(RUN) --against $(or $(AGAINST),HEAD) $(if $(CALIB),--calibration $(CALIB))

SPEED_LOG_pitch = $(UNIT_APP)/build_pitch/unit.log
SPEED_ESPDL_pitch = ml/$(RUN)/int8/timing/pitch.espdl

##@ Detect: YOLO26 from a server's package (KEHOACH 3.18)

DETECT = $(ML) --extra train --extra espdl --extra detect python -m srpipe.tasks.detect
.PHONY: detect-coco detect-coco-broken detect-import detect-ptq detect-eval detect-deploy
detect-coco: ## The COCO 2017 test package from raw/vision/coco2017 into interim/detect (E15-T3)
	$(DETECT).data coco

detect-coco-broken: ## The test package with every label on the wrong class, the mAP gate's negative control
	$(DETECT).data coco-broken

detect-import: ## A package as a run under ml/artifacts/detect/runs: splits locked, model.pt to ONNX held to it [SET="key=value ..."]
	$(DETECT).quant import $(foreach s,$(SET),--set $(s))

# Calibration and TQT on the GPU for tens of minutes, TQT's cache in RAM: run it in tmux, nothing heavy beside it.
detect-ptq: ## Espressif's YOLO26 recipe into <run>/int8/{graph.native, model.espdl}: RUN=<run>
	$(DETECT).quant ptq $(RUN)

detect-eval: ## mAP50-95 of float and int8 on a split, into <run>/int8/map_<split>.yaml: RUN=<run> [SPLIT=val|test] [LIMIT=N]
	$(DETECT).eval $(RUN) --split $(or $(SPLIT),val) $(if $(LIMIT),--limit $(LIMIT))

detect-deploy: ## A run that passed the val gate into firmware/models/detect and the lock's detect row: RUN=<run>
	$(DETECT).quant deploy $(RUN)

##@ Wake

.PHONY: wake-features wake-train wake-ptq wake-deploy eval-tts wake-synth
wake-features: ## Run each file of the wake split through the board simulation into processed/wake (E4-T8)
	$(ML) --extra praat python -m srpipe.tasks.wake.data simulate

wake-train: ## Train the wake TCN on processed/wake/<split> on the GPU into ml/artifacts/wake/runs (E11-T11)
	$(ML) --extra train python -m srpipe.tasks.wake.train

WAKE_QUANT = $(ML_CPU) --extra train --extra espdl python -m srpipe.tasks.wake.quant
wake-ptq: ## Rung 2 of the wake TCN, each calibration scored as the chip scores, into <run>/int8: RUN=<run> [CALIBRATIONS="kl ..."] (E11-T11)
	$(WAKE_QUANT) ptq $(RUN) $(if $(CALIBRATIONS),--calibrations $(CALIBRATIONS))

wake-deploy: ## Lock a ladder row of the wake, its threshold and smoothing, into firmware/models/wake: RUN=<run> ROW=<row> (E11-T11)
	$(WAKE_QUANT) deploy $(RUN) --row $(ROW)

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
ctc-features: ## Run each file of the command split through the board simulation with pitch into processed/command [SET="key=value ..."]
	$(ML_CPU) --extra train --extra espdl --extra swiftf0 python -m srpipe.tasks.command.ctc.data simulate $(foreach s,$(SET),--set $(s))

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
ctc-tone-flip: ## The tone checks of KEHOACH 3.11 into <run>/tone_flip_<check>.yaml: CHECK=val|owner|places|gate RUN=<run> [STEPS=2000,4000] [KALDI=1] [BUILD=v8_pitch_kl] [PITCH_RUN=<pitch run> PITCH_ROW=kl [PITCH_STREAM=1]]
	$(ML_CPU) --extra train --extra espdl --extra swiftf0 --extra praat python -m srpipe.tasks.command.ctc.tone_flip $(or $(CHECK),val) $(RUN) $(if $(STEPS),--steps $(STEPS)) $(if $(KALDI),--kaldi-pitch) \
	  $(if $(BUILD),--build $(BUILD)) $(if $(PITCH_ROW),--pitch-row $(PITCH_RUN) $(PITCH_ROW)) $(if $(PITCH_STREAM),--pitch-stream)

##@ Command: ctc int8 ladder (KEHOACH 3.14)

# CONTRACT=<build> hears a run on the contract's pitch, its sentences from that build of the run's split, KALDI=1 on
# Kaldi's as the board ran it until 29/10, and HOLD folds a pitch dim at its train mean; each ladder step then writes
# into <run>/int8_contract_<build>/ or <run>/int8_kaldi_<hold>/.
CTC_HEARD = $(if $(CONTRACT),--contract-pitch $(CONTRACT)) $(if $(KALDI),--kaldi-pitch) $(if $(HOLD),--hold $(HOLD))
CTC_QUANT = $(ML_CPU) --extra train --extra espdl python -m srpipe.tasks.command.ctc.quant

.PHONY: ctc-ptq ctc-int16 ctc-qat ctc-thresholds ctc-deploy ctc-timing ctc-same-int8
ctc-ptq: ## Rungs 1 and 2: each calibration beside float: RUN=<run> [CONTRACT=<build> | KALDI=1] [HOLD=voicing|f0|pitch] (E11-T12)
	$(CTC_QUANT) ptq $(RUN) $(CTC_HEARD)

ctc-int16: ## Rung 3: the convolutions ESP-PPQ ranks worst at 16 bits, on ctc-ptq's best: RUN=<run> [CONTRACT=<build> | KALDI=1] [HOLD=]
	$(CTC_QUANT) int16 $(RUN) $(CTC_HEARD)

ctc-qat: ## Rung 4: QAT on the GPU from ctc-ptq's best calibration: RUN=<run> [CONTRACT=<build> | KALDI=1] [HOLD=] (E11-T12)
	$(ML) --extra train --extra espdl python -m srpipe.tasks.command.ctc.quant qat $(RUN) $(CTC_HEARD)

ctc-thresholds: ## Choose delta1..3 of a ladder row on val_commands and val, as the chip decides: RUN=<run> ROW=<row> (E11-T13)
	$(CTC_QUANT) thresholds $(RUN) --row $(ROW) $(CTC_HEARD)

ctc-deploy: ## Export a ladder row and its thresholds into firmware/models/command and lock them: RUN=<run> ROW=<row> (E11-T19)
	$(CTC_QUANT) deploy $(RUN) --row $(ROW) $(CTC_HEARD)

ctc-timing: ## Step 2.1 of KEHOACH 3.14's procedure: the timing row beside the ladder's rows: RUN=<run> [CONTRACT=<build> | KALDI=1] [HOLD=]
	$(CTC_QUANT) timing $(RUN) $(CTC_HEARD)

ctc-same-int8: ## Step 2.4: the net as quant.py hears it now and at AGAINST, same integers: RUN=<run> [AGAINST=HEAD] [CALIB=] [CONTRACT=<build> | KALDI=1] [HOLD=]
	$(CTC_QUANT) same-int8 $(RUN) --against $(or $(AGAINST),HEAD) $(if $(CALIB),--calibration $(CALIB)) $(CTC_HEARD)

SPEED_LOG_ctc = $(UNIT_APP)/build_ctc/unit.log
SPEED_ESPDL_ctc = ml/artifacts/command_ctc/probe/command_ctc/command_ctc.espdl

##@ Command: rnnt track (E11-T20)

.PHONY: rnnt-ptq
rnnt-ptq: ## Rung 2: three graphs with each calibration, Gate 3 after int8 beside float, rows rnnt_*: RUN=<run>
	$(ML_CPU) --extra train --extra espdl python -m srpipe.tasks.command.rnnt.quant ptq $(RUN)

##@ Command: Gate 3 (E11-T13, E11-T17)

.PHONY: command-eval
# SET is a command set file from the repo root; CONTRACT=<build> hears a run through the contract's pitch as the chip
# computes it, the build read only by the ladder; KALDI=1 through Kaldi's as the board ran it until 29/10; HOLD puts
# those pitch dims at the run's train mean.
command-eval: ## Score Gate 3 of a track on the board sessions: TRACK=kws|ctc|rnnt RUN=<run> [SET=] [CONTRACT=<build> | KALDI=1] [HOLD=voicing|f0|pitch]
	$(ML) --extra train --extra espdl --extra swiftf0 python -m srpipe.tasks.command.eval $(TRACK) $(RUN) $(if $(SET),--commands $(abspath $(SET))) \
	  $(if $(CONTRACT),--contract-pitch) $(if $(KALDI),--kaldi-pitch) $(if $(HOLD),--hold $(HOLD))

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

# A test app leaves its own partition table on the board, so the product's goes back with the app, all in one esptool
# call: a reset between them boots the test app, which writes its model slot over models_0. OTA data stays.
fw-bench-flash: fw-bench ## Flash the bench app, its bootloader and partition table over the CH340; storage, set.json among it, stays
	cd firmware/build_bench && python -m esptool --chip esp32s3 --port "$(PORT)" --baud $(FLASH_BAUD) write-flash \
	  --skip-flashed $$(sed -n 1p flash_args) $$(sed 1d flash_args | grep -v -e ota_data -e storage)

fw-prod: ## Build the prod profile in build_prod, from its own sdkconfig
	rm -f firmware/build_prod/sdkconfig
	cd firmware && idf.py -B build_prod -D SDKCONFIG=build_prod/sdkconfig \
	  -D SDKCONFIG_DEFAULTS="$(SDKCONFIG_BASE);sdkconfig.prod$(SECRETS)" build

##@ Board B: bridge, flash, monitor

BRIDGE_SESSION := board-bridge
bridge_up = netstat.exe -an -p TCP | tr -d '\r' | grep -q " $(BRIDGE_HOST):$(BRIDGE_TCP_PORT) .*LISTENING"

.PHONY: board-attach board-detach flash monitor wifi models-flash capture-flash capture-radio-off-flash
# The bridge runs on Windows' Python from its temp folder, with host/'s pyserial copied beside it.
board-attach: ## Serve board B's COM port to WSL over RFC 2217, so every target reaches it at PORT (KEHOACH 2.1)
	@command -v py.exe >/dev/null || { echo "board-attach needs Python on Windows (py.exe)"; exit 1; }
	@if usbipd.exe list 2>/dev/null | tr -d '\r' | grep " $(BOARD_USB_ID) " | grep -q Attached; then \
	  usbipd.exe detach --hardware-id $(BOARD_USB_ID); fi
	@if tmux has-session -t $(BRIDGE_SESSION) 2>/dev/null && $(bridge_up); then \
	  echo "board B is on $(PORT) already"; exit 0; fi; \
	tmux kill-session -t $(BRIDGE_SESSION) 2>/dev/null; set -e; \
	win=$$(py.exe -3 -c "import sys, tempfile; print(sys.executable); print(tempfile.gettempdir())" | tr -d '\r'); \
	python=$$(wslpath -u "$$(echo "$$win" | sed -n 1p)"); dir=$$(wslpath -u "$$(echo "$$win" | sed -n 2p)")/esp_sr_bridge; \
	serial=$$(cd host && uv run python -c "import pathlib, serial; print(pathlib.Path(serial.__file__).parent)"); \
	mkdir -p "$$dir"; cp -rT "$$serial" "$$dir/serial"; cp host/src/srhost/bridge.py "$$dir/"; \
	tmux new-session -d -s $(BRIDGE_SESSION) "cd '$$dir' && '$$python' bridge.py --usb-id $(BOARD_USB_ID) \
	  --host $(BRIDGE_HOST) --port $(BRIDGE_TCP_PORT) 2>&1 | tee bridge.log"; \
	for i in $$(seq 40); do \
	  if $(bridge_up); then echo "board B on $(PORT), bridge in tmux $(BRIDGE_SESSION)"; exit 0; fi; \
	  tmux has-session -t $(BRIDGE_SESSION) 2>/dev/null || break; sleep 0.5; done; \
	cat "$$dir/bridge.log"; echo "board B's bridge did not start"; exit 1

board-detach: ## Stop board B's bridge, which hands its COM port back to Windows programs
	@if tmux kill-session -t $(BRIDGE_SESSION) 2>/dev/null; then echo "board B's bridge stopped"; \
	else echo "board B's bridge was not running"; fi

flash: ## Flash and monitor the dev profile over the CH340
	@$(call fresh_sdkconfig,firmware/sdkconfig,$(FW_DEFAULTS) firmware/sdkconfig.dev)
	cd firmware && $(IDF_DEV) -p $(PORT) flash $(ONLY_CHANGED) monitor

monitor: ## Open the serial monitor on the CH340
	cd firmware && idf.py -p $(PORT) monitor

wifi: ## Put board B on a Wi-Fi network, the PC's by default, and point its broker and stream at this PC (KEHOACH 7.2)
	cd host && uv run python -m srhost.wifi --port "$(PORT)"

models-flash: ## Pack every model of contracts/models.lock.json and write models_0 of board B (KEHOACH 4.5.6, 6.1)
	cd ml && PORT=$(PORT) BAUD=$(FLASH_BAUD) AFTER=$(AFTER) ./scripts/50_pack_and_flash.sh

PRODUCT_PARTS = python $$IDF_PATH/components/partition_table/parttool.py --port $(PORT) --baud $(FLASH_BAUD) \
  --partition-table-file firmware/partitions.csv
# Writes FILE $(2) at partition $(1) where board B's flash differs, without erasing the partition first (KEHOACH 2.1).
write_at = python -m esptool --chip esp32s3 --port "$(PORT)" --baud $(FLASH_BAUD) --after $(or $(AFTER),hard-reset) \
  write-flash --skip-flashed \
  $$($(PRODUCT_PARTS) get_partition_info --partition-name $(1) --info offset) $(2)

.PHONY: nvs-save nvs-restore storage-flash product
# NVS holds the roster, its voiceprints and the owner's thresholds; a test app that writes NVS runs between these
# two. The copy holds voiceprints, a person's data (KEHOACH 1.4), so it never lands in the repo.
nvs-save: ## Copy board B's NVS partition to FILE, a path outside the repo
	@case "$(abspath $(FILE))" in "" | $(CURDIR)/*) echo "usage: make nvs-save FILE=<path outside the repo>"; exit 1;; esac
	$(PRODUCT_PARTS) read_partition --partition-name nvs --output "$(abspath $(FILE))"

nvs-restore: ## Write FILE, a copy nvs-save made, back to board B's NVS partition
	@test -f "$(FILE)" || { echo "usage: make nvs-restore FILE=<a copy nvs-save made>"; exit 1; }
	$(PRODUCT_PARTS) write_partition --partition-name nvs --input "$(abspath $(FILE))"

storage-flash: fw-bench ## Write the bench build's LittleFS image, set.json among it, to board B's storage partition
	$(call write_at,storage,firmware/build_bench/storage.bin)

# Models and storage go first, each write leaving the chip in its loader: the product's first boot finds its models and
# keeps the NVS thresholds, and no test app left in ota_0 boots in between to write its own slots over models_0.
product: ## Put the product back on board B after test apps: models, storage, then firmware, and show its boot [SPEAKER=b0|b3]
	$(if $(SPEAKER),$(MAKE) --no-print-directory speaker-use SPEAKER=$(SPEAKER))
	$(MAKE) --no-print-directory models-flash AFTER=no-reset
	$(MAKE) --no-print-directory storage-flash AFTER=no-reset
	$(MAKE) --no-print-directory fw-bench-flash
	cd host && uv run python -m srhost.boot --port "$(PORT)"

capture-flash: ## Flash test_apps/capture, the raw-only recorder (E5-T12)
	cd $(CAPTURE_APP) && idf.py -p $(PORT) flash $(ONLY_CHANGED)

capture-radio-off-flash: ## Flash capture that records 60 s with the radio off, then sends: the Wi-Fi off floor (E2-T4)
	cd $(CAPTURE_APP) && idf.py -B build_radio_off -D SDKCONFIG=build_radio_off/sdkconfig \
	  -D CAPTURE_PROFILE=radio_off -p $(PORT) flash $(ONLY_CHANGED)

##@ Board B: calibration (E2-T5, E2-T6)

.PHONY: calib-flash calib-estimate calib-write calib-shift
calib-flash: ## Flash test_apps/calib, the console that stores NVS calib/*
	cd $(CALIB_APP) && idf.py -p $(PORT) flash $(ONLY_CHANGED)

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

.PHONY: bench-board bss espsr-compare ai-probe ai-unit ai-unit-rnnt ai-unit-speaker ai-unit-ctc ai-unit-pitch ai-unit-wake ai-unit-cores \
  ai-profile listen-unit
bench-board: ## Run bench_afe on board B, keep its rows in docs/measurements/bench, then rebuild budget.md
	@$(call fresh_sdkconfig,$(BENCH_APP)/sdkconfig,$(call app_defaults,$(BENCH_APP)) firmware/sdkconfig.afe)
	cd $(BENCH_APP) && idf.py build
	cd $(BENCH_APP) && pytest pytest_bench_afe.py $(ON_BOARD) -s
	python3 -m tools.budget

# The board and this PC share a network (make wifi); Docker Desktop publishes the receiver's port on it.
# Docker Desktop still starting after WSL restarts leaves docker, and so the receiver, hanging without a word.
DOCKER_ANSWER_S := 20
bss: ## Separate live talkers on board B, one WAV per seat: asks mode, seats, ns, seconds, delay [MODE= SEATS= NS= SECONDS= DELAY=]
	@test -f host/.env || { echo "copy host/.env.example to host/.env and fill it"; exit 1; }
	@timeout $(DOCKER_ANSWER_S) docker info >/dev/null 2>&1 || \
	  { echo "Docker Desktop does not answer: start it, wait until it runs, then make bss again"; exit 1; }
	@$(call fresh_sdkconfig,$(BSS_LIVE_APP)/sdkconfig,$(call app_defaults,$(BSS_LIVE_APP)) firmware/sdkconfig.afe)
	cd $(BSS_LIVE_APP) && idf.py build
	cd $(BSS_LIVE_APP) && BSS_LIVE_DATA_ROOT="$(DATA_ROOT)" BSS_LIVE_STREAM_PORT="$(STREAM_PORT)" \
	  BSS_LIVE_MODE="$(MODE)" BSS_LIVE_SEATS="$(SEATS)" BSS_LIVE_NS="$(NS)" BSS_LIVE_SECONDS="$(SECONDS)" \
	  BSS_LIVE_DELAY="$(DELAY)" \
	  pytest pytest_bss_live.py $(ON_BOARD) -s

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
	cd $(UNIT_APP) && rm -f build/unit.status && idf.py build && \
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
	  exit $$(cat build/unit.status 2>/dev/null || echo 1)
	@if [ -f $(UNIT_APP)/main/probe/ctc_gate.json ]; then \
	  cd ml && uv run --extra train python -m srpipe.tasks.command.ctc.probe --gate-log ../$(UNIT_APP)/build/unit.log; \
	fi

# rnnt/probe.py's three graphs go to model slot 0, every rnnt call is checked against Python and timed; slot 0 ends
# erased.
ai-unit-rnnt: ## Run the rnnt build of the ai_engine suite on board B [RNNT_RUN=<run> RNNT_ROW=<rnnt_* row>] (E11-T20)
	$(ML_CPU) --extra train --extra espdl python -m srpipe.tasks.command.rnnt.probe \
	  $(if $(RNNT_RUN),--run $(RNNT_RUN) --row $(RNNT_ROW))
	@$(call fresh_sdkconfig,$(UNIT_APP)/build_rnnt/sdkconfig,$(call app_defaults,$(UNIT_APP)))
	cd $(UNIT_APP) && rm -f build_rnnt/unit.status && idf.py -B build_rnnt -D SDKCONFIG=build_rnnt/sdkconfig -D UNIT_PROFILE=rnnt build && \
	  $(WRITE_PART) models_0 --input main/probe/rnnt_models.bin && \
	  if [ -f main/probe/rnnt_gate.bin ]; then \
	    $(WRITE_PART) voice --input main/probe/rnnt_gate.bin; \
	  else \
	    $(ERASE_PART) voice; \
	  fi && \
	  { pytest pytest_unit.py $(ON_BOARD) --build-dir build_rnnt -s; echo $$? > build_rnnt/unit.status; } 2>&1 \
	    | tee build_rnnt/unit.log; \
	  exit $$(cat build_rnnt/unit.status 2>/dev/null || echo 1)
	@if [ -f $(UNIT_APP)/main/probe/rnnt_gate.json ]; then \
	  cd ml && uv run --extra train python -m srpipe.tasks.command.rnnt.probe --gate-log ../$(UNIT_APP)/build_rnnt/unit.log; \
	fi

# A row of the ctc ladder goes to model slot 0 with one of its layers; each streams a chunk a step, checked against
# Python bit for bit and timed module by module; slot 0 ends erased.
ai-unit-ctc: ## Run the ctc build of the ai_engine suite on board B: RUN=<run> ROW=<row of its ladder> [CONTRACT=<build> | KALDI=1] [HOLD=] [IN_FLASH=1] (E11-T30)
	$(ML_CPU) --extra train --extra espdl python -m srpipe.tasks.command.ctc.probe --run $(RUN) --row $(ROW) --net-only \
	  $(CTC_HEARD) $(if $(IN_FLASH),--in-flash)
	@$(call fresh_sdkconfig,$(UNIT_APP)/build_ctc/sdkconfig,$(call app_defaults,$(UNIT_APP)))
	cd $(UNIT_APP) && rm -f build_ctc/unit.status && idf.py -B build_ctc -D SDKCONFIG=build_ctc/sdkconfig -D UNIT_PROFILE=ctc build && \
	  $(WRITE_PART) models_0 --input main/probe/ctc_models.bin && \
	  { pytest pytest_unit.py $(ON_BOARD) --build-dir build_ctc -s; echo $$? > build_ctc/unit.status; } 2>&1 \
	    | tee build_ctc/unit.log; \
	  exit $$(cat build_ctc/unit.status 2>/dev/null || echo 1)

# A row of speaker-ptq's ladder goes to model slot 0, its window is checked against Python and timed; slot 0 ends
# erased. CHAIN=1 puts the deployed speaker there instead and the chain's windows in voice, run from PCM to decision.
# CORES=2 builds AI_ENGINE_CORES_TWO in a build folder of its own, so switching never reuses the other's sdkconfig.
SPEAKER_BUILD = build_speaker$(if $(filter 2,$(CORES)),_two)
ai-unit-speaker: ## Run the speaker build of the ai_engine suite on board B: RUN=<run> ROW=<row of its int8/ladder.yaml> [CUT_RANGE="<first op> <last op>"] [CUTS=<n>] [IN_FLASH=1] [CHAIN=1 [WORKERS=6] | PCM=<int16 .npy>] [CORES=2] [LOAN_CAP=<bytes>] (E11-T25, E11-T26, E11-T31, E11-T35)
	$(ML_CPU) --extra train --extra espdl python -m srpipe.tasks.speaker.probe --run $(RUN) --row $(ROW) $(if $(CUT_RANGE),--cut-range $(CUT_RANGE)) $(if $(CUTS),--cuts $(CUTS)) $(if $(IN_FLASH),--in-flash) \
	  $(if $(CHAIN),--chain) $(if $(PCM),--pcm $(abspath $(PCM))) $(if $(WORKERS),--workers $(WORKERS))
	@$(call fresh_sdkconfig,$(UNIT_APP)/$(SPEAKER_BUILD)/sdkconfig,$(call app_defaults,$(UNIT_APP)))
	cd $(UNIT_APP) && rm -f $(SPEAKER_BUILD)/unit.status && idf.py -B $(SPEAKER_BUILD) -D SDKCONFIG=$(SPEAKER_BUILD)/sdkconfig -D UNIT_PROFILE=speaker -D UNIT_CORES=$(or $(CORES),1) \
	  -D UNIT_LOAN_CAP=$(or $(LOAN_CAP),0) build && \
	  $(WRITE_PART) models_0 --input main/probe/speaker_models.bin && \
	  $(if $(CHAIN),$(WRITE_PART) voice --input main/probe/speaker_chain.bin,$(ERASE_PART) voice) && \
	  { pytest pytest_unit.py $(ON_BOARD) --build-dir $(SPEAKER_BUILD) -s; echo $$? > $(SPEAKER_BUILD)/unit.status; } 2>&1 \
	    | tee $(SPEAKER_BUILD)/unit.log; \
	  exit $$(cat $(SPEAKER_BUILD)/unit.status 2>/dev/null || echo 1)

# A row of pitch-ptq's ladder goes to model slot 0 with its cuts; one window is checked against Python and timed,
# module by module too; slot 0 ends erased (KEHOACH 3.11 step 2). With STEP the graph streams STEP frames a call
# instead and the window goes through it call by call (step 3.2); with CHAIN PCM goes through the front end, the
# network and the decode as the product runs them (step 3.4).
ai-unit-pitch: ## Run the pitch build of the ai_engine suite on board B: RUN=<run> ROW=<row of its int8/ladder.yaml> [FRAMES=<n>] [CUT_RANGE="<first op> <last op>"] [CUTS=<n>] [STEP=<frames a call>] [CHAIN=1] (E11-T37)
	$(ML_CPU) --extra train --extra espdl python -m srpipe.tasks.pitch.probe --run $(RUN) --row $(ROW) \
	  $(if $(FRAMES),--frames $(FRAMES)) $(if $(CUT_RANGE),--cut-range $(CUT_RANGE)) $(if $(CUTS),--cuts $(CUTS)) \
	  $(if $(STEP),--stream $(STEP)) $(if $(CHAIN),--chain)
	@$(call fresh_sdkconfig,$(UNIT_APP)/build_pitch/sdkconfig,$(call app_defaults,$(UNIT_APP)))
	cd $(UNIT_APP) && rm -f build_pitch/unit.status && idf.py -B build_pitch -D SDKCONFIG=build_pitch/sdkconfig -D UNIT_PROFILE=pitch build && \
	  $(WRITE_PART) models_0 --input main/probe/pitch_models.bin && \
	  { pytest pytest_unit.py $(ON_BOARD) --build-dir build_pitch -s; echo $$? > build_pitch/unit.status; } 2>&1 \
	    | tee build_pitch/unit.log; \
	  exit $$(cat build_pitch/unit.status 2>/dev/null || echo 1)

ai-unit-cores: ## Run the product's four graphs on core 0, preempting each other as xac_task and nhan_task do, beside sach's frames on core 1, on board B: each one's bits alone (E11-T37)
	$(ML) python -m srpipe.export.pack_models artifacts/models.bin --lock
	@$(call fresh_sdkconfig,$(UNIT_APP)/build_cores/sdkconfig,$(call app_defaults,$(UNIT_APP)))
	cd $(UNIT_APP) && rm -f build_cores/unit.status && idf.py -B build_cores -D SDKCONFIG=build_cores/sdkconfig -D UNIT_PROFILE=cores build && \
	  $(WRITE_PART) models_0 --input $(CURDIR)/ml/artifacts/models.bin && \
	  { pytest pytest_unit.py $(ON_BOARD) --build-dir build_cores -s; echo $$? > build_cores/unit.status; } 2>&1 \
	    | tee build_cores/unit.log; \
	  exit $$(cat build_cores/unit.status 2>/dev/null || echo 1)

ai-unit-wake: ## Stream the wake TCN of wake.yaml, random weights, on board B: its profile in build_wake/unit.log [STEP=<hops a call>] (E11-T11)
	$(ML_CPU) --extra train --extra espdl python -m srpipe.tasks.wake.quant probe $(if $(STEP),--step $(STEP))
	@$(call fresh_sdkconfig,$(UNIT_APP)/build_wake/sdkconfig,$(call app_defaults,$(UNIT_APP)))
	cd $(UNIT_APP) && rm -f build_wake/unit.status && idf.py -B build_wake -D SDKCONFIG=build_wake/sdkconfig -D UNIT_PROFILE=wake build && \
	  { pytest pytest_unit.py $(ON_BOARD) --build-dir build_wake -s; echo $$? > build_wake/unit.status; } 2>&1 \
	    | tee build_wake/unit.log; \
	  exit $$(cat build_wake/unit.status 2>/dev/null || echo 1)

# Step 2.2 of KEHOACH 3.14's procedure, any branch: LOG holds esp-dl's profile_module table of a board run, ESPDL
# the model that ran, its .info beside it (make ai-unit-speaker leaves $(SPEAKER_BUILD)/unit.log and <run>/int8/<row>/).
ai-profile: ## Rank a board run's modules by time, each slow esp-dl path tagged with its patch: LOG=<log> ESPDL=<model> [TOP=40] (E11-T29)
	$(ML) --extra espdl python -m srpipe.compress.quant.espdl_profile --log $(abspath $(LOG)) --espdl $(abspath $(ESPDL)) \
	  $(if $(TOP),--top $(TOP))

# The locked models but the wake go to model slot 0, so every utterance has its window (KEHOACH 5.4); each window
# is decided as Python decides its int8 simulation, field by field.
listen-unit: ## Run svc_listen on board B over every Gate 3 session, round by round [SPEAKER=1: the speaker check on] (E11-T14, E11-T26)
	$(ML_CPU) --extra train --extra espdl python -m srpipe.tasks.command.ctc.probe --listen $(if $(SPEAKER),--speaker)
	$(ML) python -m srpipe.export.pack_models artifacts/models.bin --lock --skip wake
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

.PHONY: broker-up broker-down host-live commands speakers speaker-enrol speaker-name speaker-forget
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

speakers: ## Send a speaker roster to one board, retained, and show what it keeps: DEVICE=<deviceId> SET=<roster.json> | LIST=1 | CLEAR=1 (E11-T31)
	cd host && uv run python -m srhost.speakers --device "$(DEVICE)" \
	  $(if $(LIST),--list,$(if $(CLEAR),--clear,"$(abspath $(SET))"))

speaker-enrol: ## Enrol one person of the board's roster, prompting the commands to say: DEVICE=<deviceId> ID=spk_NNN [SET=<command set>] (E11-T31)
	cd host && uv run python -m srhost.speakers --device "$(DEVICE)" --enrol "$(ID)" $(if $(SET),--set "$(abspath $(SET))")

ROSTER ?= host/speakers/home_vi.json

speaker-name: ## Rename one person of the roster file, or add them with CONSENT=, and send it: DEVICE=<deviceId> ID=spk_NNN NAME="<name>" [CONSENT=<code>] [ROSTER=<file>] (E11-T31)
	cd host && uv run python -m srhost.speakers --device "$(DEVICE)" "$(abspath $(ROSTER))" --rename "$(ID)" \
	  --name "$(NAME)" $(if $(CONSENT),--consent "$(CONSENT)")

speaker-forget: ## Have the board drop one person's voiceprint and keep them on the roster: DEVICE=<deviceId> ID=spk_NNN [ROSTER=<file>] (E11-T31)
	cd host && uv run python -m srhost.speakers --device "$(DEVICE)" "$(abspath $(ROSTER))" --forget "$(ID)"

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
endif
