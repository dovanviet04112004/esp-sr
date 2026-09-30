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
