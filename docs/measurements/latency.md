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
| `bss_live` 15 s `mode 6` (128 KB/s), hotspot `esp` đặt gần board, hai lượt (E8-T5) | `bss_live` @ `5518509`, `9a6453b` | 938 / 0 cả hai | 0 | — · 0 | sống và lối ra trễ 4 s |
| `bss_live` `mode 6`, `TPLINK24G` (2,4 GHz, kênh 1), board −73 dBm, máy tính cũng ở 2,4 GHz kênh 1 (E8-T5) | `bss_live` @ `1741128` | 1 374 / 3 (1 chỗ hở) | 0 | — · 0 | một lần gửi khựng quá hạn 1 s; nối lại mất 3 khung còn trong socket |
| như trên, −72 dBm, hai lượt liền nhau, hạn gửi 4 s (E8-T5) | `bss_live` @ `2555e56` | 938 / 0, rồi 289 / — | 0, rồi 193 | — · 0 | lượt hai gửi chỉ được ~1/3 nhịp thật: `sb_stream` 1 MB đầy sau 10 s, kết nối không đứt mà chậm |

Trên `TPLINK24G`, board B ở −72 dBm và máy tính cùng ở 2,4 GHz kênh 1, nên mỗi khung đi trên không hai lần trên cùng một
kênh của một văn phòng có 35 mạng. Ba lượt cho một lượt sạch, một lượt khựng quá 1 s, một lượt chỉ được một phần ba nhịp
thật; hotspot `esp` đặt gần board sạch cả hai lượt. Cùng router, máy tính ở `TPLINK5G` (5 GHz, cùng mạng 192.168.40.0/24)
thì đường board → router mới dùng 2,4 GHz 🔬.

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

## 11. Encoder kiểu MultiNet7 của `ctc` chạy dòng trên esp-dl (E11-T12)

Board B, `ai_engine/test_apps/unit` (profile mặc định `-Og`), IDF 6.0.2, esp-dl 3.3.11, ESP-PPQ 1.3.11 cộng hai bản vá
nhánh `command_ctc` khai (`compress/quant/esp_ppq_patches.py`), `f1302f1`, 30/09. Mạng của `configs/models/command_ctc.yaml`
với trọng số ngẫu nhiên có seed, hệ số chuẩn hoá rút đều trong 0,5–1,5, vào 40 log-mel + 3 cao độ mỗi hop; lượng tử bậc 1
và 2 mặc định của KẾ HOẠCH §3.14. Dựng bằng `make ai-probe`, đo bằng `make ai-unit`.

| Mạng | Một bước | `.espdl` | Chênh int8 so với mô phỏng cả chuỗi | Một bước, trung bình / đỉnh | Mỗi 32 ms audio | Dựng mạng | PSRAM | Không `reset` (đối chứng âm) |
|---|---|---|---|---|---|---|---|---|
| `ctc_lay`: một lớp của tầng đầu (bề rộng 128, nhân 17) | 1 hop, 256 bước | 356 KB | **0** | 5 030 / 5 151 µs | 10,1 ms | 36 ms | 40,1 KB | chênh 13 |
| `ctc_net`: cả mạng (3 tích chập 2D, 6 lớp, đầu CTC 45 lớp; 1,98 triệu tham số) | 16 hop (256 ms), 16 bước | 2 276 KB | **0** | 46 484 / 46 830 µs | **5,8 ms** | 331 ms | 269,6 KB | chênh 46 |

`model->test()` qua ở cả hai. Ngân sách của `ctc` ở KẾ HOẠCH §3.3 là 11–18 ms mỗi 32 ms trong cửa sổ lệnh: cả mạng cỡ
MultiNet7 dùng 5,8 ms, còn chỗ để rộng hơn hay sâu hơn theo chất lượng (ADR-0013). Một lớp đẩy từng hop tốn 10,1 ms mỗi
32 ms, gấp đôi cả mạng đẩy 16 hop một lần: mỗi bước đọc trọng số từ PSRAM một lần cho mọi khung của bước, nên bước dài là
điểm chạy của mạng. `ctc_net` còn 12 `Transpose` quanh chuẩn hoá của các lớp trong tầng (`ctc_lay` không còn cái nào);
phần thời gian của chúng chưa đo tách.

Ba chỗ phải sửa trước khi board khớp mô phỏng, theo thứ tự tìm ra:

- Chuẩn hoá viết `x / sqrt(mean(x * x))` theo kênh thành chuỗi int8: bình phương làm tròn bước 0,125, và `Div` int8 có số
  chia một phần tử dựng bảng tra một lần theo bước đầu. Viết theo dạng ESP-PPQ gộp thành `RMSNormalization` (tính float
  trong esp-dl): trên PC, sai số int8 so với float của một lớp giảm từ 0,0444 xuống 0,0299 trung bình, 1,224 xuống 0,536
  lớn nhất.
- Hai lỗi xuất của ESP-PPQ 1.3.11, vá ở `esp_ppq_patches`: input đồ thị bị hai op đọc ở hai số mũ không được đổi mũ
  (`model->test()` của `ctc_lay` lệch 8 bậc), và bộ đệm lấy trục khung của input đồ thị, là trục kênh khi `Slice` đứng
  trước các tích chập (esp-dl assert ở `Reshape` lúc dựng `ctc_net`).
- Bản ghi của probe: ESP-PPQ giữ input của `ctc_net` theo thứ tự (chiều, hop) của ONNX vì chỉ `Slice` đọc nó, còn output
  theo (khung, lớp); probe đọc bố cục từ dữ liệu kiểm lưu trong `.espdl` và so byte bước đầu trước khi ghi.

## 12. Hai ứng viên của khe `ns` chạy dòng có trạng thái trên esp-dl (E9-T10)

Board B, `ai_engine/test_apps/unit` dựng với thiết lập trình biên dịch của profile bench (`-O2`, KẾ HOẠCH §4.5.8), IDF
6.0.2, esp-dl 3.3.11, ESP-PPQ 1.3.11, 01/10. Mỗi ứng viên của `configs/models/ns.yaml` với trọng số ngẫu nhiên có seed,
dựng thành đồ thị một bước: đặc trưng đã chuẩn hoá và trạng thái của từng GRU vào, đầu ra và trạng thái sau bước ra; sau
mỗi bước board chép trạng thái ra về trạng thái vào. Lượng tử bậc 1 và 2 mặc định (KẾ HOẠCH §3.14). Dựng bằng
`make ai-probe`, đo bằng `make ai-unit`: 200 bước, mỗi bước tính giờ.

| Mạng | Vào / ra mỗi bước | GRU | `.espdl` | PSRAM lúc dựng | Chênh int8 so với mô phỏng từng bước, ra / trạng thái | Một bước, trung bình / đỉnh | Không chép trạng thái (đối chứng âm) |
|---|---|---|---|---|---|---|---|
| RNNoise-16k (82 603 tham số) | 31 / 19 | 24, 48, 96 | 104 KB | 83,0 KB | **0** / 0 | 4 809 / 4 858 µs | chênh 15 |
| NSNet-16k S (161 248) | 256 / 256 | 96, 96 | 173 KB | 54,5 KB | **0** / 0 | **3 019** / 3 084 µs | chênh 15 |
| NSNet-16k M (264 064) | 256 / 256 | 128, 128 | 276 KB | 56,3 KB | **0** / 0 | 4 414 / 4 475 µs | chênh 29 |
| NSNet-16k L (324 688) | 256 / 256 | 144, 144 | 337 KB | 57,1 KB | 1 / 1, từ bước 112 | 5 054 / 5 127 µs | chênh 15 |

`model->test()` qua ở cả bốn. Dựng mạng 60–89 ms, RAM nội 0 B. Trạng thái vào và ra của từng GRU cùng một số mũ, vì
ESP-PPQ gắn `initial_h` và `Y_h` theo `Y` của GRU ấy; không có `initial_h`, GRU của esp-dl xoá trạng thái mỗi lần chạy.
Bộ quản lý bộ nhớ của esp-dl được đặt một trạng thái ra vào chỗ của một trạng thái vào đã đọc xong: chép thẳng từng cặp
thì ghi đè trạng thái chưa chép, NSNet-16k S lệch 83 bậc từ bước 1. Mọi trạng thái phải chép ra chỗ tạm trước rồi mới
ghi về; bộ nối của E9-T5 và E9-T11 làm đúng như vậy.

Trần của ADR-0014 là nsnet2 của ESP-SR trên cùng board, cùng thiết lập trình biên dịch: 4 182 µs trung bình, 395 760 B
PSRAM. Tính cả `.espdl` nằm trong ảnh đã nạp ở PSRAM:

- NSNet-16k S dùng 3,0 ms và 227 KB, lọt trần; M dùng 4,4 ms, vượt trần thời gian 6%; L dùng 5,1 ms và 394 KB, vượt
  trần thời gian 21%.
- Thời gian của NSNet-16k tăng gần theo số byte trọng số: 57, 63, 67 MB/s đọc từ PSRAM mỗi bước.
- RNNoise-16k tốn 4,8 ms dù ít phép tính nhất: độ dài 24, 31, 79 và 103 không chia hết cho 16 nên tích vô hướng của
  esp-dl chạy đường C, và mạng có 26 op nhỏ.

Cùng lượt, cùng thiết lập: TCN của §8 1 944 µs, `kws` S 41,6 ms và M 276,7 ms của §10, `ctc_lay` 4 899 µs và `ctc_net`
45 227 µs của §11, trong 4% so với số `-Og` đã ghi ở các mục ấy: thời gian nằm ở nhân dịch sẵn của esp-dl và ở PSRAM.

## 13. Chấm lệnh của `ctc` sau mạng (E11-T13)

Board B, `ai_engine/test_apps/unit` với cờ trình biên dịch của `sdkconfig.bench` (`-O2`, 240 MHz), IDF 6.0.2, 01–02/10.
Bộ lệnh mặc định của `contracts/commands/default_vi.json` qua `lang_vi` ba vùng (10 lệnh, 19 biến thể); 64 lệnh là bộ ấy
lặp lại tới `AI_ENGINE_COMMANDS_MAX`. Một cửa sổ 3 s nói lệnh đầu (`ctc_score.said`, 94 khung × 45 lớp), log-xác suất và
vùng làm việc ở PSRAM. Dựng bằng `make ai-probe`, đo bằng `make ai-unit`: một lần không tính giờ, rồi 10 lần trên nhân 0.
Mỗi cách khớp bản soi gương của nó từng bit, cả điểm từng lệnh lẫn quyết định, trên máy tính và board B.

| Cách tính | Commit | 10 lệnh, 19 biến thể | 64 lệnh, 124 biến thể |
|---|---|---|---|
| Miền log: hai, ba `exp` và một `log` double mỗi trạng thái mỗi khung | `af7d3ef` | 705,9 ms, đỉnh 744,7 | — |
| Miền xác suất, số mũ riêng từng trạng thái; bit của float đọc qua `memcpy` | `3c5fd74` | 43,5 ms | 257,8 ms |
| **Như trên, bit đọc qua `union` — đang dùng** | `a3091cb` | **14,7 ms** | **79,4 ms** |

Hai dòng dưới có đỉnh lệch trung bình dưới 0,01 ms. Miền log gọi khoảng 106 nghìn `exp` và `log` double mỗi lần chấm; S3
chỉ có FPU float nên double chạy giả lập, khoảng 6,7 µs mỗi lần. Miền xác suất tính `exp` một lần mỗi khung × lớp (4 230
lần, đa thức float32), còn mỗi trạng thái mỗi khung chỉ cộng, nhân và chỉnh số mũ. GCC cho S3 dịch `memcpy` 4 byte thành
một lời gọi hàm, năm, sáu lời gọi mỗi trạng thái mỗi khung, và vì thế không inline `normalized`; đọc bit qua `union` như
`ns_omlsa.c` để vòng trong không còn lời gọi nào. Suy từ hai cột của bản đang dùng: khoảng 3 ms cố định (bảng `exp` và
vòng tự do) cộng 0,62 ms mỗi biến thể, tức khoảng 0,37 µs (89 chu kỳ) mỗi trạng thái mỗi khung. Ngân sách của KẾ HOẠCH
§3.12 là ≤ 100 ms một lần chấm: 64 lệnh dùng 79,4 ms.

## 14. `ctc` đã học trên chip: chạy dòng, chấm và Cửa 3 (E11-T19)

Board B, `ai_engine/test_apps/unit` với cờ trình biên dịch của `sdkconfig.bench` (`-O2`, 240 MHz), IDF 6.0.2, esp-dl
3.3.11, ESP-PPQ 1.3.11 cộng bốn bản sửa của nhánh, `2b49747`, 02/10. Mạng là dòng `qat` của run
`20261002_128545c-dirty_64e7a4` (ADR-0015, `command.md` §2), xuất bằng `make ctc-deploy ROW=qat`; đo bằng
`make ai-unit CTC_RUN=<run> CTC_ROW=qat`.

| Đo | Kết quả |
|---|---|
| Dựng mạng | 308 ms; PSRAM 274 KB, RAM nội 0 B |
| 48 bước 16 hop (256 ms audio), so mô phỏng cả chuỗi | chênh int8 lớn nhất **0**; 44,8 ms trung bình, 45,1 ms đỉnh một bước |
| `_step` | 30 µs ở hop chỉ đệm; 46,0 ms trung bình, 46,4 ms đỉnh ở hop đủ khối 16 hop: **5,7 ms mỗi 32 ms audio** |
| `_score`, cửa sổ Cửa 3 tới 3 s, 10 lệnh, 19 biến thể, luật phần | **56,3 ms** trung bình, 64,9 ms đỉnh |
| Chấm riêng, cửa sổ 3 s (94 khung) | 16,3 ms với 10 lệnh; 79,7 ms với 64 lệnh, 124 biến thể |
| Cửa 3 trên chip: 198 cửa sổ từ phân vùng `voice` qua `_begin`, `_step`, `_score` | **198/198** trùng quyết định Python từng trường; đếm trên quyết định của chip: nhận đúng 68/112, nhận nhầm 3/86, bằng dòng `qat` của `command.md` §2 |

Ngân sách của KẾ HOẠCH §3.3 là 11–18 ms mỗi 32 ms trong cửa sổ lệnh, của §3.12 là ≤ 100 ms một lần chấm: mạng dùng 5,7 ms,
chấm 56,3 ms. Lượt đầu cùng ngày, trước bản sửa `rmsnorm_as_espdl`, 196/198 cửa sổ trùng: cửa sổ 67 và 197 lệch một bước
điểm vì `RMSNormalization` của ESP-PPQ làm tròn khác nhân esp-dl (KẾ HOẠCH §3.14); mô phỏng lại đúng số học esp-dl thì
Python ra đúng 198 quyết định của chip.

## 15. `rnnt` trên chip: ba đồ thị int8 và chấm chính xác (E11-T20)

Board B, bản dựng `rnnt` của `ai_engine/test_apps/unit` (`idf.py -D UNIT_PROFILE=rnnt`) với cờ trình biên dịch của
`sdkconfig.bench` (`-O2`, 240 MHz), IDF 6.0.2, esp-dl 3.3.11, ESP-PPQ 1.3.11 cộng bốn bản sửa của nhánh, 03/10. Ba đồ thị
của `rnnt/probe.py` trên trọng số ngẫu nhiên theo seed của `probe` (thời gian không phụ thuộc trọng số), bộ 10 lệnh mặc
định: cây lệnh có 75 ngữ cảnh. Đo bằng `make ai-unit-rnnt`.

| Đo | Kết quả |
|---|---|
| `command_rnnt` (encoder cộng phép chiếu khung 128 → 384), 16 bước 16 hop | chênh int8 lớn nhất **0**; 45,4 ms một bước, như mạng `ctc` (§14) |
| `rnnt_predictor`, 75 ngữ cảnh | **75/75** trùng Python; 2,0 ms một lần, mỗi ngữ cảnh một lần trong lúc model nạp |
| `rnnt_joiner`, 256 cặp khung và ngữ cảnh | **256/256** trùng Python; 98 µs một lần |
| Log-softmax 45 lớp của một đầu ra bộ nối (hàm của `ctc`) | 52 µs |
| Vòng tìm riêng, cửa sổ 3 s (94 khung), mọi hàng log-xác suất chép sẵn | 173 ms, **1,84 ms mỗi khung**; 80 hàng mỗi khung: 75 ngữ cảnh của cây và đường tham lam |
| `_score`, 12 cửa sổ 1–188 hop | **12/12** trùng Python từng trường; **26,8 ms mỗi khung**, cửa sổ 3 s 2,6 s |
| Như trên, khung và prefix lượng tử lại sang lưới của bộ nối một lần (`63fc4de`) | 12/12 trùng; **15,5 ms mỗi khung**, cửa sổ 3 s **1,56 s** |

Ngân sách của KẾ HOẠCH §3.12 là ≤ 100 ms một lần chấm: còn vượt khoảng 15 lần. Phần đắt là 80 lần gọi bộ nối mỗi khung,
mỗi lần một đồ thị esp-dl 98 µs và một log-softmax 52 µs; vòng tìm chỉ chiếm 1,84 ms mỗi khung.

**Sau khi tỉa, chạy bộ nối theo lô và chấm dần** (KẾ HOẠCH §3.12 sửa ở `91c0e2f`), 03/10. Ngưỡng tỉa chọn trên 198 cửa sổ Cửa 3
qua mô phỏng int8 của run `20261002_1538154-dirty_d6d74a`, dòng `kl`, quyết định so với chấm không tỉa:

| Ngưỡng (nat) | Quyết định trùng | Hàng log-xác suất mỗi khung |
|---|---|---|
| không tỉa | 198/198 | 76,1 |
| 30 | 198/198 | 69,4 |
| 20 | 198/198 | 44,8 |
| **15** (chọn) | **198/198** | **15,2** |
| 10 | 198/198 | 5,3 |
| 6 | 195/198 | 3,0 |

Tỉa giữ quyết định (nhận lệnh nào hay từ chối), không giữ thứ hạng: ở ngưỡng 15, lệnh điểm cao nhất đúng của dòng float
còn 79/112 thay vì 81/112, cả hai cửa sổ khác đều bị từ chối như trước; nhận đúng 42/112 và nhận nhầm 2/86 không đổi.

Trên board B, cùng dòng `kl` lượng tử lại với bộ nối 16 cột, `make ai-unit-rnnt RNNT_RUN=<run> RNNT_ROW=rnnt_kl` (`07f39e7`):

| Đo | Kết quả |
|---|---|
| `command_rnnt`, 48 bước 16 hop của một câu test | chênh int8 lớn nhất **0**; 45,7 ms một bước |
| `rnnt_predictor`, 75 ngữ cảnh | **75/75** trùng Python; 2,0 ms một lần |
| `rnnt_joiner`, 256 cặp, 16 cột một lần chạy | **256/256** trùng Python; 568 µs một lần, 35 µs một ngữ cảnh |
| Vòng tìm riêng, cửa sổ 3 s, hàng chép từ một đầu ra thật của bộ nối | không tỉa 1 932 µs mỗi khung, 80 hàng; tỉa 15 nat **162 µs**, 12 hàng |
| Cửa 3 trên chip: 198 cửa sổ của phân vùng `voice` qua `_begin`, `_step`, `_score` | **198/198** trùng Python từng trường; nhận đúng 30/112, nhận nhầm 1/86, đếm trên quyết định của chip |
| `_score` trên 198 cửa sổ ấy | trung vị **79,6 ms**, phân vị 90 **100,8 ms**, nhỏ nhất 0,2 ms, lớn nhất **445 ms** |

Phần lớn của `_score` là khối 16 hop cuối của encoder (45,7 ms) và các khung của nó; cửa sổ kết thúc đúng ở biên khối chỉ
còn quyết định. Cửa sổ lớn nhất là cửa sổ đầu sau khi nạp: chưa có cây lệnh để chấm dần, và mạng dự đoán chạy lần đầu cho
từng ngữ cảnh. Chất lượng của dòng này còn dưới `ctc` đang chạy (68/112 của §14) vì `δ₁` `δ₂` là của `ctc`, chưa chọn trên
`val` cho `rnnt`, và `rnnt` mới có bậc 2 của thang §3.14.

## 16. Cửa sổ lệnh chạy theo luồng: từ lúc câu chốt tới quyết định (E11-T14)

Board B, IDF 6.0.2, cờ của `sdkconfig.bench`, `d530195`, 03/10. Model đang khoá: dòng `qat` của run
`20261002_128545c-dirty_64e7a4`, `δ₁` 300‰, `δ₂` 50‰. Cửa sổ cắt theo KẾ HOẠCH §5.4: mở `utterance.lead_s` 1,25 s trước
bước `vad` đầu, kết ở bước sau đoạn `vad` cuối, mọi điểm chia `T_W` = 94 khung.

**`svc_listen` trên chip** (`make listen-unit`): 35 phiên Cửa 3, mỗi phiên cộng quãng lặng sau nó, nạp từng bước qua
`svc_listen_feed`; sau mỗi bước `svc_listen_work` chạy tới khi hết việc, nên luồng theo kịp như trên máy thật khi nhân 0
đủ rảnh. "Sau bước chốt" là từ lúc `feed` chốt câu tới lúc quyết định ra.

| Đo | Kết quả |
|---|---|
| Cửa sổ trùng Python từng trường | **198/198**, ba lượt nạp 72, 66, 60 |
| Từ bước chốt tới quyết định | **47,7 ms** trung vị, 49,4 ms p95; 10 cửa sổ dưới 1 ms vì dài đúng bội của khối 16 bước; 2 câu dài quá 3 s, cắt lùi từ cuối, 0,97 và 0,99 s |
| Cao độ, mạng và chấm của một cửa sổ, cộng dồn qua các bước | 665 ms trung bình, 985 ms đỉnh |

**`ai_engine` trên chip** (`make ai-unit CTC_RUN=… CTC_ROW=qat`, 8/8 bài thử):

| Đo | Kết quả |
|---|---|
| `_prepare`, 10 lệnh | 177 µs |
| `_step` | 30 µs ở hop chỉ đệm; 47,4 ms trung bình ở hop đủ khối, gồm thuật toán tiến của 19 biến thể qua 8 khung mới |
| `_score`, 198 cửa sổ Cửa 3 | 44,7 ms trung bình, 50,5 ms đỉnh (§14 khi chấm một lần: 56,3 và 64,9 ms) |
| Phần kết sau khi chấm dần, cửa sổ 94 khung | **1,5 ms** với 10 lệnh, **1,8 ms** với 64 lệnh; chấm một lần cả cửa sổ là 15,7 và 83,0 ms |
| Cửa 3 trên chip | 198/198 trùng Python; nhận đúng 71/112, nhận nhầm 3/86, bằng `command.md` §5 |

**`rnnt` trên chip** (`make ai-unit-rnnt`, dòng `rnnt_kl` của run `20261002_1538154-dirty_d6d74a`, 4/4 bài thử):
`_prepare` dựng cây và chạy mạng dự đoán cho cả 75 ngữ cảnh của nó, 157 ms; `_score` qua 198 cửa sổ Cửa 3 73,6 ms trung bình,
122 ms đỉnh (§15: 77,1 và 445 ms, đỉnh là cửa sổ đầu phải dựng cây); 198/198 trùng Python; nhận đúng 26/112, nhận nhầm
1/86, cách cắt cũ 30/112 và 1/86, với `δ₁` `δ₂` vẫn là của `ctc` và `rnnt` chưa có QAT.

Từ lúc người thôi nói tới quyết định: `vad` kéo dài 240 ms (KẾ HOẠCH §3.10), câu chốt sau `utterance.gap_s` 400 ms nữa,
rồi chừng 48 ms tính, tổng chừng 0,69 s 🔬. Cửa sổ cắt lùi từ cuối câu thì phần tính sau bước chốt là cả cửa sổ, 0,72–0,88
s trung bình và 0,99 s đỉnh trên cùng các phiên (`make listen-unit` ở `6eeb568`), tổng chừng 1,4 s 🔬. Phần còn lại phần
lớn là chờ hết câu, 640 ms; trong 48 ms tính, khối dở cuối của mạng chiếm gần hết, vì mạng chạy theo khối 16 bước và cuối
cửa sổ chỉ biết khi câu chốt.

## 17. Cửa sổ theo luồng trên firmware thật: từ ngắn chưa đuổi kịp lúc câu chốt (E11-T14)

Board B chạy firmware dev ở `332b540`, cùng Wi-Fi, MQTT và khối lọc âm, model đang khoá, bộ lệnh 22 lệnh của chủ repo
gửi bằng `make commands`; chủ repo nói trực tiếp trước board, 03/10. Mỗi dòng là một quyết định trong log của `nhan_task`.
Ở bản này cửa sổ chỉ bắt đầu tính khi câu đủ `utterance.min_s`, và `nhan_task` chạy mỗi lần một bước rồi chờ một tick.

| Quyết định | Bước của cửa sổ | Tính cả cửa sổ | Sau bước chốt |
|---|---|---|---|
| `COMMAND` mở cửa | 131 | 945 ms | 68 ms |
| `COMMAND` đóng cửa | 129 | 935 ms | 63 ms |
| `COMMAND` bật điều hoà | 141 | 1 026 ms | 73 ms |
| `COMMAND` đắt | 105 | 760 ms | 221 ms |
| `REJECT LOW_MARGIN`, bốn câu "bần cùng", "bắn cung" | 106–107 | 766–771 ms | 226–232 ms |
| `REJECT LOW_SCORE`, hai tiếng ngắn | 99 | 745–751 ms | 323–324 ms |

Trên firmware thật một bước của cửa sổ tốn chừng 7,2 ms (766 ms cho 107 bước), so với chừng 5,2 ms ở app thử của §16, chỉ
nhanh hơn thời gian thực (16 ms một bước) hơn hai lần. Khi bắt đầu, cửa sổ đã sau 78 bước trước câu cộng 16 bước chờ đủ
`utterance.min_s`; từ ngắn, cửa sổ chừng 100 bước, chốt trước khi đuổi kịp nên quyết định ra muộn 220–320 ms, còn lệnh
dài từ 129 bước trở lên đã đuổi kịp và ra 63–73 ms sau bước chốt.

## 18. Mạng `ctc` rộng 160 trên 80 dải chạy dòng trên esp-dl (ADR-0017, ADR-0018, E11-T20)

Board B, `ai_engine/test_apps/unit` dựng với thiết lập trình biên dịch của profile bench (`-O2`), bảng phân vùng
`test_apps/partitions_unit.csv` (`models_0` 6 MB), IDF 6.0.2, `e1a544b` cộng bề rộng 160 / feedforward 320 chưa commit
trong `configs/models/command_ctc.yaml`, 05/10. Trọng số ngẫu nhiên có seed như §11, vào 80 log-mel + 3 cao độ mỗi hop;
lượng tử bậc 1 và 2 mặc định của KẾ HOẠCH §3.14. Dựng bằng `make ai-probe`, đo bằng `make ai-unit`.

| Mạng | Một bước | `.espdl` | Chênh int8 so với mô phỏng cả chuỗi | Một bước, trung bình / đỉnh | Mỗi 32 ms audio | Dựng mạng | PSRAM | Không `reset` (đối chứng âm) |
|---|---|---|---|---|---|---|---|---|
| `ctc_lay`: một lớp của tầng đầu (bề rộng 160, nhân 17) | 1 hop, 256 bước | 523 KB | **0** | 7 038 / 7 159 µs | 14,1 ms | 36 ms | 44,1 KB | chênh 6 |
| `ctc_net`: cả mạng (3 tích chập 2D trên 80 dải, 6 lớp, đầu CTC 45 lớp; 2,95 triệu tham số ở các tầng) | 16 hop (256 ms), 16 bước | 3 358 KB | **0** | 81 387 / 81 699 µs | **10,2 ms** | 330 ms | 293,2 KB | chênh 26 |

`model->test()` qua ở cả hai. Qua các lệnh gọi của `command` (`_step` chuẩn hoá đặc trưng thô về int8 và gom khối 16 hop,
`_score` chạy khối dở cuối rồi chấm), 12 cửa sổ đặc trưng thô cho đúng quyết định của mô phỏng Python: 56 µs mỗi hop,
83 884 / 83 985 µs mỗi khối 16 hop, tức 10,5 ms mỗi 32 ms, và 76 780 / 84 186 µs mỗi lần chấm. Ảnh probe ba mục nạp vào
PSRAM 3 881 KB; nạp qua bảng mới ánh xạ cả `models_0` 6 MB mà không lỗi.

So với mạng cỡ MultiNet7 trên 40 dải (§11, 5,8 ms mỗi 32 ms), mạng này chậm hơn 1,75 lần, nhiều hơn tỉ lệ tham số 1,56
lần của các tầng; tích chập 2D đầu chạy trên số dải gấp đôi, phần của nó chưa đo tách. Vẫn dưới ngân sách 11–18 ms mỗi
32 ms trong cửa sổ lệnh của KẾ HOẠCH §3.3.

## 19. Bộ 300 lệnh trên firmware thật (E11-T14)

Board B, model `command/v8` hàng `percentile` nghe bằng Kaldi, độ hữu thanh gập (`contracts/models.lock.json`), δ₁ 100
‰, cùng Wi-Fi, MQTT và khối lọc âm, 07/10 tối. Các dòng `dev` (`-Og`, log `DEBUG`, δ₂ 0 ‰) chỉ để so; số báo cáo của
KẾ HOẠCH §4.5.8 là dòng `bench` (`-O2`, log `INFO`, δ₂ 25 ‰ theo chủ repo, ngưỡng không đổi thời gian). Mỗi dòng gom
các quyết định trong log của `nhan_task`, cửa sổ mở trên tiếng trong phòng; hai dòng cuối có thêm chủ repo nói thử trước
board. "Tính" là thời gian máy chấm cả cửa sổ chia cho số hop của nó, "sau bước chốt" là từ bước chốt câu tới quyết
định; mỗi ô là nhỏ / giữa / lớn:

| Firmware | Bộ lệnh | Cách đọc | Quyết định | Cửa sổ, hop | Tính mỗi hop, ms | Sau bước chốt, ms |
|---|---|---|---|---|---|---|
| `eb7e362` `dev`, chấm từng cách đọc | 47 lệnh của chủ repo | 107 | 59 | 60 / 162 / 234 | 7,4 / 7,7 / 8,4 | 2 / 449 / 2 322 |
| `2c07781` `dev`, chấm từng cách đọc | `test300_vi.json` | 651 | 90 | 44 / 161 / 234 | 15,6 / 16,1 / 17,2 | 1 963 / 4 855 / 7 785 |
| `913e1e5` `dev`, cây tiền tố | bộ mặc định 10 lệnh | 19 | 4 | 111 / 119 / 181 | 6,6 / 7,0 / 7,9 | 104 / 188 / 313 |
| `913e1e5` `dev`, cây tiền tố | `test300_vi.json` | 651 | 103 | 51 / 173 / 234 | 11,0 / 11,4 / 12,6 | 1 072 / 1 334 / 3 573 |
| `913e1e5` **`bench`**, cây tiền tố | `test300_vi.json` | 651 | 92 | 46 / 156 / 234 | 8,1 / 8,4 / 9,9 | **110 / 479 / 2 344** |

Trên `bench`, quyết định tới sau bước chốt giữa 479 ms, p25 193 ms, p75 741 ms; lớn nhất 2,3 s khi câu nối câu. Mỗi hop
8,35 ms thay 11,4 ms của `dev`, nên cửa sổ đuổi được 0,9 hop mỗi hop thay 0,4.

Bộ 300 lệnh nạp được lúc boot và chấm đúng khuôn: `svc_listen: commands …: 300 commands, 651 readings`, PSRAM còn 1,8 MB
khi nghe. Chấm từng cách đọc tốn mỗi hop hơn 16 ms của chính hop ấy, nên cửa sổ không bao giờ đuổi kịp: `q_clean` bỏ 49
hop, `window … dropped: the ring moved past it`. Cây tiền tố (4 412 nút thay 8 447 đơn vị) đưa mỗi hop về 11,4 ms, phần
của 651 cách đọc chừng 4,4 ms trên 7,0 ms của bộ mặc định; quyết định tới sau bước chốt 1,07–1,4 s khi cửa sổ đứng một
mình, 2,5–3,6 s khi câu nối câu (p75 3 167 ms). Cửa sổ mở với 125 hop trước câu chờ chấm (`utterance.lead_s` 2,0 s) và mỗi
hop thời gian thực chỉ đuổi được 16 / 11,4 − 1 = 0,4 hop, nên không cửa sổ nào đuổi kịp trước khi câu chốt: cửa sổ 173
hop còn chừng 106 hop, 1,2 s, sát số đo.

**Nhân 0 đói trên `dev`, không trên `bench`.** Với 651 cách đọc trên `dev`, cửa sổ hầu như luôn còn việc, nên
`nhan_task` (ưu tiên 10) chỉ chờ một tick giữa hai đợt (KẾ HOẠCH §5.4) và các task thấp hơn ở nhân 0 gần như không
chạy. Trên `bench`, 9 phút với 92 quyết định không có lần watchdog nào, `heartbeat` đều 30 s, `q_event_up` bỏ một sự
kiện trong 8 s đầu sau boot rồi không bỏ nữa. Với cây tiền tố trên `dev`, watchdog báo `gui_task`,
`luong_task` hay `net_task` không chạy suốt 5 s 20 lần trong 15 phút; `heartbeat` lặng 76 s; 22 sự kiện bị bỏ
(`events` của `heartbeat`), tức `host` không thấy 22 quyết định. Bộ đếm ấy gộp hai đường: `q_event_up` đầy, và
`gui_task` bỏ sự kiện khi phiên MQTT mất hay publish lỗi (`send_events`), nên chưa biết phần nào; `heartbeat` lặng 76 s
cho thấy phiên có lúc không đi được. Chấm từng cách đọc bỏ 47 sự kiện trong 372 s. Log `DEBUG` của `dev` ra UART 115 200 baud trung bình 2,7 KB/s, đỉnh 14 KB/s, quá sức đường truyền; phần lớn là
`vfs_calls` của `select` (20 701 dòng) và `mqtt_client` (1 090 dòng).

## 20. Đổi bộ lệnh lúc chạy: chuỗi làm sạch rơi khung không dứt (E11-T14)

Board B, `913e1e5` profile `dev`, boot với bộ mặc định 10 lệnh rồi gửi `test300_vi.json` qua `down/commands`, 07/10
23:21. `nhan_task` đổi bảng lệnh: `lang_vi` 312 ms, ghi `/lfs/cmd/set.json` 222 ms. Từ đó `frames` của `heartbeat`
(khung `thu_task` bỏ vì hết ô `q_free`) tăng đều 47,6 khung/s, 76% số khung, từ 840 ở giây 35 tới 13 700 ở giây 305;
không quyết định nào; nhân 1 không lúc nào rảnh (watchdog báo `IDLE1` mỗi 5 s). Watchdog in backtrace của nhân 1 57 lần:
35 lần trong `init` của OM-LSA (bảng E1 `e1_smooth`, `exp_series`, cửa sổ Hann `cos_series`), 12 lần trong
`fill_phasors` của `doa`, 10 lần trong phần xử lý khung thường.

Chuỗi nhân quả: ghi flash tắt cache cả hai nhân 222 ms, `q_free` cạn, `thu_task` bỏ khung; `svc_front` thấy hở `seq` và
gọi `dsp_afe_reset`; hàm ấy chạy lại `init` của mọi khâu, dựng lại các bảng bằng chuỗi số double giả lập, lâu hơn 8 ô
`q_free` (128 ms), nên lại hở `seq`, lại `reset`. Từ tốc độ bỏ khung, mỗi vòng chừng 0,54 s, tức một lần `reset` chừng
0,5 s 🔬. Boot với bộ đã nằm trong `set.json` thì không gặp (`dropped: frames 0`); bộ 64 lệnh ngày 02/10 ghi `set.json`
92 ms, dưới 128 ms, nên không mất khung qua bảy lần đổi.

**Sau `0664351`** (KẾ HOẠCH §4.5.5): mỗi khâu có `reset` đưa trạng thái về như sau `init` và giữ bảng; trên máy, chuỗi
sau `reset` trùng từng bit chuỗi vừa dựng ở cả ba bản dựng, chuỗi không `reset` là đối chứng âm. Board B, `bench`, 08/10
00:00: đổi bộ lệnh lúc chạy 10 lần, bộ mặc định 10 lệnh và `test300_vi.json` xen nhau: `lang_vi` 190 ms và ghi
`set.json` 112–168 ms với bộ 300 lệnh, 27–37 ms với bộ 10 lệnh; `frames 0`, không watchdog, lệnh vẫn nhận sau mỗi lần
đổi. Không lần nào mở ra chỗ hở, nên đường `reset` chưa chạy trên board; chi phí một lần `reset` trên board chưa đo 🔬.

## 21. Từ quyết định tới `host`: esp-mqtt giữ sự kiện tới hết vòng `select` (E11-T14)

Board B, profile `bench`, bộ `test300_vi.json`, broker EMQX trên máy tính cùng Wi-Fi, 08/10 00:35. Đo không cần người
nói: gửi một bộ lệnh có dòng "zzz" mà `lang_vi` không đọc được, `nhan_task` báo `ERROR COMMANDS_INVALID` theo đúng
đường của `COMMAND` và `REJECT` (`send_up` → `q_event_up` → `gui_task` → `net_mqtt_publish_event` → outbox của
esp-mqtt) và giữ bộ đang dùng. Mỗi lần đo là hiệu giữa giờ máy tính nhận dòng log `ERROR` qua cổng nối tiếp, đọc mỗi
5 ms và in sau khi sự kiện đã vào hàng, với giờ máy tính nhận sự kiện từ broker, cùng một đồng hồ:

| `MQTT_POLL_READ_TIMEOUT_MS` | Lần | Nhỏ / giữa / lớn, ms |
|---|---|---|
| 1000, mặc định của esp-mqtt | 10 | 972 / 978 / 1 014 |
| 50 (KẾ HOẠCH §4.5.8) | 10 | 24 / 168 / 179 |

esp-mqtt chỉ gửi tin đã xếp hàng khi vòng của nó thức, mỗi vòng một tin, và vòng ấy ngủ trong `select` tới hạn poll
khi broker không gửi gì (`mqtt_client.c`, `esp_mqtt_task`). Với poll 50 ms, phần còn lại là nhịp 100 ms của `gui_task`
(0–100 ms), cộng poll (0–50 ms), cộng mạng và broker.

## 22. `svc_listen` trên chip với `command/v8`: lệch vài ‰ vì log-mel, trùng 336/336 khi `dsp_spec` khớp từng bit (E11-T14, E6-T8)

Board B, `make listen-unit` ở `1a50f2a` (cây tiền tố, mục 2 và 4 của chủ repo), model khoá `command/v8` hàng
`percentile` nghe bằng Kaldi, độ hữu thanh gập; mọi phiên Cửa 3 hiện có, bảy lượt nạp; ngưỡng của bản dựng phiên
(δ₁ 300 ‰, δ₂ 50 ‰), 08/10 08:44–09:18. Mỗi cửa sổ so với mô phỏng int8 của Python từng trường:

| Đo | Kết quả |
|---|---|
| Cửa sổ trùng Python từng trường | 304/319 |
| Cửa sổ khác loại quyết định hay khác lệnh | 0: 8 `COMMAND` cùng lệnh, 6 `REJECT`, một dòng log hỏng giữa đường nối tiếp |
| Lệch ở 15 cửa sổ ấy, score / margin / gap | tới 15 / 13 / 22 ‰, phần lớn 1–9 ‰ |
| Ca `FRAME_GAP` (câu đang mở gặp chỗ hở) và ca click | qua cả bảy lượt |
| Quyết định sau bước chốt, giữa / đỉnh mỗi lượt | 79–114 ms / 86 ms, tới 1,27 s ở cửa sổ cắt lùi |

Lần trước (`make listen-unit` 03/10, model cũ, chấm từng cách đọc) trùng 198/198.

**Chỗ lệch nằm ở log-mel, không ở mạng esp-dl hay phép chấm** (08/10). Python dựng lại cả 336 cửa sổ của bảy lượt như
`probe.listen_rounds`; quyết định của nó trùng bản ghi chip in ra ở cả 316 dòng đọc được (20 dòng rơi trên đường nối
tiếp). Mỗi cửa sổ trong 14 cửa sổ lệch có một phần tử log-mel nằm sát ranh làm tròn của lưới vào int8 (bước 1/16 độ lệch
chuẩn), cách ranh 2,5e-7 tới 8,5e-6. Lật đúng phần tử ấy sang ô bên cạnh, Python ra đúng điểm, margin và gap của chip ở
**14/14** cửa sổ, và không cửa sổ nào cần lật một chiều cao độ. Có ít nhất một phần tử log-mel cách ranh dưới 1e-5 ở 14/14
cửa sổ lệch, so với 235/302 cửa sổ trùng.

Log-mel trên chip khác bản soi gương ở ba chỗ (parity.md: lệch tới 9,5e-7 trên board):
- `mel.c` dựng không có `-ffp-contract=off`, chỉ `pitch.c` có. Mã máy của `build_bench` gộp `re·re + im·im` và
  `energy + w·power` thành `madd.s`, mỗi phép chỉ làm tròn một lần.
- Log là `logf` của newlib, còn Python lấy log double rồi làm tròn một lần.
- FFT là `dl_fft` (hợp ngữ S3 có `madd.s`, bảng `cosf`/`sinf` của newlib), còn Python dùng `np.fft.rfft`.

Model cũ đọc 40 dải trên bước 1/8, v8 đọc 80 dải trên bước 1/16. Số phần tử sát ranh vì thế gấp chừng bốn lần (ước lượng
từ hai tỉ số), nên lần 198/198 trước là may, không phải khớp từng bit. `make ai-unit` với v8 dừng trước bài thử:
`ctc_gate.bin` (83 chiều, 336 cửa sổ) lớn hơn phân vùng `voice` 2 MB.

**Sau khi `dsp_spec` khớp bản soi gương từng bit** (E6-T8, `aeef82a`, FFT cơ số 4 mặc định), `make listen-unit` lại trên
board B, 08/10 10:59–11:33, cùng model, cùng ngưỡng, bảy lượt sinh lại từ bản soi gương mới. Đếm trên log đủ của
pytest-embedded; bản `tee` của `make` rơi các dòng cuối mỗi lượt.

| Đo | Kết quả |
|---|---|
| Cửa sổ trùng Python từng trường | **336/336**, mọi lượt `0 decided otherwise than python` (55, 50, 54, 58, 41, 38, 40 cửa sổ) |
| Ca `FRAME_GAP` và ca click | qua cả bảy lượt, 3/3 bài thử mỗi lượt |
| Quyết định sau bước chốt, trung bình mỗi lượt | 76–114 ms; đỉnh 86–88 ms ở lượt không có cửa sổ cắt lùi, tới 1,28 s ở cửa sổ cắt lùi |
| Việc của một cửa sổ, trung bình / đỉnh mỗi lượt | 775–949 ms / tới 1,27 s |

## 23. FFT viết tay khớp bản soi gương từng bit, so với `dl_fft` (E6-T8, ADR-0020)

Board B, `dsp_spec/test_apps/unit` dựng `-O2`, 240 MHz, IDF 6.0.2, thư mục build mới từ `sdkconfig.defaults` của ngày
08/10. Cùng app, cùng lớp bọc, chạy liền nhau trên một board: `dl_fft` 0.7.0 từ `335859c`, FFT viết tay từ cây làm việc
sau đó. Trung bình 1000 lượt sau một lượt làm nóng.

| Đo | `dl_fft` 0.7.0 | Viết tay cơ số 2 | **Viết tay cơ số 4** |
|---|---|---|---|
| Thuận / nghịch, 256 điểm | 56,2 / 67,2 µs | 87,9 / 95,1 µs | **55,5 / 58,7 µs** |
| Thuận / nghịch, 512 điểm | 118,2 / 135,9 µs | 189,0 / 203,1 µs | **116,9 / 123,0 µs** |
| Thuận / nghịch, 1024 điểm | 259,6 / 297,2 µs | 404,8 / 432,9 µs | **257,1 / 269,1 µs** |
| STFT / iSTFT một bước | 143,3 / 164,7 µs | 214,1 / 238,3 µs | **142,0 / 158,3 µs** |
| Log-mel 40 dải một bước | 61,6 µs (`logf`) | 59,2 µs | **59,2 µs** |
| Sai số lớn nhất so với DFT double, 512 điểm | 2,13e-6 trên đỉnh 17,35 | 1,92e-6 | **1,69e-6** |
| Dựng lại STFT | 136,3 dB | 135,9 dB | **136,6 dB** |
| Bộ nhớ, 512 điểm | vùng làm việc 2 080 B + bảng thư viện 6 244 B ở RAM nội | vùng làm việc 4 656 B | **vùng làm việc 5 680 B**, bảng nằm trong đó |

Một khung của chuỗi (§3.1: hai phân tích, một tổng hợp) tốn 2 × 142,0 + 158,3 = **442 µs** với bản cơ số 4, so với
451 µs của `dl_fft`. Cơ số 4 gộp hai tầng cơ số 2 vào một lượt: ba phép nhân phức cho bốn điểm thay vì bốn, mỗi điểm đọc
và ghi một lần cho hai tầng; bướm có hệ số xoay bằng 1 không nhân; vạch k và n/2 − k tách cùng lúc. Chiều nghịch ghép vạch
rồi chạy đúng các lượt của chiều thuận qua phép liên hợp, nên nhanh hơn `dl_fft` 13 µs.

Trên máy tính (`make parity-host`), bản C khớp Python từng bit ở mọi ca vàng: `stft` (phổ và tín hiệu dựng lại), `mel`
(log-mel và MFCC), và cả PCM của `chain`, `chain_modules`, trước đây lệch 1 LSB; mọi đối chứng âm vẫn đỏ.

## 24. `command/v8` qua `ai_engine` trên chip: Cửa 3 theo lượt (E11-T14)

Board B, `ai_engine/test_apps/unit` với cờ trình biên dịch của `sdkconfig.bench` (`-O2`, 240 MHz), IDF 6.0.2, `d183cf2`,
08/10. Mạng là dòng `percentile` của `command/v8` nghe bằng Kaldi, độ hữu thanh gập (`make ai-unit CTC_RUN=… CTC_ROW=percentile
CTC_KALDI=1 CTC_HOLD=voicing`); ngưỡng δ₁ 300 ‰, δ₂ 50 ‰ của config. Bản ghi Cửa 3 (335 cửa sổ, 4,4 MB) chia ba lượt vừa
phân vùng `voice`; hai lượt sau chạy riêng ca Cửa 3 trước cả bộ.

| Đo | Kết quả |
|---|---|
| Dựng mạng | 346 ms; PSRAM 309 KB, RAM nội 0 B |
| 48 bước 16 hop, so mô phỏng cả chuỗi | chênh int8 lớn nhất **0**; 82,0 ms trung bình một bước |
| `ctc_lay`, 256 bước một hop | chênh int8 lớn nhất **0**; 7,0 ms một bước |
| `_prepare`, 10 lệnh | 296 µs |
| `_step` | 56 µs ở hop chỉ đệm; 84,2 ms trung bình, 84,3 ms đỉnh ở hop đủ khối 16 hop, tức **10,5 ms mỗi 32 ms** |
| `_score` | 79,7 ms trung bình, 89,1 ms đỉnh trên Cửa 3; 84,6 ms trung bình trên 12 cửa sổ đặc trưng thô |
| 12 cửa sổ đặc trưng thô qua `_begin`, `_step`, `_score` | 12/12 trùng Python từng trường |
| Cửa 3 trên chip, ba lượt 158, 18, 159 cửa sổ | **335/335** trùng Python từng trường; đếm trên quyết định của chip: nhận đúng **146/209**, nhận nhầm **3/126** |

Cả bộ 8/8 bài thử qua. Lượt trước cùng ngày dừng trước bài thử: `ctc_gate.bin` lớn hơn phân vùng `voice`, và các ca `kws`,
`ns`, chạy dòng giữ mạng của mình trong PSRAM tới hết bộ, nên ca `ctc` hết PSRAM khi nạp (`4a09a16`).

## 25. ReDimNet2 b0 int8 trên esp-dl: 3,04 s một cửa sổ 1,5 s (E11-T25)

Đo ngày 10/10 trên board B bằng `make ai-unit-speaker RUN=artifacts/speaker/runs/20261010_5a45475-dirty_94e5a9 ROW=kl`.
Đồ thị là b0 viết lại cho esp-dl (`speaker.md` §6.2), hiệu chỉnh kl, đầu vào 72 mel × 149 khung.

| Đo | Kết quả |
|---|---|
| Ảnh model trong PSRAM | 1722 KB |
| Dựng mạng, gồm một lần chạy thử | 3668 ms; PSRAM 3 277 280 B cho tensor, RAM nội 192 B |
| Một lần chạy | **3039 ms** trung bình, 3039 ms đỉnh, qua 10 lần sau một lần không đo |
| So mô phỏng ESP-PPQ | 158/192 giá trị lệch quá một bước int8, lệch lớn nhất 18; `model->test()` báo không khớp |

- **Gấp 3 lần ngưỡng cắm** ≤ 1 s của KẾ HOẠCH §3.17. Ước theo MAC là 0,55–2,7 s 🔬; số đo nằm trên khoảng ấy.
- **Dạng lệch:** các cặp board/mô phỏng cùng dấu và cùng cỡ (20/10, −10/−14, 111/109, −35/−43), không phải ×2 đều
  khắp. Đó là sai số tích luỹ, chưa biết bắt đầu ở op nào.
- **Watchdog:** nhân 0 bận liền 3–4 s khi dựng và chạy, nên task watchdog cảnh báo; test vẫn chạy hết.

Thời gian từng module, đo bằng `model->profile_module()` của esp-dl trong cùng ca test, 700 module, tổng 3022 ms:

| Loại op | Số op | Thời gian | Phần |
|---|---|---|---|
| Conv | 175 | 1086 ms | 36% |
| ReduceSum | 9 | 596 ms | 20% |
| Transpose | 81 | 534 ms | 18% |
| Mul | 40 | 133 ms | 4% |
| Concat | 8 | 110 ms | 4% |
| MatMul | 46 | 93 ms | 3% |
| LayerNormalization | 19 | 81 ms | 3% |
| Pow (F32) | 8 | 72 ms | 2% |

- **ReduceSum và Concat** là phép gộp có trọng số đầu ra các stage của ReDimNet2 (`weigth1d`): xếp chồng các đầu ra
  rồi cộng theo trục 1. Mỗi ReduceSum mất 70–102 ms, vì esp-dl cộng chậm theo trục không nằm trong cùng. Viết thành
  tổng tường minh Σ wᵢ·xᵢ thì bớt được cỡ 0,7 s 🔬.
- **Conv** riêng phần mình đã 1,09 s, vượt ngưỡng 1 s. Chậm nhất là conv 3×3 đầu stem chỉ có 1 kênh vào: 110 ms cho
  1,15 triệu MAC. Kế đến là hai conv 1×1 của lớp gộp, 78 ms và 79 ms.
- **Đọc:** viết lại đồ thị chỉ đưa b0 về cỡ 2 s 🔬, chưa tới ≤ 1 s. Muốn đạt thì cần mạng nhỏ hơn, cửa sổ ngắn hơn
  hay chip nhanh hơn.

Sau khi viết lại phép gộp đầu ra các stage thành conv depthwise 1×1 cộng nhau (`StageSum`, `772b795`), đo trên một bản
lượng tử nhanh của cùng đồ thị: minmax, không equalization, không bias correction, hàng `fast` của run. Thời gian chỉ
phụ thuộc cấu trúc đồ thị:

| Đo | Trước | Sau |
|---|---|---|
| Một lần chạy | 3039 ms | **2567 ms** |
| PSRAM cho tensor | 3 277 280 B | 2 124 004 B |
| Conv | 1086 ms, 175 op | 1212 ms, 203 op |
| Transpose | 534 ms, 81 op | 691 ms, 93 op |
| ReduceSum, Concat | 706 ms | 0 |
| Giá trị lệch mô phỏng | 158/192, lớn nhất 18 | 160/192, lớn nhất 24 |

- 28 conv depthwise và 12 Transpose mới ăn lại khoảng 0,28 s trong 0,7 s tiết kiệm được.
- Điểm nóng kế tiếp: Transpose 27%; conv 3×3 đầu stem 110 ms; hai conv 1×1 của lớp gộp 87 ms và 78 ms.

Sau hai bản vá mô phỏng (`layernorm_as_espdl`, `softmax_as_espdl`), cùng đồ thị chạy **2560 ms** một cửa sổ và khớp
mô phỏng từng bit: 0/192 giá trị lệch, `model->test()` qua.

Thử đặt bản đồ 2-D theo (khung, băng, kênh) để đổi sang 1-D chỉ còn Reshape, không dời dữ liệu (`to1d_tfopt` của
ReDimNet2): vẫn khớp từng bit, nhưng **2588 ms**, Transpose 738 ms, 50 cái do ESP-PPQ chèn. ESP-PPQ chèn Transpose sau
mỗi Reshape đi vào Conv, bất kể dữ liệu đã đúng thứ tự trong bộ nhớ; nên bỏ, giữ bố cục cũ.

Ba bước tối ưu tiếp, mỗi bước đo trên board B với bản lượng tử nhanh của đồ thị, cả ba vẫn khớp mô phỏng từng bit
(mọi bản cắt và cả đồ thị qua `model->test()`, 0/192 giá trị lệch):

| Bước | Một lần chạy | Transpose |
|---|---|---|
| Hai bản vá mô phỏng (LayerNorm, Softmax) | 2560 ms | 691 ms, 93 |
| GELU bản tanh thành một bảng int8 (`fuse_tanh_gelu`) | 2501 ms | 702 ms, 94 |
| Bỏ Transpose trùng, gộp chuỗi Transpose–Reshape–Transpose khi xuất (`lean_transposes`) | 2307 ms | 510 ms, 61 |
| Bản đồ 2-D theo (khung, băng), đổi sang 1-D không dời dữ liệu | 1934 ms | 128 ms, 39 |
| Conv 3×3 đầu stem một kênh vào thành 12 bản sao rồi conv depthwise (`one_channel_inputs`) | 1877 ms | 148 ms, 39 |
| Một Transpose chung cho mọi op đọc cùng biến, kể cả op khác lưới lượng tử (`lean_transposes`) | 1787 ms | 76 ms, 29 |
| Hai Conv theo khung của lớp gộp tách theo kênh ra, mỗi phần ≤ 36 KB trọng số (`output_slices`) | 1635 ms | 76 ms, 29 |
| Bình phương trong lớp gộp bằng phép nhân int8, không qua Pow chạy float | 1588 ms | 76 ms, 29 |
| Một QuantizeLinear cho mọi op đọc cùng biến trên cùng lưới (`lean_quantizers`); Conv 1×1 12 và 24 kênh gộp cột kề nhau vào kênh (`aligned_pointwise`) | 1288 ms | 76 ms, 29 |
| Gộp cột cả cho Conv đọc biến do Transpose chèn và Conv (1, 2) bước 2 (`stage0.2`, `stage1.2`) | 1248 ms | 76 ms, 29 |
| Bỏ `Upsample` hệ số 1 cuối stage 0 và 1 (`unit_upsamples`) | 1233 ms | 76 ms, 29 |
| 12 bản sao đầu stem bằng Add với 0 phát sóng, không Concat và Transpose (`copies_by_broadcast`) | 1212 ms | 43 ms, 28 |
| Tách Conv của lớp gộp chuyển sang bước xuất (`cache_sized_convolutions`), không đổi int8 | 1211,5 ms | 43 ms, 28 |
| Attention tính khoá trước: điểm số khoá × truy vấn, softmax theo trục khoá, Vᵀ × trọng số (`KeysFirstAttention`) | 1178 ms | 45 ms, 27 |
| Bỏ Conv nhân 1 của tổng một đầu vào ở stage 0 (`identity_convolutions_dropped`) | 1176 ms | 33 ms, 27 |
| Bình phương trong lớp gộp đọc bản NWC sẵn có, bỏ một Transpose (`lean_transposes`) | 1163 ms | 33 ms, 25 |
| Cả nhánh ngữ cảnh của lớp gộp (trung bình, độ lệch theo thời gian) chạy trên bản NWC (`sink_layouts`) | 1155 ms | 19 ms, 25 |
| Lớp gộp đọc kênh theo băng trước, đúng thứ tự bộ nhớ của chip, trọng số hoán vị theo | **1142 ms** | 8 ms, 24 |

- **Conv đầu stem** từ 110 ms xuống 12 ms, nhưng bản ghép 12 bản sao nằm theo kênh trước nên ESP-PPQ chèn thêm một
  Transpose 33,6 ms trước conv depthwise.
- **Hai Conv của lớp gộp** (576 → 128 và 128 → 576 kênh) mỗi cái 72 KB trọng số int8, lớn hơn cache dữ liệu 64 KB. Nhân
  1×1 của esp-dl đọc lại toàn bộ trọng số ở mỗi khung, nên 148 khung đều đọc từ PSRAM: 94 ms và 77 ms, 2 chu kỳ một
  phép nhân-cộng. Tách mỗi cái thành hai phần 36 KB: 15 ms cả hai.
- **Conv 1×1 của stage 0–2** có 12 hoặc 24 kênh, nên esp-dl chạy nhân "unaligned": 1,4 chu kỳ một phép nhân-cộng, so với
  0,15 ở Conv có số kênh chia hết cho 16. Gộp 2 (hay 4) cột kề nhau vào kênh là Reshape không dời dữ liệu; trọng số khối
  chéo cho đúng từng số nguyên cũ, nhiều gấp 2 (hay 4) phép nhân-cộng nhưng chạy đường vector: bớt 300 ms.
- **Attention:** đầu ra mỗi head chỉ 6 đặc trưng, mà MatMul int8 của esp-dl chạy vector theo 16 cột ra, ít hơn thì chạy
  vòng C: trọng số × giá trị mất 19,3 ms ở stage 0 và 1. Tính chuyển vị (Vᵀ × trọng số, 148 cột ra) còn 2,85 ms; softmax
  theo trục khoá chạy bước nhảy nên 14,8 ms thay 11,3.
- **Tách Conv mức mạng làm đổi int8:** cùng phép tính float, nhưng bias correction của ESP-PPQ hiệu chỉnh theo khối op,
  nên 551/768 giá trị int8 lệch tới một bước dưới thang thật (kl, equalization, bias correction). Đã chuyển sang bước
  xuất; các viết lại khác ở mức mạng (`unit_upsamples`, bình phương bằng phép nhân, attention khoá trước) cho int8
  giống hệt dưới thang ấy.
- Còn lại: gộp đầu ra các stage (`weigth1d`) ~200 ms, sát băng thông PSRAM; Conv depthwise 3×3 24 kênh của stage 0–2
  ~154 ms, nhân unaligned; LayerNorm đầu stem 65 ms, nhân float của esp-dl; Softmax và QuantizeLinear sau nó ~70 ms,
  cũng float; ReLU sau phép cộng dư 52 ms.

**Khớp từng bit trên lưới hiệu chuẩn bằng tiếng thật** (10/10, chuỗi `make speed`). Hàng `fast` ở mọi bước trên được
hiệu chuẩn bằng 4 cửa sổ nhiễu Gauss, nên "khớp từng bit" ở bảng trên chỉ đúng cho lưới ấy. Hàng `timing` của chuỗi
hiệu chuẩn trên 64 cửa sổ tiếng của thang. Trên board, cả 4 bản cắt và cả đồ thị lệch mô phỏng. Chia đôi bằng
`CUT_RANGE`: conv đầu stem khớp, LayerNorm đầu stem lệch.

- **Gốc lỗi:** esp-dl dựng với `-ffast-math -O3` (`CMakeLists.txt` của component). Trong mã máy của LayerNorm,
  trung bình tính qua nghịch đảo `1.0 / n` kiểu double. Tổng bình phương cộng dồn bằng `madd.s`, làm tròn một lần.
  Phương sai nhân với `(1.0f / n)·s²` gộp với epsilon. Gamma nhân với độ lệch nghịch đảo trước, rồi mới nhân giá trị
  đã trừ trung bình. Softmax nhân với nghịch đảo của tổng thay vì chia. Hai bản vá mô phỏng chép theo mã C nên lệch
  1 ulp ở vài giá trị; giá trị nào sát nửa bước thì ra số nguyên khác.
- **Sửa:** `layernorm_as_espdl` và `softmax_as_espdl` chép đúng chuỗi lệnh máy, `madd.s` mô phỏng bằng `fma32`, làm
  tròn một lần.
- **Sau sửa,** hàng `timing` khớp từng bit: 4/4 bản cắt, 0/192 giá trị lệch, lệch lớn nhất 0. Một lần chạy **1140,0 ms**
  (10 lần), PSRAM cho tensor 2 136 796 B.
- **Các nhân float khác của b0:**
  - Sqrt (F32) gọi `sqrtf` IEEE của FPU, làm tròn đúng.
  - ReduceMean chia thật rồi mới nhân tỉ lệ.
  - Div của lớp gộp chia cho hằng 74, nên thương không bao giờ sát nửa bước.
  - Gelu, Tanh là bảng dựng lúc xuất.
  - Cả bốn khớp mô phỏng không cần vá.
- **Các hàng của thang** (kl, percentile, minmax, mse) lượng tử trước bản sửa. Bias correction của chúng chạy trên mô
  phỏng cũ, nên phải chạy lại thang.
- **Hàng kl của thang chạy lại** (equalization, bias correction, mô phỏng đã sửa; 11/10), `make ai-unit-speaker
  ROW=kl`, test board đã siết về khớp từng bit: 4/4 bản cắt, 0/192 giá trị lệch, lệch lớn nhất 0. Một lần chạy
  **1128,5 ms** (10 lần), PSRAM cho tensor 2 135 040 B.
- **Chuỗi `make speed BRANCH=speaker` chạy trọn bốn bước** sau bản sửa:
  - so số nguyên báo không có gì phải so, vì mã mạng là của HEAD;
  - lượng tử hàng `timing`;
  - bản dò khớp từng bit, 1139,3 ms;
  - bảng xếp hạng: 677 module, 1118,7 ms. Chỉ còn nhãn không có bản vá: LayerNorm đầu stem 62 ms và Softmax ~15 ms
    mỗi cái (nhân float), Conv 3×3 24 kênh (nhân unaligned), QuantizeLinear sau Softmax ~9 ms mỗi cái.
- **b0 với trọng số để ở flash** (11/10, E11-T26). Một ca thử tạm của bản dò, không commit, mmap `models_0` rồi
  dựng `dl::Model` ngay trên mục `speaker` với `param_copy` tắt. Hàng kl khớp từng bit: `model->test()` qua, lệch 0.
  Một lần chạy **1171,9 ms**, chậm hơn 1128,6 ms khi trọng số ở PSRAM 43 ms (3,8%). PSRAM chỉ còn vùng tensor,
  1 976 404 B, thay 1,72 MB ảnh cộng 2 135 040 B. Với tối đa 64 lệnh (`ram.md` §4) b0 vừa PSRAM, dư cỡ 1,0 MB.
- **Conv depthwise 3×3 không lợi gì khi đệm kênh lên bội 16.** Bảng xếp hạng đủ 677 module cho chu kỳ trên một phép
  nhân-cộng:
  - stage 0–1, 24 kênh, nhân unaligned: 1,70–1,86;
  - stage 2, 48 kênh, đường aligned: 1,66–1,87;
  - stage 3–5, 96 kênh trở lên: 0,55–0,97.

  Phí nằm ở vòng lặp theo vị trí, không ở số kênh. Đệm 24 → 32 kênh chỉ thêm 33% phép tính, nên bỏ.

**Op riêng của `ai_engine`** (E11-T34, KẾ HOẠCH §3.14 bước 2.5), 27/10 trên board B, `make ai-unit-speaker ROW=kl`, hàng
đang khoá. Mỗi lượt xuất lại `.espdl` từ `graph.native` của hàng với các bản vá ở `speaker.yaml`. Mỗi lượt qua
`model->test()` ở cả đồ thị lẫn 4 bản cắt, lệch 0. Số ms một cửa sổ là trung bình 10 lần chạy không bật
`profile_module`.

| Lượt | Một cửa sổ | PSRAM cho tensor | Bảng xếp hạng |
|---|---|---|---|
| Hàng kl như 11/10 | 1173,7 ms | 2 134 984 B | Softmax 14,9 ms và QuantizeLinear 9,2 ms ở mỗi attention 148 khung |
| `SoftmaxQuant`, tổng cộng thẳng vào bộ nhớ | 1155,5 ms | 2 135 328 B | 20 ms ở mỗi attention 148 khung, ~55 chu kỳ một phần tử |
| `SoftmaxQuant` cộng bốn cột trong thanh ghi, bảng tra theo byte không dấu; `LayerNormS8` | 1100,7 ms | 2 140 252 B | `SoftmaxQuant` 46,4 ms cả 7 op; LayerNorm 123,3 → 92,7 ms, stem 101,5 → 73,6 ms |
| `StageSum` | 943,6 ms | 1 488 352 B | 6 `StageSum` 55,0 ms thay 27 Mul, 21 Add và 4 Resize (~210 ms); PSRAM bớt các tổng giữa và đầu ra Resize |
| `ConvBlock`, nhân pointwise lấy từ face attendance | 701,3 ms | 1 422 656 B | 21 khối 260,7 ms thay ~500 ms Conv, Add, Relu, RequantizeLinear; stage 0 42,7 ms một khối |
| `ConvBlock` viết lại nhân: depthwise nạp kèm nhân-cộng, BN gộp vào pointwise thứ nhất, nhánh dư vào pointwise thứ hai | 611,4 ms | 1 347 148 B | 21 khối 173,9 ms; stage 0 28,4 ms một khối |
| `LayerNormS8`: trung bình bằng tích sửa một FMA, bước Newton đầu không chia, phép chia không lời gọi | 591,1 ms | 1 347 452 B | LayerNorm 92,0 → 72,6 ms, stem 73,6 → 56,7 ms |
| `LayerNormS8` và `SoftmaxQuant` tính 4 giá trị xen nhau, làm tròn bằng một phép cộng rồi cắt | 557,5 ms | 1 349 764 B | LayerNorm 55,7 ms, stem 44,2 ms; SoftmaxQuant 46,3 → 28,8 ms, 7,7 ms mỗi attention 148 khung |
| `softmax_scale`: `Mul` của attention thành bảng tra theo byte | 549,6 ms | 1 348 604 B | Mul 20,3 → 13,0 ms, bỏ 6 `Mul` của attention |
| `MatMulS8`: 36 phép chiếu của TCM cùng `Add` bias | 531,7 ms | 1 424 476 B | MatMul và Add bias ~27 ms → 4,6 ms; PSRAM thêm hàng đệm 32 byte của mỗi phép chiếu 24 kênh |
| `AttentionCore`: cắt head, điểm, softmax, nhân value, gộp head của 6 attention | 508,5 ms | 1 418 652 B | 6 op 28,2 ms thay MatMul 23,9 ms, Transpose 7,6 ms và softmax ~21 ms; attention 148 khung 9,8 ms, ~90% là softmax |
| `StemConv`: bản sao log-mel và depthwise 3×3 của stem | 493,6 ms | 1 422 860 B | 4,5 ms thay Add 7,5 ms và Conv 12,0 ms; phần lớn là ghi 128 KB đầu ra ra PSRAM |
| Softmax theo dải 8 cột bằng nhân PIE, tổng và thang trong thanh ghi FPU | 490,0 ms | 1 422 860 B | `AttentionCore` 28,2 → 24,6 ms (8,4 ms ở 148 khung); softmax của pool vẫn 8,2 ms: map 85 KB lớn hơn cache, mỗi dải cột nạp lại các dòng của nó |
| `StrideDepthwise`: depthwise bước 2 của stage 2 và bước 4 của stage 4 | 489,3 ms | 1 426 392 B | 3,5 và 2,1 ms thay 6,6 và 2,3 ms; cả hai giờ ở trần PSRAM: 128 KB đọc vào, 64 và 32 KB ghi ra |
| `WeightedStats`: x·x, p·x, p·(x·x) và hai `ReduceSum` theo thời gian của pool | 473,8 ms | 1 427 400 B | 2,9 ms thay 3 Mul và 2 ReduceSum (~18,5 ms) |
| `GlobalStats`: trung bình theo thời gian và trung bình bình phương lệch tâm của pool | 462,9 ms | 1 427 444 B | 3,2 ms thay 2 ReduceMean, RequantizeLinear, Sub và Mul (~14,5 ms) |
| `LayerNormS8` hai vị trí một lượt bằng nhân asm, các bước Newton của hai vị trí xen từng lệnh | 439,0 ms | 1 470 804 B | 19 op 55,6 → 31,6 ms, stem 44,2 → 25,1 ms; mỗi op giữ độ lệch float của một khối 16 vị trí |
| Pointwise của `ConvBlock` 24 và 48 kênh trải phẳng, thanh ghi trọng số nạp trước hai bước | 418,2 ms | 1 470 804 B | 21 khối 171,9 → 151,0 ms; khối stage 0 26,7 → 23,2 ms |
| `PointwiseS8`: mọi conv 1×1 còn lại, cùng `Add` dư sau nó khi 24 hay 48 kênh vào | 397,2 ms | 1 439 452 B | 47 op 81,7 ms thay ~94 ms Conv và 13 Add dư; Conv còn 21,9 ms, Add 1,0 ms |
| Depthwise 3×3 của `ConvBlock` từng vector qua cả hàng, tap 0–3 giữ trong thanh ghi | 391,5 ms | 1 439 452 B | 21 khối 151,5 → 145,4 ms |
| `PointwiseS8` nhận conv 2×2 bước 2 của stage 3 theo mảnh | 388,3 ms | 1 443 560 B | 7,8 → 4,8 ms; conv 4×2 của stage 5 (trọng số 37 KB) chạy theo mảnh 8,3 ms, esp-dl 7,9 ms, nên giữ esp-dl |
| Softmax theo nhóm 64 cột, chuẩn hoá khi các hàng còn trong cache; lượt tổng xen 8 cột từng bước | 386,0 ms | 1 443 560 B | softmax của pool 8,2 → 7,0 ms, `AttentionCore` 24,5 → 23,5 ms |
| `ConvBlock` nhận conv 1×1 đầu và cuối stage 0, đầu stage 1 (trọng số gốc lấy lại từ dạng gộp cột) | 366,9 ms | 1 410 952 B | ba `PointwiseS8` gộp cột (8,1, 7,8 và 4,8 ms) bỏ; khối nhận chúng 21,4, 23,2 và 11,2 ms, từ 22,2, 22,1 và 10,4 |
| `PointwiseS8` nhận `Concat` của các lát conv pool, chạy theo lát khối ghi thẳng cột của lát trong hàng ra | 364,2 ms | 1 408 280 B | `Concat` 3,2 → 0,1 ms; `frames` 8,1 → 7,8 ms, `linear2` 8,4 → 5,8 ms |
| `LayerNormS8`: căn của phương sai bằng một bước Newton từ `sqrtf`, giá trị bị đánh dấu về Newton của esp-dl | **356,1 ms** | **1 408 280 B** | LayerNorm 31,5 → 23,5 ms, stem 25,1 → 17,9 ms |

- **`SoftmaxQuant`:** bảng `exp` và nghịch đảo tổng như esp-dl. Xác suất không âm, nên `floor(v + 0,5)` của esp-dl là
  phép cắt của tổng float `v + 0,5`: dưới 2^22 tổng ấy không làm tròn sang phần nguyên khác. Mỗi lượt chuẩn hoá tính 4
  cột xen nhau, còn ~9 chu kỳ một phần tử; `Mul` trước nó thành bảng 256 tích theo byte, tra trước bảng `exp`.
- **`LayerNormS8`:** trung bình là tích với `fl(1/n)` sửa một FMA, đúng thương float, cũng là số của phép `double`
  esp-dl, với mọi tổng int8 khi phần lẻ của n là 1 hay 3 (test kiểm hết); tích trần sai 1018 trên 3061 tổng ở n = 12.
  Bước Newton đầu chia x cho chính nó nên không chia. Trên cửa sổ của bản dò, phương sai của stem nằm trong 13–82,
  86% vị trí cần 5 phép chia Newton, còn lại 6–7, rồi một nghịch đảo. Đo trên board B bằng một app đo riêng: `madd.s`,
  `add.s`, `mul.s` trễ 4 chu kỳ, mỗi chu kỳ nhận một lệnh; chuỗi `div0.s` … `divn.s` của `__divsf3` 33 chu kỳ khi nối
  nhau. GCC xếp vòng float như thể trễ ngắn, ~15 chu kỳ một giá trị, nên cả ba pha viết tay: độ lệch ~147, Newton ~282
  (hai vị trí, ~46 chu kỳ một bước một vị trí), ghi ra ~123 chu kỳ một vị trí stem, từ ~1040. Nhớ kết quả Newton theo
  đối số chỉ trúng 22% với 1024 ô, 39% với 4096 ô.
  Bước cuối Newton của esp-dl, từ r sát √x, là fl(fl(r + fl(x/r))·0,5): nó chỉ phụ thuộc r qua δ²/√x ≥ 0 (δ = r − √x),
  nên một bước từ s0 = `sqrtf(x)` cho đúng số ấy, trừ khi x/s0 nằm sát dưới một điểm giữa lưới. `sqrtf` một mình lệch
  1 ulp ở ~25% giá trị. Duyệt mọi float32 của [2^-3, 2^26): không sai giá trị nào khi đánh dấu phần dư trong 2^-7·h dưới
  ±h và s0 là lũy thừa của 2, 0,78% giá trị; dưới 2^-3 Newton của esp-dl dừng trước khi hội tụ, sai ở 183 giá trị của
  [2^-4, 2^-3). Trên app đo: pha căn 289,8 → 102,0 chu kỳ một vị trí stem, 150 trên 10 656 vị trí về Newton cũ; nhân
  asm so với `sqrt_newton` C++ ở mọi float32 của [2^-3, 2^12), ~126 triệu giá trị, không sai giá trị nào.
- **`StageSum`:** mỗi tổng đọc thẳng các đầu ra stage nó cộng, mỗi hàng một lần, đọc bản 74 và 37 khung trước Resize
  theo hàng `t / k`. Nhân PIE làm đúng các lệnh của `Mul` int8 esp-dl (`EE.VMULAS.S8.QACC`, làm tròn như
  `tie728_s8_vector_round_result`) rồi cộng bão hoà `EE.VADDS.S8` như `Add`. ~50 MB/s đọc PSRAM: chạm trần băng
  thông, không phải phép tính.
- **`ConvBlock`:** mỗi điểm ảnh của khối 24 kênh (đệm 32): chép hàng vào ~100 chu kỳ, mỗi depthwise ~72, pointwise kèm
  BN ~153, pointwise kèm nhánh dư ~160 với nhân chung có vòng. Trên app đo: `ee.vsmulas.s8.qacc.ld.incp` nạp vào thanh
  ghi mà lệnh ngay sau nhân thì mỗi lệnh 2 chu kỳ; nạp lại chính thanh ghi vừa nhân, dùng sau hai bước, thì 1 chu kỳ.
  Mã trải phẳng không chậm hơn vòng phần cứng (1,02 so với 1,00 chu kỳ một lệnh). Nhân trải phẳng một điểm ảnh, đầu vào
  giữ trong thanh ghi: 24 kênh BN 150 → 113, nhánh dư 147 → 94; 48 kênh 301 → 243 và 293 → 209 chu kỳ một điểm ảnh.
  96 kênh chỉ bớt ~12% mà mã dài 2,6 KB mỗi nhân, nên giữ nhân chung. Depthwise 3×3: QACC không thêm chu kỳ nào (9
  nhân-cộng, làm tròn và ghi: 16 chu kỳ cho 14 lệnh); nhân cũ theo điểm ảnh ~30 chu kỳ một vector cho 25 lệnh, nhân
  theo vector giữ tap 0–3, 20 lệnh, 23,5 chu kỳ ở hàng 72 điểm ảnh, 24,7 ở 18 và 25,6 ở 9.
  Gộp các khối liền nhau của một stage thành một op (mỗi khối một vòng hàng, hàng ra vào thẳng vòng khối sau) khớp
  từng bit nhưng cửa sổ 391,5 → 452,9 ms: vòng hàng và trọng số của mọi khối phải cùng nằm trong dcache 64 KB.
  Stage 5 (96 kênh, ~55 KB trọng số) 13,4 → 38,5 ms, stage 3 30 → 49 ms, stage 0 44,4 → 44,2 ms. Bỏ.
  Conv 1×1 ngay trước và sau một khối thì vào được: nhân 12 giá trị vào mỗi điểm ảnh ~75 chu kỳ, nhân 24 vào 12 ra
  ~58 chu kỳ trên app đo, trọng số dưới 1 KB, nên vùng dữ liệu nóng của khối gần như không lớn thêm.
- **`PointwiseS8`:** trên app đo, nhân có vòng với K bội 16 chạy 0,083–0,093 chu kỳ một phép nhân-cộng; K = 24 giữ
  trong thanh ghi cộng nhánh dư 0,11–0,12; esp-dl 0,13–0,45. Bỏ hẳn `aligned_pointwise` thì ba conv 12 kênh của stage
  0 và 1 rơi về nhân không căn của esp-dl, 2–3 chu kỳ một phép nhân-cộng, cửa sổ lên 472,6 ms; nên `aligned_pointwise`
  chỉ để nguyên cột các conv `PointwiseS8` nhận. `exp_dim` với `Add` 9,6 → 6,3 ms ở stage 0 và 1, `red_dim` 4,5 →
  3,4 ms; ba conv gộp cột 12 kênh còn 8,1, 7,8 và 4,8 ms, phần lớn là ghi map 256 KB ra PSRAM. Hai conv của pool
  (trọng số 72 KB, lát 48/48/32 và 3 × 192 cột) thành một op mỗi conv, lát nào cũng qua mọi hàng rồi ghi đúng cột
  của mình: `linear2` bớt đúng phần `Concat` chép map 85 KB (2,6 ms); `frames` chỉ bớt 0,3 ms vì mỗi lát vẫn đọc lại
  map vào 85 KB, lớn hơn dcache.
- **Depthwise theo thời gian của TCM** (24 conv, kernel 7–59, cùng Gelu sau nó; ~7,6 ms): một op riêng có nhân
  khớp từng bit, 0,05–0,18 ms một op trên app đo, nhưng cả op 0,2–0,6 ms, 24 op 8,6 ms, cửa sổ 366,9 → 367,7 ms. Phần
  thêm là bộ đệm riêng của mỗi op trong PSRAM (hàng vào đệm lên 32 kênh, hàng ra, ~10 KB) nạp nguội mỗi lần chạy, và
  lượt tra bảng. Đọc x tại chỗ (hàng 24 byte) thì mỗi vector phải nạp hai nửa 8 byte, nhân chậm gấp đôi; lợi còn
  ~4 ms. Bỏ, giữ conv của esp-dl.
- **`MatMulS8`:** MatMul của esp-dl với 24 cột chạy 16 cột qua nhân unaligned, 8 cột còn lại qua vòng C, ~2,25 chu kỳ
  một phép nhân-cộng; cả hai làm tròn nửa lên như tích chập không bias, nên một hàng là một pointwise.
- **`AttentionCore`:** hai tích chạy nhân pointwise của `MatMulS8` theo khối 16 khung query, ~0,5 ms ở attention 148
  khung; phần còn lại là softmax của khối, ~23 chu kỳ một phần tử với nhân dải 8 cột. Trên app đo: lượt tổng 5,2 → 4,2
  chu kỳ một phần tử khi 8 cột xen nhau từng bước, lượt chuẩn hoá 8,3.
- **PSRAM của board B** (27/10, vùng 256 KB, cache trống trước mỗi lượt): đọc tuần tự 91 MB/s, ~0,7 µs mỗi dòng cache
  64 byte; `memcpy` 31,6 MB/s, vì mỗi dòng đích được nạp vào cache trước khi ghi đè rồi mới ghi ngược. Autoload của
  dcache mặc định tắt (`EXTMEM_DCACHE_AUTOLOAD_CTRL` = 0x8); bật cho đúng vùng đọc, mọi kiểu kích và mọi cỡ, không
  đổi gì. Bật cho cả vùng 32 MB từ 0x3C000000 thì cache báo lỗi cấu hình preload và chip khởi động lại. Op chạm trần
  băng thông chỉ nhanh lên khi bớt lượt đọc ghi, tức là gộp op.
- Op tốn nhất còn lại: `ConvBlock` 146,8 ms; `PointwiseS8` 66,3 ms; `StageSum` 53,6 ms; LayerNorm 31,1 ms, stem
  25,1 ms; `AttentionCore` 24,6 ms; Conv 14,2 ms, trong đó conv bước 4×2 của stage 5 7,9 ms; softmax theo thời
  gian của pool 8,2 ms.

## 26. `command_ctc` qua chuỗi tăng tốc (E11-T30)

Đo ngày 10/10 trên board B bằng `make speed BRANCH=ctc RUN=artifacts/command_ctc/runs/20261007_439d763-dirty_f438bd
KALDI=1 HOLD=pitch`. Đây là run đang nằm trong firmware (hàng `percentile`, nghe trên pitch Kaldi, chiều `pitch` gập về
trung bình). Bản dò chạy hàng `timing` của run, dựng `UNIT_PROFILE=ctc`.

| Bước | Kết quả |
|---|---|
| So số nguyên với HEAD | 0 giá trị lệch |
| `model->test()` | qua |
| `ctc_lay`, 256 bước 1 hop | lệch 0; 7043 µs trung bình, 7162 µs đỉnh |
| `command_ctc`, 48 bước 16 hop (256 ms âm thanh) | lệch 0; **81,2 ms** trung bình, 81,5 ms đỉnh; PSRAM 310 552 B |
| Bảng xếp hạng | 328 module, 82,4 ms một bước |

- **Khớp từng bit:** phép kiểm luồng của mạng ctc nay đòi lệch 0. Trước đây nó cho lệch một bước. Lần đo ngày 08/10 cũng
  đã ra 0.
- **Theo loại op:** Conv 68,0 ms trên 89 op; còn lại mỗi loại dưới 3,3 ms.
- **Chậm nhất:**
  - `/front/proj`, Conv 1×1 963 → 160 kênh, 14,5 ms. Hai nhãn: 963 không chia hết cho 16, nên chạy nhân unaligned; và
    150 KB trọng số vượt cache dữ liệu.
  - `/front/convs.0`, Conv 3×3 1 → 8 kênh, 9,1 ms, nhân unaligned.
  - Hai Transpose do bước xuất chèn, 2,1 ms.
- **Các conv còn lại** mỗi cái 0,7–1,3 ms, sát băng thông PSRAM. Mỗi bước 16 hop đọc lại toàn bộ 3,1 MB trọng số, cỡ
  38 MB/s.
- **Lỗi của bảng:** cột chu kỳ trên phép nhân-cộng của bảng xếp hạng sai với mạng chạy dòng. Nó lấy hình dạng trong
  `.info`, ghi cho cả chuỗi 768 hop, không phải bước 16 hop.
- **Bản vá xuất cho ctc (11/10), bước 2.3 của quy trình:** bật `cache_sized_convolutions`, `lean_transposes`,
  `lean_quantizers`. `command_ctc` từ 82,2 xuống **69,7 ms** một bước, vẫn lệch 0. `/front/proj` 963 → 160 kênh tách
  thành các lát vừa cache dữ liệu, ra khỏi đầu bảng. Đầu bảng giờ là `/front/convs.0`, Conv 3×3 1 → 8 kênh, 9,1 ms,
  không bản vá nào xử lý được.
- **Viết lại `front` (11/10), bước 2.4:** Conv 3×3 đầu tiên 1 → 8 kênh thành 8 bản sao của kênh mel đặt trước phần
  đệm nhân quả, rồi một conv depthwise (`CopiedFront`). `ctc-same-int8` báo 0 giá trị lệch so với bản chưa viết lại.
  Đặt bản sao sau phần đệm, như cách của b0, thì Pad nằm lẻ giữa đồ thị chạy dòng và esp-dl assert trong
  `Concat::get_output_shape` lúc dựng. Đặt trước thì Pad vẫn sát conv, ESP-PPQ gộp nó rồi đổi thành cache.
  `/front/first` từ 9,1 xuống 1,3 ms; `command_ctc` **63,9 ms** một bước, lệch 0.
- **`copies_by_broadcast` không bật cho ctc:** bản vá ghi cứng hình dạng của cả chuỗi vào Reshape, nên với đồ thị
  chạy dòng nó đổi `[1, 1, 16, 80]` thành `[1, 768, 80, 1]`. `espdl_shapes` chặn ngay trên máy tính, trước khi nạp
  board. Bản vá chỉ bỏ được một Transpose cỡ 1 ms một bước.
- **Dừng giai đoạn tăng tốc của ctc ở 63,9 ms một bước**, từ 82,2. Các conv của stack mỗi cái cỡ 1 ms, sát băng
  thông PSRAM, vì mỗi bước đọc lại 3,1 MB trọng số. `/front/first` và `/front/convs.1` 1,3 ms mỗi cái, kênh 8 không
  bản vá nào xử lý. Hai Transpose đổi bố cục ở đầu mạng.
- **RMSNorm theo mã máy (11/10):** bản dựng S3 của esp-dl tính `mean_sq + eps` bằng một `madd.s`, với hằng
  `(1.0f / n)·s²` tính sẵn. Bản vá cũ chia thật cho n rồi mới cộng, mà 1/160 không biểu diễn đúng ở float32. Sau khi
  `rmsnorm_as_espdl` chép đúng mã máy, chuỗi chạy lại vẫn khớp từng bit: `command_ctc` lệch 0, 82,2 ms một bước.
- **Cả chuỗi nghe trên chip với mạng đã tăng tốc** (11/10, `make listen-unit`, bảy lượt, 08:45–09:21). Ảnh model là
  hàng `percentile` của run đã deploy lại sau bước 2.4 (`command_ctc.espdl` sha256 `1e57538e…`), cùng ngưỡng của ảnh.
  So với lần đo ngày 08/10 ở §22, cùng 336 cửa sổ:

  | Đo | 08/10, 82,2 ms một bước | **11/10, 63,9 ms một bước** |
  |---|---|---|
  | Cửa sổ trùng Python từng trường | 336/336 | **336/336**, mọi lượt `0 decided otherwise than python` |
  | Ca `FRAME_GAP` và ca click | qua cả bảy lượt | qua cả bảy lượt, 3/3 bài thử mỗi lượt |
  | Quyết định sau bước chốt, trung bình mỗi lượt | 76–114 ms | **61–91 ms** |
  | Đỉnh ở lượt không có cửa sổ cắt lùi | 86–88 ms | **68–71 ms** |
  | Đỉnh ở cửa sổ cắt lùi | tới 1,28 s | **tới 1,01 s** |
  | Việc của một cửa sổ, trung bình mỗi lượt / đỉnh | 775–949 ms / 1,27 s | **614–752 ms / 1,01 s** |

  - Mạng nhanh hơn 22% thì phần sau bước chốt bớt 15–23 ms trung bình, vì lúc câu chốt chỉ còn khối dở cuối.
  - Cửa sổ cắt lùi chạy lại cả cửa sổ sau khi câu chốt, nên bớt gần đúng tỉ lệ ấy: 1,28 → 1,01 s.
  - Watchdog kêu tên IDLE0 khi bài thử phát lại các phiên liền nhau trên task chính. Đó là app thử, không phải firmware
    sản phẩm.
- **`make ai-unit` cùng ngày, cùng ảnh model** (CTC_ROW=percentile, KALDI, HOLD=pitch; 09:21–09:49). App thử dựng
  được với `<initializer_list>`. Lượt Cửa 3 đầu, 158 cửa sổ trên phân vùng `voice`, **0 quyết định khác Python**,
  56 µs một hop. Ngay sau đó board ngừng in giữa dòng tổng kết của lượt ấy (ký tự thứ ~70 của ~210), pytest chờ đủ
  900 s rồi báo hết hạn; các lượt còn lại không chạy. Console in theo kiểu chờ FIFO, nên CPU dừng giữa `printf`, hoặc
  đường USB/IP ngừng chuyển; dmesg không có lỗi `vhci_hcd` lúc ấy, và esptool nối lại được ngay sau đó. Cùng đoạn mã
  qua ở lần đo 08/10. Chưa rõ gốc, chưa coi là qua (TASKS E11-T30).

## 27. `speaker` trên chip trong chuỗi: từ PCM tới quyết định (E11-T26)

Đo ngày 11/10 trên board B bằng `make ai-unit-speaker RUN=artifacts/speaker/runs/20261010_5a45475-dirty_94e5a9 ROW=kl
CHAIN=1` (`1fb1725`). Ảnh model xếp như `pack_models --lock`: mục `speaker` là `speaker.espdl` đã deploy (sha256
`c3373e4e…`), cờ để ở flash; header mang `speaker_hash` `0xd94df25c` và ngưỡng 744 ‰. Ca `[speaker_chain]` đi đúng
đường của `xac_task`: 24 000 mẫu PCM → `spk_fbank` → lưới int8 của đầu vào → b0 → độ dài đơn vị → mẫu giọng → điểm.

| Đo | Kết quả |
|---|---|
| Cửa sổ | 22: 10 cửa sổ đăng ký (phiên 28/09, 1 m); 6 cửa sổ của chủ repo lấy lần lượt từ ba phiên còn lại; 6 người lạ khác nhau, mỗi người một cửa sổ ngoài bộ hiệu chuẩn |
| So Python trên đường chip | 0/22 embedding lệch, mẫu giọng trùng từng bit, 0/12 điểm và quyết định lệch |
| Chủ repo qua | 6/6, điểm 839–882 ‰ |
| Người lạ lọt | 0/6, điểm 582–728 ‰ |
| Một lần xác minh, `ai_engine_speaker_embed` | **1273,0 ms** trung bình, 1273,4 ms đỉnh |
| trong đó `spk_fbank` | 96,3 ms trung bình |
| Riêng mạng b0, ca `[speaker]`, trọng số ở flash, 10 lần | 1174,6 ms; PSRAM cho tensor 2 134 920 B |

- Ngoài mạng và `spk_fbank`, phần còn lại là lượng tử đầu vào và độ dài đơn vị, cỡ 2 ms.
- Người lạ gần ngưỡng nhất ở 728 ‰, dưới ngưỡng 16 ‰.

**Cờ tắt, `make listen-unit`** (11/10 10:59–11:46, `46592aa`). Ảnh model do `pack_models --lock` ghi, có cả mục
`speaker`: 5,25 MB, trong đó 3373 KB chép vào PSRAM, còn `speaker` để ở flash. Mỗi lượt tự tắt cờ, bất kể NVS đang
giữ gì.

| Đo | Kết quả |
|---|---|
| Cửa sổ trùng Python từng trường | **336/336**, bảy lượt đều `0 decided otherwise than python` |
| Ca `FRAME_GAP` và ca click | qua cả bảy lượt |
| Quyết định sau bước chốt, trung bình mỗi lượt | 61–91 ms |
| Đỉnh ở lượt không có cửa sổ cắt lùi / ở cửa sổ cắt lùi | 69 ms / 1,01 s |
| Việc của một cửa sổ, trung bình mỗi lượt / đỉnh | 614–753 ms / 1,00 s |
| Lúc nạp: dựng b0, gồm một lần chạy thử | 1784 ms, PSRAM 2 134 924 B |
| Lúc nạp: `spk_fbank_init` | ~490 ms |

- Mọi con số trùng §26. Vậy có `speaker` trong ảnh mà cờ tắt thì Cửa 3 không đổi.
- Lượt 1 mất byte ở hai dòng in dồn cuối phiên, trong đó có dòng tổng kết, trên đường USB/IP (TASKS E11-T30). Dòng
  `PASS` và `3 Tests 0 Failures` của lượt ấy vẫn nguyên.
- `spk_fbank_init` tính `log10` kiểu double mềm cho mỗi cặp băng và vạch, 72 × 255 lần.

**`spk_fbank` nhanh hơn** (`4e68ac5`, `6e9b719`). Đo lại bằng ca `[speaker_chain]` (`8cc541f`, 11/10 12:01): 22 cửa
sổ vẫn khớp Python từng bit, chủ repo qua 6/6, người lạ lọt 0/6.

| Đo | Trước | Sau |
|---|---|---|
| `spk_fbank` một cửa sổ | 96,3 ms | **79,3 ms** |
| Một lần xác minh | 1273,0 ms | **1256,0 ms** trung bình, 1256,4 ms đỉnh |
| Từ dòng dựng b0 tới dòng sẵn sàng của nhánh, gần hết là `spk_fbank_init` | ~490 ms (app `svc_listen`) | ~141 ms |

- Lần xác minh đầu sau khi nạp mất 1305,0 ms, vì đọc trọng số khi cache flash còn nguội. Lần ấy không giữ byte heap
  nào.
- **Không bỏ được lần chạy thử lúc dựng b0.**
  - Bỏ thì dựng chỉ còn 0,60 s thay vì 1,78 s.
  - Nhưng lần xác minh đầu khi ấy giữ 1 232 B RAM nội và 55 360 B PSRAM: esp-dl cấp phát ở lần chạy đầu của đồ thị,
    ngoài `ExternalMallocGuard`.
  - Vì vậy giữ lần chạy thử. Ca chuỗi đo heap quanh lần xác minh đầu, giữ một byte là đỏ.

**Ca chuỗi với đủ 8 mẫu giọng** (E11-T31, 11/10 15:13, `ec612f9`). `make ai-unit-speaker RUN=… ROW=kl CHAIN=1`.
- Ảnh mang bảng 8 ngưỡng của `speaker.md` §6.4.
- Mẫu giọng của chủ repo gập từ PCM của 10 cửa sổ đăng ký.
- 7 thành viên là 7 người lạ đầu tiên theo mã người nói, có 11 cửa sổ ngoài bộ hiệu chuẩn. Mỗi người gập từ embedding
  của 10 cửa sổ, Python tính trên đường chip.
- Cửa sổ thử: 6 của chủ repo, 1 cửa sổ nữa của mỗi thành viên, 6 người lạ khác.

| Đo | Kết quả |
|---|---|
| So Python | 0/29 embedding lệch, 8/8 mẫu giọng trùng từng bit, 0/19 điểm lớn nhất, mẫu cho điểm ấy và quyết định lệch |
| Mẫu khớp nhất | chủ repo 6/6 rơi vào mẫu của mình; thành viên 5/7 |
| Qua ở 853 ‰, ngưỡng cho 8 người | chủ repo 3/6, điểm 839–882 ‰; thành viên 1/7; người lạ 0/6, cao nhất 842 ‰ |
| Một lần xác minh, `ai_engine_speaker_embed` | **1257,0 ms** trung bình, 1257,5 ms đỉnh; lần đầu sau nạp 1306,1 ms |
| trong đó `spk_fbank` | 79,3 ms trung bình |
| Lần xác minh đầu sau nạp giữ lại | 0 B RAM nội, 0 B PSRAM |

- 1257,0 ms so với 1256,0 ms khi chấm với 1 mẫu. Thêm 7 tích vô hướng 192 chiều chỉ cỡ chục µs, nên 1 ms là dao
  động giữa hai bản dựng.
- Chủ repo bị chặn 3/6 ở ngưỡng 8 người, đúng như bảng của `speaker.md` §6.4 báo trước.

**Lệch hiếm của b0: bảng `exp` của Softmax** (11/10, `c659b67`). Lượt `make listen-unit SPEAKER=1` đầu tiên khớp hai
lượt, kể cả 10 lệnh đăng ký và mẫu giọng giữ qua khởi động lại. Ở lượt 2, cửa sổ 326..500 của phiên thứ ba trên
`models_1` có điểm board 829 ‰, Python 844 ‰; quyết định vẫn là qua, mọi cửa sổ khác trùng.

- **Khoanh vùng:**
  - tiếng board giữ là đúng 24 000 mẫu cuối của cửa sổ;
  - `spk_fbank` bằng C trên đúng cửa sổ ấy khớp bản soi gương từng bit;
  - bản dò board trên cửa sổ ấy (`make ai-unit-speaker PCM=…`, `77b8880`), chia đôi bằng `CUT_RANGE`, cho b0 khớp
    tới phép nhân tỉ lệ sau MatMul QK trong attention của `stage1.7/tcm.4`, và lệch ở MatMul sau Softmax.
- **Gốc lỗi:**
  - Softmax int8 của esp-dl dựng bảng 256 ô ở lần chạy đầu bằng `tool::gen_lut_8bit(table, exponent, expf)`;
  - chip liên kết `expf` của picolibc: thuật toán float32 của fdlibm, `libm/math/sf_exp.c`, mỗi bước làm tròn một
    lần, không gộp `madd.s`, phép chia là `__divsf3`;
  - bản vá `softmax_as_espdl` lấy `exp` kiểu double rồi làm tròn về float32, lệch 1 ulp ở 16–33 trên 256 ô của mỗi
    số mũ từ −9 tới 0;
  - một ulp chỉ thỉnh thoảng đẩy một xác suất qua nửa bước lưới của op sau, nên các ca trước đều khớp.
- **Sửa:** `expf_s3` chép đúng mã máy ấy, `exp_table_s3` dựng bảng như `gen_lut_8bit`.
- **Sau sửa:**
  - bản dò của cửa sổ ấy khớp ở mọi bản cắt và cả đồ thị: 0/192 giá trị lệch, lệch lớn nhất 0;
  - `make speaker-deploy` chạy lại cho ngưỡng 744 ‰ như cũ và `speaker.espdl` giống từng byte.
- **Cũng thấy ở đây:**
  - Lần chạy đầu cấp phát bảng `exp` (1 KB RAM nội mỗi Softmax, `MALLOC_CAP_DEFAULT`). Đó là phần lớn của 1 232 B RAM
    nội mà lần xác minh đầu giữ khi bỏ lần chạy thử lúc dựng.
  - Đường USB/IP còn làm mất dòng kết quả của vài bản cắt và dòng tổng kết. pytest khi ấy chờ tới hết giờ (TASKS
    E11-T30).

**Cờ tắt, firmware có danh sách người nói** (E11-T26, E11-T31, 11/10 16:51–17:22). Firmware dựng lúc 16:42 từ
`db5ed04`, trước Z-norm. Chạy `make listen-unit` qua cầu RFC 2217 (TASKS E11-T30).

| Đo | Kết quả |
|---|---|
| Cửa sổ trùng Python từng trường | **336/336**, 43 phiên, bảy lượt đều `0 decided otherwise than python` |
| Ca `FRAME_GAP` và ca click | qua cả bảy lượt |
| Quyết định sau bước chốt, trung bình mỗi lượt | 61–91 ms |
| Đỉnh ở lượt không có cửa sổ cắt lùi / ở cửa sổ cắt lùi | 69–71 ms / 1,01 s |
| Việc của một cửa sổ, trung bình mỗi lượt / đỉnh | 613–751 ms / 1,01 s |

- Các số cùng khoảng với lượt 10:59 ở trên. Vậy khi cờ tắt, danh sách người nói và ba hàng đợi của nó không đổi Cửa 3.
- Cả bảy lượt in đủ dòng tổng kết: qua cầu RFC 2217, log không mất byte nào.

**Ca chuỗi với 8 mẫu giọng, chấm bằng Z-norm** (E11-T31, ADR-0022, 11/10 17:31). Firmware dựng từ `7c77692`.
- Ảnh khuôn 7 mang nhóm chuẩn 380 hàng ở flash, cùng bảng ngưỡng theo ‰ z của `speaker.md` §6.5. Ngưỡng cho 8 người là
  2034.
- Cửa sổ và thành viên như ca 15:13 ở trên. Thành viên và người lạ nằm ngoài nhóm chuẩn.

| Đo | Kết quả |
|---|---|
| So Python | 0/29 embedding lệch, 8/8 mẫu giọng trùng từng bit, 0/8 cặp μ, σ lệch, 0/19 quyết định lệch (điểm z, mẫu cho điểm ấy, cosine của mẫu ấy) |
| Qua ở 2034 ‰ z | chủ repo **6/6**, điểm 2695–3200; thành viên 1/7; người lạ 0/6, cao nhất 1564 |
| Mẫu cho điểm cao nhất là mẫu của chính người nói | chủ repo 6/6; thành viên 2/7 |
| Một lần xác minh, `ai_engine_speaker_embed` | **1256,7 ms** trung bình, 1257,9 ms đỉnh; lần đầu sau nạp 1298,2 ms |
| trong đó `spk_fbank` | 79,3 ms trung bình |
| Lần xác minh đầu sau nạp giữ lại | 0 B RAM nội, 0 B PSRAM |

- So với điểm thô ở 853 ‰ (ca 15:13): chủ repo qua 3/6 → 6/6. Người lạ vẫn lọt 0/6, thành viên vẫn qua 1/7.
- Chấm theo z không làm lần xác minh chậm đi: 1256,7 ms so với 1257,0 ms. μ, σ của mỗi mẫu tính một lần, lúc nạp và
  khi đăng ký xong.
- Mẫu cho điểm cao nhất đúng là mẫu của thành viên 2/7, khi chấm bằng cosine là 5/7. Thành viên là người lạ qua đường
  mô phỏng, cùng kênh với nhau, mà giữa những người cùng kênh b0 tách kém (EER 19,3%, `speaker.md` §6.4). Z-norm đổi
  thứ tự giữa các mẫu: cửa sổ của thành viên thứ ba cho z cao nhất với mẫu của chủ repo, 931, dưới ngưỡng.

**Cờ bật, Z-norm, `make listen-unit SPEAKER=1`** (11/10 17:43–18:19). Firmware dựng từ `c0a2a5c`; ADR-0023 thay Z-norm
ngay sau đó, nên lượt này chỉ còn là bằng chứng cho phần danh sách và đăng ký.

| Đo | Kết quả |
|---|---|
| Cửa sổ trùng Python từng trường, kể cả điểm, mẫu khớp nhất và quyết định của người nói | **336/336**, 43 phiên, bảy lượt |
| Đăng ký | lượt 0 xoá mẫu, lấy 10 lệnh đầu thành mẫu của `spk_001`; sáu lượt sau khởi động lại với 1 mẫu đọc từ NVS |
| Quyết định của board | 150 lệnh được nhận, đều mang `spk_001`; 10 `SPEAKER_ENROLLING`; 10 `SPEAKER`, điểm −581 tới 1593, dưới ngưỡng 1656 |
| Quyết định sau bước chốt, trung bình mỗi lượt / đỉnh | 61–91 ms / 1,01 s, như cờ tắt |

**Ca chuỗi với 8 mẫu giọng, chấm bằng AS-norm** (E11-T31, ADR-0023, 11/10 18:27). Firmware dựng từ `67b1c69`.
- Ảnh khuôn 8: nhóm chuẩn 380 hàng, K = 100, bảng ngưỡng của `speaker.md` §6.7. Ngưỡng cho 8 người là 3795.
- Cửa sổ và thành viên như ca 15:13.

| Đo | Kết quả |
|---|---|
| So Python | 0/29 embedding lệch, 8/8 mẫu giọng trùng từng bit; 0/8 cặp μ, σ của mẫu giọng, 0/19 cặp μ, σ của cửa sổ và 0/19 quyết định lệch |
| Qua ở 3795 | chủ repo **6/6**; thành viên 1/7; người lạ 0/6, cao nhất 1874 |
| Mẫu cho điểm cao nhất là mẫu của chính người nói | chủ repo 6/6; thành viên 2/7 |
| Một lần chạy `ai_engine_speaker_embed` | 1257,0 ms trung bình, 1258,3 ms đỉnh; lần đầu sau nạp 1306,1 ms; `spk_fbank` 79,3 ms |
| Lần xác minh đầu sau nạp giữ lại | 0 B RAM nội, 0 B PSRAM: chỗ chấm trên nhóm chuẩn cấp lúc nạp |

- Như Z-norm ở ca 17:31: chủ repo qua 6/6, người lạ 0/6, thành viên 1/7 và 2/7 đúng người. Ngưỡng nay đặt ở 1% người lạ
  lọt thay vì 5%.
- Ca này chưa đo riêng phần chấm, tức μ, σ của cửa sổ trên 380 hàng cùng 8 điểm.

**Cờ bật, AS-norm, `make listen-unit SPEAKER=1`** (E11-T26, E11-T31, 11/10 18:39–19:15). Firmware dựng từ `67b1c69`,
ảnh khuôn 8 như ca chuỗi 18:27.

| Đo | Kết quả |
|---|---|
| Cửa sổ trùng Python từng trường, kể cả điểm, mẫu khớp nhất và quyết định của người nói | **336/336**, 43 phiên, bảy lượt |
| Đăng ký | lượt 0 xoá mẫu, lấy 10 lệnh đầu của phiên 28/09 1 m thành mẫu của `spk_001`; sáu lượt sau đọc mẫu từ NVS |
| Quyết định của board | **150** lệnh được nhận, đều mang `spk_001`; 10 `SPEAKER_ENROLLING`; **10 `SPEAKER`**, cosine 562–798 ‰ |
| Quyết định sau bước chốt, trung bình mỗi lượt / đỉnh | 61–91 ms / 1,01 s, như cờ tắt |

- **Như Z-norm ở ngưỡng 5%:** cũng 150 lệnh được nhận, 10 lệnh bị chặn, và 8 trong 10 lệnh bị chặn trùng nhau. Vậy
  AS-norm giữ lệnh của chủ repo như Z-norm, với 1% người lạ lọt thay vì 5%.
- **Vì sao 93,8% chứ không ~97,6% như bảng `speaker.md` §6.7:**
  - 10 lệnh đăng ký của lượt phát lại chỉ có 3 câu: "bật đèn" 5 lần, "tắt đèn" 4 lần, "bật quạt" 1 lần. Kịch bản phiên
    thu lặp mỗi câu liền nhau.
  - Trên vật liệu của khảo sát, mẫu từ 10 cửa sổ ấy giữ 94,6% (95,5% ở 1 m, 93,7% ở 3 m), kém mọi lần trong 20 lần
    rút ngẫu nhiên (97,0–98,2%, trung vị 97,6%). Đó là chỗ lệch, không phải ở chip.

**Cờ tắt, AS-norm, `make listen-unit`** (E11-T26, 11/10 19:25–19:57). Firmware dựng từ `6521d03`, ảnh khuôn 8.

| Đo | Kết quả |
|---|---|
| Cửa sổ trùng Python từng trường | **336/336**, 43 phiên, bảy lượt đều `0 decided otherwise than python` |
| Ca `FRAME_GAP` và ca click | qua cả bảy lượt |
| Quyết định sau bước chốt, trung bình mỗi lượt / đỉnh | 61–91 ms / 1,01 s, như các lượt trước |

- Như lượt 16:51 trên `db5ed04`. Vậy khi cờ tắt, AS-norm không đổi Cửa 3.
- Lượt 3: log in ra màn hình mất 6 cửa sổ cuối cùng dòng tổng kết, lúc pytest đóng phiên. `dut.log` của pytest-embedded
  có đủ 58/58 cửa sổ và `0 decided otherwise than python`; số của lượt ấy lấy từ đó.

## 28. `speaker` trên firmware sản phẩm: danh sách, đăng ký, xác minh (E11-T26, E11-T31)

Đo ngày 11/10 trên board B, profile `bench`, ảnh khuôn 8 của `990fe70`, qua cầu RFC 2217. Broker EMQX ở máy tính,
`make host-live` ghi mọi tin `up/` vào một tệp jsonl.

**Trả board về sản phẩm.**
- `make fw-bench-flash` và `make models-flash` không ghi phân vùng `storage`, mà LittleFS lúc ấy không còn `set.json`.
  Board khởi động nhưng không nghe (`/lfs/cmd/set.json: not listening`). Ghi lại `storage.bin` dựng cùng `fw-bench`
  thì board nghe bộ 10 lệnh mặc định.
- App thử `svc_listen` xoá `kws/cmd_reject`, `cmd_margin`, `cmd_syllable`, nên firmware gieo lại bộ của ảnh: δ1 200,
  δ2 0, δ3 60. Ngưỡng cũ của board vẫn đọc được trong NVS, ở các mục đã xoá: 300, 50, 65535 (δ3 tắt).
  - Với bộ của ảnh, 168 câu nói thử liền không câu nào được nhận: 110 `LOW_SCORE`, 55 `LOW_SYLLABLE`, 3 `PART`.
  - Chủ repo bảo đặt lại như cũ; đặt qua console `nvs set`, board nghe ở 300 / 50 / 65535.

**Danh sách người nói qua `make speakers`.**

| Bước | `up/speakers` đọc lại |
|---|---|
| Danh sách chỉ có `spk_002`, bỏ `spk_001` đang có mẫu giọng (app thử để lại) | 1 người, `spk_002` chưa có mẫu |
| `host/speakers/bench_1_add.json` | 2 người: `spk_002` "Khách", `spk_001` "Chủ nhà" |
| `bench_2_edit.json` | 3 người: `spk_002` thành "Khách quen", thêm `spk_003` "Bé Na" |
| `bench_3_delete.json` | 2 người: `spk_001`, `spk_003` |
| Danh sách trùng `id`, rồi danh sách có `id` ngoài khuôn, gửi không retained | `ERROR SPEAKERS_INVALID` cả hai lần; danh sách giữ nguyên |
| `make speaker-enrol ID=spk_009` | `ERROR SPEAKER_UNKNOWN` |

- **Xoá người là xoá mẫu giọng.** NVS đọc bằng `parttool` ngay sau khi gửi danh sách, trước khi app khởi động lại:
  - trước: `speaker/tpl0` ở trạng thái Written, 768 B;
  - sau: cả hai mục của `tpl0` là Erased, không còn khoá `tpl` nào Written.
- NVS chỉ đánh dấu xoá: 768 B ấy còn nằm trên trang flash tới khi NVS dọn trang.

**Đăng ký và xác minh.**
- `make speaker-enrol DEVICE=sr-3485188f7a70 ID=spk_001`: chủ repo nói 10 lệnh khác nhau, board giữ đủ 10 và
  `spk_001` thành `enrolled`. Mẫu giọng còn sau mỗi lần khởi động lại.
- **Rơi khung sau mỗi lần chạy b0** (`FREERTOS.md` 2.7):
  - qua 10 lệnh đăng ký, `clean_dropped` lên 0 → 192 → 290, và hai cửa sổ ra `REJECT FRAME_GAP`;
  - lệnh tới `xac_task` từ giữa đợt việc cửa sổ, lúc `nhan_task` còn ở ưu tiên 2. Vậy suốt 1,26 s của b0, `nhan_task`
    không lấy khung, mà `q_clean` 64 bước chỉ chứa ~1 s;
  - sửa ở `ab23bce`: `q_clean` 128 bước (KẾ HOẠCH §5.3).
- **Sau khi sửa,** chủ repo nói hai lượt lệnh, có cặp nói liền nhau:

| Đo | Kết quả |
|---|---|
| Lệnh được nhận và qua kiểm người nói | 9/9 mang `spk_001`, cosine 809–882 ‰; 3 lệnh có log console cho điểm AS-norm 5654–6571 (ngưỡng 1 người 2964) |
| `clean_dropped`, `frames_dropped`, `dma_overflows` qua 9 lần chạy b0 | **0**, không còn `FRAME_GAP` |
| Một lần xác minh `svc_listen_verify` trên firmware, log của `app_tasks` | **1340–1343 ms**; lần đầu sau khởi động 1399 ms |
| PSRAM còn khi nghe | 538 004 B (`ram.md` §4) |

- **Xác minh trên firmware chậm hơn ca chuỗi.** 1340 ms so với 1257 ms của `ai_engine_speaker_embed` không tải ở §27:
  - trong khi b0 chạy, `nhan_task` vẫn nạp khung ở ưu tiên 10 trên cùng nhân 0;
  - thêm phần chấm AS-norm trên 380 hàng nhóm chuẩn, chưa tách riêng 🔬.
- **Các lệnh không được nhận đều do khâu nhận lệnh, không do khâu người nói.**
  - "mở cửa" rồi ngay "đóng cửa" thành một câu, chỉ "đóng cửa" được nhận.
  - "tăng âm lượng", "giảm âm lượng", "dừng lại" bị `LOW_MARGIN` hay `PART` ở δ2 50.

## 29. `speaker` theo độ ồn trên chip: dải, cổng và bước giữ tiếng (E11-T33)

Đo ngày 12–13/10 trên board B, qua cầu RFC 2217.

**Dải độ ồn trên chip, `make parity-board`** (12/10, `9a4539d`):
- cả hai bản dựng, chuỗi mặc định và chuỗi có module;
- khối `speaker_noise`: dải và quyết định của `case_000`, `case_001` lệch 0;
- bốn đối chứng âm đều đỏ: dải lệch 1, 1, 3, và quyết định lệch 1264 ‰.

**Cửa sổ lệnh với ảnh của khoá `898a1ba`, `make listen-unit SPEAKER=1`** (13/10, 08:03–08:50):
- khuôn 9; cổng của `speaker-deploy` chặn bảng dải, nên biên là 0;
- 7 lượt, 336 cửa sổ, 150 lệnh được nhận;
- điểm AS-norm, cosine và dải của từng cửa sổ trùng bản soi gương. Mọi cửa sổ ở dải 3, dải yên nhất.

**Bước giữ tiếng**, `svc_listen_hold` ở `nhan_task`, nhân 0:
- chép 1,5 s cuối cửa sổ sang ô xác minh;
- cộng hai tổng bình phương trên cả cửa sổ lệnh, đọc từ vòng PCM ở PSRAM.

| Lượt | 0 | 1 | 2 | 3 | 4 | 5 | 6 | Cả 7 lượt |
|---|---|---|---|---|---|---|---|---|
| Số lần giữ | 26 | 35 | 21 | 2 | 20 | 33 | 33 | 170 |
| Trung bình, µs | 3512 | 3551 | 3612 | 2977 | 3721 | 3757 | 3848 | **3663** |
| Đỉnh, µs | 3908 | 4363 | 4441 | 3151 | 3891 | 4467 | 4156 | **4467** |

- **Số đo gồm cả phần chép đuôi.** ADR-0024 ước "dưới 1 ms" cho riêng hai tổng, và chưa đo tách hai phần.
- **Không chạm nhân khung.** `nhan_task` ở nhân 0, `q_clean` 128 bước đỡ ~2 s, nên 4,5 ms mỗi lệnh không làm rơi
  khung.
- **Với biên 0, hai tổng không đổi quyết định nào,** nhưng vẫn được tính.

**Trả board về sản phẩm** (13/10, 08:51–08:59): `fw-bench-flash`, `models-flash`, `storage-flash`, rồi `nvs-restore`
từ bản `nvs-save` ngày 12/10. Log boot:
- `noise edges 0, 0, 0`, và một mẫu giọng có ngưỡng 2964 ở cả ba dải ồn;
- ngưỡng 2964–3795 cho 1–8 mẫu giọng, model `c3373e4e`;
- roster có 1 mẫu giọng, kiểm người nói bật;
- δ1 300, δ2 50, δ3 65535, bộ 10 lệnh của `set.json`.

## 30. Nạp qua cầu RFC 2217: timeout đọc và ảnh không đổi (E1-T13)

Đo ngày 20/10 trên board B qua cầu RFC 2217 của KẾ HOẠCH §2.1, với esptool 5.4.0, pyserial 3.5, pytest-embedded 2.9.3
của ESP-IDF v6.0.2.

pyserial phía WSL coi mỗi lần timeout đọc đổi là đổi cấu hình cổng. Nó gửi lại baud, số bit, parity, stop bit sang
cầu rồi ngủ ít nhất 50 ms chờ trả lời. esptool đặt timeout của từng lệnh rồi trả lại giá trị cũ:
- pytest-embedded để cổng ở 0,05 s, lệnh thường của esptool dùng 3 s, nên mỗi lệnh đổi timeout hai lần;
- esptool chạy từ dòng lệnh (`idf.py flash`, parttool) mở cổng không có timeout, nên cũng đổi hai lần mỗi lệnh.

| Phép đo | Timeout đổi qua cầu | Timeout chỉ ở phía WSL |
|---|---|---|
| `read_reg` qua stub, cổng ở 0,05 s, trung bình 20 lần | 305 ms | 3,9 ms |
| `read_reg` khi cổng đã ở 3 s | — | 3,9 ms |
| `esptool flash-id` từ dòng lệnh, gồm cả kết nối | 34,3 s | 4,2 s |

Mọi log nạp qua cầu từ 11/10 có một khoảng 21–39 s không in gì, giữa dòng đổi baud và dòng `Configuring flash size`.
Trong khoảng đó esptool dò chip flash bằng vài chục lệnh thanh ghi.

**`make bss`**, tính từ lúc pytest mở cổng tới lúc reset sau khi nạp:

| Lượt | Ảnh ghi | Pha nạp | Tới menu |
|---|---|---|---|
| như cũ, hai lượt | cả bốn ảnh; app 21,5–23,2 s | 71 s | — |
| chỉ ghi ảnh khác (`--skip-flashed`) | app, sau một lượt nạp bị ngắt; 21,2 s | 67 s | 66,4 s |
| chỉ ghi ảnh khác | `ota_data` 8 KB | 49 s | 51,3 s |
| thêm timeout ở phía WSL | `ota_data` và app; app 7,6 s | 15 s | 39,2 s |
| thêm timeout ở phía WSL | `ota_data` | 6 s | 15,6 s |

`ota_data` được ghi lại ở mọi lượt vì app cập nhật nó lúc boot. Sau mỗi commit, chuỗi phiên bản nhúng trong app đổi,
nên lượt đầu tiên ghi lại app.

**Mọi đích chạm board, sau khi sửa** (cùng ngày; `tools/board_link`, `firmware/conftest.py`, `--skip-flashed`):

| Đích | Trước | Sau |
|---|---|---|
| `make bench-board`, pha nạp | 54 s | 8 s khi ghi bootloader, bảng và app 230 KB; 9 s khi bỏ qua cả ba |
| `make bench-board`, từ lệnh tới dòng báo cáo đầu | — | 23,5 s; chạy lại 10,2 s |
| `make parity-board`, lượt `build` (pytest, cả bộ vàng) | 369 s | 196 s, trong đó ghi ảnh vàng 16 MB mất 134,9 s |
| `make parity-board`, lượt `build_modules` | 380 s | 63 s: ảnh vàng trùng lượt trước, bỏ qua |
| `make product` sau một ngày app thử | khoảng 8 phút (13/10, gồm `nvs-restore`) | 148 s: model 5,5 MB 55,3 s, storage, bootloader, app 2 MB 15,8 s; bảng phân vùng bỏ qua |
| `make product` chạy lại | — | 59 s: chỉ ghi lại storage, vì sản phẩm đã lưu bộ lệnh retained vào LittleFS |
| `make espsr-compare`, pha nạp | — | 24 s: bootloader, bảng và app 1,39 MB (14,7 s); ảnh model ESP-SR của biến thể đầu sau đó 1,27 MB trong 12,6 s |

Cả hai lượt parity đều qua. Mọi lượt không còn bốn cảnh báo cuối pytest, cũng không còn cảnh báo `hard_reset` của
esptool. Dòng `Failed to get VID/PID` vẫn còn: esptool in nó mỗi lần nối, vì cổng qua mạng không có mã USB.

## 31. ReDimNet2 b3 nhiều ngôn ngữ trên board B (E11-T35)

Đo ngày 27/10 trên board B. Run `20261027_1a7464c-dirty_92a838` nhập bằng `make speaker-import
EXTRACTOR=redimnet2_b3_mix` (`b3-vb2+vox2+cnc2_v0-lm`, 4,51 M tham số, `speaker.md` §2). So với b0: C = 24 thay 12,
kênh mở rộng trong khối 4, 3, 2, 1, 1, 1 theo stage thay 2, 1, 1, 1, 1, 1.

**GroupNorm.** b3 có `agg_gnorm`: một `GroupNorm` 24 nhóm trên 1728 kênh sau stem và sau mỗi stage, mỗi nhóm chuẩn
hoá 72 kênh trên cả trục thời gian. Bảng thô và hai bước tối ưu đầu đo trên hàng `timing` (minmax) dựng bằng một
script dùng một lần, 7 `GroupNorm` bỏ đi. Từ bước thứ ba, `timing` xuất lại qua `Embed` với 7 chuỗi `GroupNorm1d`, và
chip chạy chúng.

Hai chặn trước khi chạy được:
- `cache_sized_convolutions` dựng một `Concat` một đầu vào cho conv `red_dim` (1728 → 72, 124 KB trọng số), vì 72
  không chia thành các lát bội 16; esp-dl assert khi định cỡ tensor. Sửa ở `d81dabc`.
- Ảnh dò gồm đồ thị và 4 bản cắt nặng 13,4 MB, quá khe 6 MB, nên chạy `CUTS=0`. Đồ thị 5,3 MB chép vào PSRAM thì
  tensor của b3 không còn chỗ, và `Slice` đầu tiên đọc phải tensor rỗng. `IN_FLASH=1` để đồ thị ở flash, như sản
  phẩm chạy speaker.

| | b0, hàng kl | b3 nhiều ngôn ngữ, hàng timing, không GroupNorm |
|---|---|---|
| `.espdl` | 1 482 448 B | 5 412 928 B |
| Một cửa sổ 1,5 s, đồ thị trong PSRAM | 356,1 ms | không vừa PSRAM |
| Một cửa sổ 1,5 s, đồ thị ở flash | 408,5 ms | **7 089,2 ms** |
| PSRAM cho tensor | 1 408 256 B | 3 927 576 B |
| Khớp từng bit với mô phỏng | đồ thị và 4 bản cắt | đồ thị |

Profile của b3, tổng 7 086 ms:

| Phần | Giờ | Vì sao |
|---|---|---|
| Conv 4×2 của stage 5 (esp-dl) | 1 540 ms, 7,5 chu kỳ một phép nhân-cộng | 144 KB trọng số, đọc lại từ flash ở mỗi vị trí ra |
| 6 conv `red_dim` 1728 → 72 (`PointwiseS8`) | 2 248 ms | 124 KB trọng số, không cắt lát được |
| 19 `ConvBlock` | 2 406 ms | ~370 ms mỗi khối stage 1, ~300 ms mỗi khối stage 0 |
| Các conv esp-dl còn lại, `StageSum`, `LayerNormS8` | 150, 136 và 61 ms | |

- Đồ thị ở flash tốn b0 thêm 52 ms (15%). Sản phẩm chạy speaker như thế (`deploy.weights_in_flash`), nên 408,5 ms
  là giờ của b0 trên sản phẩm.

Tối ưu riêng cho b3, hàng `timing` không GroupNorm, đồ thị ở flash, mỗi bước khớp từng bit ở embedding ra:

| Bước | Một cửa sổ | PSRAM cho tensor | Ghi chú |
|---|---|---|---|
| Thô: các op viết cho b0 | 7 089,2 ms | 3 927 576 B | như bảng trên |
| `PointwiseS8` cắt lát cả khi N lẻ 8, và với mảnh | 3 464,3 ms | 4 183 324 B | conv 4×2 của stage 5 1 540 → 69 ms; mỗi `red_dim` dưới 51 ms, từ tới 642 ms; thêm 256 KB bộ đệm mảnh. `ConvBlock` còn 2 353 ms |
| Thêm 7 `GroupNorm`, mỗi chuỗi chạy bằng op esp-dl (`Transpose`, `LayerNormS8` theo nhóm, `Mul`, `Add`) | 6 706,4 ms | 8 293 692 B | **lệch 38** ở embedding ra; 14 `Transpose` 2 677 ms, `LayerNormS8` 586 ms |
| `GroupNormS8`: mỗi chuỗi một op, bản C | 4 553,1 ms | 4 804 072 B | 6 phép 587 ms; chuỗi của stem còn hai `Transpose`, 376 ms |
| `GroupNormS8` đọc thẳng map 2-D [T, F, C] của stem | 4 183,7 ms | 3 932 744 B | 7 phép 685 ms |
| Mỗi nhóm một bảng: LN int8 của từng giá trị int8 vào | 4 073,1 ms | 3 976 756 B | 7 phép 579 ms, ~82,5 ms mỗi phép |
| Nhân viết tay: bình phương 4 nhóm xen nhau, ghi ra cả hàng qua QACC | 3 710,1 ms | 3 989 004 B | 7 phép 215 ms |
| Lô hai nhóm qua mọi lượt, tổng bằng ACCX, độ lệch tra bảng | 3 642,7 ms | 3 992 332 B | 7 phép 154 ms, ~22 ms mỗi phép. `ConvBlock` 2 428 ms, `PointwiseS8` 538 ms, `StageSum` 155 ms |
| `ConvBlock` nhân chung mượn RAM nội cho trọng số (ADR-0025), mỗi hàng theo dải 4 điểm ảnh | 2 154,1 ms | 3 992 332 B, cộng 79 680 B RAM nội trong lúc chạy | `ConvBlock` 2 428 → 919 ms: stage 0 ~140, stage 1 ~119, stage 2 ~32 ms một khối |
| `PointwiseS8` nhân có vòng mượn cùng vùng theo lát | 1 935,3 ms | 3 992 332 B, cùng 79 680 B RAM nội | `PointwiseS8` 530 → 314 ms: conv bước của stage 5 67 → 32, `PPQ_Operation_199` 87 → 29, `head` 46 → 23 ms. Còn lại: `ConvBlock` 919, `GroupNormS8` 152, `StageSum` 155, conv esp-dl 113, `LayerNormS8` 58 ms |
| `StemConv` nhận 24 kênh, mỗi 16 kênh một lượt depthwise | 1 929,9 ms | 4 274 980 B, cùng 79 680 B RAM nội | `Add` sao chép 5,8 ms và depthwise 16,9 ms của stem thành một `StemConv` 9,5 ms. Lợi ròng chỉ 5,4 ms: trình xếp greedy của esp-dl bày tensor lại, xin thêm 282 KB PSRAM, và `ConvBlock` chậm ~6 ms trên chỗ đặt mới |
| `PointwiseS8` nhận K bội 8 trên map 1-D, hàng chép sang bộ đệm bước bội 16 | 1 876,7 ms | 4 326 240 B, cùng 79 680 B RAM nội | mỗi conv 72 → 1728 (bốn conv esp-dl và một `Concat`) thành một `PointwiseS8`: 12,9, 8,8, 6,5 ms ở 148, 74, 37 khung; `pwconv1` 72 kênh của TCM cũng vào op. Conv esp-dl rời bảng đầu |
| `GroupNormS8` đọc map trước `Resize` lặp khung, k = 2 hay 4 | **1 819,2 ms** | **4 324 384 B**, cùng 79 680 B RAM nội | 4 `Resize` (25 ms) bỏ đi; `GroupNormS8` của stage 2–5 đọc 74 hay 37 khung, tính mỗi hàng nguồn một lần |
- Từ bước `GroupNormS8`, mỗi bước khớp từng bit cả ở đồ thị lẫn bản cắt.
- Lượt dò có profile chạy đồ thị hai lần liền nhau (~4 s ở nhân 0) thì task watchdog báo IDLE0; bản báo rơi vào
  giờ của một module, ~43 ms ở một `GroupNormS8` hay `LayerNormS8`. Giờ một cửa sổ đo ở lượt có `vTaskDelay(1)`
  giữa các lần chạy, nên không dính.
- **`GroupNormS8` trên app đo** (map 148 × 1728 trong PSRAM, 24 nhóm), chu kỳ một giá trị:

  | Bản | Tổng | Bình phương | Bảng | Ghi ra | Một phép |
  |---|---|---|---|---|---|
  | C, bảng mỗi nhóm | 5,5 | 26,5 | 0,9 | 44,1 | 82 ms |
  | Nhân 4 nhóm, ghi ra cả hàng | 5,5 | 10,6 | 0,9 | 11,1 | 30 ms |
  | Lô hai nhóm | 5,1 | 5,1 | 0,85 | 9,7 | 22,5 ms |

  - Bình phương 4 nhóm tra bảng độ lệch chạy 15,5–30,4 chu kỳ tuỳ dữ liệu. Các hàng của lô 4 nhóm (~52 KB dòng
    cache) cộng bảng 4 KB tràn vài tập của cache 8 đường, nên mỗi lượt quét theo khung trượt lại. Lô hai nhóm
    (~28 KB) không trượt.
  - Tổng ACCX một mình 2,8 chu kỳ, sát băng thông đọc PSRAM. Ở lô hai nhóm, nó gánh thêm việc ghi ngược các dòng y
    của lô trước.
  - Ghi ra gần trần băng thông: mỗi dòng y nạp vào cache trước khi ghi đè, rồi mới ghi ngược.
- **`ConvBlock` của b3 trên app đo** (28/10). App gọi các nhân của khối theo đúng thứ tự `forward`, ở cỡ của từng
  stage, map x và y ở PSRAM, trọng số ngẫu nhiên. ms một khối:

  | Stage (C, F, T) | Trọng số ở flash | ở PSRAM | ở RAM nội |
  |---|---|---|---|
  | 0 (96, 72, 148) | 288,5 | 238,9 | 173,5 |
  | 1 (144, 36, 148) | 386,3 | 258,2 | 127,5 |
  | 3 (96, 18, 74) | 19,9 | 17,0 | 15,0 |
  | 5 (192, 9, 37) | 105,5 | 50,3 | 11,0 |

  - Cột flash khớp bảng xếp hạng của bản dò (stage 1 ~385 ms, stage 5 ~107 ms).
  - Pointwise chạy lặp trên cùng một hàng, trọng số đã nóng: 0,097, 0,087, 0,082 chu kỳ một phép nhân-cộng ở C =
    96, 144, 192. Số như nhau dù trọng số ở flash, PSRAM hay RAM nội, nên cache trúng không chậm hơn RAM nội.
  - **dcache của S3 không giữ dòng nóng.** Đọc S KB dữ liệu PSRAM khác chen giữa hai lượt pointwise, trọng số 20,7 KB
    (C = 144): 8 KB làm lượt sau chậm 34%, 16 KB chậm 53%; ở C = 192, 8 KB chậm 49%. Cache 64 KB thay dòng theo LRU thì
    8 KB không đẩy trọng số nào ra. Mỗi hàng của khối chuyển x, y và các hàng vòng qua cache, nên trọng số liên tục bị
    đẩy ra và đọc lại từ flash.
  - Chỉ PSRAM, xếp lại: lô R hàng (mỗi pha một pointwise), mỗi hàng theo dải P điểm ảnh, trọng số ở flash. Tốt nhất
    stage 0 215,3 ms (R16 P4), stage 1 221,5 ms (R16 P4), stage 5 35,4 ms (R16 P2). Stage 3 chậm đi khi R tăng, từ
    20,0 tới 29,7 ms ở R37.
  - Trọng số chép từ flash vào RAM nội lúc khối bắt đầu, mỗi hàng theo dải 4 điểm ảnh, kể cả lần chép:

    | Stage | 0 | 1 | 2 | 3 | 4 | 5 |
    |---|---|---|---|---|---|---|
    | ms một khối | 149,2 | 118,3 | 32,0 | 15,8 | 8,3 | 13,6 |
    | Trọng số chép | 20 KB, 0,68 ms | 44 KB, 1,45 ms | 20 KB | 20 KB | 20 KB | 77 KB, 2,54 ms |

    21 khối ước ~0,93 s 🔬, từ 2,43 s. Đầu ra như nhau ở mọi R và P.
- **`PointwiseS8` của b3 trên app đo** (28/10), trọng số ở flash. Op chạy theo lát như `run_slices`, hoặc theo lát
  vừa 79 680 B: mỗi lát chép vào RAM nội trước khi chạy. ms, kể cả lần chép; đầu ra như nhau:

  | Op (K → N, hàng) | Lát của `run_slices` | Lát chép vào RAM nội | Chép |
  |---|---|---|---|
  | `red_dim` (1728 → 72, 148) | 42,4 | 17,1 | 4,9 ms |
  | `PPQ_Operation_199` (2016 → 128, 148) | 87,7 | 29,0 | 8,8 ms |
  | conv bước 4×2 của stage 5 (768 → 192, 333) | 54,6 | 24,0 | 5,3 ms |
  | `head` (192 → 224, 1332) | 44,8 | 23,0 | 1,8 ms |
  | `PPQ_Operation_208` (128 → 2016, 148) | 32,8 | 23,9 | 9,7 ms |

  Lần chép đọc mỗi trọng số từ flash đúng một lần, ~27–31 MB/s.
- **`ConvBlock` chia theo phần trên app đo** (28/10), trọng số chép vào RAM nội, dải 4 điểm ảnh, ms một khối:

  | Stage | Nạp hàng x | Depthwise 1 + 2 | Pointwise 1 + 2 | Cả khối | Hàng vòng cũng ở RAM nội |
  |---|---|---|---|---|---|
  | 0 | 20,2 | 46,4 (0,6 chu kỳ/MAC) | 80,2 | 149,7 | **109,5**: nạp 12,1, depthwise 15,4 (0,20) |
  | 1 | 15,6 | 19,7 (0,34) | 80,6 | 118,8 | **107,2** |
  | 2 | 5,2 | 5,5 | 20,1 | 32,3 | **28,6** |
  | 5 | 1,2 | 1,2 | 8,4 | 13,9 | 12,5 |

  - Pointwise sát mức nhân: 0,082–0,098 chu kỳ một phép nhân-cộng như khi trọng số nóng.
  - Depthwise quét từng vector qua dải, đọc lại ba hàng vòng; hàng vòng ở PSRAM bị cache đẩy ra giữa hai lần dùng,
    nên stage 0 chạy 0,6 chu kỳ một phép nhân-cộng, gấp ba khi hàng nằm ở RAM nội.
  - Trọng số cộng hàng vòng: stage 0 63,5 KB, stage 1 79,3 KB khi hai đệm dải dùng chung, vừa 79 680 B; stage 5 90,5 KB,
    không vừa. 21 khối ước bớt ~140 ms 🔬.
- **Chưa đo:** độ chính xác của b3 sau int8 (thang int8 đang chạy).
- **Đọc:** đây là số thô. b3 chạy qua các op viết cho hình dạng của b0, chưa có bước nào tối ưu riêng cho nó. b3 có
  2,16 tỉ MAC (`torch.utils.flop_counter`, không tính GroupNorm), b0 0,24 tỉ. 1,61 tỉ nằm trong 19 `ConvBlock`, chạy
  ~0,36 chu kỳ một phép nhân-cộng; khối của b0 ~0,24 (147 triệu MAC trong 146,8 ms). Hai loại conv quá dcache tốn 3,8 s
  cho ~115 triệu MAC. Tối ưu như b0 (cắt lát hai loại conv ấy, pointwise của khối ở 96 và 144 kênh gần 0,09 chu kỳ
  như `PointwiseS8`, trọng số của khối không đọc lại từ flash, gộp phần còn lại, thêm `GroupNorm`) ước còn ~1,3–1,8 s
  một cửa sổ 🔬, vẫn trên mức ~1 s của điều kiện cắm (ADR-0021).
