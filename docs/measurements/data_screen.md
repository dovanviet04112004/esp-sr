# Sàng lọc dữ liệu (E11-T16)

`make screen` (`srpipe.core.screen measure` rồi `judge`) và `make screen-audit` tại `e1ddea6`, ngày 28/09. Luật và cách
đặt ngưỡng ở KẾ HOẠCH §1.2, cấu hình ở `ml/configs/common/screen.yaml`. Kết quả: `interim/screen/measures/` (số đo
từng mẩu) và `interim/screen/rejects.tsv` (mẩu bị loại, lý do).

## 1. Đo

| Mục | Giá trị |
|---|---|
| Kho | 5 kho tiếng nói, 4 kho nhiễu, OpenSLR 28; 832 878 mẩu, 970 giờ |
| Mỗi mẩu | RMS và đỉnh (dBFS), phần mẫu ở trần (≥ 0,999), mức khung 32 ms ở phân vị 10 và 95, thời gian trong 40 dB của khung to nhất, số âm tiết của lời sau `lang.normalize`, băm PCM |
| Thời gian | 17 phút, 20 tiến trình. Kho parquet nhanh (Bud500 649 000 mẩu trong 285 s); kho nhiều file nhỏ chậm vì WSL đọc ổ E: qua 9p (Common Voice 20 100 file trong 155 s, CPU rảnh 90%) |
| Bộ nhớ | mỗi tiến trình giữ một nhóm dòng parquet, ~400 MB trên Bud500; 20 tiến trình đẩy WSL 10 GB vào swap, nên cấu hình để 12 |
| Chấm lại | `judge` đọc số đo, 20 s; chỉ đo lại kho nào đổi bố cục, đổi thiết lập đo hay đổi code đo |

## 2. Đặt ngưỡng tiếng nói bằng quét

Rút ngẫu nhiên 30 mẩu mỗi ô, trong các mẩu giải mã được và có lời, và với mọi thước trừ độ lớn thì chỉ lấy mẩu không
câm. PhoWhisper-large nghe lại. Một mẩu **không dùng được** khi PhoWhisper nghe sai từ nửa số âm tiết của lời trở lên.
Ô nào từ nửa số mẩu trở lên không dùng được thì luật cắt tới ranh của ô ấy. 877 mẩu khác nhau, 12 phút.

| Thước | Ô | Mẩu trong ô | Nghe | Âm tiết sai, trung vị | Không dùng được |
|---|---|---|---|---|---|
| RMS (dBFS) | < −70 | 455 | 30 | 108% | 100% |
| | −70 … −60 | 35 | 30 | 100% | 80% |
| | −60 … −55 | 23 | 23 | 100% | 87% |
| | −55 … −50 | 36 | 30 | 25% | 43% |
| | −50 … −45 | 240 | 30 | 0% | 13% |
| | −45 … −40 | 771 | 30 | 3% | 0% |
| | −40 … −35 | 3 637 | 30 | 0% | 0% |
| | ≥ −35 | 758 825 | 30 | 0% | 0% |
| Phần mẫu ở trần | < 0,001 | 754 360 | 30 | 11% | 7% |
| | 0,001 … 0,003 | 6 483 | 30 | 0% | 3% |
| | 0,003 … 0,01 | 2 458 | 30 | 0% | 3% |
| | 0,01 … 0,03 | 213 | 30 | 0% | 3% |
| | 0,03 … 0,1 | 15 | 15 | 10% | 13% |
| | ≥ 0,1 | 3 | 3 | 0% | 33% |
| Khung to − khung nhỏ (dB) | < 2 | 5 | 5 | 0% | 40% |
| | 2 … 3 | 53 | 30 | 0% | 20% |
| | 3 … 4 | 140 | 30 | 0% | 10% |
| | 4 … 5 | 252 | 30 | 0% | 23% |
| | 5 … 6 | 429 | 30 | 0% | 17% |
| | 6 … 8 | 1 649 | 30 | 3% | 13% |
| | 8 … 10 | 4 180 | 30 | 5% | 7% |
| | ≥ 10 | 743 662 | 30 | 0% | 0% |
| Âm tiết mỗi giây tiếng hoạt động | < 0,5 | 49 | 30 | 67% | 50% |
| | 0,5 … 0,75 | 160 | 30 | 100% | 57% |
| | 0,75 … 1 | 571 | 30 | 42% | 50% |
| | 1 … 1,25 | 533 | 30 | 6% | 33% |
| | 1,25 … 1,5 | 876 | 30 | 0% | 17% |
| | 1,5 … 6 | 749 009 | 30 | 0% | 0% |
| | 6 … 7 | 11 480 | 30 | 0% | 0% |
| | 7 … 8 | 750 | 30 | 0% | 7% |
| | 8 … 9 | 79 | 30 | 0% | 7% |
| | 9 … 10 | 10 | 10 | 39% | 30% |
| | 10 … 12 | 5 | 5 | 79% | 100% |
| | ≥ 12 | 10 | 10 | 100% | 100% |

Đọc bảng:

- **Câm: dưới −55 dBFS.** Từ −55 dBFS trở xuống, 80–100% mẩu là rác. Ô −55 … −50 có 43% không dùng được, dưới mức nửa
  nên được giữ. Từ −50 dBFS trở lên, gần như mọi mẩu đều nghe ra lời.
- **Chạm trần và khoảng động: không có luật.** Không ô nào có tới nửa số mẩu không dùng được. Mẩu có 1–3% mẫu ở trần vẫn
  nghe tốt như phần thân. Khoảng động nhỏ (2–6 dB) có 10–23% mẩu hỏng, cao hơn phần thân nhưng xa mức nửa. Cả hai thước
  vẫn được đo và nằm trong bảng quét.
- **Tốc độ: giữ 1–10 âm tiết mỗi giây.** Dưới 1 âm tiết/s, phần lớn là lời thiếu so với tiếng (tiếng còn nói tiếp sau
  câu ghi). Trên 10 âm tiết/s, mẩu ngắn hơn hẳn lời. Ô 9 … 10 có 30% hỏng nên được giữ.
- Phần thân (RMS ≥ −35 dBFS, tốc độ 1,5 … 6) có 0% mẩu không dùng được. Nghĩa là PhoWhisper không làm hỏng phép đo:
  mẩu tốt thì nó nghe đúng.

## 3. Kết quả

| Kho | Mẩu | Giờ | Câm | Lời lệch tiếng (tốc độ) | Trùng | Còn lại (giờ) |
|---|---|---|---|---|---|---|
| Common Voice 27.0 (validated, other) | 20 100 | 22,35 | 62 (0,07 h) | 81 (0,09 h) | | 22,20 |
| VIVOS | 12 420 | 15,67 | 4 | 13 (0,01 h) | | 15,65 |
| FPT | 25 917 | 30,18 | 442 (0,67 h) | 28 (0,06 h) | | 29,45 |
| VLSP2020-100h | 56 427 | 101,38 | 5 (0,01 h) | 668 (0,39 h) | 28 (0,04 h) | 100,94 |
| Bud500 | 649 158 | 462,08 | | 5 (0,01 h) | 10 (0,01 h) | 462,06 |
| DEMAND | 272 | 22,67 | | | | 22,67 |
| MUSAN | 2 016 | 109,29 | 1 | | | 109,29 |
| DNS Challenge | 65 302 | 180,45 | 109 (0,30 h) | | 23 (0,06 h) | 180,09 |
| Speech Commands, nền | 6 | 0,11 | | | | 0,11 |
| OpenSLR 28 | 61 260 | 26,25 | 1 | | 842 (5,91 h) | 20,34 |

Không mẩu nào hỏng giải mã hay có lời mà `normalize` từ chối. Tổng cộng loại 2 322 mẩu: tiếng nói 1 346 mẩu (1,36 trên
631,7 giờ), nhiễu và RIR 976 mẩu.

- **FPT**: 437 mẩu của Set002 giải mã ra toàn số 0 dù vẫn có lời, soundfile và ffmpeg cho cùng kết quả. Đây là lỗi của
  chính bản dữ liệu. Ba trong số đó từng là giọng mẫu cho F5, và F5 nhân bản chúng thành tiếng rác bão hoà, giống hệt
  nhau với mọi câu.
- **VLSP**: 668 mẩu lệch lời, gần hết là đọc chậm, tức lời ghi thiếu một phần tiếng.
- **OpenSLR 28**: cả 842 mẩu trùng đều nằm ở `pointsource_noises`, và mỗi mẩu trùng hệt một file nhiễu của MUSAN. Để lại
  thì cùng một nhiễu được trộn với trọng số gấp đôi.
- **Nhiễu và RIR**: sàn câm là một bước lượng tử 16 bit (−90,3 dBFS). Sàn −55 dBFS của tiếng nói sẽ loại nhầm: DEMAND có
  phòng khách yên tĩnh ở −57 dBFS, và RIR mô phỏng chỉ là một xung ngắn trong 2 s, RMS xuống tới −80 dBFS. 109 mẩu DNS
  câm, phần lớn toàn số 0.
