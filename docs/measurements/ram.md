# RAM

## 1. Vùng làm việc theo module (E3-T3, rồi đo lại ở bước 4 của mỗi module)

Luật đặt chỗ: phần nào bị chạm **mỗi khung** thì vào `hot` (RAM nội), phần lớn mà ít chạm thì vào `cold`
(PSRAM). Mỗi module nhận **một** vùng qua `_workspace_bytes` / `_init`; mặt tiền `dsp_afe` đặt cả vùng của
module vào `hot` hay `cold` theo cột dưới đây. Số tính cho ESP32-S3 (con trỏ 4 B, `float` 4 B, phức 8 B,
257 vạch); mỗi vùng cộng 16 B căn lề.

| Component | Module | `sizeof` trạng thái B | Vùng `hot` B | Vùng `cold` B | Ước hay đo |
|---|---|---|---|---|---|
| `dsp_spec` | `fft` 512 điểm | 12 | 2 080 | 0 | tính từ code; khớp số đo 2 080 B (`latency.md` §1) |
| `dsp_spec` | bảng `dl_fft` tự cấp, một lần mỗi `fft` | — | 6 244, ngoài vùng người gọi (luật 7 §4.5.3) | 0 | đo trên board B (`latency.md` §1) |
| `dsp_spec` | `stft` phân tích | 6 148 (cửa sổ + đệm + khung, 3 × 512 `float`) | 6 176 | 0 | tính từ code |
| `dsp_spec` | `istft` tổng hợp | 6 148 | 6 176 | 0 | tính từ code |
| `dsp_spec` | `mel`, mọi cấu hình ≤ 80 dải | 3 664 (lọc thưa 514 trọng số + bảng cos 320) | 3 680 | 0 | tính từ code |
| `dsp_spec` | `window` | không trạng thái, ghi vào mảng người gọi | 0 | 0 | — |
| `dsp_afe` | `hpf`, 2 kênh | ~44 (5 hệ số + 2 × 2 trạng thái) | ~64 | 0 | ước 🔬 |
| `dsp_afe` | `balance` | không trạng thái; 257 hệ số phức do mặt tiền giữ | 2 056 (ở mặt tiền) | 0 | ước 🔬 |
| `dsp_afe` | `doa` | ~2 450 (Φ 257 phức + R(θ) 91 góc) | ~2 460 | 0 | ước 🔬 |
| `dsp_afe` | `gsc` | ~3 100 (W 257 phức + P_B 257) | ~3 120 | 0 | ước 🔬 |
| `dsp_afe` | `bss` | ~24 700 (V₁, V₂, W: 3 ma trận 2×2 phức mỗi vạch) | ~24 720 | 0 | ước 🔬 |
| `dsp_afe` | `ns` sàn OM-LSA + IMCRA | 33 280 đo: 16 KB là 2 × 8 cửa sổ cực tiểu của IMCRA, chạm mỗi 8 bước; bảng E₁ 256 điểm, hệ số Hann và hằng số dựng lúc khởi tạo | 33 280 | 0 | `bench` @ `5636ed9` |
| `dsp_afe` | `vad` | ~1 150 (GMM 6 dải × 2 lớp × 2 thành phần, 16 cực tiểu mỗi dải) | ~1 170 | 0 | ước 🔬 |
| `dsp_afe` | `agc` | ~560 (đường nhìn trước 64 mẫu + hàng giữ đỉnh 64) | ~580 | 0 | ước 🔬 |
| `dsp_afe` | `aec`, 2 micro × 8 phân đoạn, chỉ `"MMR"` | ~60 650 (trọng số 32 896 + phổ tham chiếu 16 448 + đệm) + 4 B mỗi mẫu `calib/aec_delay` | ~60 700 + đường trễ | 0 | ước 🔬 |
| `dsp_afe` | mặt tiền: struct + `DSP_AFE_FIFO_FRAMES` × 524 B | ~2 250 | ~2 250 | 0 | ước 🔬 |
| `dsp_afe` | mặt tiền: đệm tạm một khung, `"MM"` | — | 11 296 (trộn trần, `gsc`) · 13 352 (`bss`) | 0 | ước 🔬 |
| `lang_vi` | `normalize`, `g2p`, `lexicon_entry` | không trạng thái; `lang_vi_pron_t` 197 B do người gọi cấp | 0 | 0 | tính từ header |

`cold` bằng 0 ở mọi dòng: trong chuỗi hiện tại không module nào có phần lớn mà ít chạm. Ứng viên đầu tiên là
đường trễ khối của `aec` (mỗi khung chỉ ghi và đọc một bước); E10-T4 quyết định có tách ra không. Vùng `cold`
cũng cho phép đặt cả chuỗi ở PSRAM để đo giá của chỗ đặt (E14-T6).

Cộng theo chuỗi, RAM nội, ước 🔬:

| Chuỗi | `sach_task` B | `nhan_task` (đặc trưng: `fft` + bảng + `stft` + `mel` + đệm) B | Cộng B |
|---|---|---|---|
| App khung rỗng E5-T11: chỉ STFT → trộn trần → iSTFT | **47 268 đo** (ước ~38 300) | — | **47 268 đo** |
| Mốc demo E13-T11: `hpf`, `balance`, `doa`, trộn trần, `ns` sàn, `vad`, `agc` | ~59 100 | ~20 400 | ~79 500 |
| như trên, `gsc` thay trộn trần | ~62 300 | ~20 400 | ~82 700 |
| như trên, `bss` thay trộn trần | ~85 900 | ~20 400 | ~106 300 |
| `"MMR"` + `aec` + `bss` (cần loa, E10) | ~148 700 + đường trễ | ~20 400 | ~169 100 + đường trễ |

Dòng app khung rỗng đã đo trên board B ở E3-T4: heap nội giảm 47 268 B qua `svc_front_init` = ~41 KB `hot` + 6 244 B
bảng `dl_fft`. Bản sản phẩm E7-T5 (`hpf`, `balance`, `vad`, `agc` của `firmware/sdkconfig.afe`, `calib/bal` nạp): giảm
51 364 B, tức bốn module thêm 4 096 B. Ước thấp vì đệm tạm trong struct mặt tiền (~19,7 KB: PCM float ba kênh, phổ hai micro, hai lối
ra không gian, công suất, gain, FIFO, bản sao hiệu chuẩn) được cấp cho mọi cấu hình, kể cả phần chỉ `bss` hay
`"MMR"` dùng. Mọi dòng khác cộng thêm chừng ấy chênh lệch.

So với heap nội đo được sau Wi-Fi + MQTT (§3: còn 139 047 B, thấp nhất 133 431 B), tính trên mức thấp nhất: mốc demo còn dư ~54 KB, `bss`
còn ~27 KB, còn `aec` + `bss` **không vừa** nếu không dùng đường lùi của KẾ HOẠCH §6.5. Khoản ~50 KB cho
"`dsp_spec` + `dsp_afe` trừ `aec`" ở §6.5 thấp hơn ước này: bốn thể hiện STFT (~24,7 KB), hai bộ bảng `dl_fft`
(~12,5 KB, `sach_task` và `nhan_task` mỗi bên một bộ) và đệm tạm (~11–13 KB) chưa được tính ở đó.

## 2. Ngăn xếp task (E14-T3)

| Task | Cấp B | Watermark còn trống B | Biên B | Profile | Ngày |
|---|---|---|---|---|---|
| `thu_task` | 3072 | 2240 | — | `dev` @ `cb082c8`, thân task mới chặn ở hàng đợi (E5-T6) | 26/09 |
| `sach_task` | 6144 | 5356 | — | `dev` @ `cb082c8`, thân task mới chặn ở hàng đợi (E5-T6) | 26/09 |
| `nhan_task` | 8192 | 6892 | — | `dev` @ `cb082c8`, thân task mới chặn ở hàng đợi (E5-T6) | 26/09 |
| `dieu_task` | 4096 | 2588 | — | `dev` @ `cb082c8`, thân task mới chặn ở hàng đợi (E5-T6) | 26/09 |
| `gui_task` | 4096 | 3292 | — | `dev` @ `cb082c8`, thân task mới chặn ở hàng đợi (E5-T6) | 26/09 |
| `net_task` | 4096 | 3552 | — | `dev` @ `cb082c8`, thân task mới chặn ở hàng đợi (E5-T6) | 26/09 |

Số của E5-T6 là **sàn**: thân task chưa có `dsp_afe`, `wake`, MQTT. Chưa đặt biên; E14-T3 đo lại khi đủ việc.

`lang_vi_lexicon_entry` chạy trong `nhan_task` mỗi lần đổi bộ lệnh (§5.3 `q_cmdset`). Một task riêng của `bench_afe` chỉ
làm vòng đo quanh hàm ấy dùng hết **2 844 B** ngăn xếp (`d259771`, profile `bench`), trong đó 1 KB là 256 mục chữ đã ghép
và 256 B là đệm chuẩn hoá; `nhan_task` cấp 8 192 B.

## 3. Heap sau boot (E5-T11)

| Mốc | Heap nội còn B | Mảnh liền lớn nhất B | PSRAM còn B | Profile | Ngày |
|---|---|---|---|---|---|
| `heap_init` lúc khởi động, app chưa có gì, chưa Wi-Fi | 346292 (323984 + 22308) | 323984 | 8 388 608 (pool 8192 KiB) | `dev` @ `32e9b55` | 26/09 |
| sau `sys_storage_init` (NVS + LittleFS) | 277099 | 217076 | 8 384 660 | `dev` @ `cb082c8` | 26/09 |
| sau `app_wiring_init` (pool 8 × 1540 B nội, 38 064 B hàng đợi ở PSRAM) | 277099 | 217076 | 8 346 260 | `dev` @ `cb082c8` | 26/09 |
| sau `drv_audio_init` (DMA 8 × 256 mẫu) — trước sáu task (29 696 B ngăn xếp tĩnh, đã nằm trong .bss) | 258415 | 217076 | 8 346 260 | `dev` @ `cb082c8` | 26/09 |
| Wi-Fi vào mạng (`net_task`: link up) | 139183 | 98292 | — | `dev` @ `b38ffb3` | 26/09 |
| MQTT chạy, heartbeat đầu tiên, 5 phút sau boot: còn / thấp nhất | 139047 / 133431 | — | 8 317 652 / 8 317 224 | `dev` @ `b38ffb3` | 26/09 |
| sau `svc_front_init`: `dsp_afe` mặc định, mọi module tắt | 150139 | 106484 | 8 329 360 | `dev` @ `87b0337` | 26/09 |
| như trên, Wi-Fi + MQTT chạy, heartbeat ~70 s sau boot: còn / thấp nhất | 93799 / 88075 | — | 8 317 652 / 8 317 232 | `dev` @ `87b0337` | 26/09 |
| thêm `sb_stream` 64 KB ở PSRAM, luồng `mode 2` đang gửi: còn / thấp nhất | 77471 / 64019 | — | — | `dev` @ `253ddf7` | 26/09 |
| như trên, máy nhận nghẽn 30 s, board nối lại mỗi giây: thấp nhất | 45895 | — | — | `dev` @ `253ddf7` | 26/09 |
| `sb_stream` 512 KB ở PSRAM, luồng `mode 5` gửi liền 10 phút (E5-T11): còn / thấp nhất | 83659 / 64791 | — | 7 776 928 / 7 776 148 | `dev` @ `3d3335d` | 26/09 |
| sau `svc_front_init`: `dsp_afe` với `hpf`, `balance`, `vad`, `agc`, `calib/bal` nạp (E7-T5) | 135147 | 90100 | 7 788 684 | `dev` @ `ca660e9` | 27/09 |
| như trên cộng `ns_omlsa` (E9-T1) | 102231 | 59380 | 7 788 684 | `dev` @ `c2f97b5` | 27/09 |
| như trên, Wi-Fi + MQTT chạy, heartbeat 5 phút: còn / thấp nhất | 43979 / 36931 | — | 7 776 928 / 7 776 484 | `dev` @ `c2f97b5` | 27/09 |

Đọc từ log `heap_init` của board B qua CH340. Đây là **trần** cho mọi thứ ở KẾ HOẠCH §6.5 cộng lại, trước khi Wi-Fi và lwIP lấy phần của chúng; nó khớp khoảng ước 300–340 KB của §6.5.

Wi-Fi, lwIP và esp-mqtt lấy khoảng **119 KB** RAM nội (258 415 → 139 183 B), vượt khoảng ước 50–90 KB của KẾ HOẠCH §6.5 với cấu hình đệm mặc định của IDF (32 đệm RX động, 32 TX động, 10 RX tĩnh × 1600 B). Đây là số đo cho hàng "Wi-Fi + lwIP" của §6.5; đường lùi của §6.5 là giảm đệm Wi-Fi.

## 4. PSRAM theo bước khởi động và theo cỡ bộ lệnh tối đa (11/10)

Board B, firmware `bench` @ `8bf4ccb`, ảnh model khoá trong `contracts/models.lock.json` (`command_ctc` và bảng
chuẩn hoá, 3370 KB), `set.json` là bộ 301 lệnh, 653 cách đọc. Đọc từ dòng `app_boot` của log khởi động qua CH340.

| Bước | PSRAM còn B | Dùng thêm | Gồm |
|---|---|---|---|
| `storage` | 8 384 752 | — | NVS, LittleFS |
| `wiring` | 7 805 724 | 579 KB | hàng đợi, `q_clean`, `sb_stream` 512 KB |
| `audio` | 6 970 132 | 836 KB | `net_mqtt`: hai vùng JSON 2 × 160 KB, hai ô bộ lệnh mỗi ô chứa JSON 154 KB và bộ đã phân tích |
| `models` | 2 314 468 | 4 656 KB | ảnh 3370 KB, tensor `command_ctc` 314 KB, vùng chấm ctc (cây tiền tố tối đa 32 768 nút) |
| `front` | 2 314 468 | 0 | `dsp_afe` ở RAM nội |
| `listen` | **1 811 988** | 502 KB | `svc_listen`: vòng 512 bước, hai bảng lexicon 152 KB |

Cùng board, một bản dựng tạm, không commit, hạ tối đa xuống 64 lệnh:
`AI_ENGINE_COMMANDS_MAX` 65, `NET_MQTT_COMMANDS_TEXT_BYTES` 65 × 512, cây tiền tố 8 192 nút, vùng JSON 40 KB. Bộ 301
lệnh không còn vừa nên máy không nghe; phần `svc_listen` tính từ code, 258 B mỗi lệnh tối đa ở mỗi bảng.

| Khoản | 301 lệnh | 64 lệnh | Giữ lại |
|---|---|---|---|
| `net_mqtt` (bước `audio`) | 836 KB | 344 KB | **480 KB** đo |
| vùng chấm ctc (phần của bước `models` ngoài ảnh và tensor) | ~890 KB | ~314 KB | **576 KB** đo |
| bảng lexicon `svc_listen` | 152 KB | 33 KB | ~119 KB tính |
| **Cộng** | | | **≈ 1,2 MB** |

- PSRAM còn khi nghe lên khoảng 3,0 MB với tối đa 64 lệnh. Bộ demo `host/sets/demo_vi.json` có 47 lệnh, bộ mặc định 10.
- KẾ HOẠCH §6.6 ghi `net_mqtt` khoảng 0,2 MB (vùng JSON 2 × 48 KB, ô bộ lệnh 2 × 53 KB); số đo là 836 KB.
- b0 của `speaker` cần thêm ~3,86 MB (ảnh 1,72 MB, tensor 2,14 MB; E11-T26). Với tối đa 64 lệnh vẫn thiếu ~0,85 MB.
  Để trọng số b0 ở flash thì b0 chỉ cần 2,14 MB. Số đo trên firmware có `speaker` ở bảng dưới: còn 0,51 MB lúc nghe,
  không phải ~0,87 MB như ước.

Firmware `bench` @ `ab23bce` có `speaker`, đo tối 11/10 qua cầu RFC 2217:
- ảnh khuôn 8 của `990fe70`: `command_ctc`, bảng chuẩn hoá, `speaker` và nhóm chuẩn, hai mục sau để ở flash;
- tối đa 64 lệnh, `set.json` là bộ mặc định 10 lệnh, 19 cách đọc;
- `q_clean` 128 bước.

| Bước | PSRAM còn B | Dùng thêm | Gồm |
|---|---|---|---|
| `storage` | 8 384 752 | — | NVS, LittleFS |
| `wiring` | 7 767 184 | 603 KB | hàng đợi, kể cả ba hàng của danh sách người nói; `q_clean` 2 s, `sb_stream` 512 KB |
| `audio` | 7 570 772 | 192 KB | `net_mqtt`: vùng JSON và ô bộ lệnh cho tối đa 64 lệnh, đệm `down/speakers` |
| `models` | 1 292 308 | 6 131 KB | phần ảnh chép vào PSRAM, tensor `command_ctc` 326 KB, tensor b0 2 135 KB, vùng làm việc `spk_fbank` 58 KB, vùng chấm ctc |
| `front` | 1 292 308 | 0 | `dsp_afe` ở RAM nội |
| `listen` | **538 004** | 737 KB | `svc_listen`: vòng 512 bước, bảng lexicon, vòng PCM và hai ô xác minh của `speaker`, 8 mẫu giọng |

- Trước khi `q_clean` lên 128 bước, bước `listen` còn 571 796 B. 64 bước thêm tốn đúng 33 792 B.
- RAM nội còn 96 023 B lúc nghe, mảnh liền lớn nhất 55 296 B.

Firmware `bench` @ `83789d9` với b3 (ADR-0028), đo 28/10 qua cầu RFC 2217, Wi-Fi chưa nối:
- bảng phân vùng một khe app, `models_0` 11 MB;
- ảnh 4 mục, cả `command_ctc` lẫn `speaker` để ở flash, 0 KB chép vào PSRAM;
- tối đa 64 lệnh, bộ mặc định.

| Bước | PSRAM còn B | Dùng thêm | RAM nội còn B, mảnh liền lớn nhất |
|---|---|---|---|
| `storage` | 8 384 752 | — | 200 055, 139 264 |
| `wiring` | 7 767 184 | 603 KB | 200 055, 139 264 |
| `audio` | 7 570 772 | 192 KB | 182 603, 139 264 |
| `models` | 2 777 080 | 4 681 KB: tensor `command_ctc` 314 KB, tensor b3 4 012 KB, vùng chấm ctc | 176 923, 131 072 |
| `front` | 2 777 080 | 0 | 90 903, **49 152** |
| `listen` | **2 022 720** | 737 KB | 90 615, **49 152** |

- PSRAM còn khi nghe lên 1,93 MB, từ 0,51 MB với b0 khi trọng số `command_ctc` còn chép vào PSRAM.
- **Vùng mượn 79 680 B của b3 (ADR-0025) không còn chỗ:** sau `dsp_afe`, mảnh RAM nội liền lớn nhất chỉ 49 152 B, mà
  vùng mượn là một khối liền xin ở mỗi lần chạy. Lúc ấy op đọc trọng số tại chỗ, cùng kết quả, chậm hơn. Wi-Fi nối thì
  RAM nội còn ít hơn.
