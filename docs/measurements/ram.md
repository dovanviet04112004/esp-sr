# RAM

## 1. Vùng làm việc theo module (E3-T3, rồi đo lại ở bước 4 của mỗi module)

| Component | Module | `sizeof` trạng thái B | Vùng `hot` B | Vùng `cold` B | Ước hay đo |
|---|---|---|---|---|---|

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

## 3. Heap sau boot (E5-T11)

| Mốc | Heap nội còn B | Mảnh liền lớn nhất B | PSRAM còn B | Profile | Ngày |
|---|---|---|---|---|---|
| `heap_init` lúc khởi động, app chưa có gì, chưa Wi-Fi | 346292 (323984 + 22308) | 323984 | 8 388 608 (pool 8192 KiB) | `dev` @ `32e9b55` | 26/09 |
| sau `sys_storage_init` (NVS + LittleFS) | 277099 | 217076 | 8 384 660 | `dev` @ `cb082c8` | 26/09 |
| sau `app_wiring_init` (pool 8 × 1540 B nội, 38 064 B hàng đợi ở PSRAM) | 277099 | 217076 | 8 346 260 | `dev` @ `cb082c8` | 26/09 |
| sau `drv_audio_init` (DMA 8 × 256 mẫu) — trước sáu task (29 696 B ngăn xếp tĩnh, đã nằm trong .bss) | 258415 | 217076 | 8 346 260 | `dev` @ `cb082c8` | 26/09 |
| Wi-Fi vào mạng (`net_task`: link up) | 139183 | 98292 | — | `dev` @ `b38ffb3` | 26/09 |
| MQTT chạy, heartbeat đầu tiên, 5 phút sau boot: còn / thấp nhất | 139047 / 133431 | — | 8 317 652 / 8 317 224 | `dev` @ `b38ffb3` | 26/09 |

Đọc từ log `heap_init` của board B qua CH340. Đây là **trần** cho mọi thứ ở KẾ HOẠCH §6.5 cộng lại, trước khi Wi-Fi và lwIP lấy phần của chúng; nó khớp khoảng ước 300–340 KB của §6.5.

Wi-Fi, lwIP và esp-mqtt lấy khoảng **119 KB** RAM nội (258 415 → 139 183 B), vượt khoảng ước 50–90 KB của KẾ HOẠCH §6.5 với cấu hình đệm mặc định của IDF (32 đệm RX động, 32 TX động, 10 RX tĩnh × 1600 B). Đây là số đo cho hàng "Wi-Fi + lwIP" của §6.5; đường lùi của §6.5 là giảm đệm Wi-Fi.
