# esp-sr

Bộ nghe và nói tiếng Việt chạy trọn trên ESP32-S3 với một hoặc hai micro: thu, làm sạch, dò từ đánh thức, nhận
lệnh, nói lại, và gửi số liệu về máy tính để xem và chấm. Viết lại bằng mã đọc được trên `esp-dl` và FFT viết tay
(`dl_fft` chọn được bằng Kconfig); không dùng thư viện nhị phân của ESP-SR.

| Đọc gì | Ở đâu |
|---|---|
| Vì sao làm, bậc thang thuật toán | [docs/tong_quan_version_5.md](docs/tong_quan_version_5.md) |
| Kiến trúc — nguồn sự thật | [docs/KE_HOACH_esp_sr_esp32s3.md](docs/KE_HOACH_esp_sr_esp32s3.md) |
| Việc cần làm, điều kiện xong | [docs/TASKS.md](docs/TASKS.md) |
| Luật chia task và kiểm lỗi đồng thời | [docs/FREERTOS.md](docs/FREERTOS.md) |

Trạng thái: sản phẩm chạy trên board B. Nó thức bằng "vừng ơi", nhận lệnh bằng đường `ctc`, kiểm người nói bằng
ReDimNet2 b0, và nhận diện vật thể bằng YOLO26n trên ảnh gửi từ máy tính. Kết quả đi lên server qua MQTT. Tiến độ từng
việc ở [docs/TASKS.md](docs/TASKS.md).

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

ESP32-S3-WROOM-1, 16 MB flash, 8 MB PSRAM, hai micro INMP441 ở GPIO 19/20/16, chưa có loa; nạp qua CH340. Windows
giữ cổng COM: `make board-attach` bật cầu RFC 2217 để mọi lệnh make trong WSL tới được board, `make board-detach` tắt
cầu (KẾ HOẠCH §2.1). GPIO 19/20 trùng USB của chip nên console chỉ qua UART — xem KẾ HOẠCH §2.2.

## Cài một lần trên máy phát triển

```bash
uv tool install pre-commit && pre-commit install        # kiểm mỗi commit, ci_status mỗi lần push
gh auth login                                           # GitHub: bản sao công khai chạy CI
git remote add github https://github.com/dovanviet04112004/esp-sr.git
git remote set-url --add --push origin http://192.168.40.80:3000/vanviet/esp-sr.git
git remote set-url --add --push origin https://github.com/dovanviet04112004/esp-sr.git
git config credential.http://192.168.40.80:3000.helper \
  '!f() { test "$1" = get && echo username=vanviet && echo "password=$(cat ~/.config/esp-sr/gitea_token)"; }; f'
```

Token Gitea (quyền `write:repository`) đặt ở `~/.config/esp-sr/gitea_token`, quyền 600. Sau đó
`git push origin` đẩy lên cả hai nơi, GitHub Actions chạy, và trạng thái hiện trên commit ở Gitea —
xem KẾ HOẠCH §4.1.
