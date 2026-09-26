# TASKS.md

Backlog toàn dự án `esp-sr`. Kiến trúc ở `docs/KE_HOACH_esp_sr_esp32s3.md` (**KẾ HOẠCH**), lý do và bậc
thang thuật toán ở `docs/tong_quan_version_5.md` (**TỔNG QUAN**), quy tắc ở `CLAUDE.md`.

Mã task: `E<epic>-T<số>`. Cột **V5** là mã tương ứng trong TỔNG QUAN.
🔬 = task sinh ra số đo, kết quả ghi vào `docs/measurements/`.
~~Gạch~~ = xong, kèm ngày và bằng chứng trong ô "Xong khi".

Mỗi module thuật toán đi đúng bốn bước của TỔNG QUAN §5.3, không bỏ bước nào: **(1)** bản Python ·
**(2)** bộ vàng có đối chứng âm · **(3)** bản C khớp Python trong ngưỡng · **(4)** đo chi phí trên board,
cộng vào `budget.md`. Một task module chỉ gạch khi đủ cả bốn.

---

## Đường đi

```
E1 nền repo ─┬─► E2 phần cứng, dàn micro ──┐
             ├─► E3 hợp đồng, lưới ────────┼─► CỬA 0 ─┬─► E7 afe một kênh ─┐
             ├─► E4 ml/ nền ───────────────┤          ├─► E8 afe không gian ┼─► CỬA 1
             └─► E5 firmware nền ──────────┤          ├─► E9 dìm nhiễu ─────┘
                 E6 dsp_spec ──────────────┘          ├─► E10 khử vọng
                                                      ├─► E11 lang_vi + nhận dạng ─► CỬA 2, 3
                                                      └─► E12 tiếng nói ra ────────► CỬA 4
                                        E13 mạng, máy tính nhận (song song từ E5)
                                                      ▼
                                          E14 ôm về thành app ─► CỬA 5
```

**Nguyên tắc**: nền và hợp đồng trước, module sau, app cuối — nhưng **đo ngay từ module đầu tiên**.
Ba bẫy của TỔNG QUAN §5.2 (ngân sách chỉ lộ lúc ghép, hợp đồng chốt muộn, không có chỗ đo tại chỗ) bị
chặn bằng E3, E5-T11 và bước 4 của mỗi module.

**Dữ liệu đi sớm.** E11-T1 và E11-T2 bắt đầu ngay sau E1, không đợi Cửa 0: đó là việc dài nhất và không
cần firmware.

**Đích trước mắt là mốc demo MQTT (E13-T11)** trên board hiện có — hai INMP441, chưa có loa. Mốc này
không bỏ task nào: E8, E9-T5, E10, E12 vẫn nằm nguyên và làm sau nó.

---

## E1 — Nền repo

| ID | Task | Xong khi | Chặn bởi | V5 |
|---|---|---|---|---|
| ~~E1-T1~~ | **Xong 26/09.** File gốc theo KẾ HOẠCH §4.1: `.gitignore` (loại `CLAUDE.md`, `.claude/`, mọi file tiếng), `.gitattributes`, `.editorconfig`, `README.md`, `Makefile`. Không có `LICENSE` theo ý chủ dự án. Thư mục khối ra đời cùng task đầu tiên đặt file vào nó, vì git không giữ thư mục rỗng | `make help` in 15 đích; `git status --ignored` thấy `CLAUDE.md` và `.claude/` bị bỏ qua | — | — |
| ~~E1-T2~~ | **Xong 26/09.** Repo private `vanviet/esp-sr` trên Gitea nội bộ, nhánh `main` | push được, `git status` sạch; `.git/config` không mang mật khẩu | E1-T1 | — |
| ~~E1-T3~~ | **Xong 26/09.** `contracts/`: `grid.yaml`, `array.yaml` (khoảng cách danh định 6,5 cm, E2-T3 thay số đo), tám schema (bảy payload + `responses`), `mqtt_topics.yaml` kèm nhịp heartbeat và telemetry, `stream/frame.yaml` (đầu 24 B, mọi trường đúng biên), `commands/default_vi.json` chín lệnh demo, `responses/vi.json`, `models.lock.json` rỗng, `README.md` ghi luật viết schema | `tools/tests/test_contracts.py` 14/14 qua: schema hợp lệ Draft 2020-12, nằm trong tập con generator, bộ lệnh và câu trả lời hợp lệ và trỏ đúng nhau, đầu khung căn biên; mỗi luật có một ca đối chứng âm | E1-T1 | — |
| ~~E1-T4~~ | **Xong 26/09.** `tools/gen_contracts.py` sinh 12 file: `gen_grid.h` `gen_array.h` `gen_stream.h` (common), `gen_topics.h` `gen_payload.h` (net_mqtt), `grid.py` `array.py` (ml), `stream.py` `topics.py` `payload.py` (host). Giá trị suy ra chỉ tính ở đây: số vạch, trễ lớn nhất 3,03 mẫu, tần số gập 2 638 Hz, `GEN_GRID_HASH` = `0x9c914b61`. Phân tích payload từ chối, không cắt; mọi hàm sinh ra có doc comment kèm `@ctx` | chạy hai lần ghi y hệt; `make check` sạch; `tools/tests/test_gen_contracts.py`: header dịch bằng gcc `-Werror`, bộ lệnh mặc định / telemetry / lệnh lồng đi trọn vòng JSON → struct → JSON, bốn ca đối chứng âm đỏ đúng chỗ | E1-T3 | — |
| ~~E1-T5~~ | **Xong 26/09.** `check_comments.py` (chuyển từ face attendance: quét Python bằng `tokenize`, bắt cả banner kẻ khung `─`, nhận banner file sinh dạng `#`), `check_layers.py` (bảng §4.5.4, cạnh cấm của luật 2, chu trình, cấm `esp-sr` trong `REQUIRES` lẫn `idf_component.yml`), `check_purity.py` (tầng thuần không include nền tảng, không cấp phát, không log) | cả ba sạch trên repo; `tools/tests/test_checks.py` 22/22: mỗi luật một ca vi phạm cố ý bị bắt, đầu vào sạch qua. Chính `gen_contracts.py` dính 6 banner và đã sửa | E1-T1 | — |
| ~~E1-T6~~ | **Xong 26/09.** `.pre-commit-config.yaml`: ba script kiểm · sinh lại `contracts/` · tests của `tools/` · `ruff` · `ruff-format` · `clang-format` (bỏ qua `gen_*.h`) · kiểm cuối dòng, YAML, JSON, khoá riêng. Hook cần PyYAML/jsonschema tự khai `additional_dependencies`. Cấu hình kèm: `ruff.toml` ở gốc, `firmware/.clang-format` thụt 4 kiểu ESP-IDF (cả hai đã ghi vào KẾ HOẠCH §4 trước khi tạo) | `uvx pre-commit run --all-files`: 15 hook qua, `clang-format` bỏ qua vì chưa có file C tự viết | E1-T5 | — |
| E1-T7 | CI trên GitHub Actions của bản sao private `dovanviet04112004/esp-sr` (remote `github`); kết quả gắn ngược lên commit trên Gitea bằng `make ci-status`. Runner Docker ở máy đã bỏ (ổ C: đầy 93%, Docker Desktop lỗi đĩa), Actions của Gitea đã tắt | push lên `github` là bốn workflow chạy; `make ci-status` gắn tích xanh hoặc đỏ lên đúng commit trên Gitea | E1-T4, E1-T6 | — |
| ~~E1-T8~~ | **Xong 26/09.** `docs/DU_LIEU.md` (nguồn, split, phiên thu theo mã người nói), `docs/measurements/{budget,latency,ram,parity,mic_array}.md` có sẵn cột, mẫu `docs/adr/0000-mau.md` | file tồn tại, bảng có cột đúng với task sẽ điền; pre-commit sạch | E1-T1 | — |
| E1-T9 | Sửa TỔNG QUAN theo KẾ HOẠCH §0.2 rồi xoá bảng §0.2 | hai file không còn chỗ lệch; `grep "mica_"` trong `docs/` ra rỗng | — | — |

---

## E2 — Phần cứng và dàn micro

Board B là board duy nhất có micro (KẾ HOẠCH §2.1). Mọi phép đo thu âm chạy trên nó.

| ID | Task | Xong khi | Chặn bởi | V5 |
|---|---|---|---|---|
| E2-T1 | Micro là **2 × INMP441** (chủ dự án xác nhận 26/09). Tải datasheet; ghi SNR 61 dBA là giới hạn đã biết (KẾ HOẠCH §0.2 dòng 19) | datasheet và sha256 ở `hardware/datasheets/INDEX.md`; §2.1 khớp | — | V5.0.1 |
| E2-T2 | Xác nhận bảng chân §2.2 trên board thật; viết `app_config.h` **cùng commit** với §2.2 | micro thu được ở GPIO 19/20/16 với console UART và `ESP_CONSOLE_SECONDARY_NONE` | E5-T3 | — |
| E2-T3 | Đo khoảng cách hai micro, giữa tâm hai lỗ âm | số ghi vào `contracts/array.yaml` và `mic_array.md` | — | V5.0.1 |
| E2-T4 | 🔬 Đo bốn chỉ tiêu của dàn: khoảng cách, chênh độ nhạy, chênh pha, SNR; cộng nền ồn khi Wi-Fi phát và khi tắt | bảng năm dòng ở `mic_array.md`, không dòng nào trống | E2-T3, E5-T12 | V5.0.1 |
| E2-T5 | 🔬 Chọn `pcm_shift` (24 → 16 bit): tiếng nói to ở 10 cm không cắt đỉnh, nền ồn phòng yên vẫn trên bước lượng tử `int16` | giá trị ghi NVS `calib/pcm_shift` và `mic_array.md` kèm hai phép đo | E5-T4 | — |
| E2-T6 | Hiệu chuẩn `balance`: loa ngoài chính diện 1 m, ồn trắng 30 s, thu bằng `capture` → hệ số phức mỗi vạch tính bằng `srpipe` → ghi NVS `calib/bal` qua `test_apps/calib`; nếu cần chạy từ console thì thêm lệnh `calib` ở đây. Không cần module C của E7-T2 | chênh biên độ sau bù **dưới 1 dB** toàn băng 50 Hz – 8 kHz; chênh pha sau bù ghi thành số ở hai nhiệt độ phòng | E2-T4, E6-T1 | V5.0.2 |
| E2-T7 | Chốt quy ước kênh và dấu (§2.3): micro nào là `ch0`, dấu của trễ, góc 0° ở đâu, `ref` là kênh thứ ba | vỗ tay phía `ch1` cho `τ` dương trên bản thu; §2.3 và `array.yaml` khớp | E2-T3 | V5.0.3 |
| E2-T8 | Lắp MAX98357A + loa 4 Ω 3 W vào GPIO 17/18 (§2.2), loa trên đường trung trực của dàn. **Lắp khi tới E10**; mốc demo không cần | phát được một tông 1 kHz qua `drv_audio` TX với `APP_SPEAKER_ENABLE=y` | E10-T1 | V5.4.1 |
| E2-T9 | 🔬 Đo trễ khối TX → RX bằng tiếng quét tần | trễ ghi NVS `calib/aec_delay`, đo lại năm lần lệch không quá một mẫu | E2-T8 | V5.4.1 |
| E2-T10 | Khung `hardware/`: `README.md`, `datasheets/INDEX.md` (tên file ↔ URL ↔ sha256), ảnh board, luật ignore PDF | `git status` sạch sau khi thả PDF vào | E1-T1 | — |

---

## E3 — Hợp đồng và lưới thời gian

Đóng trước khi viết module đầu tiên (TỔNG QUAN V5.0.4). Sau E3-T5 mọi thay đổi đi qua CLAUDE.md §1.2.

| ID | Task | Xong khi | Chặn bởi | V5 |
|---|---|---|---|---|
| ~~E3-T1~~ | **Xong 26/09.** `contracts/grid.yaml` (§3.1) và `array.yaml` (§2.3) từ E1-T3; `gen_grid.h`, `grid.py` từ E1-T4; ADR-0001 so bốn lưới và chốt 16 kHz, bước 256, FFT 512 | `gen_grid.h` và `grid.py` có `GEN_GRID_HASH` = `0x9c914b61`; `docs/adr/0001-luoi-16k-256-512.md` | E1-T4 | — |
| ~~E3-T2~~ | **Xong 26/09.** Header công khai: `dsp_spec` (fft, window, stft, mel), `dsp_afe` (mặt tiền `feed`/`fetch` với `"MM"`/`"MMR"`, `dsp_afe_frame_t`, khe `dsp_afe_ns_ops_t`, chín header module), `lang_vi`, `ai_engine`, `drv_audio`. Một khuôn cho mọi module thuần: `_workspace_bytes` → `_init` vào bộ nhớ người gọi cấp → `_process` không chặn | 67/67 hàm công khai có doc comment kèm `@ctx`; `check_comments`, `check_purity`, `clang-format` sạch; mọi header dịch được dưới gcc C11 và g++ C++17 với `-Wall -Wextra -Werror` | E3-T1 | V5.0.4 |
| E3-T3 | Luật bộ nhớ: `_workspace_bytes` + vùng `hot`/`cold` cho mọi module thuần | bảng `sizeof` và byte vùng làm việc từng module ở `ram.md` (ước, đánh 🔬) | E3-T2 | V5.0.4 |
| E3-T4 | Bản giả của mọi hàm trong E3-T2 chạy được trong app khung | E5-T11 dựng và chạy với bản giả, không sửa header | E3-T2, E5-T6 | V5.0.4 |
| E3-T5 | Đóng băng: tag `contract-v1` | tag có trên Gitea | E3-T4 | V5.0.4 |

---

## E4 — `ml/` nền Python

| ID | Task | Xong khi | Chặn bởi | V5 |
|---|---|---|---|---|
| ~~E4-T1~~ | **Xong 26/09.** `ml/pyproject.toml` + `uv.lock` (nền: numpy, scipy, soundfile, pyyaml, pydantic, pyroomacoustics; PyTorch ở nhóm `train`), `configs/common/{paths,hardware}.yaml`, `.env.example` với `SRPIPE_DATA_ROOT`, `ml/data/README.md` | `uv sync` sạch, `import srpipe` và `srpipe.generated.grid` chạy; `ruff` sạch | E1-T1 | — |
| ~~E4-T2~~ | **Xong 26/09.** `srpipe/core/`: `config` (gộp YAML, ghi đè dạng `a.b=giá trị`, `data_paths` với `SRPIPE_DATA_ROOT`), `run_dir` (thư mục run có `config.resolved.yaml`, `split.lock`, `env.txt`), `seed`, `audio_io` (int16 chia 32768 như firmware), `logger` | `ml/tests/test_core.py` 11/11 qua; ruff sạch | E4-T1 | — |
| ~~E4-T3~~ | **Xong 26/09.** `srpipe/golden/gold.py` giữ khuôn `.gold` ở một chỗ; bộ đọc C không cấp phát `firmware/test_apps/parity/main/gold_read.h` | Python ghi → C (gcc `-Werror`) đọc → khớp từng byte trên năm dtype `f32 i8 i32 u8 i16`, kể cả tensor vô hướng; ca hỏng magic, dư byte, tên dài, dtype lạ bị từ chối. Lượt chạy trên board đi cùng `test_apps/parity` ở E6-T4 | E4-T1 | — |
| E4-T4 | `srpipe/scenes/`: dựng cảnh có nhãn bằng `pyroomacoustics` — phòng, RT60, hướng người nói và nhiễu, SNR, dàn micro từ `array.yaml` | một lệnh sinh bộ cảnh chuẩn có manifest và sha256; nhãn hướng khớp hình học | E4-T1, E3-T1 | V5.2 |
| E4-T5 | `srpipe/metrics/`: SI-SDR, STOI, PESQ (giấy phép bản cài ghi rõ), ERLE, lỗi góc, DET | mỗi thước có một phép kiểm với giá trị biết trước | E4-T1 | — |
| E4-T6 | Đích `make golden`, `make measure`, `make report` khung | `make report` chạy trên repo rỗng không lỗi, ra bảng trống | E4-T2 | — |
| ~~E4-T7~~ | **Xong 26/09.** Cây `ml/data/` theo KẾ HOẠCH §4.4.1: `README.md`, `manifests/{speech,noise,rir,device}`, `splits/{ns,wake,command,device}` trong repo, `board_b.csv` có sẵn dòng tiêu đề; `paths.yaml` có `manifests` và `cache`; `srpipe/core/splits.py` + `tests/test_splits.py` | 26/26 pytest qua: năm luật §1.3 mỗi luật một ca đối chứng âm đỏ đúng chỗ, dòng thiếu cột bị từ chối; chưa có split nào thì phép kiểm split thật vẫn xanh | E4-T2 | — |

---

## E5 — Firmware nền và app khung

| ID | Task | Xong khi | Chặn bởi | V5 |
|---|---|---|---|---|
| ~~E5-T1~~ | **Xong 26/09.** `firmware/`: CMake với `PROJECT_VER` 0.1.0, bốn profile theo §4.5.8 (console UART, `SECONDARY_NONE`, Wi-Fi/lwIP/esp_timer/MQTT ghim nhân 0, ISR I2S ở IRAM), `partitions.csv` §6.1, `main/Kconfig.projbuild` với `APP_CONSOLE`, `APP_SPEAKER_ENABLE`, `SR_PROFILING` | `make fw-dev` 183 KB và `make fw-bench` 141 KB trên slot 3 MB (còn 94–95%); nạp lên board B, log `esp-sr 0.1.0, grid 0x9c914b61`, thấy PSRAM 8 MB và flash 16 MB. Cảnh báo còn lại là khoá Kconfig của component chưa viết | E1-T1 | — |
| ~~E5-T2~~ | **Xong 26/09.** `common`: `app_events.h` (bit `eg_system`, trạng thái `LISTEN`/`COMMAND`/`REPLY`, `app_event_t`, `app_speak_req_t`), `app_err.h` (mã lỗi dự án, mã sự kiện), cùng `gen_grid.h` `gen_array.h` `gen_stream.h` | `check_purity` sạch; header dịch dưới gcc `-Werror` | E1-T4 | — |
| ~~E5-T3~~ | **Xong 26/09.** `bsp_board`: `app_config.h` theo §2.2 (I2S 19/20/16, ampli 17/18 dành cho E10, LED 21), `bsp_board_init` giữ ampli câm và LED tắt từ lệnh đầu của `app_main` | bật `ESP_CONSOLE_USB_SERIAL_JTAG` (cả console phụ) thì dịch dừng với thông báo rõ, console UART thì dịch được — kiểm cả hai chiều; firmware có `bsp_board` chạy trên board | E5-T1 | — |
| ~~E5-T4~~ | **Xong 26/09.** `drv_audio` phía thu: I2S0 master, khe 32 bit × 2 chuẩn Philips cho INMP441, DMA 8 × 256 mẫu, `pcm_shift` là số bit dịch phải (16 = giữ 16 bit cao), đếm tràn DMA trong ISR ở IRAM và `seq` cộng khung mất. TX trả `ESP_ERR_NOT_SUPPORTED` tới E10-T1 | trên board B, `test_apps/unit` 3/3 qua: 625 khung (10 s) liền, `seq` 0…624, **0 tràn**; hai kênh sống và khác nhau (`ch0` −39,7 dBFS, `ch1` −44,0 dBFS, số sơ bộ ở `mic_array.md`) | E5-T3 | V5.0.9 |
| ~~E5-T5~~ | **Xong 26/09.** `sys_storage`: mọi khoá NVS §6.2 kèm kiểu ở `storage_format.h`, hàm đọc ghi `u8` `i8` `u16` `u32` `str` `blob` (chuỗi rỗng là vắng), `sys_storage_seed` có số hiệu, LittleFS thay file qua `.tmp` + một lần `rename`, mmap ảnh model kiểm khuôn §6.3, `littlefs` 1.22.3 | trên board B, `test_apps/unit` 12/12 qua: ghi, đọc, xoá cả 7 namespace; **20 lần tự cắt điện**, cả 20 rơi giữa lúc ghi `set.json`, cả 20 để lại file cũ nguyên vẹn; `deviceId` `sr-3485188f7a70`; thay 12 KB mất 157–166 ms (`latency.md` §2) | E5-T1 | — |
| ~~E5-T6~~ | **Xong 26/09.** `main`: `app_boot` (board → NVS/LittleFS → wiring → I2S, in heap sau từng bước; `pcm_shift` từ NVS, lùi về `APP_PCM_SHIFT_FALLBACK`), `app_tasks` (bảng tĩnh §5.2, `xTaskCreateStaticPinnedToCore`, ngăn xếp tĩnh, watchdog mọi task có vòng lặp, mọi chỗ chờ có hạn), `app_wiring` (§5.3 kèm `q_free`; hàng đợi nhân 0 ở PSRAM); `dsp_spec` `dsp_afe` `net_mqtt` đăng ký dạng chỉ header, `cjson` 1.7.19 | trên board B (`dev`): bảng 6 task, 29 696 B ngăn xếp tĩnh; watermark từng task in sau 5 s (`ram.md` §2); `APP_SPEAKER_ENABLE=n` không có `noi_task`, 2 kênh, TX không mở; thăm dò tạm: 62,4 khung/s qua `thu` → `sach`, 0 bỏ, 0 tràn; `bench` và bản có loa dịch sạch | E5-T2 | V5.7.3 |
| ~~E5-T7~~ | **Xong 26/09.** `app_console` trên UART0, task REPL ở nhân 0 ưu tiên 2 (§5.2): `wifi set <ssid> [pass]`, `nvs get` (tự dò kiểu str/u8/i8/u16/u32), `nvs set … -t <kiểu>` có kiểm miền, `nvs del`; mật khẩu chỉ in độ dài, không lưu lịch sử xuống flash. **`calib run` chuyển sang E2-T6**: hệ số tính bằng `srpipe` trên máy tính rồi ghi qua `test_apps/calib`, trên board chưa có gì để chạy | `prod` không có symbol nào của console (`nm`); trên board B mọi lệnh đúng, 70000 cho `u16` và mật khẩu 3 ký tự bị từ chối; log `dev` không còn in mật khẩu (sửa ở `sys_storage`); board đã giữ SSID `esp` | E5-T5 | — |
| E5-T8 | `net_wifi` + `net_task`: chờ link tới khi có, lùi dần 1 → 30 s | rút router 5 phút rồi cắm lại, máy tự về không khởi động lại | E5-T6 | — |
| ~~E5-T9~~ | **Xong 26/09.** `net_mqtt`: URI từ NVS `device/mqtt_uri`, lùi về `NET_MQTT_URI_FALLBACK` (mặc định rỗng); username `deviceId`, mật khẩu NVS `device/mqtt_pass`; `status` `ONLINE` retained + LWT `OFFLINE`, keepalive 15 s; heartbeat 30 s qua `gui_task`; mọi lần gửi dùng `esp_mqtt_client_enqueue`; cJSON trên hai vùng PSRAM cấp lúc boot, `jsonArenaPeak` thêm vào `contracts/` | trên `sr-emqx`: client `srhost` thấy `ONLINE` retained, heartbeat đều 30 s (heap nội 139 KB, 0 khung bỏ, vùng JSON cao nhất 1800 B); giữ board trong reset thì `OFFLINE` sau ~19 s, thả ra `ONLINE` sau ~6 s; 9/9 payload đúng schema. Phải tắt service Mosquitto của Windows đang chiếm cổng 1883 | E5-T8, E13-T5 | — |
| E5-T10 | `net_stream` + `svc_report`/`luong_task`: khách TCP, khung theo `gen_stream.h`, bỏ cả khung khi nghẽn | `host/stream_rx.py` ghi WAV; chặn mạng 5 s thì thấy hở `seq`, board không chậm khung nào | E5-T9, E13-T3 | — |
| E5-T11 | **App khung rỗng** = `main` với mọi module `dsp_afe` tắt: thu → STFT → iSTFT → gửi ra | 🔬 chạy liền **10 phút 0 khung mất**; sai số dựng lại ghi bằng số; RAM nội còn lại ghi vào `ram.md` | E3-T4, E5-T4, E5-T10, E6-T4 | V5.0.9 |
| E5-T12 | `test_apps/capture`: chỉ thu, `mode` 3, không có `dsp_afe` | một phiên 30 phút về `host` không hở `seq` | E5-T10 | V5.7.1 |
| E5-T13 | `test_apps/bench_afe`, `bench_mem` + `tools/budget.py` → `budget.md` | **một lệnh** in bảng RAM và µs theo module, để trống chờ điền | E5-T6 | V5.0.10 |
| E5-T14 | `drv_led`: sáng khi luồng tiếng mở | mở `SET_STREAM` thì LED sáng, tắt thì tắt, tự tắt sau 10 phút | E5-T10 | — |

---

## E6 — `dsp_spec`

| ID | Task | Xong khi | Chặn bởi | V5 |
|---|---|---|---|---|
| ~~E6-T1~~ | **Xong 26/09.** Bản Python `srpipe/dsp/spec/`: `window` (căn Hann tuần hoàn dạng sin), `fft` (thuận không chia, nghịch 1/n, float32), `stft` (chạy dòng, một bước vào một phổ ra), `mel` (Slaney, tam giác chuẩn hoá diện tích, log tự nhiên có sàn, MFCC DCT-II trực chuẩn) — soi gương bốn header E3-T2 | dựng lại bốn tín hiệu trễ đúng một bước, **138,6–139,2 dB**, sai số lớn nhất 2,4e-7 (`parity.md`); 46/46 pytest | E4-T2 | V5.0.5 |
| ~~E6-T2~~ | **Xong 26/09.** Component `dsp_spec`: `fft` bọc `dl_fft` (ghim `==0.7.0`), `window`, `stft` chạy dòng, `mel` Slaney lưu thưa + MFCC; người gọi cấp mọi vùng làm việc, trừ bảng `dl_fft` (ngoại lệ luật 7 §4.5.3); `README.md`, không `CHANGELOG`. Lúc đầu có cả backend `esp-dsp` qua Kconfig, bỏ sau E6-T3 | firmware chính dựng được với `dsp_spec`; `test_apps/unit` 8/8 trên board B: FFT khớp DFT `double` 1,2e-7, dựng lại STFT 136,3 dB | E3-T2 | V5.0.5 |
| ~~E6-T3~~ | **Xong 26/09.** 🔬 Đo `dl_fft` so với `esp-dsp` trên board, float32 và int16, 256 / 512 / 1024 điểm | bảng ở `latency.md` §1: 512 điểm thuận/nghịch 118/136 µs so với 149/161 µs, một khung chuỗi STFT **451 µs (2,8% một nhân)**; **chọn `dl_fft`** ở ADR-0002 (nhanh hơn 87 µs mỗi khung, dựng lại hơn 16 dB, có sẵn FFT nghịch thực; tốn thêm ~2,2 KB RAM nội) | E6-T2 | V5.0.6 |
| ~~E6-T4~~ | **Xong 26/09.** Bộ vàng STFT / iSTFT, có đối chứng âm: `srpipe.dsp.emit_golden` sinh 4 ca 16 bước + `case_neg_000` lệch một mẫu vào `contracts/golden/stft/`; app `test_apps/parity` nướng chúng vào `/lfs/golden`, `pytest_parity.py` phán theo `tolerance.yaml` | C khớp Python **137,2 dB** trên phổ, **137,4 dB** trên tín hiệu dựng lại (ngưỡng 115 dB); ca lệch một mẫu ra −3,1 dB và bị bắt đỏ; chạy qua pytest-embedded: 1 passed | E6-T1, E6-T2, E4-T3 | V5.0.7 |
| E6-T5 | Bộ vàng mel, có đối chứng âm | như trên | E6-T4 | V5.0.8 |
| E6-T6 | Chốt đường kiểm trên máy tính: target `linux` của IDF, hoặc CMake thường + lớp đệm `esp_err.h` | `test_apps/host` của `dsp_spec` chạy trong CI | E6-T2 | V5.0.5 |
| E6-T7 | 🔬 Chi phí từng hàm vào `budget.md` | µs trung bình và đỉnh, RAM tĩnh và vùng làm việc | E6-T4, E5-T13 | V5.0.10 |

> **Cửa 0** đóng khi E2-T4, E2-T6, E2-T7, E3-T5, E5-T11, E6-T4, E6-T5 xong. Không đóng thì dừng.

---

## E7 — `dsp_afe` tầng một kênh

Bốn module không cần mô hình, không cần dữ liệu. Làm trước vì rẻ và lấp đầy `budget.md` sớm.

| ID | Module | Thuật toán (KẾ HOẠCH) | Xong khi | Chặn bởi | V5 |
|---|---|---|---|---|---|
| E7-T1 | `hpf` | biquad Butterworth 80 Hz (§3.4) | độ lệch một chiều sau lọc ghi bằng dB; đủ bốn bước | Cửa 0 | V5.1.1 |
| E7-T2 | `balance` | hệ số phức mỗi vạch, sau STFT (§3.4) | khớp Python trên bộ vàng; đủ bốn bước | Cửa 0 | V5.1.2 |
| E7-T3 | `vad` | GMM sáu dải, kéo dài 240 ms, **trước `agc`** (§3.10) | hơn ngưỡng năng lượng trần bằng F1 trên tập có nhãn; đủ bốn bước | Cửa 0 | V5.1.4 |
| E7-T4 | `agc` | hai tầng, chỉ thích nghi khi `vad = 1` (§3.10) | mức ra ±3 dB quanh đích với vào −50 … −10 dBFS, không cắt đỉnh, không dao động; đủ bốn bước | E7-T3 | V5.1.3 |

---

## E8 — `dsp_afe` tầng không gian

Dựng cả hai đường rồi chấm, không chọn trước.

| ID | Module | Thuật toán | Xong khi | Chặn bởi | V5 |
|---|---|---|---|---|---|
| E8-T1 | `doa` | GCC-PHAT, dò lưới 2°, dải 200 Hz – c/2d (§3.6) | 🔬 sai số góc trung bình và % trong ±10° theo SNR, RT60; ghi riêng vùng 0–30° và 150–180°; so với dải đầy đủ; đủ bốn bước | E4-T4, E7-T2, E7-T3 | V5.2.1 |
| E8-T2 | `gsc` | trễ và cộng, ma trận chặn, NLMS rò có điều khiển thích nghi (§3.7) | cải thiện SIR với một nhiễu có hướng; **phép kiểm DOA sai ±20° và ±45°** ghi mức người nói bị dìm; đủ bốn bước | E8-T1 | V5.2.2 |
| E8-T3 | `bss` | AuxIVA online, IP2 cho 2×2, chiếu ngược (§3.8) | khớp `pyroomacoustics.bss.auxiva` trên cảnh dựng, có đối chứng âm; SIR, SDR theo RT60; đủ bốn bước | E4-T4, E7-T2 | V5.2.3 |
| E8-T4 | chọn luồng ra của `bss` + so ba đường | (a) hướng từ `W⁻¹`, (b) `wake` trên cả hai luồng, (c) theo `vad`; rồi `gsc` / `bss` / trộn trần trên cùng vật liệu | bảng ba cột, thước quyết định là tỉ lệ bắt `wake` và đúng `command` trên tập thu qua board (§3.15); **chốt mặc định Kconfig bằng số**, ghi ADR | E8-T2, E8-T3, E11-T11 | V5.2.4 |

---

## E9 — Dìm nhiễu

| ID | Module | Việc | Xong khi | Chặn bởi | V5 |
|---|---|---|---|---|---|
| E9-T1 | `ns_omlsa` | OM-LSA + IMCRA, hằng số quy đổi theo bước 256, `E₁` tra bảng (§3.9) | dìm nhiễu và phần mất của tiếng nói đo trên bản thu thật; đủ bốn bước | Cửa 0 | V5.3.1 |
| E9-T2 | khe `ns` | `dsp_afe_ns_ops_t` và bản giả | một bản giả cắm từ ngoài chạy trong chuỗi, `dsp_afe` không `REQUIRES` gì thêm | E3-T2 | V5.3.3 |
| E9-T3 | dữ liệu | tiếng Việt sạch + nhiễu §1.2 + nhiễu phòng dùng thu bằng `capture` | bảng nguồn, số giờ, giấy phép ở `DU_LIEU.md`; split có `SPLIT.md` | E11-T1, E5-T12 | V5.3.2 |
| E9-T4 | RNNoise-16k | dựng lại 18–22 dải trên 257 vạch, huấn luyện ở lưới §3.1 | run có đủ `config.resolved.yaml`, `split.lock`; điểm trên tập thử | E9-T3, E4-T2 | V5.3.3 |
| E9-T5 | `ai_engine/src/ns/` | lượng tử int8, GRU esp-dl, hậu xử lý dải có golden, cắm qua khe | `model->test()` đạt; **hơn sàn E9-T1 bằng thước của §3.15**, không hơn thì bỏ và giữ sàn; ghi ADR | E9-T4, E9-T2, E11-T9 | V5.3.3 |
| E9-T6 | bộ lọc cao độ | thử thêm lại bộ lọc cao độ của RNNoise | có lợi bằng số thì giữ, không thì ghi lý do bỏ | E9-T5 | — |
| E9-T7 | 🔬 đo trên board | trọng số và vùng làm việc ở PSRAM (KẾ HOẠCH §6.5), có Wi-Fi chạy | đỉnh một khung có vượt nhịp không, ghi vào `latency.md`; vượt thì ADR, không kéo về RAM nội | E9-T5 | V5.5.9 |

---

## E10 — Khử vọng

Không chặn module nào khác: không có `ref` thì chuỗi chạy với `"MM"`.

| ID | Module | Việc | Xong khi | Chặn bởi | V5 |
|---|---|---|---|---|---|
| E10-T1 | `drv_audio` TX | TX chung BCLK/WS, khe 32 bit × 2, `auto_clear` phát số 0 khi im, bản sao phát căn theo bộ đếm khối chung với RX | TX chạy liên tục 30 phút, bản sao và khung RX lệch một hằng số | E5-T4 | V5.4.1 |
| E10-T2 | 🔬 âm lượng tối đa | quét tần ở nhiều mức, đo méo | mức cao nhất loa còn tuyến tính ghi vào `mic_array.md` và Kconfig | E2-T8 | V5.4.1 |
| E10-T3 | `aec` Python | MDF chồng-lưu, 8 phân đoạn, bước học tự chỉnh, khử vọng dư (§3.5) | khớp SpeexDSP trên cùng đầu vào trong ngưỡng ghi rõ; có đối chứng âm | E6-T1 | V5.4.2 |
| E10-T4 | `aec` C | bản C + golden, phổ vọng dư chuyển cho khe `ns` | 🔬 ERLE **≥ 20 dB** khi chỉ loa nói ở mức E10-T2; đủ bốn bước | E10-T3, E10-T1, E2-T9 | V5.4.2 |
| E10-T5 | AECM đối chứng | chỉ khi E10-T4 vượt ngân sách | bảng so ERLE và chi phí; ADR | E10-T4 | V5.4.2 |
| E10-T6 | hai bên cùng nói | bản thu có nhãn | không phân kỳ; tiếng người gần bị dìm **≤ 3 dB** | E10-T4 | V5.4.3 |

---

## E11 — `lang_vi` và nhận dạng

| ID | Module | Việc | Xong khi | Chặn bởi | V5 |
|---|---|---|---|---|---|
| E11-T1 | | Khảo sát nguồn tiếng Việt (KẾ HOẠCH §1.2) | `DU_LIEU.md`: nguồn, số giờ, số người nói, giấy phép, sha256 | E1-T8 | V5.5.1 |
| E11-T2 | | Phiếu đồng ý, quy trình thu, bảng mã người nói ngoài repo; kiểm điều khoản Nghị định 13/2023 và Luật BVDLCN 2025 | phiếu có bản in; quy trình xoá theo mã người nói chạy được một lượt | — | V5.5.6 |
| E11-T3 | | Chốt đơn vị nhận dạng bằng đo: bốn đường của §3.12 trên cùng mạng nền, cùng dữ liệu công khai | bảng bốn cột, đo trên các cặp lệnh chỉ khác thanh; ADR | E11-T4, E11-T12 | V5.5.2 |
| E11-T4 | `lang_vi` | `normalize` (số, viết tắt, từ mượn), `g2p`, `lexicon` với biến thể Bắc/Nam; Python + C | golden **khớp tuyệt đối** trên mọi âm tiết hợp lệ + bộ thử có nhãn gồm số, từ mượn, tên riêng; đủ bốn bước | E3-T2 | V5.5.3 |
| E11-T5 | | Chọn **một** từ đánh thức 3–4 âm tiết (§3.11) | lý do âm học ghi ADR: pha thanh, tương phản nguyên âm, tần suất trong kho lời nói | E11-T1 | V5.5.5 |
| E11-T6 | | Thu qua board bằng `capture` + `host/session.py`: dương cho `wake`, câu lệnh, âm bản gần âm | ≥ 100 người nói, nam nữ, gần và xa, ít nhất hai phòng; split theo người nói và phòng | E11-T2, E11-T5, E5-T12, E13-T3 | V5.5.6 |
| E11-T7 | | Tiếng tổng hợp để tăng lượng | giấy phép mô hình TTS ghi ở `DU_LIEU.md`; không mẩu nào vào tập thử | E11-T5 | — |
| E11-T8 | đặc trưng | log-mel 40 + chuẩn hoá đi theo model; A/B với log-mel 80 và log-mel + cao độ | bảng trên cặp lệnh chỉ khác thanh; thắng mới dựng `dsp_spec/pitch`; phép kiểm canh thống kê lúc huấn luyện và lúc chạy | E6-T5 | V5.5.4 |
| E11-T9 | `ai_engine` core | nạp ảnh model, kiểm `grid_hash`, cấp vùng làm việc, chạy, đo | ảnh có `grid_hash` sai bị từ chối với `ESP_ERR_INVALID_VERSION` | E5-T5, E3-T2 | — |
| E11-T10 | | Xuất mạng dòng qua ESP-PPQ dùng `StreamingCache` | một TCN nhỏ chạy dòng trên board khớp mô phỏng; không được thì ghi đường lùi | E11-T9 | V5.5.7 |
| E11-T11 | `wake` | TCN giãn nở int8 (§3.11) | 🔬 **Cửa 2**: bắt ≥ 95% ở 1 m, báo nhầm ≤ 1 lần mỗi giờ trên ≥ 24 giờ âm bản; ghi thêm 3 m, SNR 10 và 5 dB | E11-T6, E11-T8, E11-T10 | V5.5.7 |
| E11-T12 | `command` mạng | mạng âm học dòng + CTC trên đơn vị của E11-T3 | run đầy đủ; tỉ lệ lỗi đơn vị trên tập thử | E11-T1, E11-T8 | V5.5.8 |
| E11-T13 | `command` giải | chấm CTC có ràng buộc, max trên biến thể, từ chối theo `δ₁` `δ₂` (§3.12) | 🔬 **Cửa 3**: mỗi lệnh ≥ 90%, từ chối đúng ≥ 95%; **thêm một lệnh chưa có trong dữ liệu huấn luyện chỉ bằng một dòng chữ** và đo nó | E11-T12, E11-T4 | V5.5.8 |
| E11-T14 | `svc_listen` | `nhan_task`, đổi chế độ `NGHE`/`LENH`, `q_cmdset` đổi bảng lệnh giữa hai câu | đổi bộ lệnh qua MQTT trong lúc chạy, không mất khung, lệnh mới dùng được ngay câu sau | E11-T13, E5-T6 | V5.5.8 |
| E11-T15 | | 🔬 Đo `wake` và `command` trên board, trọng số và vùng làm việc ở PSRAM (KẾ HOẠCH §6.5), **có Wi-Fi chạy** | đỉnh một khung có vượt nhịp không, ghi vào `latency.md`; vượt thì ADR, không kéo về RAM nội | E11-T11, E11-T13 | V5.5.9 |

Con số báo nhầm quan trọng hơn con số bắt được: một máy tự bật mỗi mười phút là máy không ai dùng.

---

## E12 — Tiếng nói ra

| ID | Module | Việc | Xong khi | Chặn bởi | V5 |
|---|---|---|---|---|---|
| E12-T1 | | Bảng so ghép mẩu / ghép âm tiết / mạng chưng cất: bộ nhớ, flash, phép tính, chất lượng | bảng đủ ba cột, **có phương án không mạng**; ADR | E11-T4 | V5.6.1 |
| E12-T2 | `svc_speak` | Ghép mẩu: thu câu trả lời và từ số (có phiếu đồng ý người đọc), IMA-ADPCM, phân vùng `voice`, dựng trước rồi phát | nói được mọi câu trong `responses/vi.json` và mọi số 0–9999 | E10-T1, E11-T2 | V5.6.3 |
| E12-T3 | | Nối `lang_vi` vào đường mạng, thêm trường độ và ngôn điệu | **không viết lại `g2p`**; chỉ khi E12-T1 chọn mạng | E12-T1 | V5.6.2 |
| E12-T4 | `ai_engine/src/synth/` | mạng huấn luyện ở **16 kHz**, iSTFT qua `dsp_spec` | 🔬 **Cửa 4**: dưới 1× thời gian thực, vừa bộ nhớ; không đạt thì lui về E12-T2, không nới ngân sách | E12-T3 | V5.6.3 |
| E12-T5 | | Chấm chất lượng | điểm tự động cộng nghe thật, ghi cả hai | E12-T2 hoặc E12-T4 | V5.6.4 |

---

## E13 — Mạng và máy tính nhận

Chạy song song từ lúc `net_mqtt` có ở E5.

| ID | Task | Xong khi | Chặn bởi | V5 |
|---|---|---|---|---|
| E13-T1 | Hoàn thiện bảy schema và `mqtt_topics.yaml` theo §7.3; sinh code cho `host/` | `host` và firmware cùng sinh từ một nguồn, `contracts.yml` xanh | E1-T4 | V5.7.4 |
| E13-T2 | `host/mqtt_rx.py` + `live.py` | xem trực tiếp hướng, cờ tiếng nói, mức, sự kiện; payload sai schema bị ghi lại | E13-T1 | — |
| E13-T3 | `host/stream_rx.py` + `session.py`: phiên thu có nhãn, mã người nói, mã phiếu | một phiên ra đúng khuôn KẾ HOẠCH §4.4.1 ở `raw/device/board_b/<phiên>/`: WAV từng kênh + `session.json` + `gaps.txt`, và một dòng mới trong `manifests/device/board_b.csv` | E13-T1 | V5.5.6 |
| E13-T4 | `host/score.py` gọi `srpipe.metrics` | chấm một phiên đã thu ra bảng giống `make report` | E13-T3, E4-T5 | — |
| ~~E13-T5~~ | **Xong 26/09.** `deploy/`: `sr-emqx` (EMQX 6.3.1 trong Docker, KẾ HOẠCH §4.7), 1883 ra LAN, dashboard `127.0.0.1:18084`, retained xuống đĩa; mật khẩu từ bảng user nội bộ nạp từ `users.csv` (gitignore); `acl.conf` theo `deviceId` + `srhost`; `gen_certs.sh` cho mqtts ở prod; `make broker-up` đòi đủ `.env` và `users.csv` | 13/13 ca kiểm bằng `paho-mqtt` với danh tính board: sai mật khẩu bị từ chối, board này không đọc `down/` và không ghi `up/` của board kia, `srhost` không giả `up/`; retained còn sau `docker restart`. Board thật nối ở E5-T9 | E1-T1 | — |
| E13-T6 | 🔬 Băng thông ở chế độ thường | **dưới 1 KB/s**, không gửi tiếng (§7.3) | E5-T9, E14-T4 | V5.7.4 |
| E13-T7 | Lệnh xuống: `SET_CONFIG`, `SET_STREAM`, `SPEAK`, `CALIBRATE`, `REBOOT` | mỗi lệnh có một phép kiểm từ `host` | E5-T9 | — |
| E13-T8 | Cấp Wi-Fi bằng SoftAP (`network_provisioning`) — tuỳ chọn | board mới nhận Wi-Fi không cần cáp | E5-T8 | — |
| E13-T9 | OTA firmware và model A/B có rollback — tuỳ chọn | ảnh hỏng tự quay về; khung mất lúc OTA được khai báo | E5-T5 | — |
| E13-T10 | Profile `prod`: `mqtts://`, mã hoá NVS, không console, không luồng tiếng | `prod` từ chối URI `mqtt://`; symbol của `net_stream` và `app_console` vắng trong ảnh | E13-T5 | — |
| E13-T11 | **Mốc demo MQTT** (KẾ HOẠCH §8): trên board hiện có, `APP_SPEAKER_ENABLE=n`, đường không gian trộn trần, `ns` sàn — nói từ đánh thức rồi một lệnh, sự kiện lên server, `host/live.py` hiện ra | 🔬 100 lượt liên tiếp không kẹt trạng thái; mỗi lượt có `event` thức và `event` lệnh đúng thứ tự; độ trễ từ lúc nói xong tới lúc `host` hiện ghi thành số; một phiên ghi lại để chiếu | E7-T4, E9-T1, E11-T14, E13-T2, E13-T5 | V5.7.4 |

---

## E14 — Ôm về thành app

Mở khi pha B có đủ module cho một đường chạy trọn: `hpf` → `balance` → không gian → `ns` → `vad` →
`agc` → `wake` → `command` → ghép mẩu.

| ID | Task | Xong khi | Chặn bởi | V5 |
|---|---|---|---|---|
| E14-T1 | Chốt danh sách app và vai: `main`, `bench_afe`, `bench_kws`, `bench_mem`, `soak`, `capture`, `calib`, `parity` | mỗi app một dòng vai trong §4.5.7, không app nào trùng vai | E5-T12 | V5.7.1 |
| E14-T2 | Bảng bộ nhớ theo module, tĩnh và động | tổng khớp file map của bản dựng `bench`; **không ô nào là suy** | E6-T7, E7…E12 | V5.7.2 |
| E14-T3 | Chốt bảng task §5.2 bằng số: watermark + biên, tải từng nhân ở `bench` | §5.2 không còn 🔬 ở cột ngăn xếp | E5-T6 | V5.7.3 |
| E14-T4 | `svc_dialog`: máy trạng thái §5.4, ánh xạ lệnh → câu trả lời | kịch bản thức → lệnh → trả lời → nghe chạy 100 lượt không kẹt trạng thái | E11-T14, E12-T2 | — |
| E14-T5 | 🔬 Chạy dài | **Cửa 5**: liền 30 phút, 0 khung mất ngoài `OTA_RUNNING`, heap không trôi; thêm một lượt 8 giờ trước khi báo cáo | E14-T4 | V5.7.5 |
| E14-T6 | 🔬 Đo chỗ chỉ lộ lúc ghép | điểm cao nhất `q_clean` khi Wi-Fi bận; `synth` hay `command` ở nhân 0 có làm rớt gói MQTT không; `command` đọc trọng số PSRAM làm `sach_task` chậm bao nhiêu | E14-T4 | V5.7.6 |
| E14-T7 | 🔬 Ghi flash trong lúc nghe | ghi NVS liên tục 10 phút, đếm khung mất; chiều sâu DMA 128 ms có đủ không | E14-T4 | — |
| E14-T8 | 🔬 Tầm thu | tỉ lệ bắt `wake` và đúng `command` ở 0,5 / 1 / 2 / 3 m; giới hạn ghi thành một con số (TỔNG QUAN §9.1) | E14-T4 | — |
| E14-T9 | `make report` dựng lại mọi số trong báo cáo bằng một lệnh | chạy trên máy sạch từ repo + dữ liệu đã tải ra đúng các bảng ở `docs/measurements/` | E14-T2 | — |

---

## Bảng cửa

| Cửa | Đóng khi | Không đạt thì |
|---|---|---|
| 0 | E2-T4, E2-T6, E2-T7, E3-T5, E5-T11, E6-T4, E6-T5 | dừng hẳn |
| 1 | E8-T4, E9-T5 | giữ trộn trần, giữ sàn, ghi đúng như vậy |
| 2 | E11-T11 | thu thêm dữ liệu; vẫn không đạt thì đổi từ đánh thức |
| 3 | E11-T13 | giảm số lệnh, không nới mô hình |
| 4 | E12-T4 | lui về ghép mẩu E12-T2 |
| 5 | E14-T5 | bỏ khối đắt nhất theo `budget.md`, đo lại |

---

## Bảng song song

| Epic | Chạy được cùng lúc với |
|---|---|
| E1 Nền repo | — |
| E2 Phần cứng | E3, E4, E5, E6, E11-T1, E11-T2 |
| E3 Hợp đồng | E2, E4 |
| E4 `ml/` nền | E2, E3, E5 |
| E5 Firmware nền | E2, E4, E6, E13 |
| E6 `dsp_spec` | E2, E5 |
| E7 afe một kênh | E8, E9, E11 |
| E8 afe không gian | E7, E9, E10, E11 |
| E9 Dìm nhiễu | E7, E8, E10, E11 |
| E10 Khử vọng | E7, E8, E9, E11, E12 |
| E11 Nhận dạng | E7–E10, E12, E13 |
| E12 Tiếng nói ra | E10, E11, E13 |
| E13 Mạng, máy tính nhận | mọi epic từ E5 |
| E14 Ôm về | E13 |

Muốn rút ngắn thì hoãn E10 và phần mạng của E12: chuỗi vẫn chạy trọn với `"MM"` và ghép mẩu, đổi lại
máy không dùng được khi vừa phát vừa nghe.
