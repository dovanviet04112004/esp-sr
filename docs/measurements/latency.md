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
