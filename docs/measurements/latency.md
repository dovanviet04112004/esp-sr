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
