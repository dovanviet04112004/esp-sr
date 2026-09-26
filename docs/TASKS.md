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
| E1-T7 | Gitea Actions: đăng ký `act_runner` có Docker, bốn workflow `contracts` `ml` `firmware` `host`; dựng firmware bằng ảnh `espressif/idf:v6.0.2` | push là CI chạy; `contracts.yml` đỏ khi code sinh ra lệch | E1-T4, E1-T6 | — |
| E1-T8 | Khung tài liệu: `docs/DU_LIEU.md`, `docs/measurements/{budget,latency,ram,parity,mic_array}.md` trống có tiêu đề cột, mẫu ADR ở `docs/adr/` | file tồn tại, bảng có cột đúng | E1-T1 | — |
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
| E2-T6 | Hiệu chuẩn `balance`: loa ngoài chính diện 1 m, ồn trắng 30 s, thu bằng `capture` → hệ số phức mỗi vạch tính bằng `srpipe` → ghi NVS `calib/bal` qua `test_apps/calib`. Không cần module C của E7-T2 | chênh biên độ sau bù **dưới 1 dB** toàn băng 50 Hz – 8 kHz; chênh pha sau bù ghi thành số ở hai nhiệt độ phòng | E2-T4, E6-T1 | V5.0.2 |
| E2-T7 | Chốt quy ước kênh và dấu (§2.3): micro nào là `ch0`, dấu của trễ, góc 0° ở đâu, `ref` là kênh thứ ba | vỗ tay phía `ch1` cho `τ` dương trên bản thu; §2.3 và `array.yaml` khớp | E2-T3 | V5.0.3 |
| E2-T8 | Lắp MAX98357A + loa 4 Ω 3 W vào GPIO 17/18 (§2.2), loa trên đường trung trực của dàn. **Lắp khi tới E10**; mốc demo không cần | phát được một tông 1 kHz qua `drv_audio` TX với `APP_SPEAKER_ENABLE=y` | E10-T1 | V5.4.1 |
| E2-T9 | 🔬 Đo trễ khối TX → RX bằng tiếng quét tần | trễ ghi NVS `calib/aec_delay`, đo lại năm lần lệch không quá một mẫu | E2-T8 | V5.4.1 |
| E2-T10 | Khung `hardware/`: `README.md`, `datasheets/INDEX.md` (tên file ↔ URL ↔ sha256), ảnh board, luật ignore PDF | `git status` sạch sau khi thả PDF vào | E1-T1 | — |

---

## E3 — Hợp đồng và lưới thời gian

Đóng trước khi viết module đầu tiên (TỔNG QUAN V5.0.4). Sau E3-T5 mọi thay đổi đi qua CLAUDE.md §1.2.

| ID | Task | Xong khi | Chặn bởi | V5 |
|---|---|---|---|---|
| E3-T1 | `contracts/grid.yaml` (§3.1), `array.yaml` (§2.3); ADR-0001 lưới 16 kHz, bước 256, FFT 512 | `gen_grid.h` và `grid.py` sinh ra, có `GEN_GRID_HASH` | E1-T4 | — |
| E3-T2 | Header công khai của `dsp_spec`, `dsp_afe` (`feed`/`fetch`, `"MM"`/`"MMR"`, `dsp_afe_frame_t`, `dsp_afe_ns_ops_t`), `lang_vi`, `ai_engine`, `drv_audio` | Doxygen đủ theo CLAUDE.md §2.7, **mọi hàm có `@ctx`**; `check_comments` sạch | E3-T1 | V5.0.4 |
| E3-T3 | Luật bộ nhớ: `_workspace_bytes` + vùng `hot`/`cold` cho mọi module thuần | bảng `sizeof` và byte vùng làm việc từng module ở `ram.md` (ước, đánh 🔬) | E3-T2 | V5.0.4 |
| E3-T4 | Bản giả của mọi hàm trong E3-T2 chạy được trong app khung | E5-T11 dựng và chạy với bản giả, không sửa header | E3-T2, E5-T6 | V5.0.4 |
| E3-T5 | Đóng băng: tag `contract-v1` | tag có trên Gitea | E3-T4 | V5.0.4 |

---

## E4 — `ml/` nền Python

| ID | Task | Xong khi | Chặn bởi | V5 |
|---|---|---|---|---|
| E4-T1 | `ml/pyproject.toml` + `uv.lock`; `configs/common/{paths,hardware}.yaml`; `.env.example` | `uv sync` sạch trên máy mới | E1-T1 | — |
| E4-T2 | `srpipe/core/`: config, `run_dir`, seed, `audio_io` | một run giả tạo đủ `config.resolved.yaml`, `split.lock`, `env.txt` | E4-T1 | — |
| E4-T3 | `srpipe/golden/gold.py` + đọc `.gold` bằng C trong `test_apps/parity` | ghi Python → đọc C → so, khớp từng byte trên ba dtype | E4-T1, E5-T5 | — |
| E4-T4 | `srpipe/scenes/`: dựng cảnh có nhãn bằng `pyroomacoustics` — phòng, RT60, hướng người nói và nhiễu, SNR, dàn micro từ `array.yaml` | một lệnh sinh bộ cảnh chuẩn có manifest và sha256; nhãn hướng khớp hình học | E4-T1, E3-T1 | V5.2 |
| E4-T5 | `srpipe/metrics/`: SI-SDR, STOI, PESQ (giấy phép bản cài ghi rõ), ERLE, lỗi góc, DET | mỗi thước có một phép kiểm với giá trị biết trước | E4-T1 | — |
| E4-T6 | Đích `make golden`, `make measure`, `make report` khung | `make report` chạy trên repo rỗng không lỗi, ra bảng trống | E4-T2 | — |

---

## E5 — Firmware nền và app khung

| ID | Task | Xong khi | Chặn bởi | V5 |
|---|---|---|---|---|
| E5-T1 | `firmware/`: CMake, `PROJECT_VER`, bốn profile, `sdkconfig.defaults.esp32s3` theo §4.5.8 (console UART, `SECONDARY_NONE`, ghim lwIP/esp_timer/mqtt về nhân 0), `partitions.csv` §6.1 | `make fw-dev` và `fw-bench` dựng sạch; `idf.py size` in biên slot | E1-T1 | — |
| E5-T2 | `common`: `app_err.h`, `app_events.h`, `gen_*.h` | `check_purity` sạch | E1-T4 | — |
| E5-T3 | `bsp_board`: `app_config.h` theo §2.2 + chặn biên dịch khi console USB bật trong lúc BCLK ở GPIO 19 | bật `ESP_CONSOLE_USB_SERIAL_JTAG` thì dịch dừng với thông báo rõ | E5-T1 | — |
| E5-T4 | `drv_audio` RX: I2S chuẩn, khe 32 bit × 2, 24 → 16 theo `pcm_shift`, đếm khung, callback tràn DMA; DMA 8 × 256 mẫu | 10 s thu hai kênh sống, `seq` liên tục, 0 tràn | E5-T3 | V5.0.9 |
| E5-T5 | `sys_storage`: NVS §6.2 (chuỗi rỗng là vắng, bộ gieo có số hiệu), LittleFS, mmap ảnh model, `storage_format.h` có `static_assert` | test app: ghi, đọc, xoá từng namespace; mất điện giữa lúc ghi `set.json` không hỏng file | E5-T1 | — |
| E5-T6 | `main`: `app_boot`, `app_tasks` (bảng tĩnh §5.2, `xTaskCreateStaticPinnedToCore`), `app_wiring` (§5.3), watchdog mọi task; `APP_SPEAKER_ENABLE` trong `Kconfig.projbuild`, mặc định n (§2.1) | boot in bảng task; `bench_mem` in watermark từng task; với `APP_SPEAKER_ENABLE=n` không có `noi_task` và TX không mở | E5-T2 | V5.7.3 |
| E5-T7 | `app_console`: `wifi set`, `nvs get/set/del`, `calib run` | chỉ biên dịch ở `dev`/`bench`; `prod` không có symbol nào của nó | E5-T5 | — |
| E5-T8 | `net_wifi` + `net_task`: chờ link tới khi có, lùi dần 1 → 30 s | rút router 5 phút rồi cắm lại, máy tự về không khởi động lại | E5-T6 | — |
| E5-T9 | `net_mqtt`: URI từ NVS lùi về Kconfig, `status` retained + LWT, `heartbeat` tối thiểu | `mosquitto_sub` thấy `online`, rút điện thấy `offline`; payload dựng qua `cJSON_InitHooks` trên vùng nhớ cấp lúc boot, không `malloc` sau boot (FREERTOS.md §14 P1) | E5-T8, E13-T5 | — |
| E5-T10 | `net_stream` + `svc_report`/`luong_task`: khách TCP, khung theo `gen_stream.h`, bỏ cả khung khi nghẽn | `host/stream_rx.py` ghi WAV; chặn mạng 5 s thì thấy hở `seq`, board không chậm khung nào | E5-T9, E13-T3 | — |
| E5-T11 | **App khung rỗng** = `main` với mọi module `dsp_afe` tắt: thu → STFT → iSTFT → gửi ra | 🔬 chạy liền **10 phút 0 khung mất**; sai số dựng lại ghi bằng số; RAM nội còn lại ghi vào `ram.md` | E3-T4, E5-T4, E5-T10, E6-T4 | V5.0.9 |
| E5-T12 | `test_apps/capture`: chỉ thu, `mode` 3, không có `dsp_afe` | một phiên 30 phút về `host` không hở `seq` | E5-T10 | V5.7.1 |
| E5-T13 | `test_apps/bench_afe`, `bench_mem` + `tools/budget.py` → `budget.md` | **một lệnh** in bảng RAM và µs theo module, để trống chờ điền | E5-T6 | V5.0.10 |
| E5-T14 | `drv_led`: sáng khi luồng tiếng mở | mở `SET_STREAM` thì LED sáng, tắt thì tắt, tự tắt sau 10 phút | E5-T10 | — |

---

## E6 — `dsp_spec`

| ID | Task | Xong khi | Chặn bởi | V5 |
|---|---|---|---|---|
| E6-T1 | Bản Python `srpipe/dsp/spec/`: fft, window, stft, mel | phân tích rồi tổng hợp dựng lại sóng gốc, sai số ghi bằng số | E4-T2 | V5.0.5 |
| E6-T2 | Component `dsp_spec`: `idf_component.yml` ghim bản, `Kconfig` chọn `dl_fft` hay `esp-dsp`, `README.md`. **Không `CHANGELOG`** (KẾ HOẠCH §0.2) | dựng được trong app khung | E3-T2 | V5.0.5 |
| E6-T3 | 🔬 Đo `dl_fft` so với `esp-dsp` trên board, float32 và int16, 256 / 512 / 1024 điểm | bảng thời gian và bộ nhớ ở `latency.md`; **chọn một bản, ghi lý do bằng số** trong ADR | E6-T2 | V5.0.6 |
| E6-T4 | Bộ vàng STFT / iSTFT, có đối chứng âm | khớp Python trong `tolerance.yaml`; ca lệch một mẫu làm phép kiểm đỏ | E6-T1, E6-T2, E4-T3 | V5.0.7 |
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
| E9-T7 | 🔬 đo trên board | trọng số ở PSRAM so với RAM nội, có Wi-Fi chạy | đỉnh một khung có vượt nhịp không; quyết định chỗ đặt ghi vào `ram.md` | E9-T5 | V5.5.9 |

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
| E11-T15 | | 🔬 Đo `wake` và `command` trên board, trọng số ở PSRAM, **có Wi-Fi chạy** | đỉnh một khung có vượt nhịp không; quyết định chỗ đặt ghi vào `ram.md` | E11-T11, E11-T13 | V5.5.9 |

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
| E13-T3 | `host/stream_rx.py` + `session.py`: phiên thu có nhãn, mã người nói, mã phiếu | một phiên ra WAV từng kênh + json nhãn + danh sách hở `seq` | E13-T1 | V5.5.6 |
| E13-T4 | `host/score.py` gọi `srpipe.metrics` | chấm một phiên đã thu ra bảng giống `make report` | E13-T3, E4-T5 | — |
| E13-T5 | `deploy/`: Mosquitto, ACL theo `deviceId`, `gen_certs.sh` | `docker compose up` rồi board nối được; board này không đọc được topic của board khác | E1-T1 | — |
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
