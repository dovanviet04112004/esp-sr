# `bss` (E8-T3, E8-T7)

`bss` là cổng hướng theo ghế của KẾ HOẠCH §3.8, dựng theo module `separation` của `micarray` trong `tinyai-signal`
(commit `642e6e4`). Số của §1–3 đo ngày 19/10 trên bản Python soi gương; §4–6 đo ngày 20/10 trên bản soi gương ở
`5137faf`, LEARN dò đỉnh của mật độ độ trễ (E8-T7); §7 và §8 đo trên board B ngày 20/10.

**Thước** của mục này:
- **mất**: phần năng lượng của người được giữ mà cổng làm mất;
- **rò**: phần năng lượng của người kia lọt vào luồng của người được giữ, tính cả bài, và tính lại từ 0,5 s sau mỗi lần
  đổi người nói.

Cả hai đều so với chính đầu vào của người ấy, theo dB. Cổng là một hệ số cho cả bước, nên hai số tính đúng, không phải
ước.

## 1. Vì sao bỏ AuxIVA

Vật liệu là **bản ghép A/B**:
- lấy hai bản thu một người của board B trong `raw/device/tinyai`: `A_trai_1` và `B_phai_1`, mỗi người 15 s, cách 1 m;
- cắt thành lượt 5 s rồi xếp xen kẽ, thành 30 s có nhãn từng bước.

AuxIVA online (bản dở dang ngày 28/09, không có chốt ổn định nào) chạy trên bản ghép ấy:

| Quên τ | Mất A / B | Rò sang A / B |
|---|---|---|
| 1 s | −0,9 / −1,4 dB | −1,4 / −0,9 dB |
| 3 s | −0,7 / −2,1 dB | −2,1 / −0,7 dB |

- Hai luồng ra gần như vẫn là trộn: AuxIVA không tách được.
- Mấy giây đầu số vỡ, năng lượng lối ra lên +87 dB so với đầu vào.
- Lý do: hai người nói lần lượt, mỗi lúc chỉ có một nguồn, nên ma trận hiệp phương sai suy biến.

## 2. Lấy hướng từ đâu

Độ trễ giữa hai micro (mẫu, τ_max = 2,10) khi mỗi người nói một mình trên bản ghép A/B:

| Nguồn hướng | A: trung vị (p10 / p90) | B: trung vị (p10 / p90) |
|---|---|---|
| Bộ ước trễ của `tinyai-signal`: 300–3 800 Hz, cửa sổ 0,4 s, chuyển sang bước 16 ms | −0,80 (−1,48 / −0,12) | +0,56 (−0,17 / +1,00) |
| `doa` của chuỗi: 2–8 kHz, dò lưới 2° | +1,40 (+1,17 / +1,59) | −1,35 (−1,46 / −1,23) |

Hai bộ ước đặt quy ước dấu ngược nhau. Bộ ước của `tinyai-signal` cho hai người chồng lên nhau quanh 0. `doa` cho hai
cụm tách hẳn, khớp với đo ở E8-T1 rằng dải thấp bị tiếng vang kéo về chính diện.

Cổng chia ở chính diện, bản thử đầu trên bản ghép A/B: rò sang A / B là −10,6 / −13,8 dB với bộ ước của `tinyai-signal`,
−11,9 / −18,1 dB với `doa`.

Độ nhanh của `doa`:

| `doa` làm trơn, dò mỗi | Rò cả bài A / B | Rò từ 0,5 s sau đổi lượt A / B |
|---|---|---|
| **0,2 s, 2 bước (đang dùng)** | −11,9 / −18,1 dB | **−300 / −231 dB** |
| 0,1 s, 1 bước | −12,6 / −19,7 dB | −19,5 / −29,0 dB |
| 0,05 s, 1 bước | −11,7 / −12,3 dB | −14,5 / −12,7 dB |

Gần như mọi phần rò nằm trong 0,5 s đầu mỗi lượt, lúc `doa` còn bám người trước. Bắt `doa` bám nhanh hơn không gỡ
được phần ấy, mà lại làm rò lúc ổn định tăng lên.

## 3. Hai chỗ hỏng tìm ra khi dựng

**Cổng tự khoá.** `doa` chỉ dò khi `vad` của bước trước báo có tiếng, mà `vad` nghe lối ra đã qua cổng. Cổng vừa dìm
ghế kia là `vad` tắt, `doa` thôi dò, cổng kẹt luôn. Trên bản ghép A/B, hai luồng không tách được gì. Trên bản thu luân
phiên thật, lối ra mất 13,4 dB.

Sửa: dưới `bss`, một `vad` thứ hai trên trung bình hai micro sau `hpf` báo cho `doa` lúc có tiếng (KẾ HOẠCH §3.8, §3.10).
Độ tin của `doa` không thay được `vad` này:

| Bản thu | Độ tin p10 / p50 / p90 khi có tiếng | Khi im |
|---|---|---|
| Bản ghép A/B | 35 / 47 / 58 | 31 / 44 / 57 |
| Luân phiên 30 s | 37 / 46 / 55 | 30 / 40 / 56 |

**Ghế ma ở chính diện.** Ngay lúc khởi động, phần kéo dài của `vad` báo có tiếng 0,26 s, trong khi quyết định thô của
nó là không và đầu vào chỉ có −53 đến −62 dBFS. Lúc đó `doa` đọc nhiễu điện chung của hai micro (trễ 0) ra 88°, độ tin
112–125. LEARN biến chỗ ấy thành ghế thứ hai ở giây 3,6, khi mới chỉ có A nói.

Sửa: `doa` dò theo quyết định **thô** của `vad` đầu vào, không theo quyết định có phần kéo dài.

Luật giữ "nhiều lần dò nhất", đếm trong cửa sổ, đổi ghế giữ theo từng lượt nói (giữ A: mất −2,7 dB, rò −0,8 dB). Đếm dồn
từ lúc nhận ghế thì hết.

## 4. Kết quả

`make eval-bss` ở `5137faf`. Mỗi ô: mất / rò cả bài / rò từ 0,5 s sau đổi lượt / thời điểm LEARN tách (giây, và số mục
tách được trên số mục).

**Bản ghép A/B của board B**, mỗi người được giữ một lượt:

| Giữ | Chia ở chính diện | LEARN |
|---|---|---|
| A (~48°) | −0,03 / −14,8 / −332 dB / — | −0,03 / −14,8 / −299 dB / 6,7 s (1/1) |
| B (~130°) | −0,17 / −23,3 / −126 dB / — | −0,17 / −23,3 / −126 dB / 6,7 s (1/1) |

**Cảnh dựng `standard`**:
- lấy 96 cảnh có người gây nhiễu là người nói;
- mỗi cảnh: ảnh người nói 8 s, rồi ảnh người gây nhiễu 8 s;
- giữ người nói ở góc trên nhãn của cảnh; trung bình trên các cảnh.

| RT60, vị trí hai người | Chia ở chính diện | LEARN |
|---|---|---|
| 0,2 s, hai phía (31) | 0,00 / −26,1 / −176 dB / — | 0,00 / −26,1 / −175 dB / 10,2 s (31/31) |
| 0,4 s, hai phía (31) | 0,00 / −26,1 / −148 dB / — | −0,01 / −25,5 / −138 dB / 10,1 s (31/31) |
| 0,6 s, hai phía (29) | −0,02 / −27,0 / −150 dB / — | −0,02 / −27,0 / −150 dB / 10,4 s (29/29) |
| 0,2 s, một phía (1) | 0,00 / −0,1 / −0,1 dB / — | 0,00 / −2,2 / −2,2 dB / 10,4 s |
| 0,4 s, một phía (1) | −0,01 / 0,0 / 0,0 dB / — | −0,01 / −3,2 / −3,4 dB / 11,0 s |
| 0,6 s, một phía (3) | 0,00 / −0,2 / −0,1 dB / — | 0,00 / −5,0 / −5,0 dB / 10,8 s (3/3) |

- **Hai người ở hai phía:** cổng gần như không làm mất gì của người được giữ. Rò sau 0,5 s từ lúc đổi lượt dưới
  −125 dB ở mọi RT60, tức người kia bị chặn hết. Rò cả bài −15 đến −27 dB nằm ở đầu mỗi lượt (§2).
- **Hai người cùng một phía:** chia ở chính diện không tách được, đúng như KẾ HOẠCH đã ghi. LEARN tách được, nhưng chỉ sau
  khi người thứ hai nói khoảng 2,4–3 s: người gây nhiễu bắt đầu ở giây 8, ghế thứ hai có ở giây 10,4–11,0. Đỉnh của người
  ấy phải giữ 5% cửa sổ trong lõi, rồi hai lần kiểm lại liên tiếp phải thấy cùng các đỉnh. Trước đó người ấy lọt qua, nên
  số cả bài chỉ còn −2 đến −5 dB.
- Trên bản ghép A/B, LEARN tách 1,7 s sau khi B mở lời ở giây 5. `tinyai-signal` cần 10,4 s trên bộ dựng của nó,
  22,4–37,4 s trên bản thu người thật.

So với LEARN k-means đo ngày 19/10 ở `dc92e9d` trên cùng vật liệu:
- mất và rò cả bài như nhau ở mọi dòng, rò sau 0,5 s của hai phía vẫn dưới −125 dB;
- bản ghép A/B tách sớm hơn 3 s (6,7 thay vì 9,7 s): k-means chờ phép kiểm độ trũng giữa hai cụm, mà `doa` quét chuyển
  rải lần dò vào giữa;
- cảnh dựng tách lệch nhau không quá 0,2 s.

## 5. Bản thu thật, không nhãn

LEARN, mặc định của `contracts/afe.yaml`. Cột "đỉnh của `doa`" là histogram mọi lần dò được đếm trên cả bản thu:

| Bản thu | Đỉnh của `doa` | Tách sau | Ghế cuối cùng | LEARN k-means (19/10): tách, ghế |
|---|---|---|---|---|
| Hai người luân phiên 30 s | 54° và 144–146° | 5,8 s | 54° và 146° | 2,8 s; 63° và 137° |
| Hai người nói đè 10 s | 50–60° và 130–142° | 3,1 s | 54° và 136° | 4,1 s; 57° và 135° |
| Giọng cùng loa nhạc 9,6 s | giọng 124–134°, nhạc rải 0–40° | 3,6 s | 127° (giọng) và 31° (nhạc) | 3,6 s; 134° và 12° |

Ghế nằm trên đỉnh của bản đồ góc. Lúc nói đè, mỗi bước cổng giữ một phía, không tách được hai giọng trong cùng một bước
(KẾ HOẠCH §3.8, giới hạn).

**Bản thu luân phiên tách ở 5,8 s.** B mở lời ở giây 2,0 khi A chưa dứt, và trong 0,75 s nói đè `doa` đọc B ở 74–76°.
Từ giây 3,25, B nói một mình và `doa` đọc 52–62°. Hai cụm của B cách nhau hơn `min_gap` nên là hai đỉnh. Với
`seats_max` 2, có ba đỉnh thì LEARN chờ đến khi đỉnh bị bỏ thấp hơn nửa đỉnh thấp nhất được giữ (KẾ HOẠCH §3.8):

| Lần kiểm lại | Đỉnh (độ cao) | Việc |
|---|---|---|
| 2,8 s | 144° (134), 75° (38) | khác lần trước quá nửa `min_gap`, đếm lại từ 1 |
| 3,8 s | 144° (134), 75° (38), 54° (37) | ba đỉnh ngang nhau, chờ |
| 4,8 s | 144° (134), 57° (77), 75° (38) | 75° dưới nửa 57°, đếm 1 |
| 5,8 s | 144° (134), 56° (119), 75° (38) | khớp lần trước, tách ở 56° và 144° |

LEARN k-means tách ở 2,8 s nhưng đặt ghế B ở 63°, giữa hai cụm của B. Hai người ở hai phía chính diện, nên trước lúc
tách cổng chia ở chính diện đã đưa A và B ra hai luồng: tách muộn ở bản thu này không làm mất hay rò thêm gì. Ca vàng 005
(§7) dựng đúng tình huống này.

## 6. Bộ kịch bản

`ml/tests/test_bss_scenarios.py` dựng lần dò của `doa` như trên board B: sau mỗi lần đổi người, `doa` quét chuyển 12 bước
(làm trơn 0,2 s); mỗi lần dò rung ±2°; một phần lần dò lạc ngẫu nhiên khắp 0–180°. Mỗi kịch bản ghi số ghế phải có ở
cuối và những người phải có ghế. Ít nhất 90% bước nói ổn định của mỗi người phải có cổng lớn nhất ở ghế gần họ nhất.

Hai mươi kịch bản:
- một người trôi chậm 48 → 58° (không ghế);
- hai phía; một phía; ba người; bốn người;
- người thứ tư vào muộn, khi cả ba còn nói, khi chỉ một người còn nói, và khi nói một mình;
- hai ghế mà ba người nói;
- một người nói 90%;
- hai người gần nhau (80° và 100°);
- hai người gần hơn `min_gap` (86° và 94°, một ghế);
- một người trôi dần;
- nói đè; nghỉ dài; chỉ có lần dò lạc;
- lượt 0,5 s;
- một người rời đi, người khác đến, rồi người cũ quay lại;
- ba ghế mà bốn người nói;
- nhạc rải và một người.

Kết quả:
- 120 lượt (20 kịch bản × 3 hạt giống × lạc 3% và 6%) qua hết trong 32 s.
- 10 hạt giống (0–9) ở cả hai mức lạc, 400 lượt, ở `5137faf`: qua hết.
- LEARN k-means trượt 9 trên 15 kịch bản đầu. Nó dừng ở số ghế đầu tiên trượt phép kiểm, thay mọi ghế khi một số ghế lớn
  hơn qua, kéo tâm ghế về giữa đường quét, và để lần dò lạc kéo ghế đi.
- Giới hạn còn lại: một người chỉ nói 10% thời gian với câu 0,5 s, khi `doa` quét chuyển tới 20 bước, không đủ khối
  lượng ở lõi để có ghế (KẾ HOẠCH §3.8).

## 7. Khớp với bản C

`make parity-host`, và `make parity-board` ở bản dựng `modules` ngày 20/10 (`5137faf`), bộ vàng `contracts/golden/bss/`.
Máy tính và board B ra cùng số.

Sáu ca khớp từng bit, mọi tensor `max_abs 0` (cổng, số ghế, hướng, cổng và hướng của mọi số ghế, cổng trễ và số ghế giữ
của lối ra trễ sau mọi bước, hai lối ra của bước cuối):
- chia ở chính diện;
- LEARN hai người;
- ba ghế giữ theo số lần dò, hai ghế im đến lúc bị quên;
- hai người một phía, `conf_min` 60;
- ba người, rồi người thứ tư ở 60° vào (`seats_max` 4);
- một người ở 75° dừng ở 54°, cạnh người ở 140°, tách khi đã rõ, rồi 140° rời đi và 100° đến (dời ghế trống).

Bốn đối chứng âm đều đỏ:
- 000: cổng của chế độ chia ở chính diện trên ca một phía. Cổng lệch 1, hướng của số ghế lệch 79°.
- 001: ca ba ghế đánh số theo độ trễ thay vì giữ số. Hướng lệch 150°, cổng lệch 1.
- 002: cổng trễ lấy góc lùi một bước. Cổng lệch 1.
- 003: ca người thứ tư với `confirm` 1. Số ghế lệch 2, hướng lệch 94°.

Thêm 600 ca ngẫu nhiên, sinh từ bản soi gương bằng một script tạm:
- cấu hình khắp dải `check()` nhận, 2–4 ghế;
- lần dò có quét chuyển, lạc tới 10%, nói đè, người mới vào và khoảng lặng.

Binary của `make parity-host` khớp từng bit cả 600 ca. Các ca này đi qua 15 557 lần kiểm lại chưa tách, 475 lần thêm
ghế và 394 lần dời ghế trống.

## 8. Chi phí

`make bench-board` ngày 20/10 (`5137faf`, profile `bench`), nhân 1, số mặc định của `contracts/afe.yaml`:

| Đường | µs trung bình | µs đỉnh | % khung 16 ms | Vùng `hot` |
|---|---|---|---|---|
| Chia ở chính diện | 25,4 | 30,2 | 0,2 | 4 288 B |
| LEARN, hai người ở 48° và 130° đổi lượt mỗi 2,5 s | 86,4 | 255,5 | 0,5 | 4 288 B |
| `vad` thứ hai trên trung bình hai micro, chỉ có khi `bss` là khối không gian | 138,0 | 175,3 | 0,9 | 2 640 B |

- Đỉnh của LEARN là bước kiểm lại, mỗi `recheck_s` một lần: dồn histogram vào 68 ô độ trễ, làm trơn, dò đỉnh, tính khối
  lượng lõi. Đỉnh 255,5 µs, LEARN k-means lên 317,2 µs ở bước phân cụm. `tinyai-signal` phân cụm cả vòng đệm, đỉnh một
  nhịp lên 2,9 ms.
- Vùng `hot` 4 288 B, hơn LEARN k-means 1 344 B: mật độ 68 ô, bảng ô của 181 độ và các danh sách đỉnh, cỡ theo cấu hình.
- Trong chuỗi, `bss` cộng `vad` thứ hai tốn 163 µs mỗi bước khi chia ở chính diện, 224 µs với LEARN. `gsc` tốn 141 µs
  khi góc lái đứng yên, 485 µs khi góc lái đổi mỗi bước.
- Bench dựng kèm `bss`, nên dòng chuỗi của `budget.md` là 86 464 B, hơn 1 040 B so với 11/10: đệm trung bình hai micro
  1 KB và các con trỏ của `vad` thứ hai nằm trong struct của mặt tiền khi `bss` được dựng. Sản phẩm không dựng `bss` nên
  không mang phần này.
