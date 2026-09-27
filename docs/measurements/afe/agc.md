# `agc` (E7-T4)

## 1. Mức ra theo mức vào, qua `vad` rồi `agc`

`make eval-agc` (`srpipe.scenes.agc`, `ml/configs/afe/agc.yaml` trên nền `vad.yaml`) tại `32e6aa1`, bản Python soi gương.

| Mục | Giá trị |
|---|---|
| Cảnh | cảnh VIVOS `test` của `vad.md` §1: 6 nhiễu × SNR 20 dB × 5 mức vào = 30 cảnh, mỗi cảnh 120 s tiếng đọc |
| Mức vào | −50, −40, −30, −20, −10 dBFS trên các bước nói, bình phương toàn thang |
| Chuỗi | `srpipe.dsp.afe.vad` ở mức gieo 2 (`contracts/afe.yaml`) → cờ nói → `srpipe.dsp.afe.agc` |
| Chấm | bỏ 30 s đầu cho gain leo (24 dB ở 3 dB/s là 8 s tiếng nói); mức ra là mức hoạt động kiểu ITU-T P.56 (trung bình công suất các bước nói trong 15,9 dB dưới chính nó) trên các bước có nhãn, đã bù trễ 64 mẫu |
| Đích | −26 dBFS; thước KẾ HOẠCH §3.10: ±3 dB, không cắt đỉnh, không dao động chu kỳ |

| Mức vào dBFS | Mức ra trung bình dBFS | Lệch đích xa nhất dB | Đỉnh ra cao nhất dBFS | Mẫu chạm toàn thang | Gain dao động dB (lớn nhất) |
|---|---|---|---|---|---|
| −50 | −25,16 | 1,45 | −3,00 | 0 | 11,4 |
| −40 | −25,16 | 1,11 | −3,00 | 0 | 11,3 |
| −30 | −25,25 | 1,54 | −3,00 | 0 | 10,4 |
| −20 | −25,10 | 1,44 | −3,00 | 0 | 12,3 |
| −10 | −25,19 | 1,56 | −3,00 | 0 | 12,3 |

## 2. Đọc bảng

- Mọi mức vào đều trong ±3 dB: xa nhất 1,56 dB. Đầu ra to hơn đích một cách hệ thống 0,75–0,9 dB: `vad` gắn cờ thêm
  khoảng một phần năm số bước (kéo dài, nghỉ giữa từ) và cổng 10 dB chưa loại hết chúng.
- Không cắt đỉnh: đỉnh ra cao nhất đúng trần −3 dBFS, không mẫu nào chạm toàn thang.
- Không dao động chu kỳ: trên tín hiệu dừng gain tiến về đích một phía rồi đứng yên (`ml/tests/test_agc.py`). Gain chạy
  10–12 dB trong một cảnh là do mức từng câu VIVOS tự lệch nhau tới ±6 dB; san phẳng chỗ đó là việc của `agc`.

## 3. Các bản đã thử

Cùng cảnh, bản rút gọn 40 s mỗi cảnh trừ dòng cuối; lệch xa nhất theo mức vào −50 … −10 dBFS.

| Bản | Mức ra trung bình dBFS | Lệch xa nhất dB |
|---|---|---|
| mức từ mọi bước `vad = 1`, τ 0,5 s, `vad` mức 0 | −23,4 … −21,7 | 2,5 … 7,2 |
| thêm cổng P.56 15,9 dB, `vad` mức 0 | −24,8 … −22,3 | 2,3 … 6,7 |
| như trên, `vad` mức 2 | −24,9 … −24,1 | 1,8 … 3,6 |
| τ 2 s, cổng 15,9 dB, `vad` mức 2, bản đầy đủ | −24,9 … −24,7 | 1,5 … 2,1 |
| **τ 2 s, cổng 10 dB, `vad` mức 2, bản đầy đủ — đang dùng** | −25,3 … −25,1 | 1,1 … 1,6 |

`vad` mức 0 hỏng ở mức vào −10 dBFS vì nền ồn −30 dBFS vượt trần mô hình nhiễu của WebRTC và được gắn cờ nói gần như
liên tục (`vad.md` §4); `agc` khi ấy thích nghi trên nhiễu.
