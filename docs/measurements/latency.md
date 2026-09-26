# Độ trễ

Mỗi phép đo một mục: đo gì, trên bản dựng nào (profile, commit, bản thư viện đã ghim), bao nhiêu lượt,
trung bình và đỉnh. Số ở `budget.md` là bản rút gọn của các mục ở đây.

## 1. `dl_fft` so với `esp-dsp` (E6-T3)

| Phép | Điểm | Kiểu | `dl_fft` µs | `esp-dsp` µs | RAM B |
|---|---|---|---|---|---|

## 2. Thay `set.json` qua `sys_storage_write_file` (E5-T5)

Một lần thay trọn file 12 288 B: ghi `set.json.tmp`, đóng, đổi tên. Đo bằng `esp_timer` quanh lời gọi, trong
`sys_storage/test_apps/unit` lúc vừa khởi động (không Wi-Fi, không I2S), board B, flash QIO 80 MHz.

| Bản dựng | Lượt | Kết quả µs |
|---|---|---|
| test app `unit`, `-Og`, IDF 6.0.2, `joltwallet/littlefs` 1.22.3, @e755cb3 | 3 lần nạp, mỗi lần 1 mẫu | 157 365 · 157 423 · 166 320 |

Đây là thời gian cả lần thay file, **không phải** một khoảng tắt cache liền mạch; khoảng dài nhất mà
nhân 1 bị đóng băng là một lần xoá sector trong đó, và số khung mất khi ghi trong lúc nghe là phép kiểm
riêng của E14-T7 (KẾ HOẠCH §5.5 luật 9).

## 3. MQTT: board mất điện tới lúc broker báo `OFFLINE` (E5-T9)

Board B giữ trong reset bằng RTS (không kịp gửi DISCONNECT), `sr-emqx` trên Docker Desktop, keepalive 15 s, một client `srhost` đăng ký `sr/+/up/#`.

| Bản dựng | Lượt | Kết quả |
|---|---|---|
| `dev` @ `b38ffb3`, `espressif/mqtt` 1.1.0, EMQX 6.3.1 | 1 | `OFFLINE` sau ~19 s; thả reset thì `ONLINE` sau ~6 s và heartbeat ngay theo |

19 s nằm trong mức 1,5 × keepalive = 22,5 s mà broker chờ trước khi phát di chúc.
