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
| ~~E2-T3~~ | **Xong 26/09.** Đo khoảng cách hai micro, giữa tâm hai lỗ âm: **~45 mm** bằng thước (chủ dự án đo), trong dải 4–6,5 cm nên giữ dàn. Trễ lớn nhất ±2,10 mẫu, `c/2d` = 3,81 kHz | số ghi vào `contracts/array.yaml` và `mic_array.md`; E2-T4 kiểm lại bằng trễ vỗ tay ở đầu dàn | — | V5.0.1 |
| ~~E2-T4~~ | **Xong 26/09.** 🔬 Đo bốn chỉ tiêu của dàn: khoảng cách, chênh độ nhạy, chênh pha, SNR; cộng nền ồn khi Wi-Fi phát và khi tắt. Đo bằng `make session` + `srhost.score` (`srpipe.metrics.mic_pair`), quy trình ở `mic_array.md` §0 | bảng năm dòng ở `mic_array.md`, không dòng nào trống: 44,4 mm đạt; chênh độ nhạy ~11 dB **trượt**; chênh pha ≤ 4,7° ở 200–1600 Hz đạt, dải 1,6–6,4 kHz đổi theo hướng đặt loa nên là của phòng; SNR ≥ 54,4 / 49,3 dB (chặn dưới); Wi-Fi không đổi nền quá 0,5 dB. Hộp đã đóng, không sửa được lỗ `ch0`: giữ phần cứng, `balance` bù mức và pha tĩnh (E2-T6) | E2-T3, E5-T12 | V5.0.1 |
| ~~E2-T5~~ | **Xong 28/09.** 🔬 Chọn `pcm_shift` (24 → 16 bit): **13**, ghi NVS `calib/pcm_shift` board B, đọc lại khớp; `configs/scenes/device.yaml` mô phỏng cùng giá trị. Tiếng to 10 cm không cắt (đỉnh 25 664, dư 2,1 dB), nền phòng yên −63/−59 dBFS, trên bước lượng tử 27–31 dB. Ở dịch 14 chuỗi mất tiếng nhỏ 1 m (`vad` 29 % → 3,8 %) và tiếng 3 m (47 % → 11 %); `srhost.score --shift` dựng mọi dịch từ cùng bản thu (`mic_array.md` §2) | giá trị ghi NVS `calib/pcm_shift` và `mic_array.md` kèm hai phép đo | E5-T4 | — |
| ~~E2-T6~~ | **Xong 26/09, chủ dự án chốt đóng.** Hiệu chuẩn `balance` theo KẾ HOẠCH §3.4: ồn trắng chính diện ở ba chỗ đặt loa (1 m trước, 20 cm trước, 20 cm sau), `srpipe.dsp.afe.balance` ước, `srhost.calib` kiểm chéo và ghi qua `test_apps/calib` vào NVS `calib/bal`, CRC32 đọc lại khớp | chênh biên độ sau bù dưới 1 dB: **đạt 50 Hz – 1,6 kHz** (≤ 0,47 dB, pha ≤ 3,4°, kiểm trên chỗ đặt loa không dùng để ước); **1,6–8 kHz không kiểm được** trong phòng này (các chỗ đặt loa tự lệch 1–3,6 dB); đo ở nhiệt độ thứ hai **không làm**, chủ dự án chấp nhận — chạy lại `make calib-estimate` khi có dịp (`mic_array.md` §3) | E2-T4, E6-T1 | V5.0.2 |
| ~~E2-T7~~ | **Xong 26/09.** Chốt quy ước kênh và dấu (§2.3): micro nào là `ch0`, dấu của trễ, góc 0° ở đâu, `ref` là kênh thứ ba. Board B: lỗ A của hộp là `ch0`, lỗ B là `ch1`; nhận micro bằng dấu của `τ`, không bằng mức (`mic_array.md` §0) | vỗ tay phía `ch1` cho `τ` dương trên bản thu: +1,89 mẫu trung vị trên 10 cú vỗ (`20260926_home_008`), phía `ch0` −2,25 (`…_007`); §2.3 và `array.yaml` khớp | E2-T3 | V5.0.3 |
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
| ~~E3-T3~~ | **Xong 26/09.** Luật bộ nhớ: `_workspace_bytes` + vùng `hot`/`cold` cho mọi module thuần. Bị chạm mỗi khung thì `hot`, mặt tiền đặt cả vùng của module; `aec` mượn `fft` 512 điểm của chuỗi thay vì tự dựng bộ thứ hai (§3.5) | bảng 18 dòng ở `ram.md` §1: `dsp_spec` tính đúng từ code, `dsp_afe` ước 🔬; cộng theo chuỗi: mốc demo ~79,5 KB RAM nội, `bss` ~106 KB, `aec` + `bss` ~169 KB — không vừa 133 KB còn lại sau Wi-Fi nếu không dùng đường lùi §6.5 | E3-T2 | V5.0.4 |
| ~~E3-T4~~ | **Xong 26/09.** Bản giả của mọi hàm trong E3-T2 chạy được trong app khung. Chín module `dsp_afe/src/*.c` là bản giả trung tính sau Kconfig (mặc định tắt); `lang_vi` 3 file; `ai_engine` 5 file C++ sau mặt tiền C. Mặt tiền `dsp_afe.c` chạy thật STFT → trộn trần → iSTFT, soi gương `srpipe.dsp.afe.chain` (viết trước) với bộ vàng `chain/`; `svc_front` chạy nó trong `sach_task`, `ai_engine_load` gọi lúc boot | host: `dsp_afe` 9/9 mọi module tắt, 20/20 bật đủ chín bản giả; `lang_vi` 8/8; `ai_engine` 5/5. Bộ vàng `chain` trên board B: `pcm` ≤ 1 LSB ở 77,9 dB, ca âm đỏ. `main` trên board B qua Wi-Fi + MQTT: 0 khung mất, 0 khung sạch bỏ; firmware dựng cả khi bật đủ chín module. Không chữ ký nào đổi; doc `dsp_afe.h`, `ai_engine.h` bổ sung mã trả về | E3-T2, E5-T6 | V5.0.4 |
| ~~E3-T5~~ | **Xong 26/09.** Đóng băng: tag `contract-v1` có chú thích trên `4dba8af`, khoá 17 header công khai: `dsp_spec` (fft, window, stft, mel), `dsp_afe` + chín module, `lang_vi`, `ai_engine`, `drv_audio`. Từ đây đổi chữ ký theo CLAUDE.md §1.2, commit có `!` và `BREAKING CHANGE` | tag `d014125` có trên Gitea và GitHub; CI xanh cả bốn workflow ở `4dba8af` | E3-T4 | V5.0.4 |

---

## E4 — `ml/` nền Python

| ID | Task | Xong khi | Chặn bởi | V5 |
|---|---|---|---|---|
| ~~E4-T1~~ | **Xong 26/09.** `ml/pyproject.toml` + `uv.lock` (nền: numpy, scipy, soundfile, pyyaml, pydantic, pyroomacoustics; PyTorch ở nhóm `train`), `configs/common/{paths,hardware}.yaml`, `.env.example` với `SRPIPE_DATA_ROOT`, `ml/data/README.md` | `uv sync` sạch, `import srpipe` và `srpipe.generated.grid` chạy; `ruff` sạch | E1-T1 | — |
| ~~E4-T2~~ | **Xong 26/09.** `srpipe/core/`: `config` (gộp YAML, ghi đè dạng `a.b=giá trị`, `data_paths` với `SRPIPE_DATA_ROOT`), `run_dir` (thư mục run có `config.resolved.yaml`, `split.lock`, `env.txt`), `seed`, `audio_io` (int16 chia 32768 như firmware), `logger` | `ml/tests/test_core.py` 11/11 qua; ruff sạch | E4-T1 | — |
| ~~E4-T3~~ | **Xong 26/09.** `srpipe/golden/gold.py` giữ khuôn `.gold` ở một chỗ; bộ đọc C không cấp phát `firmware/test_apps/parity/main/gold_read.h` | Python ghi → C (gcc `-Werror`) đọc → khớp từng byte trên năm dtype `f32 i8 i32 u8 i16`, kể cả tensor vô hướng; ca hỏng magic, dư byte, tên dài, dtype lạ bị từ chối. Lượt chạy trên board đi cùng `test_apps/parity` ở E6-T4 | E4-T1 | — |
| ~~E4-T4~~ | **Xong 27/09.** `srpipe/scenes/room.py` + `configs/scenes/standard.yaml`: phòng hộp, dàn hai micro của `array.yaml` trên bàn ở hướng ngẫu nhiên, người nói VIVOS `test` và nguồn nhiễu (DEMAND hoặc người nói thứ hai) lệch ≥ 60°; ảnh từng nguồn và tiếng sạch giữ riêng; RT60 thiết kế được chỉnh tới khi RT60 đo trên RIR lệch mục tiêu ≤ 10% | `ml/scripts/11_scenes.sh` dựng 216 cảnh 8 s trong 54 s vào `interim/scenes/standard/` kèm manifest sha256, dựng lại với số tiến trình khác ra manifest giống hệt; nhãn góc là góc 3D tới trục `ch0`→`ch1`, test kiểm GCC-PHAT đo đúng độ trễ nhãn tới 1/32 mẫu và góc trường xa khớp nhãn trong 1°; RT60 đo 0,20 / 0,38 / 0,57 s cho mục tiêu 0,2 / 0,4 / 0,6; 24 cảnh ở 0–30°, 25 ở 150–180° | E4-T1, E3-T1 | V5.2 |
| ~~E4-T5~~ | **Xong 26/09.** `srpipe/metrics/`: SI-SDR và mức cải thiện, STOI qua `pystoi` (MIT), PESQ băng rộng P.862.2 qua `pesq` (vỏ MIT; mã C của P.862 thuộc Psytechnics và OPTICOM, chỉ dùng để đánh giá nội bộ, ghi ở docstring `metrics/pesq.py`), ERLE toàn phiên và theo 100 ms, lỗi góc (có lọc theo vùng góc thật), đường DET và tỉ lệ bỏ sót ở một mức báo nhầm mỗi giờ | mỗi thước có một phép kiểm với giá trị biết trước: 12 phép thử ở `ml/tests/test_metrics.py`, ví dụ SI-SDR 20 dB với nhiễu trực giao, PESQ 4,644 và STOI 1 khi hai tín hiệu giống hệt, ERLE 20 dB khi phần dư bằng 1/10, DET đếm tay | E4-T1 | — |
| E4-T6 | Đích `make golden`, `make measure`, `make report` khung | `make report` chạy trên repo rỗng không lỗi, ra bảng trống | E4-T2 | — |
| ~~E4-T7~~ | **Xong 26/09.** Cây `ml/data/` theo KẾ HOẠCH §4.4.1: `README.md`, `manifests/{speech,noise,rir,device}`, `splits/{ns,wake,command,device}` trong repo, `board_b.csv` có sẵn dòng tiêu đề; `paths.yaml` có `manifests` và `cache`; `srpipe/core/splits.py` + `tests/test_splits.py` | 26/26 pytest qua: năm luật §1.3 mỗi luật một ca đối chứng âm đỏ đúng chỗ, dòng thiếu cột bị từ chối; chưa có split nào thì phép kiểm split thật vẫn xanh | E4-T2 | — |
| E4-T8 | `srpipe/scenes/device.py`, đường mô phỏng board (KẾ HOẠCH §1.2): phòng `pyroomacoustics` hoặc RIR thật lên dàn `array.yaml`, chênh hai micro theo `calib/bal`, nền ồn micro, `pcm_shift`, rồi `dsp.afe.chain` với đúng module sản phẩm và log-mel. **Phần máy tính xong 27/09**: phiên 8 mẩu liên tiếp trong một phòng của kho 512 phòng, mức nói theo dB SPL ở 1 m qua độ nhạy datasheet, `ch0` nghe `ch1` qua `calib/bal`, nền ồn −90 dBFS(A), dịch phải rồi bão hoà như `drv_audio`; 760 câu VIVOS test ra `processed/command/vivos_test` trong 2,9 phút (`DU_LIEU.md` §3). Mô phỏng cho thấy chuỗi thiếu đích `agc` 22 dB ở trung vị (`afe/agc.md` §4). **Còn** phần 🔬 đối chiếu với bản phát loa | một lệnh biến một danh sách tiếng sạch thành đặc trưng có manifest và sha256; 🔬 đối chiếu với board: cùng câu phát qua loa ở 1 m, bản mô phỏng lệch bản thu bao nhiêu dB về phổ dài hạn, mức vào `agc` và nền ồn, ghi ở `mic_array.md` | E4-T4, E7-T5 | — |

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
| ~~E5-T10~~ | **Xong 26/09.** `net_stream` + `svc_report`/`luong_task`: khách TCP, khung theo `gen_stream.h`, bỏ cả khung khi nghẽn. `sb_stream` 64 KB ở PSRAM, `luong_task` nhân 0 ưu tiên 3, nối lại mỗi giây, tự tắt sau 600 s; mở bằng console `stream <mode> [s]` tới khi có `SET_STREAM`; `prod` không còn symbol nào | `host/stream_rx.py` ghi WAV; chặn máy nhận 30 s thì `gaps.txt` liệt kê 566 khung hở khớp `streamDropped` 564 của board, `framesDropped` và `dmaOverflows` bằng 0 (`latency.md` §4). Chặn 5 s ở phía máy nhận bị proxy Docker đệm hết nên không hở; chặn 5 s ở Wi-Fi của board làm cùng E5-T8 | E5-T9, E13-T3 | — |
| ~~E5-T11~~ | **Xong 26/09.** **App khung rỗng** = `main` với mọi module `dsp_afe` tắt: thu → STFT → iSTFT → gửi ra. `sb_stream` 512 KB ở PSRAM sau hai lượt 64 KB mất 74 và 150 khung vì đường mạng khựng; chấm bằng `srhost.score` | 🔬 chạy liền **10 phút 0 khung mất**: phiên `20260926_home_002`, 37 500 / 37 500 khung, 0 chỗ hở, mọi bộ đếm mất của 22 `heartbeat` bằng 0; dựng lại lệch lớn nhất 1 LSB, 0 mẫu vượt ngưỡng (`parity.md`); RAM nội còn 83 659 B, thấp nhất 64 791 B (`ram.md` §3) | E3-T4, E5-T4, E5-T10, E6-T4 | V5.0.9 |
| ~~E5-T12~~ | **Xong 26/09.** `test_apps/capture`: chỉ thu, `mode` 2 (board chưa có loa), không có `dsp_afe`, xả micro từ lúc boot. `make capture-flash` nạp app, `make session` mở máy nhận rồi mới thả board khỏi bootloader nên phiên bắt đầu từ `seq` 0 của một lần boot | một phiên 30 phút về `host` không hở `seq`: `20260926_home_001`, 1800,0 s, 0 chỗ hở, `dma overflows` 0 và `stream dropped` 0 suốt phiên, RAM nội thấp nhất 179 827 B; `srhost.score` in mức từng kênh | E5-T10 | V5.7.1 |
| ~~E5-T13~~ | **Xong 26/09.** `test_apps/bench_afe` + `tools/budget.py` → `budget.md`: app lấy cờ dịch từ `sdkconfig.bench`, đo µs trung bình và đỉnh, vùng làm việc và phần tự cấp của từng khối trên đúng nhân của nó; CSV ở `docs/measurements/bench/`. `bench_mem` (watermark ngăn xếp, heap đỉnh của các task) cần app đầy đủ task nên làm cùng E14-T3 | **một lệnh** in bảng RAM và µs theo module: `make bench-board` đo trên board rồi dựng lại `budget.md`; `make measure` dựng từ CSV đã commit, ra bảng trống khi chưa có CSV | E5-T6 | V5.0.10 |
| E5-T14 | `drv_led`: sáng khi luồng tiếng mở | mở `SET_STREAM` thì LED sáng, tắt thì tắt, tự tắt sau 10 phút | E5-T10 | — |
| ~~E5-T15~~ | **Xong 27/09.** Bộ vàng vượt phân vùng `storage`: app parity có bảng phân vùng riêng, `storage` 8 MB, `nvs` giữ nguyên chỗ để hiệu chuẩn trên board còn nguyên (KẾ HOẠCH §4.3); bỏ hai chỗ cắt vì thiếu chỗ (ca `vad` 160 bước, `agc` đủ mọi mẫu ra); kết quả từ board đi qua `test_report`, mỗi dòng có số thứ tự và CRC32, máy tính xin lại dòng mất (§4.5.7) | bộ vàng 25% phân vùng mới; parity hai bản dựng và bench qua trên board B; một lượt thật mất ba dòng trên cầu CH340 và tự xin lại được, không phải chạy lại (`parity.md`) | E7-T3 | — |

---

## E6 — `dsp_spec`

| ID | Task | Xong khi | Chặn bởi | V5 |
|---|---|---|---|---|
| ~~E6-T1~~ | **Xong 26/09.** Bản Python `srpipe/dsp/spec/`: `window` (căn Hann tuần hoàn dạng sin), `fft` (thuận không chia, nghịch 1/n, float32), `stft` (chạy dòng, một bước vào một phổ ra), `mel` (Slaney, tam giác chuẩn hoá diện tích, log tự nhiên có sàn, MFCC DCT-II trực chuẩn) — soi gương bốn header E3-T2 | dựng lại bốn tín hiệu trễ đúng một bước, **138,6–139,2 dB**, sai số lớn nhất 2,4e-7 (`parity.md`); 46/46 pytest | E4-T2 | V5.0.5 |
| ~~E6-T2~~ | **Xong 26/09.** Component `dsp_spec`: `fft` bọc `dl_fft` (ghim `==0.7.0`), `window`, `stft` chạy dòng, `mel` Slaney lưu thưa + MFCC; người gọi cấp mọi vùng làm việc, trừ bảng `dl_fft` (ngoại lệ luật 7 §4.5.3); `README.md`, không `CHANGELOG`. Lúc đầu có cả backend `esp-dsp` qua Kconfig, bỏ sau E6-T3 | firmware chính dựng được với `dsp_spec`; `test_apps/unit` 8/8 trên board B: FFT khớp DFT `double` 1,2e-7, dựng lại STFT 136,3 dB | E3-T2 | V5.0.5 |
| ~~E6-T3~~ | **Xong 26/09.** 🔬 Đo `dl_fft` so với `esp-dsp` trên board, float32 và int16, 256 / 512 / 1024 điểm | bảng ở `latency.md` §1: 512 điểm thuận/nghịch 118/136 µs so với 149/161 µs, một khung chuỗi STFT **451 µs (2,8% một nhân)**; **chọn `dl_fft`** ở ADR-0002 (nhanh hơn 87 µs mỗi khung, dựng lại hơn 16 dB, có sẵn FFT nghịch thực; tốn thêm ~2,2 KB RAM nội) | E6-T2 | V5.0.6 |
| ~~E6-T4~~ | **Xong 26/09.** Bộ vàng STFT / iSTFT, có đối chứng âm: `srpipe.dsp.emit_golden` sinh 4 ca 16 bước + `case_neg_000` lệch một mẫu vào `contracts/golden/stft/`; app `test_apps/parity` nướng chúng vào `/lfs/golden`, `pytest_parity.py` phán theo `tolerance.yaml` | C khớp Python **137,2 dB** trên phổ, **137,4 dB** trên tín hiệu dựng lại (ngưỡng 115 dB); ca lệch một mẫu ra −3,1 dB và bị bắt đỏ; chạy qua pytest-embedded: 1 passed | E6-T1, E6-T2, E4-T3 | V5.0.7 |
| ~~E6-T5~~ | **Xong 26/09.** Bộ vàng mel, có đối chứng âm: ba ca mang cấu hình riêng (40/80/24 dải, 13/20/24 MFCC) + `case_neg_000` lệch một dải; `parity_mel` trong app `parity` | log-mel khớp **139,9 dB**, MFCC **134,8 dB** (ngưỡng 115/110 dB); ca lệch một dải ra 3,0 dB và bị bắt đỏ; pytest-embedded 3 lần liền: passed | E6-T4 | V5.0.8 |
| ~~E6-T6~~ | **Xong 26/09.** Đường kiểm trên máy tính chốt là **CMake thường + lớp đệm** (`test_apps/host/shim/`); target `linux` của IDF bị loại vì đòi `libbsd-dev` và kéo cổng FreeRTOS. Bản host dựng `dsp_spec` + `dl_fft` bản C thuần + bộ so của `test_apps/parity` | workflow `firmware` trên GitHub (run 36225698136): 9/9 phép kiểm, 9 ca vàng đúng `tolerance.yaml`, hai ca âm đỏ | E6-T2 | V5.0.5 |
| ~~E6-T7~~ | **Xong 26/09.** 🔬 Chi phí từng hàm vào `budget.md` | µs trung bình và đỉnh, RAM tĩnh và vùng làm việc: STFT hai kênh 286,3 µs, iSTFT 164,5 µs, log-mel 40 dải 62,8 µs, bảng `dl_fft` 6 244 B tự cấp một lần; khớp số E6-T3 | E6-T4, E5-T13 | V5.0.10 |

> **Cửa 0** đóng khi E2-T4, E2-T6, E2-T7, E3-T5, E5-T11, E6-T4, E6-T5 xong. Không đóng thì dừng.
> **Đã đóng 26/09.** Chênh độ nhạy ~11 dB của board B (E2-T4) và dải 1,6–8 kHz của E2-T6 là giới hạn đã biết, chủ dự án chấp nhận; `balance` bù phần mức.

---

## E7 — `dsp_afe` tầng một kênh

Bốn module không cần mô hình, không cần dữ liệu. Làm trước vì rẻ và lấp đầy `budget.md` sớm.

| ID | Module | Thuật toán (KẾ HOẠCH) | Xong khi | Chặn bởi | V5 |
|---|---|---|---|---|---|
| ~~E7-T1~~ | `hpf` | **Xong 26/09.** Biquad Butterworth 80 Hz (`contracts/afe.yaml`), dạng II chuyển vị viết tay (ADR-0004; kernel `esp-dsp` bị loại vì không nhanh hơn mà kém chính xác 10–20 dB) | độ lệch một chiều sau lọc: −0,5 LSB về 0, −20 dBFS còn −102,4 dBFS (giảm 82 dB); đủ bốn bước: Python soi gương, bộ vàng có đối chứng âm, C khớp từng bit trên máy tính và trên board ở `-O2`, `dsp_afe` dựng không gộp nhân-cộng (`make parity-board`, ADR-0006), 41,1 µs hai kênh vào `budget.md` | Cửa 0 | V5.1.1 |
| ~~E7-T2~~ | `balance` | **Xong 27/09.** Hệ số phức mỗi vạch nhân vào `ch1` sau STFT (§3.4), vòng viết tay (ADR-0005: ghép từ `esp-dsp` chậm 2,9 lần, không có kernel nhân phức nào); `dsp_afe` dựng không gộp nhân-cộng (ADR-0006) | đủ bốn bước: `apply` soi gương float32 đúng thứ tự phép tính; bộ vàng bốn ca có đối chứng âm (hệ số liên hợp: −3,5 dB → đỏ); C khớp từng bit trên máy tính và board B ở `-O2`; 18,4 µs vào `budget.md`, chuỗi có `balance` 725,8 µs = 4,5% nhân 1 | Cửa 0 | V5.1.2 |
| ~~E7-T3~~ | `vad` | **Xong 27/09.** Bản float32 của VAD WebRTC (§3.10): hạ mẫu và cây lọc thông tất ra sáu dải, GMM hai lớp với bảng của WebRTC, mật độ Q10, tỉ số log₂ nguyên, kéo dài 240 ms; mọi bảng số ở `contracts/afe.yaml`, nguồn và giấy phép ở `firmware/third_party/webrtc_vad` | trên VIVOS `test` trộn sáu nhiễu, SNR 20–0 dB, −26 và −40 dBFS (48 cảnh, ≈ 2,2 giờ): F1 **0,765–0,791** so với **0,717** của ngưỡng năng lượng trần được ngưỡng tốt nhất (`docs/measurements/afe/vad.md`); khớp WebRTC gốc 99,3–99,6% khung; đủ bốn bước: Python soi gương, bộ vàng có đối chứng âm, C khớp từng bit trên máy tính và board B, 138 µs vào `budget.md` | Cửa 0 | V5.1.4 |
| ~~E7-T4~~ | `agc` | **Xong 27/09.** Hai tầng (§3.10): mức lời nói là trung bình công suất có cổng 10 dB của các bước `vad = 1`, τ 2 s; gain chậm ±3/6 dB/s về đích −26 dBFS, đóng băng khi im; chặn đỉnh nhìn trước 64 mẫu dưới −3 dBFS bằng cực tiểu trượt, hồi phục 50 ms và trung bình hộp, O(1) mỗi mẫu. Kéo theo: `vad` gieo ở mức 2 vì WebRTC coi nền ồn trên cỡ −40 dBFS là lời nói | trên 30 cảnh VIVOS qua `vad` rồi `agc`, mọi mức vào −50 … −10 dBFS ra trong **±1,6 dB** quanh đích (to hơn trung bình 0,8 dB), đỉnh ra −3,00 dBFS, 0 mẫu chạm toàn thang, gain trên tín hiệu dừng đứng yên (`docs/measurements/afe/agc.md`); đủ bốn bước: Python soi gương, bộ vàng có đối chứng âm, C khớp từng bit trên máy tính và board B, 258 µs (342 µs khi chặn mọi mẫu) vào `budget.md`, chuỗi 6,9% nhân 1 | E7-T3 | V5.1.3 |
| ~~E7-T5~~ | nối module thật vào firmware | **Xong 27/09.** Danh sách module sản phẩm một chỗ: `modules:` của `contracts/afe.yaml` sinh `firmware/sdkconfig.afe`, mọi bản dựng sản phẩm, CI, `bench_afe` và profile `modules` của parity cùng dùng; `app_boot` gieo `afe/*` theo `GEN_AFE_VERSION`, đọc NVS `calib/bal` (kiểm `bal_ver`) vào `svc_front`, thiếu thì chạy không `balance` và ghi cảnh báo. `srpipe.dsp.afe.chain` chạy đủ module theo `ChainConfig`; bộ vàng `chain_modules` (hiệu chuẩn và số gieo trong từng ca); máy tính thêm bản dựng đúng các module sản phẩm, một lệnh `make parity-host`. `drv_audio` đếm tràn DMA và `seq` từ lần đọc đầu: lượt boot gieo NVS làm tràn một lần khi chưa ai đọc | `chain_modules` khớp trên máy tính và board B: tới 1 LSB, SNR ≥ 77,1 dB, mọi trường số nguyên khớp tuyệt đối, đối chứng âm lệch 697 LSB (`parity.md`); `main` `dev` trên board B: gieo 3/3 khoá, `calib/bal` bản 1 nạp, heartbeat 5 phút 0 tràn DMA, 0 khung bỏ, 0 `clean` bỏ, 0 `stream` bỏ; mức vào `agc` đo trên phiên thật: nền phòng −80 dBFS (`afe/agc.md` §4) | E7-T2 | — |

---

## E8 — `dsp_afe` tầng không gian

Dựng cả hai đường rồi chấm, không chọn trước.

| ID | Module | Thuật toán | Xong khi | Chặn bởi | V5 |
|---|---|---|---|---|---|
| ~~E8-T1~~ | `doa` | **Xong 28/09.** GCC-PHAT, dò lưới 2°, **dải 2–8 kHz** chốt bằng đo (§3.6): dưới tần số gập trường vang của hai micro 4,5 cm còn kết hợp với pha 0 và kéo góc về chính diện; xoay pha dồn viết tay thay bảng và `esp-dsp` (ADR-0008); vào danh sách module sản phẩm | trên 216 cảnh, người nói một mình: 2,7° / 99% trong ±10° ở 30–150°, 5,6° / 99% ở 0–30°, 9,5° / 61% ở 150–180°, so với 7,9° / 73%, 15,6° / 4%, 22,2° / 12% của dải 200 Hz – c/2d; có nhiễu thì % trong ±10° hơn ở mọi ô; phát loa năm hướng trên board B lệch 0–28° so với 29–74° (`afe/doa.md`); đủ bốn bước: Python soi gương, bộ vàng có đối chứng âm (lệch một bước lưới → đỏ), C khớp tuyệt đối trên máy tính và board B, 22 µs mỗi bước và 641 µs bước dò vào `budget.md` | E4-T4, E7-T2, E7-T3 | V5.2.1 |
| E8-T2 | `gsc` | trễ và cộng, ma trận chặn, NLMS rò có điều khiển thích nghi (§3.7) | cải thiện SIR với một nhiễu có hướng; **phép kiểm DOA sai ±20° và ±45°** ghi mức người nói bị dìm; đủ bốn bước | E8-T1 | V5.2.2 |
| E8-T3 | `bss` | AuxIVA online, IP2 cho 2×2, chiếu ngược (§3.8) | khớp `pyroomacoustics.bss.auxiva` trên cảnh dựng, có đối chứng âm; SIR, SDR theo RT60; đủ bốn bước | E4-T4, E7-T2 | V5.2.3 |
| E8-T4 | chọn luồng ra của `bss` + so ba đường | (a) hướng từ `W⁻¹`, (b) `wake` trên cả hai luồng, (c) theo `vad`; rồi `gsc` / `bss` / trộn trần trên cùng vật liệu | bảng ba cột, thước quyết định là tỉ lệ bắt `wake` và đúng `command` trên tập thu qua board (§3.15); **chốt mặc định Kconfig bằng số**, ghi ADR | E8-T2, E8-T3, E11-T11 | V5.2.4 |

---

## E9 — Dìm nhiễu

| ID | Module | Việc | Xong khi | Chặn bởi | V5 |
|---|---|---|---|---|---|
| E9-T1 | `ns_omlsa` | OM-LSA + IMCRA, hằng số quy đổi theo bước 256, `E₁` tra bảng (§3.9). **Bốn bước xong 27/09**, đã bật trong sản phẩm: bản soi gương theo `omlsa.m` 2003, gain chặn ở 1; bộ vàng năm ca có đối chứng âm, C khớp từng bit trên máy tính và board B; 1,69 ms mỗi bước (dự trù 0,35 ms, `afe/ns.md` §4). Trên cảnh VIVOS: nhiễu dừng giảm 10–11,6 dB trong quãng nghỉ, tiếng nói mất ≤ 1,2 dB. Trên bản thu thật qua board 28/09 (`afe/ns.md` §2b): nhiễu dừng của phòng và quạt xuống 11–12 dB ở quãng nghỉ, tiếng nói mất ≤ 1 dB; nhạc chỉ xuống 4 dB. **Còn**: thu lại nhiễu quạt đặt gần board (phiên 28/09 quạt quá xa), đối chiếu `omlsa.m` gốc trong Octave | dìm nhiễu và phần mất của tiếng nói đo trên bản thu thật; đủ bốn bước | Cửa 0 | V5.3.1 |
| ~~E9-T2~~ | khe `ns` | **Xong 27/09.** `dsp_afe_ns_ops_t` (`state_bytes`, `init`, `process`) ở `dsp_afe/ns.h`, cắm qua `dsp_afe_config_t.ns` và `ns_ctx`; để trống thì chạy sàn OM-LSA khi đã dựng. Bản giả gain 1 nằm ở test app máy tính, ngoài `dsp_afe` | bản giả chạy trong chuỗi thay sàn: cùng ồn dừng, mức ra cao hơn sàn 11,8 dB (`check_ns_floor_in_chain`, trong `make parity-host` và CI); `dsp_afe` vẫn chỉ `REQUIRES common dsp_spec` | E3-T2 | V5.3.3 |
| E9-T3 | dữ liệu | tiếng Việt sạch + nhiễu §1.2 + nhiễu phòng dùng thu bằng `capture` | bảng nguồn, số giờ, giấy phép ở `DU_LIEU.md`; split có `SPLIT.md` | E11-T1, E5-T12 | V5.3.2 |
| E9-T4 | RNNoise-16k | dựng lại 18–22 dải trên 257 vạch, huấn luyện ở lưới §3.1 | run có đủ `config.resolved.yaml`, `split.lock`; điểm trên tập thử | E9-T3, E4-T2, E4-T8 | V5.3.3 |
| E9-T5 | `ai_engine/src/ns/` | lượng tử int8, GRU esp-dl, hậu xử lý dải có golden, cắm qua khe | `model->test()` đạt; **hơn sàn E9-T1 bằng thước của §3.15**, không hơn thì bỏ và giữ sàn; ghi ADR | E9-T4, E9-T2, E11-T9 | V5.3.3 |
| E9-T6 | bộ lọc cao độ | thử thêm lại bộ lọc cao độ của RNNoise | có lợi bằng số thì giữ, không thì ghi lý do bỏ | E9-T5 | — |
| E9-T7 | 🔬 đo trên board | trọng số và vùng làm việc ở PSRAM (KẾ HOẠCH §6.5), có Wi-Fi chạy | đỉnh một khung có vượt nhịp không, ghi vào `latency.md`; vượt thì ADR, không kéo về RAM nội | E9-T5 | V5.5.9 |
| E9-T8 | `ns_omlsa` nhanh hơn — **để dành**, làm khi nhân 1 chật (lúc `wake` và `command` vào chuỗi) | `fmaf` (một lệnh `madd.s`, làm tròn một lần) ở mọi chỗ nhân-cộng của `ns_omlsa.c`, viết tường minh nên vẫn dựng `-ffp-contract=off`. Bản soi gương mô phỏng đúng phép gộp: tích hai float32 đúng trong double, cộng không lỗi, làm tròn một lần sang float32, xử cả ca rơi đúng giữa hai số float32. Mọi cách khác đã đo và bỏ ở `afe/ns.md` §4 | `ns_omlsa` và `chain_modules` vẫn khớp từng bit trên máy tính và board B; µs mới vào `afe/ns.md` §4 và `budget.md` (dự kiến 1,69 → ~1,2 ms 🔬); ADR-0006 xét lại cho `fmaf` tường minh | E9-T1 | — |

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
| ~~E11-T1~~ | | **Xong 27/09.** Khảo sát nguồn tiếng Việt (KẾ HOẠCH §1.2): tải và kiểm Common Voice 27.0, VIVOS, FPT, VLSP2020-100h, Bud500 (~620 giờ lời nói), MUSAN, DEMAND, OpenSLR 28, và 28/09 nhiễu 16 kHz của DNS Challenge (180 giờ), mỗi kho một manifest có checksum và lệnh `ml/scripts/10_prepare.sh <loại>/<tên>` tải lại. FPT, VLSP, Bud500 không có mã người nói nên chỉ vào tập học | `DU_LIEU.md`: nguồn, số giờ, số người nói, giấy phép, sha256 | E1-T8 | V5.5.1 |
| E11-T2 | | Phiếu đồng ý, quy trình thu, bảng mã người nói ngoài repo; kiểm điều khoản Nghị định 13/2023 và Luật BVDLCN 2025 | phiếu có bản in; quy trình xoá theo mã người nói chạy được một lượt | — | V5.5.6 |
| ~~E11-T3~~ | | **Xong 29/09 bằng ADR-0010**, theo số đã công bố thay vì phép so bốn đường: 44 đơn vị `lang_vi`, thanh chen trong cùng chuỗi CTC (KẾ HOẠCH §3.12). Tài liệu cho thấy thanh phải nằm trong đơn vị còn cách đặt thanh không đổi lỗi gộp; chọn theo giá: bộ ký hiệu nhỏ nhất, một đầu ra, không đổi hợp đồng | ADR có bảng số đã công bố; tỉ lệ đúng trên cặp lệnh chỉ khác thanh đo sau E11-T12 và ghi vào `measurements/` | E11-T4 | V5.5.2 |
| ~~E11-T4~~ | `lang_vi` | **Xong 27/09.** Luật ở `contracts/lang_vi.yaml`, sinh cho C và Python (KẾ HOẠCH §3.12): 44 đơn vị X-SAMPA (âm đoạn + 6 thanh, chung cho đường 1 và 4 của E11-T3; Python giữ thêm cấu trúc âm tiết cho đường 2 và 3), 27 âm đầu, 159 vần + 38 vần sau `qu`, luật `c/k/q` `g/gh` `ng/ngh` `gi`, ba vùng Bắc/Trung/Nam (đầu `d gi r v s tr`, `qu`/`hw`, `-n/-ng` `-t/-c`, `-nh -ch`, `i ê` trước chúng ở miền Nam, ngã nhập hỏi, linh/lẻ, nghìn/ngàn); `normalize` ghép dấu rời theo thứ tự bất kỳ, đọc số, ký hiệu, từ điển 30 mục | golden **khớp tuyệt đối** trên máy tính và board B: 16 742 âm tiết hợp lệ × 3 vùng, 38 cụm từ, 64 đầu vào `normalize`, 27 dòng `lexicon` gồm bộ lệnh mặc định; ba đối chứng âm đỏ (`parity.md`); 44 cách đọc gán nhãn tay ở ba vùng và 20 chính tả sai bị từ chối (`test_lang_vi.py`); 1,18 ms mỗi lệnh ba vùng lúc nạp bộ lệnh, ngăn xếp 2 844 B (`budget.md`, `ram.md`). Cột Trung chưa đối chiếu người nói thật: E11-T13 đo theo vùng | E3-T2 | V5.5.3 |
| ~~E11-T5~~ | | **Xong 29/09: "trợ lý"** (ADR-0011, thay "Chào Mina" của ADR-0007). Chủ dự án chọn vì tự nhiên; 2 âm tiết, 28 lần mỗi triệu âm tiết lời nói, 223 lần người thật nói nó trong kho nên có mẫu dương giọng thật. Bản demo ưu tiên bắt được; "Chào Mina" không đạt trên bản thu thật qua ba lượt học (`measurements/wake.md`) | lý do ghi ADR, số của `srpipe.tasks.wake.candidates` trên 7,97 triệu âm tiết | E11-T1 | V5.5.5 |
| E11-T6 | | Thu **tập thử** qua board bằng `capture` + `host/session.py`: từ đánh thức ("trợ lý"; các phiên "chào mi na" 034–039 thành âm bản), câu lệnh, âm bản gần âm, nhiễu phòng (KẾ HOẠCH §1.2). Khi cần tiếng thật để học (đường lùi Cửa 2, 3), thu thêm người khác vào tập học, cùng khuôn | vài người nói có phiếu (ghi số người), nam nữ, 1 m và 3 m, ít nhất hai phòng; split `device/v1` theo người nói và phòng, qua `test_splits.py` | E11-T2, E11-T5, E5-T12, E13-T3 | V5.5.6 |
| E11-T7 | | Tiếng tổng hợp: **nguồn chính của dương `wake`**, cụm gần âm cho âm bản, tăng lượng `command`; nhiều giọng, tốc độ, ngữ điệu. **Pilot xong 28/09**: `srpipe/tts` gọi VieNeu-TTS v3 Turbo và F5-TTS ViVoice trên GPU, PhoWhisper-large nghe lại từng mẩu; "chào mi na" qua 62–73% (`measurements/tts_engines.md`), giữ cả hai bộ. **Bộ `wake` xong 28/09** (`make wake-synth`, `tts_engines.md` §3): 356 giọng nhân bản từ vật liệu học đã sàng lọc cộng 25 giọng có sẵn; 3 710 dương, giữ 3 584; 690 âm bản gần âm trên 69 cụm, giữ 384; ngưỡng độ chênh 15,40 nat đặt để 1% âm bản lọt. Với "trợ lý" (ADR-0011) phải sinh lại bộ dương và bộ âm bản TTS; bộ "chào mi na" bỏ. Còn: tăng lượng `command`, trừ "chụp ảnh" là lệnh chưa học để thử ở E11-T13; đưa bộ `wake` qua đường mô phỏng board (E4-T8) | giấy phép mô hình TTS ghi ở `DU_LIEU.md`; số giọng, số mẩu ghi rõ; không mẩu nào vào tập thử | E11-T5 | — |
| E11-T8 | đặc trưng | log-mel 40 + chuẩn hoá đi theo model; **dựng `dsp_spec/pitch`** cho `command` (ADR-0010) theo bộ dò cao độ của Kaldi chạy dòng (KẾ HOẠCH §3.11). **Plan 30/09, dựng từ 30/09:** (1) **xong 30/09**: `ml/src/srpipe/dsp/spec/pitch.py` float32, cộng dồn tuần tự đúng thứ tự bản C sẽ làm, tham số ở mục `pitch` của `configs/scenes/device.yaml`; Viterbi lấy min đúng bằng biến đổi khoảng cách của Felzenszwalb, test so với duyệt hết; test tông chuẩn biết trước F0, tông trượt, đoạn lặng (`test_pitch.py`); (2) so với chính Kaldi qua kalpy trong image của bộ căn mốc (`srpipe/metrics/pitch.py`, `ml/afe_ref/kaldi_pitch/run.py`) trên VIVOS test: lượt đầu chạy dòng phải trùng; so với bản đọc cả tệp, tỉ lệ khung hữu thanh lệch F0 quá 5% và tương quan độ hữu thanh, ghi `measurements/pitch.md`. `compute_kaldi_pitch` của torchaudio bị loại vì lớp ma trận của nó làm hỏng Viterbi; **(2) xong 30/09**: trên 760 câu VIVOS test bản soi gương chọn đúng độ trễ của Kaldi chạy dòng ở 99,94% khung, ba đặc trưng lệch cỡ làm tròn float32; so với Kaldi đọc cả tệp 3,32% khung hữu thanh lệch F0 quá 5%, tương quan độ hữu thanh 0,976 (`measurements/pitch.md`); (3) **xong 30/09**: bộ vàng `contracts/golden/pitch/`, bốn ca 96 bước và đối chứng âm trễ một bước mà `tolerance.yaml` bắt được; (4) **xong 30/09**: `dsp_spec/pitch.{h,c}` khớp từng bit bộ vàng trên máy tính và board B, cả bản dựng mặc định lẫn bản bật module; (5) **xong 30/09**: 1 980 µs trung bình, 2 081 µs đỉnh mỗi bước ở nhân 0 với vùng làm việc 155 920 B trong PSRAM, gấp 6,6 lần ước lượng (`budget.md`, `measurements/pitch.md` §3). Chọn `log_floor` theo mức ra thật của `agc`: ở `pcm_shift` 16, sàn 1e-6 nuốt 39% giá trị của bước có tiếng (`afe/agc.md` §4) | `pitch` đủ bốn bước: Python, C khớp, golden có đối chứng âm, µs trên board vào `budget.md`; phép kiểm canh thống kê lúc huấn luyện và lúc chạy | E6-T5 | V5.5.4 |
| ~~E11-T9~~ | `ai_engine` core | **Xong 28/09.** `src/core/model_image`: map slot, từ chối `grid_hash` khác lưới của bản dựng, chép từng mục lên PSRAM căn 16 B, băm sha256 bản chép bằng `esp_sha` so với đầu ảnh; lỗi nào cũng để lại không ảnh nào, tìm mục theo tên và loại. Cấp vùng làm việc và chạy mạng esp-dl đi cùng E11-T10, nơi có `.espdl` thật | ảnh có `grid_hash` sai bị từ chối với `ESP_ERR_INVALID_VERSION`, lật một bit bị từ chối với `ESP_ERR_INVALID_CRC`, cả trên máy tính và trên board B; nạp 1 MB mất 87 ms, PSRAM giữ đúng tổng các mục (`latency.md` §7) | E5-T5, E3-T2 | — |
| ~~E11-T10~~ | | **Xong 28/09.** Xuất mạng dòng qua ESP-PPQ dùng `StreamingCache`: TCN của `configs/models/wake.yaml` (`srpipe.tasks.wake.model.tcn`), lượng tử và mô phỏng ở `srpipe.compress.quant.ptq_espdl` với bộ đệm gắn theo từng tích chập (`auto_streaming` làm hỏng phép cộng dư), ảnh slot ở `srpipe.export.pack_models` đọc hằng số từ `storage_format.h`; `src/core/espdl_net` dựng mạng từ mục đã nạp, mọi tensor ở PSRAM, chạy một bước khởi động lúc nạp; esp-dl `==3.3.11`, ESP-PPQ `==1.3.11` | 200 bước trên board B **khớp từng bit** mô phỏng cả chuỗi, `model->test()` qua, 427 µs mỗi bước, đối chứng âm không `reset` lệch 27 (`latency.md` §8); `make ai-unit` chạy lại | E11-T9 | V5.5.7 |
| E11-T11 | `wake` | TCN giãn nở int8 (§3.11). **Split `wake/v1` xong 28/09** (`make splits`, `data/splits/wake/v1/SPLIT.md`): 3 466 dương và 122 483 âm bản (100 giờ lời nói + 370 âm bản gần âm) để học, 118 và 2 497 để `val`, `test_neg` 22,9 giờ Common Voice và VIVOS test. **Lượt học đầu 29/09** (`measurements/wake.md`): bắt 77% trên `val` TTS, `test_neg` 0,45 báo nhầm/giờ, nhưng trên board B 0/60 đoạn "chào mi na" qua ngưỡng 0,975 và "chào mẹ", "chào minh" lên ngang giọng thật, vì tập học thiếu cụm gần âm ấy và gần như không gặp cụm gần âm nào. **`wake/v2` 29/09** (âm bản khó, split `wake/v2`): "chào mẹ", "chào minh" chưa từng học tụt về ≤ 0,28, nhưng giọng thật nói đúng từ tụt theo (trung vị 0,77 → 0,21; 64 kênh 0,14): mẫu dương toàn TTS là giới hạn gốc; mốc và ngưỡng chọn trên 3,76 giờ âm bản của `val` không tin được. **`wake/v3` 29/09**: trọng số cuối và `val` 10,9 giờ đưa giọng thật lên trung vị 0,48; 64 kênh với việc phụ CTC lên 0,71, cụm gần âm gần như hết lọt (`wake.md` §5). **Đổi sang "trợ lý" (ADR-0011)**: split `wake/v4`, mẫu dương TTS cộng mẩu người thật của kho trích `hf_extract` (nhận theo cách đọc, "trợ lí" cũng tính; cắt theo mốc căn cưỡng bức
MFA; mẩu TTS bỏ khoảng lặng sau từ, `wake.md` §6), một nửa đoạn dương mỗi lô là người thật, không âm bản khó, 64 kênh, CTC, ưu tiên bắt cho bản demo. **Trích xong 30/09** (`core/extract.py`, KẾ HOẠCH §1.2): 2 032 mẩu "trợ lý" từ 18 kho trên Hugging Face và kho học (`raw/speech/hf_extract`, `DU_LIEU.md`). Còn: sinh dữ liệu, học, chấm trên phiên "trợ lý" thu qua board (E11-T6) | 🔬 **Cửa 2**: bắt ≥ 95% ở 1 m, báo nhầm ≤ 1 lần mỗi giờ trên ≥ 24 giờ âm bản; ghi thêm 3 m, SNR 10 và 5 dB | E11-T6, E11-T7, E4-T8, E11-T8, E11-T10 | V5.5.7 |
| E11-T12 | `command` mạng | CRNN nhỏ chạy dòng + CTC trên đơn vị của ADR-0010, như MultiNet; **trước tiên thử GRU int8 chạy dòng qua esp-dl trên board** (xuất ESP-PPQ, đẩy từng bước, khớp mô phỏng, đo µs), không đạt thì TCN. **Split `command/v1` xong 28/09** (`data/splits/command/v1/SPLIT.md`): 626 giờ học (một file mỗi kho), 1,9 giờ `val`, 2,0 giờ `test` của 59 người nói; 196 câu chứa "chụp ảnh" đã rời tập học, để lệnh ấy thành lệnh chưa học của E11-T13 | run đầy đủ; tỉ lệ lỗi đơn vị trên tập thử | E11-T1, E11-T8, E4-T8 | V5.5.8 |
| E11-T13 | `command` giải | chấm CTC có ràng buộc, max trên biến thể, từ chối theo `δ₁` `δ₂` (§3.12); chỉnh `δ₁` `δ₂` trên mẩu người thật nói cụm na ná lệnh ("bật điện", "mở cửa sổ", "đóng góp"…) cắt bằng `core/extract.py`, trước khi đo trên phiên gần âm thu qua board | 🔬 **Cửa 3**: mỗi lệnh ≥ 90%, từ chối đúng ≥ 95%; **thêm một lệnh chưa có trong dữ liệu huấn luyện chỉ bằng một dòng chữ** và đo nó | E11-T12, E11-T4 | V5.5.8 |
| E11-T14 | `svc_listen` | `nhan_task`, đổi chế độ `NGHE`/`LENH`, `q_cmdset` đổi bảng lệnh giữa hai câu | đổi bộ lệnh qua MQTT trong lúc chạy, không mất khung, lệnh mới dùng được ngay câu sau | E11-T13, E5-T6 | V5.5.8 |
| E11-T15 | | 🔬 Đo `wake` và `command` trên board, trọng số và vùng làm việc ở PSRAM (KẾ HOẠCH §6.5), **có Wi-Fi chạy** | đỉnh một khung có vượt nhịp không, ghi vào `latency.md`; vượt thì ADR, không kéo về RAM nội | E11-T11, E11-T13 | V5.5.9 |
| E11-T17 | `command` `kws` | DS-CNN phân lớp các lệnh của bộ mặc định + `other` + `silence` (ADR-0012, **chấp nhận 30/09**), cắm cùng hợp đồng `ai_engine_command_*` với `ctc`, học log-mel 40 cộng ba chiều cao độ ngay bản đầu, cỡ S, M, L chọn bằng số đo (KẾ HOẠCH §3.12). **Plan 30/09:** (1) tách `tasks/command/`: `data.py` sang `ctc/data.py`, mục `split` sang `command_ctc.yaml`, `command.yaml` giữ `backend`, `features`, `synth`; (2) `kws/model/` DS-CNN ba cỡ theo `command_kws.yaml`, test số tham số và MAC; (3) `kws/postproc/` softmax và luật từ chối, bộ vàng `contracts/golden/command_kws/` có đối chứng âm; (4) mô phỏng board thêm ba chiều cao độ, bộ dò đặt lại ở đầu mẩu, mẩu chừa trước đủ cửa sổ; đầu ra của `wake` không đổi byte nào; (5) `kws/quant.py`: ba cỡ trọng số ngẫu nhiên qua ESP-PPQ, µs mỗi câu trên board, cỡ nào quá 100 ms thì ra khỏi cuộc; (6) `kws/data.py`: split `command_kws/v1` từ TTS của E11-T7, `kws_vi_command`, mẩu `hf_extract` trích lại, lời nói thường và nhiễu, rồi đặc trưng; (7) `kws/train.py`, `backend.py`, `eval.py` (Cửa 3); (8) `ai_engine/src/command_kws/` + Kconfig `AI_ENGINE_COMMAND_BACKEND`, `meta.json` với `features` `log_mel40_pitch3` | 🔬 **Cửa 3** như `ctc`: mỗi lệnh ≥ 90%, từ chối đúng ≥ 95% trên tập thu qua board; µs mỗi câu trên board vào `budget.md` | E11-T8, E11-T7, E11-T11 | V5.5.8 |
| ~~E11-T16~~ | dữ liệu | **Xong 28/09.** Sàng lọc mọi kho tiếng nói, nhiễu và RIR trước khi dùng (KẾ HOẠCH §1.2): `srpipe.core.screen` đo 832 878 mẩu một lần (17 phút), chấm theo `configs/common/screen.yaml` trong 20 s. Ngưỡng tiếng nói đặt bằng quét PhoWhisper theo ô (`make screen-audit`): câm dưới −55 dBFS, tốc độ ngoài 1–10 âm tiết/s; chạm trần và khoảng động không có ô nào quá nửa hỏng nên không có luật. Loại 1 346 mẩu tiếng nói (1,36 trên 631,7 giờ), gồm 437 mẩu FPT Set002 toàn số 0, và 976 mẩu nhiễu/RIR, gồm 842 bản chép MUSAN trong OpenSLR 28. Giọng mẫu TTS, nhiễu của đường mô phỏng board và các cảnh có nhãn đã đọc danh sách loại; `device.build` từ chối split có mẩu bị loại | `make screen` chạy hết các kho; bảng quét ở `measurements/data_screen.md`; số mẩu, số giờ loại ở `DU_LIEU.md` §1.1 | E11-T1 | — |

Con số báo nhầm quan trọng hơn con số bắt được: một máy tự bật mỗi mười phút là máy không ai dùng.

---

## E12 — Tiếng nói ra

| ID | Module | Việc | Xong khi | Chặn bởi | V5 |
|---|---|---|---|---|---|
| E12-T1 | | Bảng so ghép mẩu / ghép âm tiết / mạng chưng cất: bộ nhớ, flash, phép tính, chất lượng; chọn bộ TTS máy tính và một giọng nguồn (KẾ HOẠCH §3.13) | bảng đủ ba cột, **có phương án không mạng**; giọng chọn bằng nghe thật; ADR | E11-T4, E11-T7 | V5.6.1 |
| E12-T2 | `svc_speak` | Ghép mẩu: sinh câu trả lời và từ số bằng giọng nguồn qua `srpipe/tts`, mỗi mẩu PhoWhisper nghe ra đúng chữ; IMA-ADPCM, phân vùng `voice`, dựng trước rồi phát | nói được mọi câu trong `responses/vi.json` và mọi số 0–9999 | E10-T1, E11-T2, E12-T1 | V5.6.3 |
| E12-T3 | | Nối `lang_vi` vào đường mạng, thêm trường độ và ngôn điệu | **không viết lại `g2p`**; chỉ khi E12-T1 chọn mạng | E12-T1 | V5.6.2 |
| E12-T4 | `ai_engine/src/synth/` | mạng huấn luyện ở **16 kHz** trên tiếng giọng nguồn do `srpipe/tts` sinh (thầy), iSTFT qua `dsp_spec` | 🔬 **Cửa 4**: dưới 1× thời gian thực, vừa bộ nhớ; không đạt thì lui về E12-T2, không nới ngân sách | E12-T3 | V5.6.3 |
| E12-T5 | | Chấm chất lượng | điểm tự động cộng nghe thật, ghi cả hai | E12-T2 hoặc E12-T4 | V5.6.4 |

---

## E13 — Mạng và máy tính nhận

Chạy song song từ lúc `net_mqtt` có ở E5.

| ID | Task | Xong khi | Chặn bởi | V5 |
|---|---|---|---|---|
| ~~E13-T1~~ | **Xong 26/09.** Hoàn thiện bảy schema và `mqtt_topics.yaml` theo §7.3; sinh code cho `host/`. Bảy schema đã đủ trường của §7.3; `telemetry.levelDbfs` mở xuống −128 cho khớp `dsp_afe`. `gen_contracts.py` sinh thêm `SCHEMAS` vào `srhost.generated.payload` (§7.7: host đọc schema từ code sinh). Gói `host/`: `pyproject.toml`, `uv.lock`, `jsonschema` | `host` và firmware cùng sinh từ một nguồn; 24/24 pytest của `host`, gồm một heartbeat thật của board B hợp lệ và ba bản bị sửa bị từ chối; `contracts`, `host`, `firmware`, `ml` xanh ở `27a2869` | E1-T4 | V5.7.4 |
| ~~E13-T2~~ | **Xong 26/09.** `host/mqtt_rx.py` + `live.py`: đăng ký mọi topic `up` từ code sinh, kiểm `SCHEMAS`, ghi jsonl; `live.py` vẽ lại màn hình 5 lần/s, chạy qua ống thì in một dòng mỗi tin. `SRHOST_MQTT_*` đọc ở `srhost.config` | xem trực tiếp hướng, cờ tiếng nói, mức, sự kiện; payload sai schema bị ghi lại. Kiểm bằng 16 phép thử với payload đúng hợp đồng và 8 ca sai (không JSON, sai schema, `deviceId` lệch topic, topic lạ); nối broker thật bằng `srhost` nhận đúng `status` retained của board B. `telemetry` và `event` chưa có trên board, xem thật ở E13-T11 | E13-T1 | — |
| ~~E13-T3~~ | **Xong 26/09.** `host/stream_rx.py` + `session.py`: phiên thu có nhãn, mã người nói, mã phiếu. `srhost.config` là chỗ duy nhất đọc biến môi trường; tên kênh theo `mode` nằm ở `frame.yaml`; kết nối mới của board thay kết nối cũ đã im | một phiên thật từ board B (`probe`, 10 s, `mode 2`) ra đúng khuôn KẾ HOẠCH §4.4.1 ở `raw/device/board_b/20260926_lab_001/`: `ch0.wav` `ch1.wav` + `session.json` + `gaps.txt`, và một dòng mới trong `board_b.csv` — ghi vào data root và manifest nháp, vì máy chưa đặt `SRPIPE_DATA_ROOT`; 51/51 pytest của `host` | E13-T1 | V5.5.6 |
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
