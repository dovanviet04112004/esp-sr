# `pitch` — bộ dò cao độ Kaldi chạy dòng không trễ (E11-T8)

## 1. Bản soi gương so với chính Kaldi, trên VIVOS test

`make eval-pitch` (`srpipe.metrics.pitch`), bản Python tại `55a9914`, tham số ở mục `pitch` của
`ml/configs/scenes/device.yaml` (mặc định của Kaldi, bước khung 16 ms của lưới).

| Mục | Giá trị |
|---|---|
| Vật liệu | VIVOS `test`: 760 câu, 164 767 khung 16 ms |
| Chuẩn so | Kaldi gốc qua kalpy trong image `mmcauliffe/montreal-forced-aligner:v3.4.2` của bộ căn mốc (`common/tts.yaml`), chạy trên CPU bằng `ml/afe_ref/kaldi_pitch/run.py`: `ComputeAndProcessKaldiPitch` |
| Kaldi chạy dòng | lượt đầu, mỗi khúc một bước 256 mẫu, `max_frames_latency` 0, trung bình log F0 trên 47 khung đã qua (0,75 s) và không nhìn trước, không cộng nhiễu vào delta; bỏ 3 khung cuối, là khung Kaldi đẩy ra khi báo hết tệp |
| Kaldi đọc cả tệp | cả câu một lần, Viterbi truy ngược từ cuối câu, trung bình log F0 trên 47 khung mỗi bên: câu trả lời tốt nhất của bộ dò |
| Khung hữu thanh | xác suất hữu thanh của bản đọc cả tệp (`NccfToPov`) trên 0,5: 79 230 khung, 48% |
| Cùng độ trễ | log F0 thô lệch dưới 1e-4, tức cùng một trong 417 độ trễ |

| Cặp | Cùng độ trễ, mọi khung | Cùng độ trễ, khung hữu thanh | F0 lệch quá 5%, khung hữu thanh | Tương quan POV | Sai lệch tuyệt đối lớn nhất mỗi câu, trung vị: POV / log F0 chuẩn hoá / delta |
|---|---|---|---|---|---|
| bản soi gương / Kaldi chạy dòng | **99,94%** | **99,97%** | **0,00%** | 1,0000 | 4,8e-5 / 4,8e-6 / 2,9e-6 |
| bản soi gương / Kaldi đọc cả tệp | 37,39% | 61,38% | 3,32% | 0,9759 | 0,44 / 1,15 / 0,73 |
| Kaldi chạy dòng / Kaldi đọc cả tệp | 37,35% | 61,38% | 3,32% | 0,9758 | 0,44 / 1,15 / 0,73 |

**Bản soi gương là Kaldi chạy dòng.** Nó chọn đúng độ trễ Kaldi chọn ở 99,94% khung; 0,06% còn lại là chỗ hai độ
trễ gần hòa, nơi thứ tự cộng float32 của hai bên quyết định; ba đặc trưng lệch nhau ở mức làm tròn float32. Viterbi lấy min
bằng biến đổi khoảng cách thay phép dò có chặn của Kaldi: thay phép dò ấy vào bản soi gương trên bốn câu đầu cho đúng từng độ
trễ như nhau.

**Chạy dòng so với đọc cả tệp** là cái giá của việc không nhìn trước, và Kaldi chạy dòng trả đúng giá ấy: 3,32% khung hữu
thanh lệch F0 quá 5%, tương quan độ hữu thanh 0,976. Ở khung vô thanh, hai bản chọn độ trễ khác nhau nhiều vì giá gần như
phẳng, nhưng khung ấy nặng rất nhẹ trong trung bình log F0 và mạng học được POV thấp đi kèm.

## 2. `compute_kaldi_pitch` của torchaudio không dùng làm chuẩn được

torchaudio 2.1 đã bỏ hàm này. Bản 2.0.2, bản cuối còn có, chạy mã Kaldi trên một lớp ma trận viết lại bằng tensor
(`third_party/kaldi/src/matrix/kaldi-vector.h` của torchaudio), và lớp ấy có hai lỗi làm sai kết quả:

| Lỗi | Hậu quả |
|---|---|
| `Vector::Swap` đổi tensor nhưng không đổi con trỏ `data_` mà `Data()` trả về | Viterbi của Kaldi đọc giá khung trước và ghi giá khung này qua `Data()`; sau lần đổi đầu tiên hai con trỏ trỏ nhầm vùng, nên giá tiến trộn giữa các khung. Khung hữu thanh vẫn ra đúng vì giá cục bộ lấn át; khung lặng nhảy ra hai mép 50 Hz và 400 Hz |
| `Resize(..., kCopyData)` giữ một bản sao cùng vùng nhớ rồi xoá vùng ấy | lượt đầu chạy dòng mất mọi hàng ghi trước lần mảng kết quả nở ra (100 → 200 → 400 hàng) |

Trên 20 câu đầu, bản soi gương chỉ trùng torchaudio ở 20% khung, lệch ở đúng các khung lặng; cùng các câu ấy trùng Kaldi
gốc 100%.

## 3. Bản C trên máy tính và board B

`dsp_spec/pitch.{h,c}` tại `614c607`. Bộ vàng `contracts/golden/pitch/`: bốn ca 96 bước (tông trượt, chuỗi tiếng ngắt,
nhiễu, lặng rồi tông có đặt lại ở bước 60) và một đối chứng âm là đặc trưng trễ một bước.

| Phép đo | Kết quả | Lệnh |
|---|---|---|
| Bốn ca, `features` và `raw` | **khớp từng bit**, max_abs 0, trên máy tính và trên board B ở cả bản dựng mặc định lẫn bản bật module | `make parity-host`, `make parity-board` |
| Đối chứng âm | max_abs 1,17, SNR 20,1 dB ở `features`: ngoài ngưỡng 1e-3 / 80 dB, phép kiểm báo đỏ đúng | như trên |
| Thời gian mỗi bước, nhân 0, vùng làm việc 155 920 B trong PSRAM | trung bình **1 980 µs**, đỉnh 2 081 µs: 12,4% khung 16 ms, gấp 6,6 lần ước lượng ~300 µs của KẾ HOẠCH §3.3 | `make bench-board` |

Bench chạy trên cây có sửa `docs/` chưa commit, nên cột commit của `budget.md` mang đuôi `-dirty`; mã firmware đúng là
`614c607`. Chưa tách thời gian theo phần (NCCF, đổi sang lưới độ trễ log, Viterbi, truy vết 48 khung).

## 4. SwiftF0 lên chip, bước 1: int8 trên máy tính (KẾ HOẠCH §3.11, E11-T36)

Đo ngày 28/10. `make pitch-import` nhập `model.onnx` của `swift-f0` 0.3.0 (sha256 `6385e8c2…`) thành run
`ml/artifacts/pitch/runs/20261028_eb08333-dirty_9ab2ce`.

**Mạng**, đo trên ONNX:
- 25 949 tham số, 3,3 triệu phép nhân-cộng mỗi hop 16 ms.
- 2,3 triệu ở CNN 2-D 12 kênh trên 128 dải log-tần số. Phần còn lại là ba STFT 512, 1024 và 2048 điểm, mà ONNX viết
  bằng DFT nhân ma trận.
- Trục thời gian của CNN ±7 khung, nên một khung chốt khi đã có 10 khung sau.

**Dựng lại để lượng tử:** `dsp/spec/swift_front.py` (FFT của `dsp_spec`), `tasks/pitch/model.py` (torch, trọng số từ
ONNX), `tasks/pitch/postproc/decode.py`. So với ONNX và `detect` của gói:

| Khâu | Lệch |
|---|---|
| Front end, 3 × 128 dải mỗi khung, miền log | lớn nhất 3,4·10⁻⁴, trung bình 3·10⁻⁶ |
| CNN, logit cao độ trên thang 19 | lớn nhất 9,5·10⁻⁶, cùng argmax ở mọi khung |
| Cả chuỗi so với `detect` | cao độ lệch lớn nhất 0,0003 cent; độ tin 4·10⁻⁶; không khung nào đổi phía ngưỡng 0,5 |

**Thang int8 bậc 2** (`make pitch-ptq`). Hiệu chỉnh trên 142 đoạn 256 khung của 8 phiên `train` của bản dựng
`command/v8`, mô phỏng lại lấy tiếng `clean`. So với float trên 208 đoạn của 16 phiên `val`, bỏ 10 khung mỗi mép đoạn;
cả hai giải trên dải F0 50–400 Hz của `contracts/listen.yaml`:

| Hiệu chỉnh | Khung hữu thanh cả hai | Lệch cao độ, trung vị / p90 / p99 (cent) | Lệch > 50 cent | Khung đổi phía hữu thanh |
|---|---|---|---|---|
| **kl** | 13 024 | **2,5 / 6,8 / 22,4** | 0,63% | **2,1%** |
| percentile | 13 087 | 2,7 / 7,4 / 23,2 | 0,60% | 2,1% |
| minmax | 12 666 | 3,0 / 8,0 / 29,2 | 0,68% | 2,5% |
| mse | 12 716 | 2,9 / 7,8 / 28,3 | 0,68% | 2,4% |

- Lệch trung vị 2,5 cent, nhỏ hơn nhiều khoảng cách giữa các thanh. Khung đổi phía hữu thanh là những khung có độ tin
  sát ngưỡng 0,5.

**Điều kiện đạt của bước 1** (KẾ HOẠCH §3.11): v8 (`20261007_439d763-dirty_f438bd`, float) nghe ba chiều cao độ của
hàng kl.

Trên 209 câu Cửa 3 (`make ctc-tone-flip CHECK=gate`): chuỗi chạy bằng code hôm nay, cao độ tính từ tiếng `clean` của
bản thu, thanh so với cách đọc miền Bắc của lệnh. Lượt float cho đúng từng số của `command.md` §12.25.

| Cao độ | Đúng nhất | Thanh âm tiết đầu | Thanh các âm tiết sau |
|---|---|---|---|
| SwiftF0 float | 196/209 | 167/209 (79,9%) | 179/233 (76,8%) |
| **SwiftF0 int8 hàng kl** | **192/209** | **169/209 (80,9%)** | **182/233 (78,1%)** |
| Điều kiện | kém float ≤ 5 câu: **đạt** | — | ≥ 72%: **đạt** |

Trên `val` (`make ctc-tone-flip CHECK=places BUILD=…`), 1 583 câu, dựng lại từ đầu bằng mô phỏng hôm nay
(`make pitch-rebuild`), một lần với SwiftF0 float, một lần với hàng kl. Log-mel đã lưu của bản dựng v8 (FFT trước
ADR-0020) không còn khớp mô phỏng hôm nay, nên `mel_from` không nối được; hai bản dựng mới cùng log-mel, chỉ cao độ
khác. Hai dòng float và không cao độ trùng `command.md` §12.25.

| Cao độ | Lỗi đơn vị | Thanh âm tiết đầu | Thanh các âm tiết sau |
|---|---|---|---|
| SwiftF0 float | 0,343 | 73,5% | 83,1% |
| **SwiftF0 int8 hàng kl** | **0,344** | 72,8% | 83,1% |
| không cao độ (ba chiều giữ ở trung bình, như board chạy v8) | 0,375 | 71,4% | 79,0% |
| Điều kiện | ≤ 0,359: **đạt** | | |

**Bước 1 đạt cả ba điều kiện.** int8 hàng kl mất 0,001 lỗi đơn vị so với float. So với cách board chạy v8 hôm nay,
không cao độ, nó bớt 0,031 lỗi đơn vị trên `val` và thêm 4,2 điểm thanh các âm tiết sau trên 209 câu Cửa 3. Bước 2 là
bản dò trên board B: giờ mỗi hop, khớp từng bit.

## 5. SwiftF0 lên chip, bước 2: bản dò trên board B (KẾ HOẠCH §3.11, E11-T37)

Đo ngày 28/10 bằng `make ai-unit-pitch RUN=artifacts/pitch/runs/20261028_eb08333-dirty_9ab2ce ROW=kl`. App unit của
`ai_engine` dựng ở cờ của `bench`; đồ thị chép vào PSRAM lúc nạp.

- Cửa sổ 256 khung (4,1 s), không chạy dòng: đoạn to nhất của một phiên `val` của `command/v8`, mô phỏng lại.
- `.espdl` 174 992 B, gồm cả giá trị thử của `model->test()`.
- Bốn bản cắt, sau `/blocks.1/Relu`, `/blocks.2/Relu_1`, `/blocks.4/Relu_1` và `Relu` của đầu ra cao độ.

| Đo | Kết quả |
|---|---|
| `model->test()`, bốn bản cắt | khớp cả bốn |
| `model->test()`, cả đồ thị | khớp |
| `EspdlNet`, 10 lượt sau một lượt không tính giờ, cả hai đầu ra | **lệch 0** |
| Giờ một cửa sổ | **6 740,3 ms** trung bình, đỉnh hơn trung bình 28 µs |
| Giờ một hop | **26,3 ms**, 165% khung 16 ms |
| Dựng, gồm một lượt chạy | 6,8 s |
| PSRAM cho đồ thị và tensor | 1 304 300 B |

**Bảng `profile_module`** (`make ai-profile`): 6 743,7 ms trên 43 module.
- Conv: 18 module, 6 487,4 ms (96%). Add, Relu, ReduceMax mỗi loại ~70–75 ms; các loại còn lại cộng 38 ms.
- Mọi conv chạy nhân "unaligned" của esp-dl, vì số kênh (12, hay 3 và 4 ở đầu và cuối) không chia hết cho 16. Ở b0,
  nhân ấy tốn 1,4 chu kỳ một phép nhân-cộng, so với 0,15 ở đường aligned (`latency.md` §25).

| Module | Hình | Giờ một cửa sổ | Chu kỳ một phép nhân-cộng |
|---|---|---|---|
| bốn conv của `blocks.0` và `blocks.5` | 7 × 3, 12 → 12 kênh | 948–1 026 ms mỗi cái | 2,30–2,48 |
| tám conv của `blocks.1`–`blocks.4` | 7, 5, 3 × 1, giãn 17–48 theo dải | 193–353 ms mỗi cái | 2,46–3,59 |
| `stem` | 3 × 3, 3 → 12 kênh | 355 ms | 8,02 |
| `head`, `spread` | 3 × 3, 12 → 4; 5 × 1, 4 → 4 | 94 ms, 79 ms | 2,15; 9,79 |

Cả cửa sổ là 589 triệu phép nhân-cộng (2,3 triệu mỗi hop), trung bình 2,75 chu kỳ mỗi phép.

Watchdog task báo 18 lần rằng IDLE0 không được chạy, vì một lượt giữ nhân 0 6,7 s, quá hạn 5 s. Đỉnh chỉ hơn trung
bình 28 µs, nên các lần báo ấy không làm lệch số đo.

**Đọc theo KẾ HOẠCH §3.11:**
- Khớp từng bit: **đạt**.
- Giờ: không vừa. CNN một mình đã gấp 13 lần chỗ Kaldi nhường (1 980 µs mỗi hop, §3 trên), và quá cả khung 16 ms.
  Phần còn lại sau ba STFT ước 🔬 chỉ cỡ 700 µs, nhỏ hơn giờ đo 38 lần.
- Nhận thêm tải ở nhân 0 không đủ, vì CNN đã vượt khung. Còn lại hai đường, chủ repo chọn: qua vòng tăng tốc, bước 2
  của §3.14, rồi mới sang bước 3; hay dừng SwiftF0 trên chip.

## 6. SwiftF0, vòng tăng tốc (KẾ HOẠCH §3.11, §3.14 bước 2; E11-T37)

Chủ repo chọn vòng tăng tốc đủ ngày 28/10. Mỗi lượt là `make ai-unit-pitch RUN=artifacts/pitch/runs/20261028_eb08333-dirty_9ab2ce ROW=kl`
trên board B, cùng cửa sổ 256 khung như §5, rồi `make ai-profile`.

| Lượt | Bản vá của nhánh | Một cửa sổ | Một hop | PSRAM cho đồ thị và tensor | Khớp |
|---|---|---|---|---|---|
| §5 | không | 6 740,3 ms | 26,3 ms | 1 304 300 B | lệch 0, bốn bản cắt khớp |
| 1 (28/10) | `aligned_channels` | **1 464,1 ms** | **5,72 ms** | 1 697 100 B | lệch 0, bốn bản cắt khớp |
| 2 (28/10) | thêm `band_block` | 1 260,9 ms | 4,93 ms | 1 184 228 B | lệch 0, bốn bản cắt khớp |
| 2, cửa sổ 16 khung | như trên, `FRAMES=16` | **41,3 ms** | **2,58 ms** | 109 060 B | lệch 0, bốn bản cắt khớp |
| 3, cửa sổ 16 khung | thêm `band_head` | **32,1 ms** | **2,01 ms** | 115 072 B | lệch 0, bốn bản cắt khớp |
| 4, cửa sổ 16 khung | nhân nhanh cho cột trong | 28,9 ms | 1,81 ms | 115 952 B | lệch 0, bốn bản cắt khớp |
| 5, cửa sổ 16 khung | thêm `band_stem` | **27,4 ms** | **1,71 ms** | 117 568 B | lệch 0, bốn bản cắt khớp |
| 6, cửa sổ 16 khung | thêm `band_chain` | **26,8 ms** | **1,675 ms** | 110 000 B | lệch 0, bốn bản cắt khớp |

**Lượt 1, `aligned_channels`** (bước 2.3, bản vá mới): đệm thân 12 kênh lên 16 bằng kênh 0, nên mọi conv của thân
chạy nhân vector của esp-dl.
- Conv 6 487 → 1 149 ms. Mười hai conv của thân 0,14–0,40 chu kỳ một phép nhân-cộng, tính trên phép đã đệm (thêm 78%).
  Conv giãn theo dải chậm hơn conv 7 × 3: 102 trong 128 hàng nằm ở mép, chạy bộ lọc cắt ngắn từng hàng.
- Tensor lớn thêm một phần ba, nên Add và Relu 74 → 100 ms mỗi loại, Slice 10 → 13 ms; PSRAM thêm 392 800 B.

Bảng xếp hạng sau lượt 1, 1 465,3 ms trên 43 module:

| Phần | Giờ một cửa sổ | Ghi chú |
|---|---|---|
| 12 conv của thân | 956 ms | `blocks.5` 145–147 ms, `blocks.0` 104 ms, `blocks.1`–`blocks.4` 39–70 ms |
| Add, Relu sau mỗi khối | 199 ms | mỗi cái 16,4–16,9 ms, đọc ghi cả tensor qua PSRAM |
| `head` 16 → 4, `spread` 4 → 4 | 96 ms, 79 ms | nhân unaligned, 1,64 và 9,80 chu kỳ |
| ReduceMax theo dải | 71 ms | |
| `stem` 3 → 16 | 55 ms | nhân unaligned, 0,93 chu kỳ |
| RequantizeLinear, Slice, ReduceMean, `pitch`, còn lại | 60 ms | |

Cả thân là conv, Add, Relu và RequantizeLinear nối nhau qua PSRAM. esp-dl không có Add gộp Relu, nên phần ấy chỉ còn
đường op riêng (bước 2.5).

**Lượt 2, `band_block`** (bước 2.5, op riêng `BandBlock`): mỗi khối dư thành một op, chạy từng cột khung, cột giữa ở
RAM nội. Khớp từng bit ngay lần đầu, cả ở cửa sổ 256 khung lẫn 16 khung.
- Ở cửa sổ 256 khung, sáu khối chỉ bớt từ ~1 176 xuống 973 ms. Khối 1 tốn 203 ms, trong khi số lệnh của nó chỉ cỡ
  27 ms.
- Nguyên nhân là cache: hai dải liền nhau cách 4 KB trong PSRAM, nên 128 dải của một cột chỉ rơi vào 2 trong 128 tập
  của cache dữ liệu (64 KB, 8 đường, dòng 64 B). Mỗi tap theo dải trượt cache.
- Chạy dòng ở bước 3 mỗi bước chỉ giữ vài chục KB, nên từ lượt này giờ đo ở cửa sổ 16 khung, một bước của `command`
  (KẾ HOẠCH §3.11).

Bảng xếp hạng ở cửa sổ 16 khung, có vùng mượn, 41,3 ms trên 23 module:

| Phần | Giờ một cửa sổ | Ghi chú |
|---|---|---|
| sáu `BandBlock` | 24,4 ms | `blocks.0`, `blocks.5` 7,2–7,3 ms; `blocks.1`–`blocks.4` 2,2–3,0 ms |
| `head` 16 → 4, `spread` 4 → 4 | 6,0 ms, 5,0 ms | nhân unaligned, 1,64 và 9,82 chu kỳ một phép nhân-cộng |
| `stem` 3 → 16 | 3,5 ms | nhân unaligned, 0,96 chu kỳ |
| `pitch`, ReduceMean, ReduceMax, Slice, còn lại | 2,4 ms | |

- `blocks.0` chạy 12,4 triệu phép nhân-cộng thật trong 7,3 ms, 0,14 chu kỳ một phép.
- Khối 3 × 1 tốn ~130 chu kỳ mỗi dải ngoài 2–3 tap của nó: nạp bias, làm tròn, nhánh dư, và lần đầu chạm từng dòng
  PSRAM của x và đầu ra.

**Lượt 3, `band_head`** (bước 2.5, op riêng `BandHead`): phần đầu ra thành một op. Lần chạy đầu, chip từ chối dựng
op vì node thiếu `quant_type`; node tạo mới sau `prepare_graph` phải mang thuộc tính ấy như mọi node esp-dl dựng. Sửa
xong thì khớp từng bit.
- Phần đầu ra 12,3 → 3,0 ms một cửa sổ 16 khung.
- Bảng xếp hạng, 32,1 ms trên 19 module: sáu `BandBlock` 24,6 ms (77%), `stem` 3,5 ms, `BandHead` 3,0 ms, ReduceMean
  0,7 ms, còn lại 0,3 ms.
- CNN giờ tốn 2,01 ms mỗi hop, ngang chỗ Kaldi nhường (1 980 µs), chưa kể ba STFT.

**Lượt 4, nhân nhanh:** cột có mọi tap thời gian trong khung chạy nhân riêng, không xét cột đệm. Mỗi đầu vào nạp
trước một tap ở q0 hay q5, không nạp quá tap cuối. Lần chạy đầu panic `LoadProhibited`: nhân hai tham số đọc `run` ở
a5 thay vì a3. `BandBlock` 24,6 → 22,1 ms, `BandHead` 3,0 → 2,5 ms.

**Lượt 5, `band_stem`** (op riêng `BandStem`): mỗi cột vào ba giá trị một dải, xếp lại 16 byte một dải ở RAM nội rồi
chạy nhân nhanh ba làn. `stem` 3,5 → 1,7 ms. Bảng xếp hạng, 27,2 ms: sáu `BandBlock` 21,8 ms (`blocks.0`, `blocks.5`
6,4–6,5 ms; `blocks.1`–`blocks.4` 2,0–2,7 ms), `BandHead` 2,6 ms, `BandStem` 1,7 ms, ReduceMean 0,7 ms, còn lại 0,4 ms.

**Lượt 6, `band_chain`** (op riêng `BandChain`): khối 1–4 thành một op, mỗi cột x gom vào RAM nội một lần, cả bốn
khối chạy trên cột RAM nội. Khối 1–4 chỉ 9,0 → 8,7 ms: ba bản đồ giữa chuỗi tốn ít; phần lớn là việc lặp ở mỗi dải
ra.
- Mỗi dải ra của khối một khung tốn ~127 chu kỳ, trong đó chỉ ~45 là nhân-cộng. Phần còn lại là nạp bias vào QACC,
  làm tròn, nhánh dư và chuỗi phụ thuộc qua QACC.
- Bảng xếp hạng, 27,0 ms: hai `BandBlock` 12,8 ms (6,4–6,5 ms mỗi khối 7 × 3), `BandChain` 8,7 ms, `BandHead` 2,6 ms,
  `BandStem` 1,8 ms, ReduceMean 0,7 ms, còn lại 0,4 ms.
- Sàn của cách tính theo dải: `ee.vsmulas.s8.qacc` dùng 12 trong 16 làn, nên 2,2 triệu phép nhân-cộng thật của thân
  cần ít nhất ~0,76 ms mỗi hop.

## 7. SwiftF0, bước 3 trên chip (KẾ HOẠCH §3.11; E11-T37)

### 7.1 Front end ở `dsp_spec` (phần 3.1, 28/10)

`dsp_spec/swift_front` qua đủ bốn bước của TỔNG QUAN §5.3.

| Bước | Kết quả |
|---|---|
| Bản soi gương | `srpipe.dsp.spec.swift_front` chạy từng hop như bản C; khớp từng bit bản cả đoạn của bước 1, lệch đường nhân ma trận và `np.log` cũ tối đa 6,6e-7 |
| Bộ vàng | `contracts/golden/swift_front/`: giọng, ồn, im lặng sang âm 220 Hz, ồn gần tràn có reset giữa chừng, mỗi ca 40 hop trên cùng bảng (cửa sổ Hann tuần hoàn, 128 tam giác trên tâm log); đối chứng âm ra khung trễ một hop |
| Khớp, máy tính | `make parity-host`: bốn ca lệch 0; đối chứng âm lệch 4,73, SNR 13,9 dB, đỏ |
| Khớp, board B | `make parity-board`: bốn ca lệch 0; đối chứng âm 4,73, 13,9 dB, đỏ |
| Giờ, board B | `make bench-board`, vùng làm việc 68 368 B và bảng ở PSRAM, nhân 0: **3 107 µs trung bình, 3 143 µs đỉnh mỗi hop**, 19,4% khung |

Giờ gấp 2,4 lần mức ước 🔬 1 300 µs ở KẾ HOẠCH §3.11. Mức ước lấy FFT đo một mình ở `latency.md` §23 và ngoại suy
2 048 điểm, chưa tính tới căn bậc hai, `ln` và PSRAM.

Tách phần bằng các hàng đo tạm trên board B (không commit), mỗi hàng 2 000 hop sau 16 hop làm nóng:

| Phần | µs mỗi hop |
|---|---|
| Ba FFT 1 024, 2 048, 512 điểm, đo một mình, vùng làm việc PSRAM hay RAM nội như nhau | 257 + 539 + 117 = 913 |
| `sqrtf` cho 1 795 bin: libm chạy chuỗi `sqrt0.s` … `divn.s` của FPU qua hai lớp gọi hàm | 535 |
| `spec_ln_f32` 384 lần, gọi sang `mel.c`, mỗi lần một phép chia | 241 |
| Dời lịch sử 7 KB rồi nhân cửa sổ 3 584 mẫu, cả hai ở PSRAM | 508 |
| Cả front end, vùng làm việc và bảng ở RAM nội | 2 365 |
| Cả front end, như hàng của `budget.md` | 3 054 |

- Bảng dải của model đã ghim dùng gần hết bin của cả ba FFT (bin 3–513, 6–1 025, 1–257), nên không bỏ bớt bin nào được.

**Lượt bớt giờ 1 (29/10):** lịch sử thành vòng đệm đôi, mỗi mẫu giữ hai lần cách nhau 2 048 nên 2 048 mẫu cuối luôn
nằm liền một dải; FFT nhân cửa sổ ngay lúc nạp điểm và ghi biên độ ngay lúc tách bin (`spec_fft_magnitudes`). Khung đã
nhân cửa sổ và các bin không còn ra bộ nhớ. Mọi phép giữ nguyên thứ tự, nên mọi ca vàng vẫn lệch 0 trên máy tính.
- **2 367 µs trung bình, 2 462 µs đỉnh mỗi hop**, 14,8% khung (từ 3 107 µs); vùng làm việc 68 368 → 60 160 B.
- `stft` và `spk_fbank` dùng chung phần tách bin, giờ không đổi.
- SwiftF0 trên chip giờ tốn ~4,0 ms mỗi hop: front end 2,37 ms cộng CNN 1,675 ms ở cửa sổ 16 khung (§6), so với
  Kaldi 2 101 µs. Bỏ Kaldi và chạy SwiftF0 thì các module theo nhịp hop của nhân 0 lên ~26% khung, dưới mục tiêu 70%
  của KẾ HOẠCH §5.6.
- Phần còn lại phần lớn là ba FFT (913 µs), `sqrtf` (535 µs) và `ln` (241 µs). Bớt hai phần sau phải đổi phép tính,
  tức đổi KẾ HOẠCH §3.11 bước 3.1 và bản soi gương: chờ chủ repo.

### 7.2 CNN chạy dòng (phần 3.2, 29/10)

`make ai-unit-pitch RUN=artifacts/pitch/runs/20261028_eb08333-dirty_9ab2ce ROW=kl STEP=16`: hàng kl xuất có
`band_stream`, 16 khung mỗi lượt. Cùng cửa sổ 256 khung của §5 đi qua 16 lượt sau `reset`, so với mô phỏng của cả cửa
sổ.

| Đo | Kết quả |
|---|---|
| Trễ, đọc từ `.info` của đồ thị đã xuất | logit cao độ 6 khung, độ tin 7 khung, như KẾ HOẠCH §3.11 bước 3.2 |
| Khớp, lượt đầu sau `reset` | **lệch 0** ở mọi khung còn đủ khung sau: 250 khung × 95 bậc logit, 249 khung độ tin |
| Giờ một lượt 16 khung | **28,1 ms** trung bình, 28,5 ms đỉnh, trên 160 lượt sau một lượt làm nóng |
| Giờ một hop | **1 758 µs**; cửa sổ 16 khung không chạy dòng 1 675 µs (§6) |
| PSRAM cho đồ thị, tensor và cột giữ | 246 632 B; cửa sổ 16 khung 110 000 B |

- Lần chạy đầu lệch 12 989 giá trị, lệch lớn nhất 91. Vùng giữ cột vào của `BandBlock` và `BandHead` cấp bằng
  `heap_caps_calloc`, không thẳng 16 byte, mà nhân PIE đọc 128 bit ở địa chỉ đã làm tròn xuống. Cấp bằng
  `heap_caps_aligned_calloc` thì khớp.
- Vùng giữ cột vào xếp như cả tensor vào (32 KB mỗi khối 7 × 3, 24 KB ở `BandHead`) để nhân đọc cột cũ và cột mới
  cùng một bước dải, dù mỗi dải chỉ dùng 2 cột. Phần PSRAM thêm phần lớn ở đây.
- SwiftF0 trên chip giờ tốn ~4,1 ms mỗi hop: front end 2 367 µs (§7.1) cộng CNN chạy dòng 1 758 µs, so với Kaldi
  2 101 µs. Còn phần 3.3: giải cao độ, độ tin và ba chiều thang Kaldi.
