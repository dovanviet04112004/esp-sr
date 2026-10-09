# Báo cáo tuần 12 — thực tập sinh kỹ thuật

Tuần 12 (05/10 – 09/10/2026)

## 1. Thông tin chung

| Mục | Nội dung |
|---|---|
| Họ và tên | Đỗ Văn Việt |
| Phòng ban | Thiết kế điện tử |
| Người hướng dẫn (Mentor) | Anh Chung |
| Tuần báo cáo | Tuần 12 (05/10 – 09/10/2026) |
| Phạm vi tuần | Dự án esp-sr: mô hình âm vị `ctc` nhận lệnh tiếng Việt trên giọng thật thu qua board, cách quyết định và các ngưỡng, cao độ cho thanh điệu, mạng giảm nhiễu. Mọi số đo trên board B: ESP32-S3, hai micro INMP441 cách 4,5 cm |

## 2. Tóm tắt tuần

Tuần này tập trung vào mô hình âm vị `ctc` trên chính giọng chủ dự án thu qua board. Qua bốn bản dữ liệu và công thức học (v5 tới v8), bản
v8 (bề rộng 160, 80 dải log-mel, 392 giờ dữ liệu, cao độ SwiftF0 lúc học) xếp đúng lệnh 194/209 câu và nhận đúng 172/209
trên Cửa 3, so với 135 và 110 của bản demo tuần trước ở cùng ngưỡng. Lỗi nặng nhất tuần trước, "tắt" nghe thành "bật",
từ 2/69 câu đúng lên 62/69. Bộ dò cao độ Kaldi trên chip dò sai âm tiết đầu của lệnh, nên board chạy v8 với ba chiều cao
độ giữ hằng số; cao độ lúc học thì vẫn phải có, vì học không có nó thì "tắt" hỏng ngay từ những bước đầu. Phần quyết
định có thêm ngưỡng thứ ba δ₃ cho âm tiết kém nhất của lệnh, chặn câu ngoài bộ lệnh; δ₃ đi đủ từ Python tới chip và
khớp từng bit. Chip trùng Python 336/336 cửa sổ sau khi FFT viết tay khớp bản Python từng bit. Hai mạng giảm nhiễu học
tới epoch 17 nhưng trên bản thu qua board chưa hơn OM-LSA với tiếng quạt, và chỉ hơn 1,5–2,8 dB với nhạc. Đang chạy lượt
học lớp nhìn trước 0,4 s từ đầu: ở bước 10 000 nó đã xếp đúng 198/209.

Cửa 3 là thước quyết định của dự án: 209 câu lệnh của bộ 10 lệnh mặc định trong các phiên chủ dự án nói qua board B,
cộng 126 cửa sổ phải từ chối (tiếng động, câu gần âm, nói tự do), chạy qua đúng chuỗi xử lý của board. "Xếp đúng" là lệnh
điểm cao nhất đúng, không ngưỡng; "nhận đúng" là xếp đúng và qua mọi ngưỡng như chip quyết.

## 3. Mô hình âm vị `ctc`: thông số đã nâng cấp

Mô hình nghe ra chuỗi 44 đơn vị âm có thanh rồi chấm từng lệnh viết bằng chữ (tuần 11, mục 5.3). Tuần này gần như mọi
thông số đã đổi; mỗi thay đổi có số đo đi kèm trong `docs/measurements/command.md` mục 12.

| Thông số | Tuần 11 (v2, bản demo 02/10) | Tuần 12 (v8, trên board B từ 08/10) | Vì sao đổi |
|---|---|---|---|
| Đặc trưng mỗi 16 ms | 40 log-mel + 3 cao độ Kaldi | 80 log-mel + 3 cao độ: lúc học từ SwiftF0, trên board giữ hằng số | chọn theo chất lượng cùng bề rộng mạng; cao độ ở mục 5 |
| Encoder | bề rộng 128, feedforward 256, 1,98 triệu tham số | bề rộng 160, feedforward 320, 3,14 triệu tham số | chọn theo chất lượng, vẫn trong ngân sách 11–18 ms mỗi 32 ms |
| Trên chip | 5,7 ms mỗi 32 ms tiếng, PSRAM 274 KB, dựng 308 ms | 10,5 ms mỗi 32 ms, PSRAM 309 KB, dựng 346 ms, RAM nội 0 | giá của mạng lớn hơn |
| Dữ liệu học | 103,4 giờ | 392,3 giờ, 204 140 câu: VIVOS, Common Voice, FPT, VLSP, LSVSC 100,6 giờ, Speech-MASSIVE 5,3 giờ, đoạn ViMD 97,0 giờ, 10 phiên board của chủ dự án; bỏ Bud500 | Bud500 25 giờ hay 462 giờ cho cùng Cửa 3 (84/112); thêm giọng người thật |
| Cắt mẫu học | mẩu cộng đệm 0,3 s | cắt đúng luật cửa sổ lệnh của board, phần trước câu rút ngẫu nhiên 0,3–2,0 s | mạng bắt đầu mỗi cửa sổ từ bộ đệm rỗng, âm tiết đầu phụ thuộc phần trước câu |
| Tăng cường | phòng và vang mô phỏng, nhiễu, SpecAugment | thêm: tốc độ 0,9 / 1,0 / 1,1, nhịp nói 0,8–1,6 lần giữ cao độ, co giãn trục tần số (VTLP) 0,8–1,2, nghiêng phổ ±3 dB, cong thời gian | mạng chưa nghe câu nói nhanh; giọng trầm của chủ dự án nằm ở rìa giọng đã học |
| Lô học | 32 câu rút thẳng | 32 câu rút trong 16 nhóm độ dài | rút thẳng thì khoảng 40% khung GPU là phần đệm |
| Cửa sổ lệnh trên board | mở 1,25 s trước câu, cao độ tính theo cửa sổ | mở 2,0 s trước câu, dài tối đa 3,75 s; cao độ tính liên tục mỗi 16 ms (1,98 ms, khoảng 12% nhân 0) | mạng nguội ở đầu cửa sổ (mục 6.3) |
| Thước `val` | mẩu cộng đệm | cửa sổ cắt như board | đo đúng thứ board gặp |
| Ngưỡng | δ₁ 300 ‰, δ₂ 50 ‰ | ảnh model: δ₁ 200 ‰, δ₂ 0 ‰, δ₃ 60 ‰; board đang chạy δ₁ 200 ‰, δ₂ 25 ‰, không δ₃ | mục 4 |

Lỗi đơn vị trên `val` không so thẳng được với tuần trước vì thước đã đổi: v2 29,5% trên `val` cũ (mẩu cộng đệm, dễ), v8
34,3% trên `val` mới (cửa sổ cắt như board, khó hơn), 37,5% khi giữ cao độ như board chạy. Lỗi đơn vị là khoảng cách sửa
giữa chuỗi đơn vị mạng nghe và chuỗi đúng, chia số đơn vị, tính cả đơn vị thanh. Cửa 3 mới là thước quyết định.

Đường đi trong tuần, mỗi bản đổi một nhóm thông số rồi chấm trên giọng chủ dự án:

| Bản | Ngày | Thay đổi | Kết quả trên bản thu qua board |
|---|---|---|---|
| v5 | 05/10 | cắt mẫu như board, mở 2 s, cao độ tính liền cả phiên | Cửa 3 cũ (112 câu) 78/112 ở bước 70 000; "tắt" 0/22 |
| v6 | 06/10 | bỏ Bud500, phần trước câu ngẫu nhiên, VTLP | 92/112 ở bước 6 000; lần đầu nhận "tắt" (16/22) mà vẫn giữ "bật" |
| v7 | 07/10 | thêm 220 giờ giọng thật | Cửa 3 mới (209 câu) 170/209; "tắt" 36/69 |
| v8 | 07/10 | cao độ SwiftF0 lúc học | 196/209 nghe bằng SwiftF0, 194/209 khi giữ cao độ như board; "tắt" 62/69 |

Từng lệnh trên Cửa 3, cùng ngưỡng δ₁ 100 ‰, δ₂ 25 ‰, xếp đúng / nhận đúng:

| Lệnh | v2 (tuần 11) | v8, cao độ giữ như board |
|---|---|---|
| bật đèn | 35 / 29 | 35 / 35 |
| tắt đèn | 1 / 0 | 32 / 26 |
| bật quạt | 35 / 35 | 35 / 35 |
| tắt quạt | 1 / 0 | 30 / 25 |
| mở cửa | 10 / 10 | 10 / 10 |
| đóng cửa | 11 / 10 | 10 / 8 |
| tăng âm lượng | 10 / 5 | 10 / 7 |
| giảm âm lượng | 11 / 3 | 11 / 6 |
| dừng lại | 10 / 9 | 10 / 10 |
| chụp ảnh (chưa có trong dữ liệu học) | 11 / 9 | 11 / 10 |
| **Cả bộ** | **135 / 110** | **194 / 172** |
| Cửa sổ lạ bị nhận nhầm | 1/126 | 0/126 |

Thanh đúng ở âm tiết đầu của lệnh, trên cùng 209 câu: 79% khi giữ cao độ, 80% khi nghe bằng SwiftF0.

## 4. Quyết định và ngưỡng

### 4.1. Ba ngưỡng và luật phần

```
log-xác suất 45 lớp mỗi khung 32 ms của cửa sổ lệnh
   ▼
chấm mọi lệnh và mọi biến thể giọng bằng thuật toán tiến; chấm vòng tự do (chuỗi đơn vị tốt nhất, không ràng buộc)
   ▼
gap: vòng tự do hơn lệnh tốt nhất bao nhiêu, ‰ mỗi khung            quá δ₁  → từ chối LOW_SCORE
lead: lệnh tốt nhất hơn lệnh nhì bao nhiêu                          dưới δ₂ → từ chối LOW_MARGIN
âm tiết kém nhất của lệnh thắng hụt so với vòng tự do (mới)         quá δ₃  → từ chối LOW_SYLLABLE
một phần của lệnh được điểm bằng hay hơn cả lệnh                     → từ chối PART
   ▼
lệnh, hoặc từ chối kèm mã
```

- δ₁ đo câu có giống lệnh nào không: câu nói thường thì vòng tự do hơn xa mọi lệnh.
- δ₂ đo hai lệnh có quá sát nhau không, như "tắt đèn" và "bật đèn".
- δ₃ chỉ áp cho lệnh từ hai âm tiết. Nó lấy đường Viterbi của biến thể thắng, cộng phần hụt so với vòng tự do theo từng
  âm tiết, rồi lấy âm tiết hụt nhiều nhất.
- Luật phần có từ tuần trước: nói mỗi chữ "chụp" thì không được nhận thành "chụp ảnh".

### 4.2. Vì sao thêm δ₃

Tối 08/10, với bộ 47 lệnh của chủ dự án (không có "bật đèn"), chủ dự án nói "bật đèn" và board nhận thành "bạn bè" rồi
"tắt đèn". Đo trên đồ thị int8 của board, 80 cửa sổ "bật đèn", "bật quạt" chấm trên bộ ấy:

- ở 63/80 cửa sổ lệnh điểm cao nhất là "tắt …", mà vòng tự do vẫn nghe phụ âm đầu "b" ở 57/63 và thanh nặng ở 58/63:
  mạng nghe ra "bật", chỉ là "bật" không có trong bộ;
- âm tiết kém nhất của đường "tắt" hụt so với vòng tự do 51–162 ‰, trung vị 98;
- ở cửa sổ nhận đúng lệnh, âm tiết kém nhất chỉ hụt 0–201 ‰, trung vị 28, phân vị 90 là 60.

Hai nhóm tách nhau ở âm tiết kém nhất, trong khi gap trung bình cả câu thì không đủ tách, nên thêm δ₃. Luật này đổi hợp
đồng quyết định, nên đi đủ bốn bước: bản Python, bộ vàng có đối chứng âm, bản C khớp Python từng bit, header ảnh model
`format_ver` 4 mang δ₃, khoá NVS `kws/cmd_syllable` để đổi lúc chạy.

### 4.3. Chọn ngưỡng

`make ctc-thresholds` quét lưới δ₁ {100 … 1 000} × δ₂ {0, 25, 50, 75, 100, 150, 200} × δ₃ {40, 50, 60, 70, 80, 100, 150,
không trần} trên đồ thị int8 của board, với `val_commands`: 216 cửa sổ của 8 lệnh do người thật nói, qua mô phỏng board.
Ràng buộc:

- câu nói thường của `val` bị nhận nhầm thành lệnh không quá 1%;
- câu ngoài bộ không quá 8%: mỗi cửa sổ `val_commands` quyết thêm một lần trên bộ thiếu chính lệnh nó nói. 8% là đúng mức
  của δ₁ 100 ‰, δ₂ 25 ‰ không trần (17/216).

Trong các bộ qua ràng buộc, chọn bộ có lệnh kém nhất được nhận nhiều nhất, rồi cả bộ. Một số dòng của lưới, đo trên đồ thị
int8 của board; "bật" lọt là 80 cửa sổ "bật đèn", "bật quạt" chấm trên bộ 47 lệnh không có chúng:

| δ₁ | δ₂ | δ₃ | Cửa 3 nhận đúng | Cửa sổ lạ nhận nhầm | `val_commands` nhận đúng | Câu ngoài bộ nhận | "bật" lọt |
|---|---|---|---|---|---|---|---|
| 100 | 25 | — | 172/209 | 0/126 | 75,0% | 17/216 | 23/80 |
| 200 | 25 | — | 182/209 | 5/126 | 78,2% | 49/216 | 42/80 |
| **200** | **0** | **60** | **175/209** | **0/126** | **75,0%** | **17/216** | **11/80** |
| 200 | 0 | 80 | 184/209 | 1/126 | 80,6% | 29/216 | 22/80 |
| 200 | 0 | — | 194/209 | 6/126 | 84,3% | 72/216 | 66/80 |

Luật chọn ra δ₁ 200 ‰, δ₂ 0 ‰, δ₃ 60 ‰, ghi vào ảnh model. So với ngưỡng board chạy lúc ấy (δ₁ 200 ‰, δ₂ 25 ‰), bộ ba này
mất 7 câu nhận đúng của Cửa 3, đổi lại "bật" lọt từ 42 xuống 11/80 và cửa sổ lạ nhận nhầm từ 5 xuống 0/126.

### 4.4. Thử trực tiếp trên board

Đêm 08/10 board B chạy ảnh mới với bộ 301 lệnh của chủ dự án; chủ dự án nói thử và thấy board bỏ nhiều câu. 23:25 nới δ₃
lên 80 ‰, 00:23 về lại δ₁ 200 ‰, δ₂ 25 ‰, không δ₃, qua NVS. Log UART theo từng quãng; cột cuối là số cửa sổ ngưỡng cũ
cũng nhận, tính nhiều nhất:

| Quãng | Nhận | LOW_SCORE | LOW_MARGIN | LOW_SYLLABLE | PART | Ngưỡng cũ nhận |
|---|---|---|---|---|---|---|
| δ₂ 0, δ₃ 60, 23:07–23:25 | 8 | 130 | — | 72 | 22 | ≤ 23 |
| δ₂ 0, δ₃ 80, 23:25–00:23 | 77 | 269 | — | 207 | 134 | ≤ 50 |
| δ₂ 25, không δ₃, 00:23–07:24 | 45 | 854 | 294 | — | 15 | 45 |

Ở mọi ngưỡng, phần lớn cửa sổ bị từ chối vì LOW_SCORE: lệnh tốt nhất kém vòng tự do quá 200 ‰, tức mạng nghe câu ấy
khác xa mọi lệnh. δ₃ không phải nguyên nhân chính khiến board bỏ câu khi nói trực tiếp. Log chỉ có điểm, không có tiếng,
nên chưa biết cửa sổ nào là lệnh thật; đây là hướng xử lý ở mục 8.

## 5. Cao độ và thanh điệu

### 5.1. Bộ dò Kaldi trên chip sai ở âm tiết đầu

Board tính cao độ bằng bộ dò Kaldi chạy dòng. Trên giọng chủ dự án, nó sai 16–58% khung ở âm tiết đầu của lệnh và 0–16%
ở âm tiết sau: đường Viterbi chạy dòng nối từ nền trước câu vào âm tiết đầu, và "tắt" giọng cao 200 Hz hay bị dò thành
nửa tần số. Năm bộ dò trên tín hiệu board, khả năng tách "tắt" (thanh sắc) với "bật" (thanh nặng) của chủ dự án theo
từng buổi thu (AUC, 0,5 là đoán mò):

| Bộ dò | AUC "tắt" / "bật", 5 buổi | Ghi chú |
|---|---|---|
| Kaldi như board | 0,56–0,97 | 1,98 ms mỗi bước trên board |
| Kaldi, dò lại đường Viterbi 0,4 s sau | 0,88–1,00 | board vốn chờ 0,4 s trước khi chốt câu |
| YIN | 0,55–1,00 | hỏng ở hai buổi |
| SwiftF0 | 0,80–1,00 | 95 842 tham số, ước 0,57 tỉ MAC mỗi giây |
| PESTO | 0,22–0,65 | nhiều câu không khung nào đủ tin |

### 5.2. Lượt v8: học với SwiftF0, chạy trên board không cao độ

SwiftF0 tách thanh tốt nhất, nhưng esp-dl trên board chạy 0,1–0,5 tỉ MAC mỗi giây, nên SwiftF0 nguyên cỡ không vừa chip.
v8 học với ba chiều cao độ của SwiftF0 (độ hữu thanh, log F0 trừ trung bình, delta) để biết mạng có dùng thanh khi thanh
đến được hay không. Trên Cửa 3, năm cách đưa cao độ vào v8:

| Cách nghe cao độ | Xếp đúng | Thanh âm tiết đầu | Thanh các âm tiết sau |
|---|---|---|---|
| SwiftF0, như lúc học | 196/209 | 80% | 77% |
| Kaldi như board dò | 186/209 | 67% | 64% |
| Kaldi, giữ độ hữu thanh | 192/209 | 77% | 66% |
| Kaldi, giữ hai chiều F0 | 170/209 | 62% | 64% |
| Không cao độ, giữ cả ba chiều | 194/209 | 79% | 66% |

Không cao độ thua SwiftF0 2 câu và hơn mọi cách dùng Kaldi, nên board chạy v8 với cả ba chiều giữ ở trung bình lúc học.
Phép giữ gập thẳng vào trọng số của phép chiếu đầu, firmware không đổi gì.

### 5.3. Cao độ lúc học vẫn phải có

Ba thử nghiệm cùng công thức, cùng seed, chấm như board chạy (cao độ giữ), "tắt" trên 69 câu của chủ dự án:

| Lượt | Cách học | Cửa 3 xếp đúng / nhận đúng | "tắt" xếp đúng |
|---|---|---|---|
| v8 | 40 000 bước, có SwiftF0 | 194 / 172 | 62 |
| A, B | học tiếp 10 000 bước từ v8, cao độ giữ | 170 / 137 và 181 / 149 | 38 và 50 |
| C, D | học tiếp 10 000 bước từ v8, có SwiftF0 | 191 / 177 và 191 / 173 | 61 và 62 |

Lượt E học từ đầu như v8 nhưng giữ cao độ từ bước 0, so với chính v8 ở cùng bước (cùng dữ liệu, seed, lịch học, chỉ khác
cao độ lúc học):

| Bước | Xếp đúng v8 / E | Nhận đúng v8 / E | "tắt" xếp đúng (nhận) v8 / E |
|---|---|---|---|
| 4 000 | 182 / 128 | 135 / 3 | 69 (63) / 65 (0) |
| 6 000 | 183 / 149 | 144 / 37 | 67 (61) / 39 (3) |
| 8 000 | 193 / 185 | 151 / 41 | 68 (67) / 66 (6) |
| 10 000 | 187 / 177 | 145 / 74 | 60 (48) / 53 (1) |

Trên `val` mô phỏng, E ngang v8 từ bước 6 000 (lỗi đơn vị 0,620 so với 0,610) và còn hơn ở bước 10 000 (0,515 so với
0,539), nhưng trên bản thu qua board E kém ở mọi bước. Nới δ₁ tới 300 ‰ không gỡ được: E vẫn chỉ nhận 75 câu. Với
giọng chủ dự án, nguyên âm của "tắt" và "bật" gần trùng nhau (F1 lệch 60–80 Hz, buổi 28/09 ở 3 m trùng hẳn: 694 so với
695 Hz), chỗ khác rõ nhất là độ cao giọng (200 so với 131 Hz). Mạng học không có cao độ không tách chắc được hai chữ ấy:
E xếp đúng "tắt" 53/69 câu nhưng chỉ 1 câu qua ngưỡng; A học tiếp không cao độ thì nghe nguyên âm của "tắt" thành "â"
của "bật" ở 64% số câu.

Kết luận: học có SwiftF0, chạy trên board không cao độ. Cách này ngược trực giác nhưng là cách cho số tốt nhất trên bản
thu thật.

## 6. Phần chạy trên chip

### 6.1. Chip trùng Python từng trường

| Đo trên board B | Kết quả |
|---|---|
| `svc_listen` cả chuỗi so với Python, 7 lượt nạp, trước khi sửa | 304/319 cửa sổ trùng từng trường; 15 cửa sổ lệch tới 22 ‰, phần lớn 1–9 ‰, không cửa sổ nào đổi loại quyết định hay lệnh |
| Nguyên nhân | một phần tử log-mel nằm sát ranh làm tròn int8: log-mel trên chip dùng `madd.s`, `logf` của newlib và `dl_fft`, Python làm tròn khác |
| Sau khi viết lại FFT cơ số 4 khớp bản Python từng bit | **336/336** cửa sổ trùng; ca hở luồng và ca click qua cả 7 lượt |
| `ai_engine` với v8 | dựng mạng 346 ms; 10,5 ms mỗi 32 ms tiếng; chấm 79,7 ms trung bình mỗi câu; Cửa 3 int8 trên chip 335/335 trùng Python |
| Quyết định sau khi câu chốt | 76–114 ms trung bình mỗi lượt; tới 1,28 s ở cửa sổ dài phải cắt lùi |
| Bộ 301 lệnh của chủ dự án | board nhận lúc chạy, dựng cây tiền tố 4 426 nút (trần 32 768) |

### 6.2. Thang int8

v8 qua thang lượng tử bốn bậc của dự án; dòng `percentile` (hiệu chuẩn theo phân vị, int8 thuần) được chọn vì hoà các
dòng tốt nhất trong biên 5 câu mà không cần lớp int16. Dòng ấy với cả ba chiều cao độ giữ nhận đúng 163/209 ở δ₁ 300 ‰,
δ₂ 50 ‰, so với 145/209 của dòng chỉ giữ độ hữu thanh.

### 6.3. Mạng nguội ở đầu cửa sổ

Mạng không chạy liên tục: mỗi khi có câu, nó chạy lại phần đặc trưng đã lưu trước câu (tối đa 2 s) để có trạng thái, rồi
mới tới câu. Mở cửa sổ 2 s thay 1,25 s đưa mạng B1 ngày 05/10 từ 89 lên 98/112. Cao độ tính liên tục chỉ để phần chạy
lại ấy không chậm quyết định. Khi hai câu nói sát nhau, cửa sổ sau không được lùi qua cửa sổ trước nên phần trước câu ngắn
hơn 2 s, nhưng `vad` gộp hai tiếng cách nhau dưới 0,4 s nên còn ít nhất khoảng 0,4 s. v8 trên Cửa 3:

| Phần trước câu | 0 s | 0,25 s | 0,5 s | 1 s | 2 s |
|---|---|---|---|---|---|
| Nhận đúng / 210 | 9 | 160 | 173 | 171 | 174 |

v8 cần khoảng 0,5 s, nên phần bị chặn chỉ mất vài câu; mạng nguội không giải thích được việc board bỏ câu khi nói trực
tiếp.

## 7. Giảm nhiễu

Hai ứng viên RNNoise-16k và NSNet-16k (ba cỡ S, M, L) học chung một lượt, cùng batch trộn lúc học. Tuần này đưa phép lọc
của mô phỏng board lên GPU: 3,75–3,81 bước/s, gấp khoảng 3,1 lần khi lọc trên CPU, GPU chờ dữ liệu 0–1% thay khoảng 80%.
Lượt học dừng ở bước 46 412/61 891 (epoch 17), chạy tiếp được bất cứ lúc nào.

Chấm trên bản thu qua board: một đoạn đọc trộn với tiếng quạt hay nhạc không lời thu riêng qua board B, ở SNR 0 và 5 dB;
ô là SNR tăng dB / tiếng mất dB, sau 3 s đầu:

| Biến thể | Epoch | Quạt SNR 0 | Quạt SNR 5 | Nhạc SNR 0 | Nhạc SNR 5 |
|---|---|---|---|---|---|
| Gain lý tưởng | — | +8,7 / 0,45 | +6,3 / 0,28 | +9,5 / 0,70 | +7,0 / 0,39 |
| OM-LSA (đang chạy trên board) | — | +7,8 / 0,9 | +7,1 / 0,5 | 0,0 / 0,5 | +0,2 / 0,3 |
| NSNet-16k L | 2 | +7,7 / 3,0 | +6,3 / 1,4 | +2,4 / 2,2 | +2,1 / 1,4 |
| NSNet-16k L | 17 | +7,6 / 1,7 | +6,2 / 0,7 | +1,9 / 1,6 | +1,7 / 0,9 |
| NSNet-16k M | 17 | +7,5 / 2,4 | +5,9 / 0,9 | +2,8 / 3,0 | +2,4 / 1,6 |
| RNNoise-16k | 17 | +7,1 / 1,7 | +6,1 / 0,9 | +4,0 / 5,7 | +2,3 / 1,2 |

- Với quạt, OM-LSA đã sát gain lý tưởng; không mạng nào hơn nó, và mạng nào cũng làm mất tiếng nói nhiều hơn.
- Với nhạc, ba cỡ NSNet-16k hơn OM-LSA 1,5–2,8 dB, xa mức lý tưởng 9,5 dB; RNNoise lấy 4,0 dB nhưng mất 5,7 dB tiếng.
- Trên tiếng mô phỏng, nhạc không phải lớp khó (NSNet-16k L +4,4 dB, ngang nhiễu dừng +4,8 dB); đoạn nhạc thu qua board khó
  hơn với mọi cách, kể cả OM-LSA (0,0 dB so với +2,0 dB trên nhạc mô phỏng). Bàn so mới có một đoạn nhạc, nên chưa tách được
  là do thu qua loa, phòng, micro hay do chính bản nhạc.

Chưa có lý do thay OM-LSA. Dự án chọn khối giảm nhiễu bằng độ đúng của bộ nhận dạng chứ không bằng dB, và thước ấy chưa dựng.

## 8. Các hướng đang xử lý

| Hướng | Trạng thái 09/10 | Điều kiện hay kỳ vọng |
|---|---|---|
| Lớp nhìn trước 0,4 s, học từ đầu với SwiftF0 | đang học, xong khoảng 15:30; ở bước 10 000: xếp đúng 198/209, nhận đúng 146, "tắt" 66/69 (v8 cùng bước: 187, 145, 60) | thay v8 khi bản cuối nhận đúng hơn v8 quá 5 câu (trên 177/209) mà từ chối không kém; thắng thì làm phần firmware của lớp ấy rồi mới nạp |
| Board bỏ câu khi nói trực tiếp | trên bản thu board nhận đúng 172/209; lúc chủ dự án nói trực tiếp, phần lớn cửa sổ bị từ chối LOW_SCORE (mục 4.4), log chỉ có điểm | cho board truyền tiếng về máy lúc thử, chỉ để chẩn đoán, rồi chấm lại đúng từng câu bị bỏ |
| Bộ dò cao độ trên chip khớp lúc học | SwiftF0 nguyên cỡ không vừa chip | Kaldi dò lại 0,4 s rồi học lại trên chính nó, hoặc một bộ dò cỡ nhỏ; lợi ước 2–4 điểm thanh ở thanh hỏi, ngã, nặng |
| "Tăng/giảm âm lượng" | mạng gắn phụ âm đầu cho "âm", nghe thanh nặng của "lượng" thành sắc ở gần nửa số câu; hai lệnh chưa có phiên board nào trong dữ liệu học | thêm mẩu người thật có sẵn trong kho của hai lệnh vào dữ liệu học |
| Giảm nhiễu | dừng ở epoch 17 | dựng thước độ đúng nhận lệnh cho khe giảm nhiễu; thêm bản thu nhạc qua board |

## 9. Kế hoạch tuần 13

| Việc | Kết quả mong đợi | Ưu tiên |
|---|---|---|
| Chốt lượt nhìn trước: chấm bản cuối trên Cửa 3, nếu thắng thì thang int8, phần firmware của lớp nhìn trước, nạp board | nhận đúng trên 177/209 trên chip, chip trùng Python | cao |
| Chẩn đoán câu nói trực tiếp bằng tiếng truyền về | biết board bỏ câu vì mạng, vì ngưỡng hay vì câu ngoài bộ | cao |
| "Tăng/giảm âm lượng" | nhận đúng hai lệnh trên Cửa 3 từ 7/13 và 6/11 lên mức Cửa 3 đòi, 90% mỗi lệnh | trung bình |
| Thước độ đúng nhận lệnh cho khe giảm nhiễu | chọn giữa OM-LSA và hai mạng bằng Cửa 3 có nhiễu | trung bình |

## 10. Phiên bản công cụ

| Thành phần | Phiên bản |
|---|---|
| ESP-IDF | 6.0.2 |
| esp-dl | 3.3.11 |
| ESP-PPQ | 1.3.11, cộng 4 bản vá của dự án |
| PyTorch, torchaudio | 2.14.0, 2.11.0 |
| SwiftF0 (`swift-f0`) | 0.3.0 |
| Praat (`praat-parselmouth`) | 0.4.7, cho phép đổi F0 giữ formant |
| Python | 3.12.3 |
| GPU học | RTX 3050 Laptop, 4 GB |
| Board | board B: ESP32-S3, hai micro INMP441 cách 4,5 cm |

## 11. Tài liệu tham khảo

1. Espressif, ESP-SR 2.5.5, ESP-DL 3.3.11, ESP-PPQ 1.3.11.
2. A. Graves và cộng sự, Connectionist temporal classification, ICML 2006.
3. P. Ghahremani và cộng sự, A pitch extraction algorithm tuned for automatic speech recognition, ICASSP 2014.
4. L. Nieradzik, SwiftF0, bộ dò cao độ từng khung, 2025 (gói `swift-f0`, giấy phép MIT).
5. D. S. Park và cộng sự, SpecAugment: a simple data augmentation method for automatic speech recognition, Interspeech 2019.
6. N. Jaitly, G. E. Hinton, Vocal tract length perturbation (VTLP) improves speech recognition, ICML Workshop 2013.
7. D. Amodei và cộng sự, Deep Speech 2: end-to-end speech recognition in English and Mandarin, ICML 2016 (row
   convolution, cách dựng lớp nhìn trước).
8. P. Boersma, D. Weenink, Praat: doing phonetics by computer.
9. I. Cohen, B. Berdugo, Speech enhancement for non-stationary noise environments, Signal Processing, 2001.
10. J.-M. Valin, A hybrid DSP/deep learning approach to real-time full-band speech enhancement (RNNoise), MMSP 2018.
11. S. Braun, I. Tashev, Data augmentation and loss normalization for deep noise suppression (NSNet2), SPECOM 2020.

## 12. Nhận xét của Mentor

- Đánh giá chung:
- Điểm mạnh:
- Điểm cần cải thiện:
- Hành động cần thực hiện:
