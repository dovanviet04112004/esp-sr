# Báo cáo tuần 14 — thực tập sinh kỹ thuật

Tuần 14 (19/10 – 23/10/2026)

## 1. Thông tin chung

| Mục | Nội dung |
|---|---|
| Họ và tên | Đỗ Văn Việt |
| Phòng ban | Thiết kế điện tử |
| Người hướng dẫn (Mentor) | Anh Chung |
| Tuần báo cáo | Tuần 14 (19/10 – 23/10/2026) |
| Phạm vi tuần | Dự án esp-sr: khối tách người nói `bss` bằng hai micro. Bỏ AuxIVA, chuyển sang cổng hướng; thay phân cụm k-means bằng dò đỉnh mật độ; mở rộng tới 4 người; chặn ghế ma từ tiếng phòng; tách trực tiếp trên board. Mọi số đo trên board B: ESP32-S3, hai micro INMP441 cách 4,5 cm |

## 2. Tóm tắt tuần

Tuần này dựng lại khối tách người nói `bss` của chuỗi tiền xử lý. AuxIVA, phương án cũ, chỉ dìm người kia 0,7–2,1 dB
khi hai người nói lần lượt, nên khối đổi thành **cổng hướng**: mỗi bước 16 ms, cả khung được giữ hay dìm theo hướng mà
`doa` dò được. Phần tự học chỗ ngồi của người nói (LEARN) lúc đầu phân cụm bằng k-means như dự án `tinyai-signal`. Bộ
kịch bản dựng tuần này cho thấy nó trượt 9/15 kịch bản và đổi số của người đang nói giữa buổi, nên được thay bằng **dò
đỉnh trên mật độ độ trễ**: mỗi người giữ một số suốt buổi, và lên được 4 người. Hai người ở hai phía board: tiếng người
kia lọt vào luồng của mình dưới −125 dB từ 0,5 s sau mỗi lần đổi lượt. Hai người cùng một phía: LEARN tách sau 2,4–3 s.
Trên board B, mỗi người ra một tệp WAV, có lối ra trễ 4 s để cả đoạn chờ tách cũng vào đúng người. Chạy trong phòng làm
việc thật, tiếng quạt và tiếng gõ phím tạo **ghế ma** (10 phút ra 4 ghế). LEARN giờ chỉ đếm tiếng nói liền và quên lần dò
cũ hơn 60 s, nên 5, 10 và 30 phút tiếng phòng không ra ghế nào, còn người nói thật vẫn tách sau 3,4–3,9 s. Trên chip,
LEARN tốn 86 µs mỗi bước 16 ms.

Thuật ngữ trong báo cáo:
- **ghế**: một vùng hướng ứng với một người nói. Mỗi ghế giữ một **số** (0, 1, 2, 3) từ lúc được nhận tới lúc bị quên;
- **mất**: phần năng lượng của người được giữ mà cổng làm mất;
- **rò**: phần năng lượng của người kia lọt vào luồng của người được giữ, tính cả bài và tính từ 0,5 s sau mỗi lần đổi
  người nói.

Mất và rò so với chính đầu vào của người ấy, theo dB; −300 dB nghĩa là cổng gần như đóng hẳn.

## 3. Khối tách người nói `bss`

### 3.1. Vì sao bỏ AuxIVA

Hai micro cách 4,5 cm không tách được hai người **nói cùng lúc** theo từng vạch tần số: `tinyai-signal` đã đo pha theo
từng ô thời gian–tần số trên board B và ra kết quả âm. Vật liệu thử chính là **bản ghép A/B**: hai bản thu một người của
board B (mỗi người 15 s, cách 1 m, một người bên trái, một người bên phải), cắt thành lượt 5 s rồi xếp xen kẽ, thành
30 s có nhãn từng bước. AuxIVA online (bản dở dang từ 28/09) trên bản ghép ấy:

| Hằng số quên | Rò sang A / B |
|---|---|
| 1 s | −1,4 / −0,9 dB |
| 3 s | −2,1 / −0,7 dB |

Hai luồng ra gần như vẫn là trộn, và mấy giây đầu năng lượng lối ra lên +87 dB so với đầu vào. Lý do: hai người nói lần
lượt, mỗi lúc chỉ có một nguồn, nên ma trận hiệp phương sai mà AuxIVA cần bị suy biến.

Cái hai micro biết chắc là **khung này đến từ hướng nào**. Vì vậy `bss` nhân cả khung với một hệ số theo hướng: người ở
ghế được giữ đi qua, người ở ghế khác bị dìm, phổ không méo. Hướng lấy từ khối `doa` sẵn có trong chuỗi chứ không từ bộ
ước trễ riêng của `tinyai-signal`. Độ trễ giữa hai micro khi mỗi người nói một mình, tính bằng mẫu, tối đa ±2,10:

| Nguồn hướng | A: trung vị (p10 / p90) | B: trung vị (p10 / p90) |
|---|---|---|
| Bộ ước trễ của `tinyai-signal`, 300–3 800 Hz | −0,80 (−1,48 / −0,12) | +0,56 (−0,17 / +1,00) |
| `doa` của chuỗi, 2–8 kHz, dò lưới 2° | +1,40 (+1,17 / +1,59) | −1,35 (−1,46 / −1,23) |

Hai bộ ước đặt dấu ngược nhau. Bộ ước của `tinyai-signal` cho hai người chồng lên nhau quanh 0, vì ở dải thấp tiếng vang
kéo hướng về chính diện; `doa` cho hai cụm tách hẳn. Bản thử đầu của cổng trên bản ghép A/B: rò cả bài −11,9 / −18,1 dB,
rò từ 0,5 s sau đổi lượt −300 / −231 dB.

### 3.2. Chuỗi xử lý

```
hai micro INMP441, 16 kHz, mỗi bước 16 ms
   ▼
hpf 80 Hz mỗi micro ──────► trung bình hai micro ──► vad thứ hai, quyết định thô ──┐
   ▼                                                                              │ có tiếng
stft 512 / 256 mỗi micro                                                          │
   ▼                                                                              │
balance: bù lệch mức và pha của ch1 theo bảng hiệu chuẩn                          │
   ▼                                                                              ▼
doa: GCC-PHAT 2–8 kHz, dò lưới 2°, làm trơn 0,2 s, dò lại mỗi 2 bước khi có tiếng
   ▼  góc θ → độ trễ τ = 2,10·cos θ mẫu
bss: tìm ghế (FIXED: chia ở 90°; LEARN: đỉnh của mật độ τ) → cổng 0…1 của từng ghế
   ▼
trung bình hai kênh × cổng
   ├─► sản phẩm: ghế được giữ → khe ns → istft → vad → agc → wake, command
   └─► tách trực tiếp: mọi số ghế → lối ra trễ D → Wi-Fi → một WAV mỗi ghế
```

Chuỗi đầy đủ của sản phẩm (KẾ HOẠCH §3.2) còn `aec` giữa `hpf` và `stft`. Khối không gian là một khe chọn bằng Kconfig:
`gsc`, `bss` hay trộn trần (trung bình hai kênh).

- **Cổng.** Ghế đánh số theo τ tăng dần. Ghế j nhận hệ số

  ```
  g_j(τ) = σ(2s·(τ − b_{j−1})) − σ(2s·(τ − b_j))
  ```

  với σ là hàm logistic, s = 20 mỗi mẫu trễ, b là ranh giới giữa hai ghế kề nhau. Các cổng cộng lại đúng 1. Cách ranh giới
  0,1 mẫu là cổng đã qua 98%. Giữa hai lần dò của `doa`, cổng giữ nguyên.
- **Hai chế độ.** FIXED chia đôi ở chính diện (90°, trục vuông góc với cặp micro), tách ngay từ lần dò đầu khi biết trước
  người ra lệnh ngồi phía nào. LEARN tự tìm ghế từ chính các lần dò, 2–4 ghế, dùng khi hai người ngồi cùng một phía hay
  không biết trước ai ngồi đâu. Trước lần tách đầu, LEARN cắt như FIXED.
- **Lối ra.** Sản phẩm đưa ghế được giữ (người ra lệnh) sang phần sau của chuỗi. Khi tách trực tiếp, mỗi số ghế ra một
  kênh.
- **`vad` thứ hai.** `doa` chỉ dò khi có tiếng nói, nên hướng là của người nói. Dưới `bss`, tín hiệu có tiếng lấy từ một
  `vad` thứ hai trên trung bình hai micro, theo quyết định thô, chứ không từ `vad` cuối chuỗi (lý do ở mục 6.1).
- **Chi phí trên chip**, mỗi bước 16 ms: chia ở chính diện 25 µs; LEARN 86 µs, đỉnh 253 µs ở bước kiểm lại mỗi giây;
  `vad` thứ hai thêm 138 µs. `tinyai-signal` lên 2,9 ms ở bước phân cụm.

### 3.3. Kết quả tách

Mỗi ô: mất / rò cả bài / rò từ 0,5 s sau đổi lượt; cột LEARN thêm lúc tách. Cảnh dựng là bộ `standard` của dự án: ảnh
người nói 8 s rồi ảnh người gây nhiễu 8 s, theo RT60.

| Vật liệu | FIXED | LEARN |
|---|---|---|
| Bản ghép A/B, giữ A (~48°) | −0,03 / −14,8 / −332 dB | −0,03 / −14,8 / −299 dB; tách ở 6,7 s, B mở lời ở 5 s |
| Bản ghép A/B, giữ B (~130°) | −0,17 / −23,3 / −126 dB | −0,17 / −23,3 / −126 dB; tách ở 6,7 s |
| Cảnh dựng hai phía, RT60 0,2–0,6 s (91 cảnh) | ≈0 / −26 đến −27 / −148 đến −176 dB | gần như FIXED; tách 2,1–2,4 s sau khi người thứ hai mở lời (91/91) |
| Cảnh dựng một phía (5 cảnh) | ≈0 / −0,1 đến −0,2 / ≈0 dB: không tách | ≈0 / −2,2 đến −5,0 dB; tách sau 2,4–3,0 s |

- **Hai phía:** rò cả bài −15 đến −27 dB gần như nằm hết trong 0,5 s đầu mỗi lượt, lúc `doa` (làm trơn 0,2 s) còn bám
  người trước. Sau đó người kia bị chặn hết. Bắt `doa` bám nhanh hơn không gỡ được phần ấy mà làm rò lúc ổn định tăng;
  lối ra trễ ở mục 7 xử lý phần này.
- **Cùng một phía:** FIXED không tách được, đúng như kế hoạch đã ghi. LEARN tách khi người thứ hai đã nói 2,4–3 s; trước
  đó người ấy lọt sang luồng kia, nên rò cả bài chỉ còn −2 đến −5 dB.
- **Ba bản thu thật của board B, không nhãn:** hai người luân phiên 30 s (ghế 54° và 146°, tách sau 5,8 s), hai người nói
  đè 10 s (54° và 136°, 3,1 s), giọng cùng loa nhạc 9,6 s (giọng 127°, nhạc 31°, 3,6 s). Ghế nằm đúng đỉnh bản đồ góc của
  `doa`.
- **So với `tinyai-signal`:** nó cần 10,4 s để khoá trên bộ dựng của nó, 22,4–37,4 s trên bản thu người thật.

## 4. Phân cụm: từ k-means sang dò đỉnh mật độ

### 4.1. Phân cụm cũ và các vấn đề

LEARN giữ histogram các lần dò của `doa` trong 8 s tiếng nói gần nhất. Bản đầu (19/10) làm như `tinyai-signal`: mỗi giây
chạy k-means một chiều trên độ trễ, thử k = 2, 3, … tới số ghế tối đa. Nó nhận k khi mỗi cụm giữ ít nhất 5% số lần dò, hai
cụm kề cách nhau ít nhất 0,5 mẫu và chỗ trũng giữa chúng đủ thấp, và dừng ở k đầu tiên trượt. Số ghế lớn hơn được nhận khi
hai lần phân cụm liền nhau cùng ra số ấy, kèm luôn các tâm mới.

Để thấy nó hỏng ở đâu, tuần này dựng **bộ kịch bản**: lần dò của `doa` mô phỏng như trên board B (khi đổi người nói,
`doa` quét qua các góc ở giữa trong khoảng 12 bước; mỗi lần dò rung ±2°; 0–6% lần dò lạc khắp 0–180°). 22 kịch bản, từ một
người tới bốn người, người vào muộn, người vắng rồi quay lại, cả phòng im lâu, nói đè, nhạc; mỗi người phải giữ một số
suốt buổi. Với k-means:

| Vấn đề | Biểu hiện |
|---|---|
| Dừng ở k đầu tiên trượt phép kiểm | cấu hình trượt ở k = 3 không bao giờ được thử k = 4, dù bốn người nói rõ |
| Nhận số ghế lớn hơn thì thay mọi ghế | tâm và số của các ghế cũ đổi theo: người đang nói đổi số giữa buổi |
| Coi đường quét của `doa` là khối lượng | k-means kéo tâm ghế về giữa đường quét. Bản thu luân phiên: ghế của B ở 63°, giữa hai cụm thật của B (75° lúc nói đè, 52–62° khi nói một mình) |
| Lần dò lạc kéo ghế đi | mỗi lần dò lạc đều góp vào tâm cụm |
| Chờ phép kiểm độ trũng | đường quét rải lần dò vào chỗ trũng: bản ghép A/B tách ở 9,7 s |
| Tổng | trượt 9/15 kịch bản đầu |

Bản gốc của `tinyai-signal` còn các chỗ hỏng đã đo ở chế độ học (báo cáo tuần 8 của dự án ấy):
- chờ khoá nhanh nhất 10,4 s, trên bản thu người thật 22,4–37,4 s;
- một người giữ lời quá 8 s là rớt về một người, phần còn lại của phiên không tách (0,0 dB);
- hai người lệch mức 12 dB thì cổng mức loại hết khung của người nhỏ tiếng;
- phân cụm cả vòng đệm 800 khung mỗi giây, đỉnh một nhịp 2,9 ms.

### 4.2. Dò đỉnh trên mật độ độ trễ

Từ 20/10, ghế của LEARN là **đỉnh nổi rõ** của mật độ các lần dò, khối lượng tính ở lõi đỉnh:

- **Mật độ theo độ trễ.** Số lần dò dồn vào 68 ô đều theo τ (8 ô cho mỗi 0,5 mẫu), làm trơn bằng nhân tam giác, toàn phép
  số nguyên.
- **Đỉnh nổi rõ.** Một cực đại là ứng viên khi chỗ trũng ngăn nó với đỉnh cao hơn thấp hơn nửa chiều cao của nó. Hai ứng
  viên gần nhau dưới 0,5 mẫu thì giữ cái cao hơn.
- **Lõi.** Chỉ các lần dò trong ±0,125 mẫu quanh đỉnh tính là khối lượng, và ứng viên phải giữ ít nhất 5% cửa sổ. Người
  nói dồn lần dò vào lõi; lần dò lạc và đường quét của `doa` trải mỏng, không đủ ở lõi nào.
- **Tách lần đầu** khi hai lần kiểm lại liền nhau (mỗi giây một lần) thấy cùng các đỉnh. Nhiều đỉnh hơn số ghế thì chỉ
  chọn khi đỉnh bị bỏ thấp hơn nửa đỉnh thấp nhất được giữ, không thì chờ. Ở bản thu luân phiên, B nói vài câu đầu ở 75°
  rồi ngồi yên ở 54°, hai đỉnh cao 38 và 37: chọn ngay thì ghế khoá ở 75°.
- **Thêm ghế** cho người mới mà không đụng ghế cũ: ghế mới lấy số nhỏ nhất còn trống. Đủ ghế mà có ghế bỏ trống thì dời
  ghế ấy về người mới, giữ số.
- **Theo ghế.** Mỗi lần kiểm lại, tâm ghế đi 1/4 quãng về đỉnh của nó; ranh giới giữa hai ghế đặt ở chỗ trũng thấp nhất
  của mật độ.
- **Số ổn định.** Mỗi ghế giữ số từ lúc được nhận tới lúc bị quên, nên mỗi tệp WAV nghe một người suốt buổi.

Hai luật thêm sau khi chạy board (quên theo tiếng nói, chỉ đếm tiếng liền) ở mục 6.

### 4.3. So sánh hai cách

| Thước | k-means (19/10) | Dò đỉnh (20/10) |
|---|---|---|
| Bộ kịch bản | trượt 9/15 kịch bản đầu | qua hết: 20 kịch bản, 10 hạt giống, lạc 3% và 6% |
| Bản ghép A/B, tách sau | 9,7 s | 6,7 s |
| Bản thu luân phiên | tách ở 2,8 s, ghế B ở 63°, giữa hai cụm | tách ở 5,8 s, ghế B ở 54°, đúng đỉnh |
| Mất và rò | như nhau ở mọi dòng | như nhau ở mọi dòng |
| Đỉnh chi phí trên chip | 317 µs | 253 µs |

Bản thu luân phiên tách muộn hơn vì chờ đỉnh 75° tắt. Hai người ngồi hai phía chính diện, nên trong lúc chờ, cổng chia ở
chính diện đã đưa A và B ra hai luồng: không mất hay rò thêm gì.

## 5. Mở rộng lên 4 người

| Phần | Đã làm |
|---|---|
| Thuật toán | số ghế 2–4. Người mới thành ghế mới mà ghế cũ giữ số; đủ 4 ghế thì dời ghế bỏ trống về người mới; người thứ năm vào ghế có vùng chứa họ |
| Lối ra | hai kênh thô `ch0`, `ch1` cộng 2, 3 hay 4 kênh ghế, 16 kHz 16 bit; 4 ghế là 6 kênh, 192 KB/s qua Wi-Fi |
| Bộ nhớ | vòng đệm của lối ra trễ chỉ giữ trộn trần và hai kênh thô, cổng của từng ghế tính lúc xuất, nên không lớn theo số ghế: 751 KB PSRAM với D 4 s |
| Kịch bản | ba người, bốn người, người thứ tư vào muộn (khi cả ba còn nói, khi chỉ một người còn nói, khi nói một mình), ba ghế mà bốn người nói, bốn người rồi cả phòng im 90 s: qua hết |
| Trên board B | lượt 10 phút, 4 ghế, có `ns` và lối ra trễ 4 s: 4,04 ms mỗi bước, đỉnh 5,66 ms, trên khung 16 ms. Lượt 22/10 lập đủ 4 ghế ở 156°, 13°, 81°, 44° từ những người nói trong phòng |

Giới hạn của hai micro cách 4,5 cm:
- **Độ phân giải hướng.** Hai người phải lệch nhau ít nhất 0,5 mẫu trễ: 14° quanh chính diện, 27° quanh 30° và 150°, rộng
  hơn nữa sát hai đầu dàn. Ở lượt 22/10, 13° và 44° lệch nhau 31° mà chỉ cách 0,54 mẫu. Bố trí gợi ý cho 4 người: 20°,
  70°, 110°, 160°, các ghế cách nhau 1,25–1,44 mẫu 🔬.
- **Trước và sau.** Hai micro không phân biệt hướng θ với hướng đối xứng qua trục micro, nên 4 người phải ngồi ở bốn hướng
  khác nhau trên nửa vòng 0–180°.
- **Nói đè.** Mỗi bước trao cho một ghế: hai người nói đè thì không tách, và càng đông người càng dễ đè.
- **Thời gian có ghế.** Người mới cần khoảng 1,5 s tiếng nói liền trong 60 s gần nhất, cộng hai lần kiểm lại: người thật
  tách sau 3,4–3,9 s. Người chỉ nói 10% thời gian, mỗi câu 0,5 s, có thể không đủ khối lượng ở lõi để có ghế.
- **Chưa đo.** Chưa có bản thu 4 người nói luân phiên có nhãn trên board, nên chưa có số mất và rò cho 4 người 🔬.
  `tinyai-signal` giữ được 2 ghế ở 15/15 phiên, 3 ghế ở 10/15.

## 6. Các vấn đề đã gặp và cách giải quyết

### 6.1. Thuật toán

| # | Vấn đề | Biểu hiện đo được | Cách giải quyết | Kết quả |
|---|---|---|---|---|
| 1 | Cổng tự khoá | `doa` dò theo `vad` cuối chuỗi, mà `vad` ấy nghe tiếng đã qua cổng: cổng dìm ghế kia là `vad` tắt, `doa` thôi dò, cổng kẹt. Bản ghép A/B không tách gì; bản thu luân phiên mất 13,4 dB | `vad` thứ hai trên trung bình hai micro báo cho `doa` lúc có tiếng | tách bình thường |
| 2 | Ghế ma ở chính diện lúc khởi động | phần kéo dài 0,26 s của `vad` báo có tiếng trên nhiễu điện chung của hai micro (−53 đến −62 dBFS); `doa` đọc ra 88°, LEARN lập ghế thứ hai ở giây 3,6 khi mới có một người nói | `doa` theo quyết định thô của `vad`, bỏ phần kéo dài | hết ghế ma lúc khởi động |
| 3 | Ghế được giữ đổi theo từng lượt | luật "giữ ghế nhiều lần dò nhất" đếm trong cửa sổ 8 s nên đổi sau mỗi lượt nói: giữ A mà mất 2,7 dB | đếm dồn từ lúc ghế được nhận | ghế được giữ đứng yên |
| 4 | k-means trượt kịch bản, đổi số ghế | mục 4.1 | dò đỉnh mật độ | qua hết bộ kịch bản |
| 5 | Số ghế đổi khi thêm ghế xen giữa | ghế đánh số lại theo độ trễ mỗi lần có ghế mới | số ổn định, ghế mới lấy số nhỏ nhất còn trống | mỗi người một số suốt buổi |
| 6 | Rò ở đầu mỗi lượt | `doa` làm trơn 0,2 s nên đi sau người nói | lối ra trễ, cổng nhìn trước 0,1 s, chỉ khi tách trực tiếp (mục 7) | 0,5 s đầu lượt, hai phía RT60 0,4 s: −7,0 → −33,5 dB |
| 7 | Người mới lọt sang trước khi có ghế | hai người cùng phía: LEARN chờ 2,4–3 s, đoạn ấy lọt sang luồng kia | lối ra trễ D dài hơn thời gian chờ | RT60 0,6 s: rò cả bài −5 dB → −26 dB (D 2 s) → −76 dB (D 4 s) |
| 8 | Im lâu hay một người vắng lâu thì đổi số | quên ghế sau 60 s theo đồng hồ: cả phòng im quá 60 s là mất ghế rồi dựng lại theo số khác; còn dưới hai ghế thì về 0 ghế, người còn nói chuyển số | đồng hồ quên đếm tiếng nói, không đếm im lặng; không bỏ xuống dưới hai ghế | qua kịch bản "bốn người rồi im 90 s" và "một người vắng 90 s" ở mọi mức lạc |
| 9 | Người quay lại sau hơn 60 s | — | theo luật của dòng 8 | hai ghế: vào ngay số cũ. Ba ghế: ghế vắng bị bỏ sau 60 s tiếng nói của hai người kia, người quay lại có 2,4 s ở ghế khác rồi lấy lại số cũ |
| 10 | Lần dò lạc của `doa` | 0–0,5% khi một người nói rõ, 1,8–2,8% khi đổi lượt hay nói đè. Độ tin của lần lạc không thấp hơn của người nói, nên ngưỡng độ tin không lọc được. Chỉ có lần dò lạc thì bản cũ lập 2 ghế ma ở 12–15° và 165–167° | lõi phải giữ 5% cửa sổ, cộng hai luật của mục 6.2 | ba nguồn lẻ tẻ suốt 15 phút: không ghế nào |

### 6.2. Ghế ma từ tiếng phòng

Phòng làm việc có tiếng quạt, tiếng gõ phím của mọi người và có thể có người nói chuyện ở xa; không ai nói với board.

- Lượt 5 phút trên board B ngày 20/10, 2 ghế: không ra ghế, vì lần tách đầu chờ mãi giữa 3–4 ứng viên ngang nhau (45°,
  90°, 175°). Cùng bản thu ấy với 3 hay 4 ghế thì có ghế từ giây 159.
- Lượt 10 phút ngày 22/10, 4 ghế: board ra 2 ghế ở giây 193, 3 ghế ở giây 211, 4 ghế ở giây 451, giữ tới hết lượt.
- Nguyên nhân: tiếng gõ và tiếng tách dài 16–48 ms làm `vad` thô bật 1–3 bước, mỗi bàn phím một hướng, và LEARN đếm từng
  lần dò ấy như tiếng nói. Một âm tiết dài hơn 8 bước.

Phần lần dò còn lại nếu chỉ tính những lần `vad` thô đã báo có tiếng liền K bước:

| Bản thu | K 4 | K 6 | K 8 |
|---|---|---|---|
| Tiếng phòng 5 phút | 34% | 19% | 11% |
| Lượt chỉ có tiếng tách | 3% | 0% | 0% |
| Năm bản thu người nói của board B | 76–89% | 65–83% | 56–79% |

Các cách đã thử, LEARN 4 ghế trên tiếng phòng:

| Cách | Phòng 5 phút | Phòng 10 phút | Phòng ghép 15–30 phút | Người thật tách sau |
|---|---|---|---|---|
| Như cũ | 4 ghế từ 159 s | 4 ghế từ 193 s | — | 3,1–5,8 s |
| Quên lần dò cũ hơn 20 hay 40 s | 4 ghế từ 166–167 s | — | — | như cũ |
| Chỉ học từ bước hữu thanh | 4 ghế từ 226 s | — | — | 3,2–3,7 s |
| `doa` chỉ dò khi `vad` thô liền 8 bước | không ghế | không ghế | 4 ghế ở phút 12,7 | 3,2–3,8 s |
| **LEARN chỉ đếm từ lần dò thứ 3 liền nhau, quên lần dò cũ hơn 60 s** | **không ghế** | **không ghế** | **không ghế (30 phút)** | **3,4–3,9 s** |

- **Cần cả hạn 60 s.** Chỉ đòi tiếng liền thì tiếng lẻ tẻ vẫn cộng dồn qua nhiều phút, vì cửa sổ đếm theo tiếng nói.
- **Bỏ cách đòi tiếng liền ngay ở chỗ `doa` dò.** Cách ấy cũng chặn được ghế ma, nhưng cổng giữ hướng người trước lâu hơn
  ở đầu mỗi lượt. Bản ghép A/B, rò sau 0,5 s đầu lượt −27,6 dB thay vì −126 dB; cảnh hai phía RT60 0,4 s với lối ra trễ
  4 s, rò cả bài −27,4 dB thay vì −58,2 dB.
- **Cách chốt.** `doa` và cổng vẫn theo mọi lần dò; chỉ LEARN đòi tiếng liền. Một nguồn thành ghế khi có 1,5 s lần dò
  trong tiếng liền, trong 60 s gần nhất. Người đang nói với board có đủ sau vài giây; tiếng gõ phím và tiếng nói lẻ tẻ ở
  xa thì không. Chọn lần thứ 3 vì đó là mức thấp nhất vẫn chặn hết tiếng phòng; lần thứ 4–5 làm một lượt có người nói tách
  muộn tới 26 s.
- **Giá phải trả.** Mất và rò trên cảnh dựng lệch dưới 1 dB. LEARN tách muộn hơn một chút (bản ghép A/B 8,5 s thay vì
  6,7 s), vì hai lần dò đầu mỗi quãng tiếng không được đếm.
- **Trên board.** Lượt 10 phút ngày 22/10 với luật mới, 4 ghế: 30 s đầu có người nói thật trong phòng, và 4 ghế lập ra là
  của họ. Chưa có lượt board trong phòng hoàn toàn không ai nói 🔬.

## 7. Tách trực tiếp trên board

Board B chạy cả chuỗi theo thời gian thực: mỗi số ghế ra một kênh, truyền qua Wi-Fi về máy tính, mỗi người một tệp WAV.

- **Lối ra trễ.** Bước t ra ở bước t + D, mặc định 4 s; cổng của nó lấy ghế đang giữ ở t + D và góc 0,1 s sau t. Nhờ vậy
  đoạn người mới nói trong lúc chờ có ghế vẫn vào đúng ghế (mục 6.1, dòng 7), và phần rò đầu mỗi lượt giảm (dòng 6). Người
  được giữ mất không quá 0,12 dB. Sản phẩm không dùng lối ra trễ, vì mỗi lệnh sẽ chậm thêm D giây.
- **Nhìn trước 0,1 s chốt bằng đo.** Từ 0,15 s trở lên, khi hai người đáp lời không nghỉ, đuôi mỗi lượt đã lấy góc của
  người sau: rò ở phần ổn định lên −29 dB ở 0,15 s, −9 dB ở 0,5 s, so với −117 dB ở 0,1 s.
- **Thời gian thực.** 2 ghế: 3,25 ms mỗi bước, đỉnh 4,09 ms, trên khung 16 ms; 4 ghế: 4,04 ms, đỉnh 5,66 ms; không rơi
  bước. Vòng đệm 4 s là 751 KB PSRAM.
- **Chỗ ngồi.** Ở lượt thử 20/10, người nói ngồi trước mặt board, các hướng chỉ lệch nhau khoảng 20°, sát độ phân giải:
  LEARN tách muộn, có lúc hai người vào chung một số. Hai người ngồi ở hai đầu cặp micro thì tách sau vài giây (bản thu
  luân phiên: 5,8 s).

## 8. Các hướng đang xử lý

| Hướng | Trạng thái 23/10 | Điều kiện hay kỳ vọng |
|---|---|---|
| Lượt board trong phòng không ai nói | lượt 22/10 có người nói thật trong 30 s đầu nên chưa tính | 10 phút, 4 ghế, không ra ghế nào |
| Lượt tách hai người thật | tách trực tiếp đã chạy trọn trên board B; còn lượt hai người nói luân phiên | hai người ở hai đầu cặp micro, cách 0,5–1 m, 30–60 s: mỗi người một WAV, đúng số suốt lượt |
| So ba đường `gsc`, `bss`, trộn trần | chưa làm; chấm trên bản thu board phải áp bảng hiệu chuẩn như trên chip | chọn mặc định bằng tỉ lệ bắt `wake` và đúng `command` trên tập thu qua board có nhiễu, không bằng SI-SDR |
| Nói đè | giới hạn vật lý của hai micro: mỗi bước một ghế | ghi rõ trong kế hoạch, không hứa tách |

## 9. Kế hoạch tuần 15

| Việc | Kết quả mong đợi | Ưu tiên |
|---|---|---|
| Lượt board trong phòng không ai nói, 10 phút, 4 ghế | không ghế ma | cao |
| Lượt hai người thật nói luân phiên | mỗi người một WAV, giữ đúng số suốt lượt | cao |
| So ba đường `gsc`, `bss`, trộn trần | bảng ba cột theo thước của bộ nhận dạng; chốt đường mặc định, ghi ADR | trung bình |

## 10. Phiên bản công cụ

| Thành phần | Phiên bản |
|---|---|
| ESP-IDF | 6.0.2 |
| esp-dl | 3.3.11 |
| dl_fft | 0.7.0 |
| Python, NumPy | 3.12.3, 2.5.3 |
| Board | board B: ESP32-S3, hai micro INMP441 cách 4,5 cm |

## 11. Tài liệu tham khảo

1. C. Knapp, G. Carter, The generalized correlation method for estimation of time delay, IEEE Trans. ASSP, 1976
   (GCC-PHAT của `doa`).
2. N. Ono, Stable and fast update rules for independent vector analysis based on auxiliary function technique, WASPAA
   2011 (AuxIVA).
3. T. Taniguchi và cộng sự, An auxiliary-function approach to online independent vector analysis for real-time blind
   source separation, HSCMA 2014 (AuxIVA online).
4. S. P. Lloyd, Least squares quantization in PCM, IEEE Trans. Information Theory, 1982 (k-means).
5. `tinyai-signal`, module `separation` của `micarray`, commit `642e6e4`, và báo cáo tuần 8 của dự án ấy.
6. O. Hoshuyama, A. Sugiyama, A. Hirano, A robust adaptive beamformer for microphone arrays with a blocking matrix using
   constrained adaptive filters, IEEE Trans. Signal Processing, 1999 (`gsc`).
7. Espressif, ESP-IDF 6.0.2, ESP-DL 3.3.11.

## 12. Nhận xét của Mentor

- Đánh giá chung:
- Điểm mạnh:
- Điểm cần cải thiện:
- Hành động cần thực hiện:
