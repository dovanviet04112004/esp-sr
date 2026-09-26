# Độ trễ

Mỗi phép đo một mục: đo gì, trên bản dựng nào (profile, commit, bản thư viện đã ghim), bao nhiêu lượt,
trung bình và đỉnh. Số ở `budget.md` là bản rút gọn của các mục ở đây.

## 1. `dl_fft` so với `esp-dsp` (E6-T3)

Board B, `dsp_spec/test_apps/unit` dựng `-O2` (`COMPILER_OPTIMIZATION_PERF`, như profile `bench`), IDF 6.0.2, `dl_fft` 0.7.0, `esp-dsp` 1.8.2, @e0ed592. Trung bình 1000 lượt sau một lượt làm nóng, qua lớp bọc của `dsp_spec` (gồm cả bước xếp lại vạch; với `esp-dsp` gồm cả bước tách phổ thực của repo).

| Phép | Điểm | Kiểu | `dl_fft` µs | `esp-dsp` µs |
|---|---|---|---|---|
| thuận | 256 | f32 | 56,2 | 70,0 |
| nghịch | 256 | f32 | 67,2 | 75,9 |
| thuận | 512 | f32 | **118,2** | 148,9 |
| nghịch | 512 | f32 | **135,9** | 160,7 |
| thuận | 1024 | f32 | 259,6 | 315,5 |
| nghịch | 1024 | f32 | 297,2 | 339,1 |
| STFT một bước (cửa sổ + thuận) | 512 | f32 | 143,3 | 174,1 |
| iSTFT một bước (nghịch + chồng cộng) | 512 | f32 | 164,7 | 189,5 |
| log-mel 40 dải trên phổ có sẵn | — | f32 | 61,6 | 61,6 |
| thuận, gọi thẳng thư viện | 512 | int16 | 13,6 | 32,9 (FFT phức 256 + đảo bit, chưa tách phổ thực) |

| Độ chính xác, 512 điểm | `dl_fft` | `esp-dsp` |
|---|---|---|
| sai số lớn nhất so với DFT `double`, trên đỉnh phổ | 1,2e-7 | 8,1e-7 |
| sai số lớn nhất của nghịch(thuận) | 1,2e-7 | 9,5e-7 |
| STFT phân tích rồi tổng hợp, 64 bước ồn trắng | 136,3 dB | 120,1 dB |

| RAM nội, 512 điểm | `dl_fft` | `esp-dsp` |
|---|---|---|
| vùng làm việc người gọi cấp | 2 080 B | 4 144 B |
| bảng thư viện tự cấp ở lần init đầu | 6 244 B | 1 988 B (bảng xoay pha nằm sẵn trong flash) |

Một khung của chuỗi (§3.1: hai phân tích, một tổng hợp) tốn 2 × 143,3 + 164,7 = **451 µs ≈ 2,8% một nhân** với `dl_fft`, so với ~510 µs ước từ số của hãng. Quyết định ở ADR-0002.

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

## 4. Luồng tiếng khi máy nhận nghẽn (E5-T10)

Board B chạy `main` bản `dev`, luồng `mode 2` (ch0 ch1, 64 KB/s) tới `srhost.stream_rx` trong container Docker
Desktop, cổng 7700 publish trên Windows như broker. Chặn bằng `docker pause` máy nhận giữa chừng. Số khung đọc ở
`gaps.txt` của máy nhận; `framesDropped`, `dmaOverflows`, `streamDropped` và heap đọc ở `heartbeat`.

| Phép thử | Bản dựng | Khung nhận / mất ở host | `streamDropped` | `framesDropped` · `dmaOverflows` | Ghi chú |
|---|---|---|---|---|---|
| 20 s, không chặn | `dev` @ `4e19b0d` | 1251 / 0 | 0 | 0 · 0 | 62,5 khung/s đúng nhịp |
| 30 s, đóng băng máy nhận 5 s | `dev` @ `4e19b0d` | 1876 / 0 | 0 | 0 · 0 | proxy Docker trên Windows đệm hết 5 s: không hở |
| 40 s, cắt mạng container 5 s | `dev` @ `4e19b0d` | 2501 / 0 | 0 | 0 · 0 | kết nối không đứt, như trên |
| 60 s, đóng băng máy nhận 30 s | `dev` @ `253ddf7` | 3185 / 566 (6 chỗ hở) | 564 | 0 · 0 | gửi hỏng sau ~12 s, nối lại mỗi giây; 2 khung mất lúc đóng socket |

Chỗ hở chỉ xuất hiện khi bộ đệm trên đường đi (proxy Windows, TCP của lwIP, 64 KB `sb_stream` ≈ 1 s) đầy; board không
chậm khung nào ở mọi phép thử. Phép "chặn mạng 5 s" thật ở phía Wi-Fi của board làm cùng lúc với E5-T8 (tắt hotspot).
RAM nội thấp nhất lúc nối lại liên tục: 45 895 B (`ram.md` §3).
