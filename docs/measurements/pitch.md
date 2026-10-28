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
