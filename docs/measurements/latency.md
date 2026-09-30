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
| 600 s `mode 5`, `sb_stream` 64 KB, không chặn — hai lượt (E5-T11) | `dev` trước `8653c9b` | — / 74 và — / 150 | 74 · 150 | 0 · 0 | đường tới máy nhận khựng 0,4–0,8 s vài lần; 64 KB chỉ giữ 0,67 s `mode 5`; tắt tiết kiệm điện radio không hết khựng |
| 600 s `mode 5`, `sb_stream` 512 KB, không chặn (E5-T11) | `dev` @ `3d3335d` | 37 500 / 0 | 0 | 0 · 0 | 512 KB giữ ~5 s `mode 5`; `cleanDropped` 0; RSSI −56 … −58 dBm; phiên `20260926_home_002` |

Chỗ hở chỉ xuất hiện khi bộ đệm trên đường đi (proxy Windows, TCP của lwIP, 64 KB `sb_stream` ≈ 1 s) đầy; board không
chậm khung nào ở mọi phép thử. Phép "chặn mạng 5 s" thật ở phía Wi-Fi của board làm cùng lúc với E5-T8 (tắt hotspot).
RAM nội thấp nhất lúc nối lại liên tục: 45 895 B (`ram.md` §3).

## 5. Biquad của `hpf`: kernel `esp-dsp` so với viết tay (E7-T1, ADR-0003, ADR-0004)

`test_apps/bench_afe`, profile `bench`, board B, nhân 1, 2000 bước sau 16 bước làm nóng, hai kênh × 256 mẫu, tại
`d05636a`; chạy lại bằng `make bench-board` (các dòng `BENCH_ALT`).

| Cách lọc | µs trung bình | µs đỉnh | Sai số float32 so với float64, hum 50 Hz −10 dBFS + một chiều −20 dBFS |
|---|---|---|---|
| `dsps_biquad_f32` của `esp-dsp` (hợp ngữ S3, dạng II) | 36,9 | 41,3 | 7,7 LSB |
| `dsps_biquad_sf32` hai kênh xen kẽ, chỉ kernel | 36,6 | 40,8 | 7,7 LSB (cùng dạng II) |
| `dsps_biquad_sf32` kèm chép xen kẽ vào và ra | 51,7 | 55,9 | như trên |
| Dạng II chuyển vị viết tay, C | 36,5 | 40,6 | 0,7 LSB |
| **Module `hpf` sau ADR-0004: dạng II chuyển vị, hệ số ở biến cục bộ (`e07ef05`) — đang dùng** | **36,8** | 41,0 | 0,7 LSB |
| Cùng module khi đọc hệ số qua con trỏ trạng thái | 53,9 | 58,1 | 0,7 LSB |

Mọi cách lọc đều ~17 chu kỳ mỗi mẫu: biquad là đệ quy, mỗi mẫu chờ kết quả của mẫu trước, nên độ trễ của bộ tính
dấu phẩy động quyết định, không phải số lệnh. Đọc hệ số qua con trỏ làm chậm thêm 17 µs: mảng mẫu có thể trùng vùng với
mảng hệ số, nên trình biên dịch nạp lại năm hệ số sau mỗi lần ghi một mẫu; chép chúng ra biến cục bộ trước vòng lặp
bỏ được việc ấy mà không đổi phép tính. Ước "< 20 µs hai kênh" của KẾ HOẠCH §3.3 thấp hơn thực tế gần hai lần;
37 µs là 0,2% một bước 16 ms.

Độ lệch một chiều sau lọc (bản soi gương float32, khớp C từng bit trên máy tính): một chiều −0,5 LSB của INMP441 về đúng 0
sau 1 s; một chiều −20 dBFS còn −102,4 dBFS, tức giảm 82 dB.

## 6. `balance` và phép gộp nhân-cộng (E7-T2, ADR-0005, ADR-0006)

Bản sao tạm của `bench_afe` có thêm `esp-dsp` (không vào repo), mã `dsp_afe` tại `9b18fec`, profile `bench`, board B,
nhân 1, 2000 bước sau 16 bước làm nóng; ba lượt, lệch giữa các lượt dưới 0,05 µs. Vạch vào ồn đều ±50, hệ số ±0,5.

| Cách tính `balance`, 257 vạch | µs trung bình | µs đỉnh | Chu kỳ mỗi vạch | So với bản Python |
|---|---|---|---|---|
| `esp-dsp`: bốn `dsps_mul_f32` bước 2 vào đệm tạm, rồi `dsps_sub_f32` và `dsps_add_f32` | 52,3 | 56,6 | 48,9 | khớp từng bit |
| Viết tay, GCC gộp nhân-cộng (`madd.s` / `msub.s`) | 16,2 | 20,6 | 15,1 | lệch tới 1,9e-6, ở 125 trên 514 số |
| **Viết tay, `-ffp-contract=off` — đang dùng** | 18,4 | 22,7 | 17,1 | khớp từng bit |

`hpf` cùng lần đo, hai kênh × 256 mẫu, ồn đều ±0,125:

| Cách dựng `hpf` | µs trung bình | So với bản không gộp |
|---|---|---|
| GCC gộp nhân-cộng | 36,8 | lệch ở 99,7% số mẫu, tối đa 2,0e-6 |
| **`-ffp-contract=off` — đang dùng** | 40,9 | — |

Lệnh `madd.s` của S3 làm tròn một lần cho cả tích lẫn tổng, nên bản gộp khác bản ghép từ `esp-dsp` (mỗi phép làm tròn
riêng), còn bản không gộp trùng nó từng bit. GCC chỉ gộp từ `-O2`; ở `-Og` hai bản như nhau, nên parity dựng bằng cờ của
`bench` mới kiểm được mã chạy thật. Tắt gộp tốn 4,1 µs ở `hpf` và 2,2 µs ở `balance`, tức 0,04% một bước 16 ms.

## 7. Nạp ảnh model lúc boot (E11-T9)

Board B, `ai_engine/test_apps/unit` (profile mặc định `-Og`), IDF 6.0.2, 28/09. Slot 1 chứa hai mục: 1 MB (cỡ mạng
`command`, KẾ HOẠCH §6.6) và 4 000 B. `ai_engine_load` map slot, chép từng mục sang PSRAM rồi băm sha256 bản chép bằng
`esp_sha`, một lượt.

| Nội dung | Thời gian | Tốc độ | PSRAM giữ |
|---|---|---|---|
| 1 027 KB, hai mục | 87 ms | 12,0 MB/s (đọc flash + chép + SHA) | 1 027 KB, đúng tổng hai mục |

Ảnh đủ bốn model (~2,1 MB theo §6.6) vì thế thêm cỡ 180 ms 🔬 vào lúc boot. Ảnh sai lưới và ảnh lật một bit bị từ chối, và
PSRAM trả về đúng số trước lượt nạp.

## 8. TCN chạy dòng trên esp-dl (E11-T10)

Board B, `ai_engine/test_apps/unit` (profile mặc định `-Og`), IDF 6.0.2, esp-dl 3.3.11, ESP-PPQ 1.3.11, 28/09. Mạng là
TCN của `configs/models/wake.yaml` với trọng số ngẫu nhiên có seed: 40 dải vào, 32 kênh, 6 khối giãn 1 … 32, kernel 3,
trường nhìn 127 bước, `.espdl` 48 KB. Dựng bằng `make ai-probe`, đo bằng `make ai-unit`.

| Đo | Kết quả |
|---|---|
| 200 bước đẩy từng khung, so mô phỏng cả chuỗi của ESP-PPQ | chênh int8 lớn nhất **0** |
| `model->test()` (bước đầu, lưu trong `.espdl`) | qua |
| Một bước (`model->run()`) | **427 µs** trung bình, 493 µs đỉnh |
| Dựng mạng lúc nạp, gồm một bước khởi động | 24,7 ms; RAM nội 0 B, PSRAM 23,5 KB |
| Chạy lại cùng đầu vào không `reset` (đối chứng âm) | chênh 27: bộ đệm giữ đúng các bước trước |

Bản đầu dùng `auto_streaming` của ESP-PPQ: phép cộng dư nhận bộ đệm cả cửa sổ, đầu ra phình tới 127 bước và
`model->test()` báo sai hình. Bộ đệm gắn theo từng tích chập (`ptq_espdl.cache_each_causal_conv`) cho kết quả trên.

Lượt 30/09 tại `b85f271`, cùng app, với 64 kênh như `wake.yaml` hiện giữ: chênh int8 lớn nhất 0, một bước **1 994 µs**
trung bình, 2 032 µs đỉnh; dựng mạng 26,3 ms, PSRAM 31,4 KB.

## 10. DS-CNN của `kws` trên esp-dl, ba cỡ (E11-T17)

Board B, `ai_engine/test_apps/unit` (profile mặc định `-Og`; nhân chập của esp-dl là mã dịch sẵn), IDF 6.0.2, esp-dl
3.3.11, ESP-PPQ 1.3.11, `b85f271`, 30/09. Mỗi cỡ của `configs/models/command_kws.yaml` với trọng số ngẫu nhiên có seed,
một cửa sổ 94 bước × 43 chiều vào, 11 lớp ra, trọng số và tensor ở PSRAM. Dựng bằng `make ai-probe`, đo bằng
`make ai-unit`: một lần chạy không tính giờ, rồi 10 lần trên nhân 0.

| Cỡ | Kênh | MAC mỗi cửa sổ | `.espdl` | PSRAM của mạng dựng xong | Chênh int8 so với mô phỏng | Một cửa sổ, trung bình | Đỉnh |
|---|---|---|---|---|---|---|---|
| S | 64 | 22,0 triệu | 40,6 KB | 148 KB | 0 | **41,9 ms** | 41,9 ms |
| M | 172 | 79,7 triệu | 156 KB | 458 KB | 0 | 277,0 ms | 277,0 ms |
| L | 276 | 230,0 triệu | 438 KB | 730 KB | 0 | 2 027 ms | 2 059 ms |

`model->test()` qua ở cả ba cỡ. Ngân sách của KẾ HOẠCH §3.12 là một lần chạy ≤ 100 ms trên nhân 0 sau khi `vad` tắt:
**chỉ S vừa**, M gấp 2,8 lần, L gấp 20 lần. Tốc độ rơi theo cỡ: S 0,53, M 0,29, L 0,11 tỉ MAC mỗi giây. Tensor
ra của tích chập đầu là 66 KB ở S, 348 KB ở M và 558 KB ở L, đều trong PSRAM, nên có thể thời gian đi vào đọc ghi
PSRAM; 172 và 276 kênh cũng không chia hết cho 16 làn SIMD. Hai nguyên nhân này chưa đo tách.

## 9. Dò lưới của `doa`: xoay pha dồn viết tay so với bảng và `esp-dsp` (E8-T1, ADR-0008)

Bản sao tạm của `bench_afe` có thêm `esp-dsp` 1.8.2 (không vào repo), mã `dsp_afe` tại `a88a6fa` cộng `doa.c` của E8-T1,
profile `bench`, board B, nhân 1, 2000 lượt, 28/09. Chỉ phần dò: 46 góc nửa lưới × số vạch của dải, phổ chéo đã chuẩn
hoá là pha ngẫu nhiên; sai số là lớn nhất trên 91 góc của `R(θ)` so với bản float64 dùng `cos`/`sin` của libm.

| Dải | Vạch | Xoay dồn viết tay | Viết tay, hai góc xen kẽ | Bảng RAM nội + `dsps_dotprod_f32` | Bảng PSRAM + `dsps_dotprod_f32` | Bảng |
|---|---|---|---|---|---|---|
| 200 Hz – 3,81 kHz | 115 | 317,3 µs, sai 6,9e-6 | 658,6 µs, cùng bit | 193,4 µs, sai 3,0e-6 | 193,4 µs | 42 320 B |
| 200 Hz – 8 kHz | 250 | 680,8 µs, sai 1,1e-4 | 1 425,2 µs | 401,5 µs, sai 1,0e-5 | 736,9 µs | 92 000 B |
| 1 – 8 kHz | 225 | 613,4 µs, sai 7,1e-5 | 1 283,4 µs | 363,0 µs, sai 1,3e-5 | 668,9 µs | 82 800 B |
| **2 – 8 kHz** | 193 | **527,2 µs, sai 7,0e-5** | 1 101,6 µs | 313,7 µs, sai 5,4e-6 | 428,7 µs | 71 024 B |

Cả module (`dsp_afe_doa_process`, cùng lần đo): gộp phổ chéo 13,2 / 28,6 / 25,7 / 22,1 µs mỗi bước; gộp và dò 387,0 /
826,5 / 745,1 / 640,9 µs, theo bốn dải trên. Sai 7e-5 trên một đáp ứng cỡ 193 là 4e-7 tương đối, nhỏ hơn nhiều khoảng
cách giữa hai góc kề nhau của lưới. Xen kẽ hai góc trong một vòng chậm gấp đôi, có lẽ vì 14 biến float sống cùng lúc tràn khỏi 16 thanh ghi FPU
(chưa đọc mã máy). Bảng ở PSRAM chỉ nhanh ngang RAM nội khi bảng lọt cache
dữ liệu (42 KB); lớn hơn thì trượt cache, và ở 250 vạch chậm hơn cả bản viết tay.
