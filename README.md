# esp-sr

Bộ nghe và nói tiếng Việt chạy trọn trên ESP32-S3 hai micro: thu, làm sạch, dò từ đánh thức, nhận lệnh,
nói lại, và gửi số liệu về máy tính để xem và chấm. Viết lại bằng mã đọc được trên `esp-dsp`, `dl_fft`,
`esp-dl`; không dùng thư viện nhị phân của ESP-SR.

| Đọc gì | Ở đâu |
|---|---|
| Vì sao làm, bậc thang thuật toán | [docs/tong_quan_version_5.md](docs/tong_quan_version_5.md) |
| Kiến trúc — nguồn sự thật | [docs/KE_HOACH_esp_sr_esp32s3.md](docs/KE_HOACH_esp_sr_esp32s3.md) |
| Việc cần làm, điều kiện xong | [docs/TASKS.md](docs/TASKS.md) |
| Luật chia task và kiểm lỗi đồng thời | [docs/FREERTOS.md](docs/FREERTOS.md) |

Trạng thái: đang lập kế hoạch, chưa mở Cửa 0.

## Khối

| Thư mục | Nội dung |
|---|---|
| `contracts/` | lưới thời gian, dàn micro, payload MQTT, khuôn luồng tiếng, bộ lệnh, vector vàng |
| `ml/` | Python: bản soi gương thuật toán, dữ liệu, huấn luyện, xuất model |
| `firmware/` | ESP-IDF v6: `dsp_*` thuật toán thuần, `lang_vi` luật ngôn ngữ, `ai_engine` mô hình học, `svc_*` ghép |
| `host/` | Python: xem, chấm, thu dữ liệu từ board |
| `deploy/` | broker MQTT |
| `tools/` | sinh code từ `contracts/`, kiểm comment, tầng, độ thuần |

## Board

ESP32-S3-WROOM-1, 16 MB flash, 8 MB PSRAM, hai micro I2S ở GPIO 19/20/16, nạp qua CH340
(`/dev/ttyUSB0`). GPIO 19/20 trùng USB của chip nên console chỉ qua UART — xem KẾ HOẠCH §2.2.
