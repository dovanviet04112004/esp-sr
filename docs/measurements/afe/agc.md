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

## 4. Mức vào `agc` trên board B (E7-T5, E9-T1)

`srhost.score` chạy `ch0 ch1` của từng phiên qua chuỗi sản phẩm tới trước `agc` (`hpf`, `balance` theo
`calib/board_b_balance.csv`, đúng bảng board giữ ở `calib/bal`, `ns_omlsa` sàn −12 dB, `vad` mức 2) và in `level_dbfs` của
từng bước; chuỗi này khớp board tới 1 LSB (`parity.md`). `pcm_shift` 16. Cột cuối là trung vị khi chưa có `ns` (E7-T5).

| Phiên | Nội dung | p10 dBFS | p50 dBFS | p90 dBFS | Bước `vad = 1` | p50 không `ns` |
|---|---|---|---|---|---|---|
| `20260926_home_005` | phòng yên, Wi-Fi tắt, 60 s | −94 | −92 | −90 | 0 % | −80 |
| `20260926_home_004` | phòng yên, Wi-Fi phát, 60 s | −94 | −93 | −91 | 0 % | −81 |
| `20260926_home_006` | cào cạnh lỗ micro A | −94 | −92 | −91 | 3,3 % | −80 |
| `20260926_home_009` | ồn trắng từ loa 1 m trước hộp | −94 | −75 | −71 | 0,2 % | −63 |
| `20260926_home_011` | ồn trắng 20 cm trước hộp, âm lượng tối đa | −92 | −68 | −66 | 3,0 % | −57 |

Nền phòng yên tới `agc` ở −92 dBFS: `ns` dìm đúng 12 dB của nền dừng. Không có `balance` nền còn cao hơn 7 dB, vì `balance`
hạ `ch1` 11 dB cho khớp `ch0` (`mic_array.md` §1), nên cả chuỗi chạy ở mức của `ch0`, micro nghe nhỏ.

**Tiếng người thật ở 1 m** (28/09). Repo tinyai-signal có bản thu cùng board B trong vỏ: hai người đọc giọng thường ở
1 m (`data/raw/tach_2nguoi/A_trai_1.wav`, `B_phai_1.wav`, 02/09, 15 s mỗi bản). Các bản ấy thu ở dịch 13: nền 2–6 kHz của
chúng cao hơn nền của `20260926_home_005` (dịch 16) 17,5–18,5 dB ở cả hai kênh, gần đúng 3 bit. Hạ về dịch lớn hơn bằng
`floor(x / 2^k)` là đúng từng mẫu như `drv_audio` dịch, nên bảng dưới là chuỗi sản phẩm (có `balance` của board B) chạy
trên đúng thứ board sẽ thu ở mỗi dịch.

| Dịch | Bước `vad = 1` | Mức vào `agc` p50 / p90 dBFS | Gain `agc` cuối bản | Mức ra p50, nửa sau, bước có tiếng |
|---|---|---|---|---|
| 16 | 4 % / 15 % | −66 / −61 và −71 / −65 | 2 / 7 dB | −65 / −64 dBFS |
| 14 | 56 % / 71 % | −61 / −51 và −61 / −53 | 25 / 30 dB | −42 / −40 dBFS |
| 13 | 67 % / 75 % | −56 / −45 và −55 / −47 | 21 / 26 dB | −36 / −34 dBFS |

Ở dịch 16 chuỗi **gần như không thấy người nói ở 1 m**: `vad` bật ở 4–15% số bước trong khi người đọc liền 15 s, nên
`agc` đứng yên gần 0 dB và tiếng ra ở −65 dBFS. Từ dịch 14 trở xuống `vad` bắt được lời và `agc` kéo lên. Bản thu chỉ
15 s nên `agc` chưa tới trạng thái dừng; mức ra lấy trung vị theo dB của từng bước, thấp hơn trung bình công suất mà
`agc` bám. Chọn dịch vẫn là E2-T5: nó cần thêm tiếng to ở 10 cm để biết dịch nhỏ nhất không cắt đỉnh (`host/plans/loud.tsv`).

**Mô phỏng board cho cùng kết luận** (27/09, E4-T8, `configs/scenes/device.yaml`, `pcm_shift` 16, `calib/bal` của board B):
760 câu VIVOS test, người nói 56–74 dB SPL ở 1 m, cách 0,3–4 m (trung vị 1,5 m), 89% phiên có nhiễu ở SNR 0–30 dB. Trên
các bước `vad = 1`: mức vào `agc` p5 / p50 / p95 là −89 / −69 / −55 dBFS, gain `agc` trung vị 26 dB và chạm trần 30 dB ở
p95, mức ra ước (mức vào + gain) −75 / −48 / −27 dBFS. Chuỗi thiếu đích −26 dBFS **22 dB ở trung vị**; đây là số mô phỏng
🔬, phép đo tiếng nói thật vẫn là E2-T5. Hệ quả cho đặc trưng: 39% giá trị log-mel của các bước có tiếng nằm trong 1 nat
trên sàn `log_floor` 1e-6, 88% ở dải 40 (E11-T8).
