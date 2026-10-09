# Cửa 3 của `command` trên phiên thu qua board

Thước của KẾ HOẠCH §3.12: mỗi lệnh ≥ 90% câu đúng lệnh, từ chối đúng ≥ 95% câu còn lại, trên phiên board B ở dịch 13
của `ml/data/manifests/device/board_b.csv`, loại `cmd`, `neg`, `noise`, trừ `20260928_home_039`. Chấm bằng
`make command-eval TRACK=<kws|ctc> RUN=<run>`.

## 1. `ctc` float, lượt CTC trơn đầu tiên (E11-T12, E11-T13)

Run `20261002_128545c-dirty_64e7a4`: 40 000 bước CTC trơn trên `command/v2` mô phỏng, `val` loss 0,91, tỉ lệ lỗi đơn vị
29,5%; chấm ở float, chưa int8. Chấm ngày 02/10 ở `80446c7`. Một người nói (`spk_001`), phòng ở nhà, 1 m và 3 m, mỗi
phiên lệnh 25 s. Cửa sổ mỗi câu kết thúc ở bước `vad` tắt và lùi tối đa 3 s, không lấn câu trước (KẾ HOẠCH §3.12); bộ
lệnh là 10 dòng của `default_vi.json` qua `lang_vi` ba vùng, 19 biến thể, "chụp ảnh" là lệnh chưa học. Ngưỡng chưa chỉnh,
nên bảng đầu đếm câu mà lệnh điểm cao nhất là lệnh đúng. Cột cuối là cùng mạng khi cửa sổ chỉ lùi 0,3 s trước lúc `vad`
bật, như khoảng đệm của mẩu học: trên board nó cắt mất đầu lệnh, mạng giải tự do "tắt đèn" ra "J a:", "J" hay không gì.

| Lệnh | 1 m | 3 m | Cả hai | Cửa sổ lùi 0,3 s |
|---|---|---|---|---|
| bật đèn | 5/7 | 5/5 | 10/12 | 6/12 |
| tắt đèn | 1/6 | 0/5 | 1/11 | 0/11 |
| bật quạt | 6/6 | 5/5 | 11/11 | 7/11 |
| tắt quạt | 0/5 | 0/6 | 0/11 | 1/11 |
| mở cửa | 5/5 | 5/5 | 10/10 | 10/10 |
| đóng cửa | 6/6 | 5/5 | 11/11 | 9/11 |
| tăng âm lượng | 6/8 | 5/5 | 11/13 | 1/13 |
| giảm âm lượng | 5/5 | 5/6 | 10/11 | 3/11 |
| dừng lại | 6/6 | 4/5 | 10/11 | 7/11 |
| chụp ảnh (chưa học) | 6/6 | 5/5 | 11/11 | 10/11 |
| **Cả bộ** | 46/60 (77%) | 39/52 (75%) | **85/112 (76%)** | 54/112 (48%) |

Quét `δ₁` ở `δ₂` 50‰: phần câu lệnh được nhận đúng lệnh và phần câu còn lại bị từ chối.

| `δ₁` ‰ | Lệnh kém nhất | Trung bình các lệnh | Từ chối (cửa 95%) | `noise` | `neg` | `cmd` tiếng Anh |
|---|---|---|---|---|---|---|
| 100 | 0% | 48% | 86/86 | 2/2 | 24/24 | 60/60 |
| 200 | 0% | 67% | 84/86 | 2/2 | 22/24 | 60/60 |
| 300 | 0% | 71% | 83/86 | 2/2 | 21/24 | 60/60 |
| 400 | 0% | 71% | 77/86 | 2/2 | 19/24 | 56/60 |
| 500 | 0% | 71% | 70/86 | 1/2 | 18/24 | 51/60 |
| 600 | 0% | 71% | 60/86 | 0/2 | 16/24 | 44/60 |
| 800 | 0% | 71% | 50/86 | 0/2 | 14/24 | 36/60 |
| 1000 | 0% | 71% | 49/86 | 0/2 | 13/24 | 36/60 |

Tám lệnh đúng 83–100%. Chỗ trượt là "tắt": 21/22 câu ra "bật đèn" hay "bật quạt" với điểm 830–960‰, khoảng cách nhất–nhì
40–115‰. Giải tự do âm tiết đầu ra `@ t T6`: mất phụ âm đầu, "ă" nghe thành "â", sắc thành nặng; giải cả phiên liền thì
thanh ra đúng sắc, phụ âm đầu và nguyên âm vẫn sai. Hai hướng chưa tách: cao độ đặt lại ở đầu cửa sổ chưa có quá khứ cho
âm tiết đầu, giọng riêng của một người nói; tiếng bật hơi của "t" thì còn nguyên sau chuỗi (`afe/ns.md` §6).

## 2. `ctc` sau int8, theo thang KẾ HOẠCH §3.14 (E11-T12, E11-T19)

Cùng run với §1. Bậc 1 (cân bằng lớp 4 vòng, ngưỡng 0,4, `opt_level` 2; sửa bias) có trong mọi dòng int8; hiệu chuẩn trên
64 câu `train` rút theo seed 20261002, mỗi câu đệm tới 768 bước. Lỗi đơn vị trên 2 000 câu `test` rút theo cùng seed,
mô phỏng int8 cả câu bằng ESP-PPQ dưới bốn bản sửa của nhánh (`esp_ppq_patches`), đúng số chip tính; Cửa 3 trên phiên
board như §1, `δ₁` 300‰, `δ₂` 50‰, luật phần của §3. Đo ngày 02/10 tại `2b49747` bằng `make ctc-ptq`, `make ctc-int16`,
`make ctc-qat`.

| Dòng | Lỗi đơn vị | Lệnh điểm cao nhất đúng | Nhận đúng ở `δ₁` 300 | Nhận nhầm |
|---|---|---|---|---|
| float | 34,45% | 85/112 | 74/112 | 2/86 |
| bậc 2: minmax | 38,61% | 81/112 | 64/112 | 5/86 |
| bậc 2: percentile | 36,34% | 83/112 | 69/112 | 3/86 |
| bậc 2: MSE | 37,50% | 85/112 | 68/112 | 4/86 |
| bậc 2: KL | 39,93% | 83/112 | 68/112 | 2/86 |
| bậc 3: percentile, int16 `/front/convs.1` | 36,13% | 84/112 | 70/112 | 3/86 |
| bậc 3: như trên, thêm `/front/convs.2` | 36,36% | 83/112 | 73/112 | 2/86 |
| bậc 3: như trên, thêm tích chập sâu đầu của tầng 0 và `/front/proj` | 36,23% | 83/112 | 70/112 | 3/86 |
| bậc 4: QAT trên percentile, 2 000 bước | 35,75% | 86/112 | 68/112 | 3/86 |

Mọi dòng int8 trừ minmax nằm trong `quant.gate_tie` 5 câu nhận đúng của dòng cao nhất (73/112), nên lỗi đơn vị quyết:
QAT thấp nhất. QAT học trên đồ thị lô 16 (hiệu chuẩn percentile trên 4 lô 16 câu), tốc độ học 3e-5 giảm cosine về 1e-6,
rồi chép tham số và thang sang đồ thị lô 1. Phần học đổi 318 873 trên 1 985 623 trọng số int8 (16%), có trọng số trôi tới
23,6 bậc. Cùng đồ thị lô 16 chép sang mà không học: lỗi đơn vị 36,56%, lệnh đúng 84/112, nhận đúng 70/112, nhận nhầm 2/86;
nên 0,81 điểm lỗi đơn vị trên `test` là của phần học, còn hiệu chuẩn theo lô không hơn hiệu chuẩn từng câu. Trên `val` phần
học không thấy: lỗi đơn vị của đồ thị lô 16 là 31,38% ở bước 0, 31,54%, 31,63%, 31,33% ở bước 500, 1 000, 1 500 và 31,38% ở
bước 2 000, loss 0,977 xuống 0,969.

## 3. `ctc`: lệnh nói thiếu âm tiết (KẾ HOẠCH §3.12, E11-T19)

Mạng float của run ở §1, `δ₁` 300‰, `δ₂` 50‰, đo ngày 02/10. Ba luật: chỉ `δ₁` và `δ₂`; thêm điều kiện phần của lệnh
thắng phải kém nó ít nhất `δ₂`; thêm điều kiện phần ấy không được hơn hay bằng nó (so dấu, luật KẾ HOẠCH chọn).

Phiên board của Cửa 3, như §1:

| Luật | Nhận đúng ở `δ₁` 300 | Nhận nhầm |
|---|---|---|
| chỉ `δ₁`, `δ₂` | 79/112 | 3/86 |
| phần kém lệnh `δ₂` | 65/112 | 2/86 |
| **phần không hơn lệnh** | **74/112** | **2/86** |

Năm câu đúng phép so dấu bỏ là "tăng âm lượng" ba câu, "giảm âm lượng" và "dừng lại" mỗi thứ một câu: mạng nghe chữ đầu
yếu đến mức bỏ hẳn chữ ấy còn hợp hơn. Bắt thêm `δ₂` thì mất thêm chín câu, phần lớn cũng là "tăng/giảm âm lượng".

Buổi demo trực tiếp sáng 02/10 (board B truyền qua `make session`, phiên `20261002_home_003` và `_004`, máy tính chạy
chuỗi sản phẩm và `ctc_score.decide`), phát lại từ bản thu: luật chỉ `δ₁`, `δ₂` nhận 64 câu, phép so dấu nhận 54. Hai câu
nói một chữ bị nhận thành "chụp ảnh": "ảnh" một mình (mạng nghe `a J T4`; cả lệnh kém phần "ảnh" 293‰ mỗi khung) và một
chữ mạng nghe `c o t T6` (cả lệnh kém phần "chụp" 217‰). Phép so dấu từ chối cả hai; tám câu còn lại nó bỏ là câu mạng
nghe thiếu hay sai một chữ của lệnh, hay không nghe ra chữ nào.

Cắt từ 11 câu "chụp ảnh" của phiên board thì không tái hiện được lỗi: "ảnh" một mình và "chụp" cắt ngay sau đơn vị thanh
của nó đều 0/11 bị nhận theo cả ba luật; cắt ở chỗ căn CTC đặt "a" của "ảnh", tức còn dính đầu âm, luật chỉ `δ₁`, `δ₂`
nhận 7/11.

## 4. `ctc` và `rnnt`: lỗi đơn vị trên `val`, tốc độ nói và tăng cường còn thiếu (E11-T20)

Buổi demo tối 02/10, chủ repo thấy "tăng âm lượng" nói bình tĩnh thì board nhận, nói nhanh thì không. Mục này ghi những
gì đo được để tìm nguyên nhân, chưa sửa gì. Đo ngày 02/10 bằng một script chẩn đoán chạy một lần, không có trong repo.
Run `20261002_1538154-dirty_d6d74a` (RNN-T cộng CTC, `command_ctc.yaml`), checkpoint bước 26 000 trên 40 000, chấm trên
`val` của `command/v2` qua mô phỏng board: 1 580 câu, 62 072 đơn vị, giải tham lam như dòng `val` của lượt học.

### 4.1 Tăng cường của lượt học

| Có | Ở đâu |
|---|---|
| phòng và vang: 512 phòng, RT60 0,15–0,8 s | `scenes/device.yaml`, mô phỏng một lần lúc dựng đặc trưng |
| người nói cách 0,3–4 m, cao 1–1,8 m; mức 45–74 dB SPL ở 1 m; nghiêng phổ −6…+3 dB/octave | như trên |
| nhiễu ở 85% phiên, SNR 0–30 dB: MUSAN noise, music, speech; DEMAND | như trên |
| tự ồn INMP441, nền ồn đo trên board B, balance và `pcm_shift` của board B | như trên |
| SpecAugment: 2 dải mel rộng tới 8, 4 đoạn dài tới 15 bước | `train.masks`, mỗi lô |

| Thiếu | Hệ quả |
|---|---|
| đổi tốc độ hay nhịp nói (speed, tempo perturbation) | mạng gần như chưa nghe câu nói nhanh (§4.2); tốc độ 0,9 và 1,1 chỉ có ở TTS lệnh của `kws` |
| time-warp của SpecAugment | |
| mô phỏng lại theo lượt | mỗi câu chỉ có một điều kiện: 40 000 bước × 32 câu ≈ 13,8 lượt qua cùng đặc trưng, chỉ mặt nạ đổi |
| lời ra lệnh tự nhiên, nói nhanh, nuốt âm | cả năm kho (Common Voice, VIVOS, FPT, VLSP, BUD500) là tiếng đọc |

### 4.2 Tốc độ nói của tập học

Âm tiết của lời chia cho thời gian tiếng hoạt động (khung 32 ms trong 40 dB của khung to nhất, số đo của `make screen`),
trên 97 443 mẩu `train` của `command/v2`. Thời gian ấy tính cả quãng ngắt ngắn, nên tốc độ thật nhỉnh hơn.

| Phân vị | 10% | 50% | 90% | 95% | 99% |
|---|---|---|---|---|---|
| Âm tiết mỗi giây | 2,5 | 3,7 | 4,9 | 5,3 | 6,0 |

Trên 6 âm tiết/s có 1,0% mẩu, trên 7 có 0,1%, trên 8 không có mẩu nào. Mẩu từ 4 âm tiết trở xuống, cỡ một lệnh, chỉ có
5 056 (5,2%) và còn chậm hơn: trung vị 2,7, phân vị 90% 3,9. Trung vị theo kho: VLSP 4,2, BUD500 4,1, VIVOS 3,6, FPT 3,5,
Common Voice 3,1.

### 4.3 Trần của khung CTC

Mạng ra 31,25 khung mỗi giây (32 ms). Giải CTC và tìm chùm RNN-T (một ký hiệu mỗi khung, như MultiNet7) đều phát nhiều
nhất một đơn vị mỗi khung, nên lệnh n đơn vị cần ít nhất n khung. Cách đọc giọng Bắc của `lang_vi`:

| Lệnh | Đơn vị | Ngắn nhất nhận được | Dài khi nói 6 âm tiết/s | 8 âm tiết/s |
|---|---|---|---|---|
| tăng âm lượng, giảm âm lượng | 11 | 352 ms | 500 ms | 375 ms |
| bật quạt, tắt quạt | 9 | 288 ms | 333 ms | 250 ms |
| bật đèn, tắt đèn, dừng lại | 8 | 256 ms | 333 ms | 250 ms |
| đóng cửa, chụp ảnh | 7 | 224 ms | 333 ms | 250 ms |
| mở cửa | 6 | 192 ms | 333 ms | 250 ms |

Ở 8 âm tiết/s, lệnh hai âm tiết từ 8 đơn vị trở lên ngắn hơn số khung tối thiểu: không giải được dù mạng nghe đúng. Ở
7 âm tiết/s lệnh hai âm tiết dài 286 ms, vừa chạm mức của lệnh 9 đơn vị. Tốc độ người nói nhanh thật trên board chưa đo 🔬.

### 4.4 Lỗi theo điều kiện mô phỏng

Tỉ lệ lỗi đơn vị, ctc / rnnt, kèm số đơn vị của ô. Cả `val` 31,1% / 30,1%.

| Nhóm | Ô và lỗi |
|---|---|
| SNR | không nhiễu 25,7 / 24,5 (7 870) · 20–30 dB 27,0 / 25,4 (20 611) · 10–20 dB 30,7 / 29,4 (20 437) · 0–10 dB 41,4 / 41,9 (13 154) |
| Khoảng cách | dưới 1 m 26,1 / 24,0 (16 262) · 1–2 m 29,7 / 28,3 (20 148) · 2–3 m 33,5 / 33,1 (16 510) · 3–4 m 38,7 / 39,7 (9 152) |
| RT60 đo, làm tròn 0,2 s | 0,2 s 27,5 / 25,8 (15 650) · 0,4 s 31,0 / 30,3 (18 598) · 0,6 s 31,9 / 31,5 (21 626) · 0,8 s 37,6 / 35,6 (6 198) |
| Mức nói ở 1 m | 50–59 dB 34,4 / 35,0 (14 865) · 60–69 dB 31,6 / 30,4 (33 936) · 70 dB trở lên 26,1 / 23,9 (13 271) |
| Kho | VIVOS 29,9 / 28,6 (56 179) · Common Voice 42,7 / 43,8 (5 893) |
| Đơn vị mỗi giây của đoạn có tiếng | dưới 4: 36,9 / 38,2 (217) · 4–8: 31,5 / 29,3 (14 848) · 8–12: 30,7 / 29,2 (34 615) · 12–16: 31,2 / 32,8 (12 090) · từ 16: 57,0 / 57,6 (302) |

Điều kiện tốt (gần, sạch, giọng to) còn 24–26%; điều kiện xấu nhất 37–42%. Ô nói nhanh nhất lỗi gấp đôi, nhưng chỉ có
302 đơn vị.

### 4.5 Lỗi theo loại đơn vị, `ctc`

| Loại đơn vị | Thay sai | Xoá | Chèn |
|---|---|---|---|
| phụ âm | 7 243: 37,5% số lỗi, 25,3% phụ âm | 1 940: 6,8% phụ âm | 835 |
| nguyên âm | 4 927: 25,5% số lỗi, 29,4% nguyên âm | 922: 5,5% nguyên âm | 358 |
| thanh | 2 529: 13,1% số lỗi, 15,1% thanh | 352: 2,1% thanh | 208 |

`rnnt` sai thanh nhiều hơn (18,1% thanh) và sai nguyên âm ít hơn (23,8%).

Cặp nhầm nhiều nhất (đúng → nghe ra):

| Loại | Cặp |
|---|---|
| phụ âm | n → ng 269 · ng → n 247 · c → t 214 · t → c 194 · m → n 175 · n → nh 121 · c → ch 117 · m → ng 117 · nh → n 108 |
| nguyên âm | ă → a 365 · ô → o 165 · â → ă 146 · o → ô 144 · ă → â 138 · a → ă 135 · iê → i 135 · i → iê 108 |
| thanh | ngang → huyền 230 · ngã → hỏi 207 · nặng → huyền 184 · nặng → hỏi 179 · huyền → ngang 158 · ngang → sắc 148 · hỏi → ngã 148 |
| xoá | âm đệm o/u 307 · n 283 · ng 210 · bán âm cuối i/y 181 · c 151 |

### 4.6 Nhãn đọc theo giọng Bắc không phải nguyên nhân

Lượt học đọc mọi câu theo giọng Bắc, vì kho không ghi vùng. Chấm lại mỗi câu theo cách đọc gần nhất trong ba vùng của
`lang_vi`: lỗi chỉ từ 31,1% xuống 30,9%; 1 475 trên 1 580 câu gần giọng Bắc nhất, 78 gần giọng Trung, 27 gần giọng Nam.
Common Voice 42,7% xuống 42,3%, VIVOS 29,9% xuống 29,7%. Mạng học trên nhãn giọng Bắc nên phép này chỉ nói đầu ra của nó
không gần cách đọc vùng khác hơn, không đo được nhãn sai làm chậm lúc học bao nhiêu.

### 4.7 Đọc kết quả

- Lỗi `val` còn giảm khoảng 0,5–1 điểm mỗi 2 000 bước ở bước 26 000; lượt CTC trơn ở §1 đi từ 31,1% ở bước 28 000 tới
  29,5% ở bước 40 000.
- Khoảng 15 điểm lỗi đến từ điều kiện khó mà mô phỏng cố ý đưa vào `val`.
- Phần còn lại, 24–26% ở điều kiện tốt, nằm ở các cặp khó của tiếng Việt: âm cuối n/ng, c/t, m; ă/a, ô/o; ngang/huyền,
  hỏi/ngã; âm đệm và âm cuối bị nuốt. Mạng cỡ MultiNet7 học 108 giờ tiếng đọc chưa tách được chúng, nhất là khi có vang
  và ồn.
- Nói nhanh nằm ngoài tập học (§4.2) và chạm trần khung ở khoảng 7–8 âm tiết/s (§4.3).
- Common Voice lỗi hơn VIVOS 13 điểm; chưa đo vì sao.

### 4.8 Cách giải của `rnnt` trên Cửa 3

Cùng run, mạng cuối (bước 40 000), float, phiên board của §1; đo ngày 02/10 bằng script chẩn đoán chạy một lần.
Nhận đúng ở `δ₁` 300‰, `δ₂` 50‰ trên 112 câu lệnh; "đầu" là lệnh điểm cao nhất đúng lệnh, không ngưỡng.

| Cách giải | Nhận đúng | Đầu | "tăng âm lượng" (đầu) | Câu lệnh không giả thuyết nào trọn lệnh |
|---|---|---|---|---|
| tìm chùm 4, một đơn vị mỗi khung | 53/112 | | 0/13 | 126 trên 198 cửa sổ |
| tìm chùm 16, một đơn vị mỗi khung | 66/112 | | 1/13 | 108 trên 198 |
| tìm chùm 16, ba đơn vị mỗi khung | 71/112 | | 2/13 | |
| tìm chùm 32, một đơn vị mỗi khung | | | 4/13 | 96 trên 198 |
| chấm chính xác mọi lệnh | 39% trung bình mỗi lệnh | 81/112 | 8/13 | không có |
| `ctc`, thuật toán tiến | 67% trung bình mỗi lệnh | 84/112 | 11/13 | không có |
| cộng điểm `ctc` và `rnnt` chính xác, tỉ trọng `rnnt` 0,3–0,5 | | 85/112 | 11/13 | không có |

Tìm chùm làm rơi lệnh đúng: phần của lệnh tạm điểm cao hơn lệnh trọn giữa câu, chùm hết chỗ, và tới cuối cửa sổ không
còn giả thuyết nào trọn một lệnh. Chấm chính xác không rơi lệnh nào. Ở chấm chính xác, nhận đúng không đổi khi quét
`δ₁` từ 100 tới 1000: thứ chặn là `δ₂` 50‰ chỉnh cho `ctc`, vì khoảng cách nhất nhì của `rnnt` nhỏ hơn (ví dụ "tăng âm
lượng" 16–83‰ so với 102–154‰ ở `ctc`); ngưỡng của `rnnt` phải chọn riêng trên `val`. Đứng đầu đúng, ba cách đều
81–85/112, trong sai số khoảng ±5 câu; 21 trong 27 câu sai ở cách tốt nhất là "tắt đèn", "tắt quạt".

Giải tham lam cho tới ba đơn vị mỗi khung trên các cửa sổ "tăng âm lượng" và "tắt đèn": 643 khung phát một đơn vị, 220
phát hai, 67 phát ba. Chuỗi giải tự do sai xa ở cả hai lệnh, ví dụ "tăng âm lượng" ra `o N T1 m u@ n T5 l o T3`, "tắt
đèn" ra `@: t T5 J a: k T6`.

## 5. Cửa sổ lệnh: neo đầu câu và chia điểm (KẾ HOẠCH §3.12, §5.4, E11-T14)

Model đang khoá: run `20261002_128545c-dirty_64e7a4`, hàng `qat` sau int8 (mô phỏng ESP-PPQ như board chạy), cùng mạng
float; phiên board của §1, 198 câu (112 câu lệnh, 86 câu khác); `δ₂` 50‰. Đo ngày 03/10 ở `6eeb568` bằng script chẩn
đoán chạy một lần. Cách cắt cũ: cửa sổ kết ở bước sau đoạn `vad` cuối, lùi tối đa 3 s. Cách cắt mới: cửa sổ mở `lead`
trước bước `vad` đầu của câu, kết cùng chỗ, câu dài quá 3 s thì lùi từ cuối như cũ. "Điểm chia" là số khung chia điểm:
số khung của cửa sổ đang chấm, hay `T_W` = 94 khung của cửa sổ 3 s. Ô nhận ghi câu lệnh nhận đúng / câu lệnh nhận
thành lệnh khác / câu khác nhận thành lệnh, ở `δ₁` 200, 300, 400‰.

| Cách cắt | Điểm chia | Đầu đúng | `δ₁` 200 | `δ₁` 300 | `δ₁` 400 | Lùi từ cuối | Bước trung bình |
|---|---|---|---|---|---|---|---|
| cũ, lùi 3 s | khung thật | 86/112 | 67/15/1 | 68/15/3 | 68/15/3 | | 158 |
| cũ, lùi 3 s | `T_W` | 86/112 | 67/13/1 | 68/13/3 | 68/13/4 | | 158 |
| `lead` 0,5 s | khung thật | 81/112 | 42/7/0 | 50/9/1 | 57/9/1 | 2 | 82 |
| `lead` 0,5 s | `T_W` | 81/112 | 53/6/1 | 54/6/4 | 54/6/4 | 2 | 82 |
| `lead` 0,75 s | khung thật | 85/112 | 50/13/0 | 66/15/2 | 70/16/3 | 2 | 97 |
| `lead` 0,75 s | `T_W` | 85/112 | 65/9/1 | 65/9/4 | 65/9/5 | 2 | 97 |
| `lead` 1,0 s | khung thật | 84/112 | 60/19/1 | 70/21/3 | 74/21/4 | 2 | 112 |
| `lead` 1,0 s | `T_W` | 84/112 | 65/15/2 | 67/15/5 | 67/15/6 | 2 | 112 |
| `lead` 1,25 s | khung thật | 84/112 | 63/19/0 | 72/19/2 | 73/19/3 | 2 | 127 |
| **`lead` 1,25 s** | **`T_W`** | **84/112** | 69/16/2 | **71/16/3** | 71/16/5 | 2 | 127 |

Float cho cùng chiều: cũ 85/112 đầu đúng, 74/16/2 ở `δ₁` 300; `lead` 1,25 s chia `T_W` 83/112, 74/18/3. Kéo cuối cửa
sổ thêm quãng `utterance.gap_s` sau đoạn `vad` cuối, như mẩu học có đệm sau câu (trung vị 0,45 s sau bước `vad` cuối),
không cho gì rõ: ở `δ₁` 300, `lead` 1,25 s chia `T_W` ra 70/14/4 thay vì 71/16/3, cách cắt cũ chia khung thật ra 72/15/4
thay vì 68/15/3; cuối cửa sổ giữ ở bước sau đoạn `vad` cuối.

Đọc kết quả:

- Mở cửa sổ càng sát câu thì mạng càng ít ngữ cảnh: `lead` 0,5 s mất 5 câu đầu đúng. Ở các mẩu `val` của `command/v2`
  mà mạng học, quãng từ đầu mẩu tới bước `vad` đầu có trung vị 0,69 s, p90 1,17 s (1 583 mẩu); `lead` 1,25 s cho mạng
  thấy trước câu ít nhất như lúc học ở chín phần mười số mẩu, và Cửa 3 không kém cách cắt cũ: 2 câu đầu đúng mất là hai
  câu "dừng lại" mà cả hai cách đều từ chối.
- Chia số khung thật thì cửa sổ ngắn hơn làm khoảng cách nhất nhì to ra, nên cùng `δ₂` nhận thêm cả lệnh đúng lẫn lệnh
  sai: ở `lead` 1,25 s, 19 câu nhận thành lệnh khác so với 15 của cách cắt cũ, phần lớn là "tắt" nghe thành "bật". Chia
  `T_W` giữ ngưỡng đúng nghĩa cũ: 71/16/3 so với 68/15/3.
- Hai câu dài quá 3 s tính cả `lead` thì lùi từ cuối như cũ.

## 6. "Tắt" của chủ repo trên board B: model nghe gì (03/10)

Model đang khoá (dòng `qat` của run `20261002_128545c-dirty_64e7a4`), 22 câu "tắt đèn", "tắt quạt" và 23 câu "bật
đèn", "bật quạt" của phiên §1, cắt theo §5; đo ở `0dc8de3` bằng script chẩn đoán chạy một lần. Nhận đúng "tắt": **0/22**.

Xác suất từng khung (32 ms) ở âm tiết đầu, câu thứ hai của phiên 1 m:

| | Phụ âm đầu | Nguyên âm | Thanh |
|---|---|---|---|
| nói "tắt" | t 0,12, b 0,10, blank 0,43 | **â 0,89**, ă 0,03 | **nặng 0,80**, sắc 0,18 |
| nói "bật" | b 0,25 | ă 0,71, â 0,20 | nặng 0,76, sắc 0,22 |

Đường giải tự do của cả 22 câu "tắt" là "ật" không phụ âm đầu (`@ t T6`, đôi khi `h`, `f`, `b` đứng trước). Cao độ thì có
đủ: "tắt" cao và đều chừng 217 Hz, "đèn" chừng 137 Hz, tức thanh sắc rõ; đặc trưng cao độ chuẩn hoá theo 0,75 s trước nên ở
âm tiết đầu câu nó vọt lên rồi tụt dần trong âm tiết, và cho bộ chuẩn hoá một mức giọng nền 140 Hz thì vài câu nghe ra sắc
nhưng vẫn 0/22 câu đúng, vì phụ âm và nguyên âm vẫn sai. Tiếng bật hơi của "t" nổi 25–28 dB trên nền sau chuỗi (`afe/ns.md`
§6): phụ âm đầu có trong đặc trưng mà model không đọc ra, và nới hay tắt lọc ồn cũng không cứu được. Lỗi chính là nguyên
âm ă nghe thành â và thanh sắc nghe thành nặng với giọng này, đúng loại nhầm
model mắc nhiều trên `val` (§4.5: ă → a 365, ă → â 138; nặng ↔ hỏi, huyền). Cùng phiên chủ repo nói trực tiếp, "tét" và "đắt"
được nhận, vì không lệnh nào khác chỉ khác chúng ở nguyên âm hay thanh, còn "tắt X" thua "bật X" ngay ở âm tiết đầu.

Bảng này là mốc: model v3 phải đo lại đúng các câu này.

## 7. Lượt học v3 đầu: nền quạt dưới mọi phiên (04/10)

Split `command/v3` mô phỏng từ 02/10 23:24 tới 04/10 05:45 với nền thu của board (`20261001_home_011`–`013`) cộng thẳng
dưới mọi phiên, người nói 45–74 dB SPL ở 1 m và nghiêng phổ −6…+3 dB mỗi octave trên 1 kHz. Ba phiên nền ấy thu khi
quạt máy tính chạy cạnh board (chủ repo, 04/10): −56…−58 dBFS ở `ch0`, vạch 35, 102 và 1 875 Hz (`mic_array.md` §4).

Run `20261004_90fd6d8-dirty_be34e4` (cấu hình đang khoá, 100 000 bước, dừng ở bước 6 000) và hai lượt thử 2 000 bước
cùng code, cùng seed:

| Lượt | Dữ liệu học | Tăng cường tempo, warp, tilt | Bước | UER `ctc` `val` | UER `rnnt` `val` |
|---|---|---|---|---|---|
| v3 | v3 nền quạt | có | 2 000 / 4 000 / 6 000 | 0,862 / 0,862 / 0,859 | 1,000 / 1,000 / 0,985 |
| thử A | v3 nền quạt | không | 2 000 | 0,902 | 0,963 |
| thử B | v2 | không | 2 000 | 0,571 | 0,565 |
| run v2 `20261002_1538154-dirty_d6d74a` | v2 | không | 2 000 | 0,558 | 0,546 |

Ở bước 6 000, mạng v3 phát blank ở 81–89% khung và sai 84–89% đơn vị trên chính câu học của mọi kho. Dữ liệu thì khớp:
mạng v2 nghe câu học v3 sai 49–61% (ngang `val`), qua đúng đường nạp của trainer sai 0,494 khi để nguyên, 0,573 khi tăng
cường. Thử B cho thấy code học không lỗi; lỗi ở dữ liệu.

Cùng 150 câu `val` ở ba bản dựng, mạng v2 nghe:

| | v2 | v3 nền quạt | v3 không nền (`3549465`) |
|---|---|---|---|
| Người nói ở 1 m, trung vị | 64,6 dB SPL | 58,8 dB SPL | 58,8 dB SPL |
| Tiếng nói nổi trên khoảng đệm, p10 / trung vị / p90 | 17,4 / 33,7 / 45,5 dB | 6,0 / 18,1 / 30,3 dB | 10,0 / 30,5 / 41,8 dB |
| UER mạng v2 | 0,298 | 0,486 | 0,348 |

Nền quạt dưới mọi phiên, cộng người nói xuống 45 dB, để tiếng nói chỉ nổi 18 dB trên nền ở trung vị, và mạng học từ đầu
không qua khỏi pha chỉ phát blank. Bỏ nền (KẾ HOẠCH §1.2) đưa `val` về gần v2; phần chênh còn lại là người nói nhỏ và
nghiêng phổ. Trên 300 câu so cặp, `val` không nền: mạng v2 sai 0,332 (v2: 0,284), 7/300 câu sai từ 80% (v2: 2/300), không
giá trị NaN hay vô cực, gò quanh 1,9 kHz của phổ trung bình 0,06 nat (bản nền quạt: ~1 nat); `test`: 0,377 (v2: 0,340),
tiếng nói nổi 29,1 dB ở trung vị (v2: 31,0 dB). Mô phỏng lại không nền từ 04/10 06:55, mỗi kho qua cùng phép so cặp với
v2 trước khi học.

## 8. Lượt học v3 không nền, và đổi nhịp trộn hai khung (04/10)

Run `20261004_651b321-dirty_3fdaec`: split `command/v3` mô phỏng lại không nền (§7), mỗi kho qua phép so cặp với v2;
`command_ctc.yaml` ở `651b321`, 745 812 câu, 717,7 giờ, vòng đệm 206,7 giờ; tăng cường tempo 0,8–1,6 rút log-đều,
warp 8 bước, tilt ±3 dB. Dừng ở bước 42 773 trên 100 000 để sửa tăng cường. Lỗi đơn vị `val` v3 qua các bước:

| Bước | 2 000 | 4 000 | 8 000 | 12 000 | 16 000 | 20 000 | 24 000 | 28 000 | 32 000 | 36 000 | 40 000 | 42 000 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `ctc` | 0,855 | 0,651 | 0,557 | 0,519 | 0,504 | 0,489 | 0,463 | 0,466 | 0,461 | 0,445 | 0,446 | 0,437 |
| `rnnt` | 0,973 | 0,657 | 0,579 | 0,559 | 0,520 | 0,535 | 0,499 | 0,489 | 0,502 | 0,467 | 0,466 | 0,468 |

Mạng ra khỏi pha chỉ phát blank giữa bước 2 000 và 4 000; lượt nền quạt của §7 còn ở 0,86 tới bước 6 000. Ở bước 3 899,
trên 40 câu học mỗi kho: BUD500 0,577, VIVOS 0,652, FPT 0,678, Common Voice 0,689, VLSP 0,693; 80 câu `val` 0,666.

Checkpoint bước 32 000 cạnh mạng cuối của run v2 `20261002_1538154-dirty_d6d74a`, trên cả `val` v3 trainer chấm
(1 580 câu tới 12 s, 62 072 đơn vị), giải tham lam CTC; câu `val` đổi nhịp qua đúng hàm tăng cường của trainer, không
warp, không tilt. Đo ngày 04/10 bằng script chẩn đoán chạy một lần.

| Nhịp | Mạng v2 | Mạng v3, bước 32 000 |
|---|---|---|
| 0,95 | 0,351 | 0,445 |
| 1,00, khung gốc | 0,345 | 0,461 |
| 1,05 | 0,342 | 0,434 |
| 1,30 | 0,362 | 0,428 |
| 1,60 | 0,436 | 0,442 |

Trainer đổi nhịp và warp bằng cách đọc trục bước ở vị trí lẻ, trộn tuyến tính hai bước kề nhau. Hệ số nhịp rút liên tục
nên gần như mọi câu học là khung trộn; `val` và board chỉ có khung gốc. Mạng v2, chưa học khung trộn nào, đi ngang quanh
nhịp 1 và kém dần khi nói nhanh. Mạng v3 nghe nhịp 0,95 và 1,05, tốc độ gần như không đổi nhưng mọi khung là khung trộn,
tốt hơn khung gốc 1,6–2,7 điểm: nó quen khung trộn. Đổi nhịp và warp nay lấy nguyên bước gần nhất (KẾ HOẠCH §3.12).

Cùng checkpoint ở nhịp 1, theo điều kiện:

| Ô | Mạng v2 | Mạng v3, bước 32 000 |
|---|---|---|
| người nói 45–50 dB SPL ở 1 m | 0,454 | 0,536 |
| 50–56 dB | 0,358 | 0,489 |
| 56–65 dB | 0,344 | 0,463 |
| 65–74 dB | 0,274 | 0,394 |
| không nhiễu | 0,313 | 0,425 |
| SNR 20–30 dB | 0,285 | 0,409 |
| SNR 10–20 dB | 0,322 | 0,450 |
| SNR 0–10 dB | 0,451 | 0,549 |
| nghiêng −6…−3 dB/octave | 0,399 | 0,510 |
| VIVOS | 0,330 | 0,452 |
| Common Voice | 0,489 | 0,555 |

Mạng v2 là mạng cuối, tốc độ học đã hạ hết; mạng v3 ở bước 32 000 còn 77% tốc độ học đầu.

## 9. "Tắt" của chủ repo: bộ lệnh, phiên mới và bộ lệnh lớn (04/10)

Mạng float, chấm như Cửa 3 không ngưỡng (lệnh điểm cao nhất của mỗi câu), đo ngày 04/10 bằng script chẩn đoán chạy một
lần. Mạng: dòng `qat` đang khoá của run `20261002_128545c-dirty_64e7a4` dùng bản float `model.pt`; run
`20261004_651b321-dirty_3fdaec` ở bước 42 000 (đổi nhịp trộn khung, §8); run `20261004_acde692-dirty_3fdaec` ở bước
52 000 (khung gần nhất). Phiên 28/09 là của Cửa 3 (1 m và 3 m); mười phiên `20261004_home_001`–`010` chủ repo thu ở
80 cm, mỗi lệnh mười lần, cộng hai–ba tiếng động ngắn mỗi phiên mà cách chấm này luôn tính sai.

| Câu "tắt", bộ mặc định | Model đang khoá | Run `651b321`, 42 000 | Run `acde692`, 52 000 |
|---|---|---|---|
| Phiên 28/09, 22 câu | 0 | 3 | 1 |
| Phiên 04/10, "tắt đèn" 14 cửa sổ / "tắt quạt" 14 | 2 / 6 | | 8 / 10 |

Gần như mọi câu "tắt" sai đều thành "bật" cùng vật. Bộ lệnh có "mở đèn", "mở quạt" thay "bật đèn", "bật quạt":

| Đổi "bật" thành "mở" | Model đang khoá | Run `651b321`, 42 000 | Run `acde692`, 52 000 |
|---|---|---|---|
| "tắt", phiên 28/09, 22 câu | 22 | 21 | 20 |
| "tắt đèn" / "tắt quạt", phiên 04/10, 14 / 14 | 11 / 12 | | 10 / 12 |
| "mở đèn" / "mở quạt", phiên 04/10, 13 / 14 | 11 / 11 | | 11 / 11 |

Ranh giới "tắt"/"bật" của mạng v3 mỏng: cùng mạng, phiên 04/10 đúng phần lớn câu "tắt", phiên 28/09 gần như không câu
nào. Không còn lệnh "bật" sát bên thì "tắt" thắng ở cả hai ngày, và "mở" đúng mọi câu không phải tiếng động.

Bộ lệnh lớn: run `651b321` ở bước 42 000, 112 câu lệnh của phiên 28/09, bộ mặc định 10 lệnh so với bộ demo 34 lệnh của
chủ repo (`host/sets/demo_vi.json`, có "tắt/bật ti vi", "tắt/bật điều hòa", "xoay trái/phải N độ", "đắt", "tát", "tét",
"tiết" và các cặp gần âm):

| | 10 lệnh | 34 lệnh |
|---|---|---|
| `ctc` | 88/112 | 83/112 |
| `rnnt` | 83/112 | 78/112 |

Cả hai đường mất năm câu khi bộ lệnh lớn lên, đều vì lệnh một âm tiết "tát", "tét", "mẹ nó" khớp một mẩu của câu dài hơn
("tăng âm lượng", "bật đèn", "giảm âm lượng"); `rnnt` không giữ tốt hơn `ctc`.

## 10. Lượt v3 100 000 bước: một lớp trôi, int8 mất (04/10)

Run `20261004_acde692-dirty_3fdaec`: split `command/v3`, đổi nhịp và warp lấy bước gần nhất (§8), 100 000 bước, cạnh run
đang khoá `20261002_128545c-dirty_64e7a4` (split v2, 40 000 bước). Thang int8 của KẾ HOẠCH §3.14 (`make ctc-ptq`,
`make rnnt-ptq`, `make ctc-qat`), ngày 04/10. Mỗi ô: lỗi đơn vị trên 2 000 câu `test` · đúng nhất /112 · nhận đúng /112 ·
nhận nhầm /86 của Cửa 3.

| Dòng | Run `128545c`, `ctc` | Run `acde692`, `ctc` | Run `acde692`, `rnnt` |
|---|---|---|---|
| float | 0,345 · 85 · 74 · 2 | 0,376 · 85 · 64 · 4 | 82 · 48 · 1 |
| `minmax` | 0,386 · 81 · 64 · 5 | 0,804 · 21 · 0 · 0 | 14 · 0 · 0 |
| `percentile` | 0,363 · 83 · 69 · 3 | 0,433 · 74 · 46 · 5 | 72 · 32 · 2 |
| `mse` | 0,375 · 85 · 68 · 4 | 0,610 · 54 · 8 · 2 | 51 · 3 · 1 |
| `kl` | 0,399 · 83 · 68 · 2 | 0,540 · 78 · 19 · 3 | 69 · 22 · 0 |
| `qat` | 0,358 · 86 · 68 · 3 | 0,550 · 34 · 2 · 2 | |

QAT của run `acde692` đi từ đồ thị `percentile`: lỗi đơn vị `val` 0,406 ở bước 0, rồi 0,420, 0,473, 0,505, 0,502 mỗi 500
bước, loss học 1,90 lên 2,30 trong khi tốc độ học hạ từ 3·10⁻⁵ về 10⁻⁶; QAT của run `128545c` đứng ở 0,314 suốt 2 000
bước.

RMS của dòng cộng dồn ở đầu vào phép chuẩn hoá mỗi lớp, cả tensor, trên 4 câu hiệu chuẩn, qua các checkpoint. Lớp đếm
qua các tầng: 0 ở tầng 1; 1, 2 ở tầng 2; 3, 4 ở tầng 3; 5 ở tầng 4.

| Bước | Lớp 0 | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| 8 000 | 5,1 | 6,9 | 5,2 | 4,4 | 19,1 | 5,4 |
| 16 000 | 9,3 | 10,3 | 5,3 | 5,2 | 104,5 | 5,5 |
| 24 000 | 10,2 | 19,5 | 4,9 | 6,4 | 252,2 | 5,2 |
| 32 000 | 13,3 | 21,3 | 4,8 | 5,5 | 411,1 | 4,2 |
| 40 000 | 9,1 | 18,9 | 5,1 | 6,3 | 33 471 | 5,1 |
| 48 000 | 13,1 | 25,9 | 4,4 | 6,7 | 93 387 | 5,4 |
| 64 000 | 10,3 | 15,5 | 4,7 | 7,7 | 94 690 | 3,9 |
| 80 000 | 9,7 | 14,2 | 4,3 | 6,4 | 51 679 | 3,9 |
| 96 000 | 9,7 | 13,0 | 4,1 | 6,4 | 135 355 | 4,0 |
| Run `128545c`, 40 000 | 38,4 | 3,0 | 2,4 | 2,9 | 4,4 | 5,8 |

RMS từng khung của dòng sau mỗi khối cộng vào, sáu điểm mỗi lớp, trên 16 câu hiệu chuẩn; mỗi ô: trung vị / lớn nhất.

| Mạng | Lớp 0 | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| Run `128545c`, 40 000 | 5,79 / 138,7 | 1,78 / 8,8 | 1,20 / 5,4 | 1,81 / 12,4 | 1,87 / 9,2 | 1,25 / 11,9 |
| Run `acde692`, 8 000 | 1,84 / 15,8 | 2,30 / 28,7 | 1,55 / 16,1 | 1,53 / 62,2 | 4,71 / 119,3 | 1,48 / 8,3 |
| Run `acde692`, 16 000 | 1,96 / 29,2 | 2,99 / 49,2 | 1,48 / 23,4 | 1,71 / 65,1 | 11,59 / 347,8 | 1,14 / 10,2 |
| Run `acde692`, 32 000 | 2,32 / 42,0 | 4,17 / 100,6 | 1,59 / 17,1 | 1,67 / 53,0 | 15,67 / 2 268 | 1,35 / 13,3 |

Dòng cộng dồn của lớp 4 lớn dần từ đầu lượt và nhảy hai bậc giữa bước 32 000 và 40 000; các lớp khác giữ dưới 30. Dòng
ấy chỉ đi vào phép chuẩn hoá RMS cuối lớp, nên mạng float không đổi theo cỡ của nó và Cửa 3 float vẫn 85/112; Adam không
có weight decay và không gì trong loss giữ cỡ ấy, nên nó trôi. int8 một số mũ cho cả tensor: ngay ở bước 32 000, bậc
lượng tử `minmax` của lớp 4, ít nhất 2 268/127 ≈ 17,9, đã lớn hơn RMS khung trung vị 15,7, và ở mạng cuối dòng ấy lớn
hơn bước 32 000 khoảng 300 lần. `minmax` giữ cả khung lớn nhất nên mất gần hết, `percentile` cắt bớt nên còn 74/112; QAT
giữ số mũ của hiệu chuẩn, chỉ học trọng số, và lỗi `val` tăng dần thay vì giảm. Lớp 0 của run `128545c` cũng có khung
tới 139, nhưng khung trung vị cách nó 24 lần, và int8 của run ấy chỉ kém float 0,02 lỗi đơn vị.

Trần `train.stream.cap_rms` = 32 (KẾ HOẠCH §3.12): ở run `128545c`, khung lớn nhất của lớp 1–5 dưới 13 và của lớp 0 tới
139; ở run `acde692` mọi lớp lớn dần, lớp 4 đã qua 32 trước bước 8 000. Đo ngày 04/10 bằng script chẩn đoán chạy một lần.

## 11. Các phiên của chủ repo vào tập học `command/v4` (04/10)

Mười phiên `20261004_home_001`–`010` của `eval.board.train` (80 cm, mỗi phiên một lời nhắc), cắt như Cửa 3 bằng
`ctc.data` vào `processed/command/v4/board`, log-mel 80 dải: 128 lượt `vad`, giữ 108 lượt dài 0,8–2,4 s. Mỗi lượt
chấm thêm bằng mạng float của run `20261004_acde692-dirty_3fdaec` trên đúng chuỗi Cửa 3 ở 40 dải: khoảng cách của
vòng tự do trên lời nhắc của phiên, ‰ nat mỗi khung (KẾ HOẠCH §3.12). Đo ngày 04/10 bằng script chẩn đoán chạy một lần.

| Lượt `vad` | Số lượt | Khoảng cách trên lời nhắc |
|---|---|---|
| ngắn hơn 0,8 s, bỏ | 15 | 435–1 017 |
| dài hơn 2,4 s, bỏ | 5 | 16–207 |
| giữ, câu thật | 105 | 5–211 |
| giữ, không lời nhắc nào khớp: `009#12` 0,90 s, `010#7` 0,85 s, `010#10` 1,31 s | 3 | 512–583 |

Lượt ngắn là tiếng click và tiếng động; năm lượt dài là lệnh thật kéo dài hay dính tiếng sau, bỏ vì cửa sổ 3 s của
Cửa 3 có thể cắt mất đầu câu. Ba lượt giữ mà không lời nhắc nào khớp cũng nhỏ hơn mọi câu thật: log-mel trung bình
−12,4 so với −9,7 tới −11,8. Chúng là tiếng động, nhãn lời nhắc sai, nên `split.board.noise` bỏ chúng khỏi tập học. Câu thật mỗi phiên: 10, 11, 11, 11, 11, 10, 9, 10, 11, 11. Chấm giữa mười lời nhắc của các phiên, mạng cuối của run
v3 nghe 36 trên 41 câu "tắt" thành "bật" cùng vật.

## 12. Âm tiết đầu của lệnh: mạng nguội ở đầu cửa sổ, cao độ, mức và màu (05/10)

Chung cho mục này: B1 là run `20261005_e1a544b-dirty_742d86` ở bước 44 000 (bề rộng 160, 80 dải). Các mạng thử học từ
đầu 8 000 bước bằng cấu hình của B1, cùng seed, trên VIVOS và Common Voice của `command/v4` mô phỏng lại có giữ mẫu sạch,
cộng 105 lượt `vad` các phiên 04/10 của chủ repo nhân sáu; `val` là `val` của `command/v4` mô phỏng lại. Cửa 3 là phiên
28/09 (112 câu lệnh, 1 m và 3 m), float, lệnh điểm cao nhất không ngưỡng. Đo ngày 05/10 bằng script chẩn đoán chạy một lần.

### 12.1 Nhầm ở đâu

B1 trên Cửa 3 đúng 89/112; sai gần hết là âm tiết đầu của cặp lệnh chung âm tiết sau: "tắt đèn" → "bật đèn" 7, "tắt quạt" →
"bật quạt" 5, "đóng cửa" → "mở cửa" 6 (4 ở 3 m). Chấm riêng từng tín hiệu của âm tiết đầu trên 22 câu "tắt" (tám âm tiết
phụ âm {t, ɓ} × nguyên âm {ă, â} × thanh {sắc, nặng} trước âm tiết sau đã nói; một tín hiệu đúng khi tổ hợp tốt nhất có nó
đúng hơn tổ hợp tốt nhất có nó sai):

| Mạng | Phụ âm đúng | Nguyên âm đúng | Thanh đúng | Cả ba |
|---|---|---|---|---|
| A (cao độ như board) | 11 | 0 | 11 | 0 |
| C (cao độ nhìn sau 0,75 s) | 14 | 0 | 13 | 0 |
| B1 | 17 | 7 | 6 | 2 |

Với 23 câu "bật", cả ba tín hiệu đúng 19–23 ở mọi mạng. Trên `val`, B1 phân biệt ă với â ở vần tắc kém sẵn: ă đúng 85,0%
(293 âm tiết), â đúng 62,6% (270). Kho học có "bật" 1 483 lần, "tắt" 806 lần; vần ất, ật cộng 112 nghìn lần, ắt, ặt 39
nghìn lần.

### 12.2 Ngữ cảnh trước câu: mạng nguội

Cửa sổ của Cửa 3 mở `lead_s` 1,25 s trước bước `vad` đầu; mạng bắt đầu từ bộ đệm rỗng, bộ dò cao độ đặt lại ở đó. Mở sớm hơn
(B1, cả log-mel lẫn cao độ, hay chỉ một trong hai):

| Mở trước câu | 0,30 s | 0,61 s | 1,25 s | 2,00 s | 3,01 s | 4,00 s |
|---|---|---|---|---|---|---|
| cả hai | 88 | 90 | 89 | **98** | 98 | 98 |
| chỉ log-mel (cao độ đặt lại ở 1,25 s) | | | 89 | 95 | 96 | |
| chỉ cao độ | | | 89 | 91 | 89 | |

Ở 2,00 s: "tắt đèn" 7/11, "tắt quạt" 9/11, "đóng cửa" 8/11. A (8 000 bước) không đổi: 87 ở cả 1,25 s và 2,00 s. Trên `val`
(242 câu có ít nhất 1,2 s trước bước `vad` đầu), cắt bớt phần trước câu, thanh đúng ở âm tiết đầu / thứ hai / sau đó:

| Phần trước câu | B1 | A |
|---|---|---|
| như cắt, ≥ 1,2 s | 69,4 / 80,6 / 81,4% | 69,0 / 76,0 / 81,1% |
| 0,61 s | 54,4 / 63,9 / 77,7% | 50,2 / 65,1 / 75,6% |
| 0,30 s | 39,4 / 54,8 / 75,0% | 32,8 / 49,0 / 72,1% |

Mẫu học có từ đầu mẩu tới bước `vad` đầu phân vị 5/25/50/75/95 là 0 / 0,46 / 0,86 / 1,09 / 1,47 s (`val`), 0 / 0,51 /
0,83 / 1,10 / 1,66 s (VIVOS). Cho mạng chạy qua phần trước cửa sổ mà không chấm nó, cửa sổ chấm giữ nguyên (B1):

| Chạy trước cửa sổ | Cửa 3 | Khoảng cách giữ 90% câu đúng | Câu na ná lệnh qua mức ấy | Tiếng ồn qua |
|---|---|---|---|---|
| 0 | 89 | 121 | 0/24 | 0/2 |
| 0,75 s | 96 | 129 | 2/24 | 0/2 |
| 1,74 s | 96 | 136 | 1/24 | 0/2 |
| 2,75 s | 97 | 145 | 1/24 | 0/2 |
| mở cửa sổ 2,00 s và chấm cả phần ấy | 98 | 144 | 3/24 | 0/2 |
| chạy liên tục cả phiên, không đặt lại | 92 | 139 | 1/24 | 0/2 |

Trường nhìn danh nghĩa của B1 khoảng 10 s (tích chập 17, 9, 5, 9 khung ở nhịp 1, 2, 4, 2 cộng bộ trộn 8 khung, sáu lớp nối
tiếp); với bộ đệm rỗng ở đầu, khoảng 2 s đầu của mỗi cửa sổ là trạng thái mạng chưa gặp khi chạy ổn định.

### 12.3 Cao độ

| Mạng | UER `val` | Thanh đúng âm tiết đầu / sau | Sắc/nặng vần tắc âm tiết đầu | Cửa 3 | "tắt" / "bật" |
|---|---|---|---|---|---|
| A: như board | 0,420 | 70,8 / 80,4% | 81,4% | 87 | 4/22 / 21/23 |
| C: nhìn sau 0,75 s (đường Viterbi cả câu) | 0,426 | 69,7 / 79,8% | 76,0% | 88 | 7/22 / 21/23 |
| D: A cộng log F0 thô, 84 chiều | 0,438 | 72,7 / 80,7% | 77,8% | 78 | 5/22 / 21/23 |

Giữ ba chiều cao độ ở trung bình lúc học: A còn 46,7 / 50,5%, C 35,8 / 46,8% thanh đúng; mạng dựa nhiều vào cao độ. Mức
log F0 chuẩn hoá trung bình 15 bước đầu của âm tiết đầu, cửa sổ mở 1,25 s, bộ dò viết lại theo `normalization-right-context`
của Kaldi (khớp Kaldi chạy dòng qua kalpy trên 12 câu VIVOS test: không khung nào lệch quá 1e-3, lệch lớn nhất 2,0e-4 POV,
1,8e-5 log F0 chuẩn hoá):

| Trái / phải | "bật" 04/10 · 28/09 1 m · 3 m | "tắt" 04/10 · 28/09 1 m · 3 m |
|---|---|---|
| 0,75 / 0 s (board) | +0,76 · +0,15 · +0,20 | +1,03 · +0,37 · +0,24 |
| 0,75 / 0,4 s | +0,41 · +0,12 · +0,15 | +0,64 · +0,36 · +0,31 |
| 0,75 / 0,75 s | +0,23 · +0,12 · +0,21 | +0,49 · +0,44 · +0,40 |
| 0,4 / 0,4 s | +0,11 · +0,08 · +0,10 | +0,23 · +0,29 · +0,24 |

NCCF của nền trước câu, trung vị mỗi câu, phân vị 10/50/90: mô phỏng `val` 0,23 / 0,34 / 0,68; 28/09 0,24–0,29 / 0,30 /
0,33–0,47; 04/10 (có quạt) 0,53 / 0,58 / 0,61.

### 12.4 Mức và màu

Mỗi câu, trên các bước `vad`: mức trước `agc`, độ lợi `agc`, log-mel trung bình sau chuỗi; trung vị (phân vị 10–90):

| | Mức trước `agc` | Độ lợi `agc` | log-mel sau chuỗi |
|---|---|---|---|
| mô phỏng `val` | −61 (−71…−49) dBFS | +21 (+6…+30) dB | −9,62 (−12,67…−8,34) |
| 28/09, 1 m | −57 (−60…−55) | +7 (+1…+12) | −11,59 (−12,09…−10,95) |
| 28/09, 3 m | −57 | +6 (+1…+10) | −11,87 (−12,21…−11,35) |
| 04/10, 80 cm | −52 (−69…−49) | +5 (+3…+8) | −10,86 (−12,43…−10,30) |

`agc` chỉ đổi độ lợi khi có tiếng, 3 dB/s: phiên mô phỏng toàn câu dài nối nhau nên nó leo gần đích, phiên lệnh thưa và
chuỗi chạy mới mỗi phiên nên nó còn thấp. Dời log-mel 28/09 từng dải bằng phổ dài hạn của phiên khác, B1 "tắt" đúng /
nguyên âm đúng: như thu 10 / 7; về phổ 04/10 (phiên trong tập học của B1): cả mức lẫn màu 17 / 20, chỉ mức 14 / 10, chỉ
màu 14 / 15; về phổ trung bình của mô phỏng `val`: cả hai 14 / 18, chỉ mức 15 / 13, chỉ màu 1 / 4. Cùng phép dời, A và C
phản ứng khác hẳn (A 3–5, C 11–17), nên phép dời chỉ cho thấy mạng nhạy với mức và màu. Cho `agc` xuất phát ở +12, +21,
+30 dB thay 0 dB, B1 "tắt" còn 7/22 thay 10/22. N (log-mel trừ độ lợi `agc`, 8 000 bước): UER `val` 0,411, thanh đúng
69,4 / 81,2%, "tắt" 0/22, "bật" 21/23.

### 12.5 Các nhánh thử, 8 000 bước

Cùng dữ liệu, seed và số bước như A (§12 đầu mục); "tắt" / "bật" là 22 / 23 câu 28/09.

| Bản | Khác A ở | UER `val` | Thanh đúng âm tiết đầu / sau | Sắc/nặng vần tắc âm tiết đầu | Cửa 3 | "tắt" / "bật" |
|---|---|---|---|---|---|---|
| A | — | 0,420 | 70,8 / 80,4% | 81,4% | 87 | 4 / 21 |
| C | log F0 chuẩn hoá ±0,75 s trên đường Viterbi cả câu | 0,426 | 69,7 / 79,8% | 76,0% | 88 | 7 / 21 |
| D | thêm log F0 thô, 84 chiều | 0,438 | 72,7 / 80,7% | 77,8% | 78 | 5 / 21 |
| N | log-mel trừ độ lợi `agc` | 0,411 | 69,4 / 81,2% | 76,6% | 80 | 0 / 21 |
| K | màu mượt ngẫu nhiên ±4 dB mỗi câu (nút mỗi 16 dải) | 0,416 | 70,1 / 80,7% | 80,8% | 83 | 0 / 21 |
| S | nhân theo chiều sâu 5, 3, 3, 3 và bộ trộn 2 khung: trường nhìn danh nghĩa ~2,6 s thay ~10 s | 0,411 | 72,1 / 80,7% | 79,0% | 81 | 0 / 21 |
| G | mức ngẫu nhiên ±6 dB mỗi câu | 0,445 | 69,6 / 80,3% | 78,4% | 82 | 8 / 21 |

Ở cỡ này hai lượt học cùng cấu hình khác nhau cỡ vài câu Cửa 3; không nhánh nào hơn A rõ. S cắt bớt phần trước câu
trên `val` như §12.2: thanh âm tiết đầu 69,4 / 49,8 / 34,9% ở ≥ 1,2 / 0,61 / 0,30 s, như A: trường nhìn ngắn không
làm mạng ấm nhanh hơn.

### 12.6 Cắt như board: pilot trên `val`

`val` của split `v4` qua đường mô phỏng với luật cắt của KẾ HOẠCH §1.2 bước 4 (lead 1,25 s lúc ấy) và độ lợi `agc`
đầu phiên rút ngẫu nhiên; so với `val` cắt theo mẩu cộng 0,3 s.

| | Cắt như board | Cắt theo mẩu |
|---|---|---|
| Mẫu / mẩu | 1 371 / 1 583: 86 cửa sổ gộp mẩu, 45 mẩu `vad` không chạm | 1 546 / 1 583 |
| Trước bước `vad` đầu, s, phân vị 5/25/50/75/95 | 0,48 / 1,25 / 1,25 / 1,25 / 1,25 | 0 / 0,32 / 0,83 / 1,08 / 1,47 |
| Sau bước `vad` cuối | 0,02 s | 0,19–1,62 s |
| Độ lợi `agc` lúc có tiếng, dB | 9 / 16 / 22 / 28 / 30 | 3 / 13 / 21 / 29 / 30 |
| Mức lời nói sau `agc`, dBFS | −58 / −44 / −36 / −31 / −28 | −64 / −46 / −36 / −31 / −28 |

Phiên 28/09 qua chuỗi mới chạy cho lời nói khoảng −51 dBFS sau `agc`; board đã nghe người dùng một lúc đưa lời nói về
đích −26 dBFS.

Bản dựng `command/v5` thật (lead 2,0 s, cao độ liền cả phiên), `val`, dựng 05/10: 1 371 mẫu từ 1 583 mẩu, 86 cửa sổ gộp
mẩu, 45 mẩu `vad` không chạm. Trước bước `vad` đầu, phân vị 5/25/50/75/95: 0,51 / 1,26 / 2,00 / 2,00 / 2,00 s; ngắn hơn
2,0 s khi câu trước chặn, như board chặn. Sau bước `vad` cuối 0,02 s. Độ lợi `agc` lúc có tiếng 9 / 16 / 22 / 28 / 30 dB.
Log-mel trung bình trên các bước `vad` −12,56 / −11,07 / −9,71 / −9,06 / −8,23; phiên thật ở §12.4 có trung vị −11,87 tới
−10,86.

Cắt trên `vad` nào. 198 phiên `val` của `command/v5` dựng lại từng phiên, luật cửa sổ board trên hai `vad`: của chuỗi trên
tín hiệu thu, có nhiễu, như board; và tham chiếu, cùng bộ dò chạy trên tiếng người nói ở micro 0, qua phòng, chưa cộng
nhiễu, đưa về −26 dBFS. Tiếng ngoài cửa sổ là bước của mẩu khô trên đỉnh mẩu trừ 30 dB:

| | `vad` có nhiễu | `vad` tham chiếu |
|---|---|---|
| Mẫu / mẩu | 1 371 / 1 583 | 1 568 / 1 583 |
| Mẩu không chạm | 45 (2,8%) | 0 |
| Cửa sổ gộp mẩu | 6,3% | 1,0% |
| Dài, s, phân vị 50 / 95 / 99 | 4,85 / 10,45 / 24,44 | 5,15 / 8,69 / 10,12 |
| Dài hơn 12 s (`train.max_s`) | 50 | 6 |
| Đủ 2,0 s trước câu | 54,6% | 33,0% |
| Tiếng sau điểm kết, hơn 5 / 15 / 30 bước | 19,4 / 13,7 / 8,8% | 4,3 / 2,4 / 0,7% |
| Như trên, chỉ phiên không nhiễu | 19,5 / 12,5 / 6,6% | 4,5 / 1,5 / 0,7% |

36 cửa sổ của `vad` tham chiếu còn tiếng sau điểm kết quá 15 bước đều chỉ ở −22,5 … −30 dB dưới đỉnh mẩu, không bước nào
trên −20 dB: hơi thở, nền bản ghi. Ở cửa sổ một mẩu, điểm kết của `vad` có nhiễu trừ của tham chiếu, phân vị 5/25/50/75/95:
−87 / −21 / −9 / −3 / +36 bước; chỉ phiên không nhiễu −57 / −20 / −8 / −2 / +1, sớm hơn quá 5 bước ở 58,5% cửa sổ. `vad`
đọc mức trước `agc`, mà lời nói trên board B ở −57 … −52 dBFS (§12.4), nên tiếng nhỏ cuối câu không qua mô hình của nó.

Bản dựng `command/v5` cắt trên `vad` có nhiễu (dừng ở VLSP 56/218): mẫu có ít khung CTC hơn số nhãn cần (đơn vị cộng số lần
lặp liền) ở `val` 4, VIVOS 18, FPT 28, VLSP 111 trên 11 417; mẫu dài hơn 12 s ở VIVOS 447 (5,0%), FPT 814 (3,9%), VLSP
1 441 (12,6%). Mẩu `lang_vi` không đọc được, bỏ lúc học như ở mọi bản dựng trước: BUD500 161 / 649 010, Common Voice
417 / 18 502 (2,3%), FPT 2 777 / 25 432 (10,9%), VIVOS 33 / 10 266, VLSP 6 268 / 55 687 (11,3%), `val` 3 / 1 583.

Cửa sổ trên board kéo thêm sau bước `vad` cuối, model `20261004_acde692` (bề rộng 128, học cắt theo mẩu), Cửa 3 float,
mở 2,0 s: thêm 0 / 8 / 16 / 25 bước được 86 / 87 / 87 / 83 trên 112; ở 25 bước "đóng cửa" từ 11 xuống 8.

Phiên thật trên board B. Cửa 3 (172 cửa sổ): trước bước `vad` đầu, phân vị 5/25/50/75, 1,20 / 1,52 / 1,70 / 2,00 s, đủ
2,0 s 29%; khoảng từ bước `vad` cuối của câu tới bước đầu câu sau 1,22 / 1,48 / 1,68 / 2,00 / 2,54 s (phân vị 5/25/50/75/95).
Phiên học 04/10 (128 cửa sổ): 0,53 / 1,26 / 1,85 / 2,00 s, đủ 39%; khoảng 0,68 / 1,30 / 1,89 / 2,22 / 2,55 s.

Pilot 20 phiên đầu mỗi file split, cắt trên `vad` tham chiếu. Quãng nghỉ giữa hai mẩu 0,4–2,0 s: phần trước câu ở BUD500
trung vị 1,06 s, đủ 2,0 s 17%, 18% cửa sổ gộp mẩu. Quãng nghỉ 1,2–3,0 s: BUD500 trung vị 1,80 s, đủ 41% (Common Voice 79%,
FPT 58%, VIVOS 79%, VLSP 56%, `val` 85%, `test` 88%); không cửa sổ gộp, không mẩu nào không chạm, không mẫu thiếu khung CTC;
mọi phiên dựng lại khớp từng mẫu đã lưu. Tiếng sau điểm kết quá 15 bước: `test`, BUD500 0; `val`, Common Voice, VLSP 0,6%;
FPT 1,9%; VIVOS 7,5%, cả 11 cửa sổ chỉ ở −26,5 … −30 dB dưới đỉnh, nền bản ghi của một người nói. Một mẩu FPT to đều cả
mẩu có 0,65 s cuối `vad` không gọi là tiếng.

### 12.7 Độ trễ quyết định ước từ chi phí trên board

172 cửa sổ của Cửa 3 (28/09), chi phí đo trên board B của mạng bề rộng 160 (81,4 ms mỗi khối 16 bước,
`latency.md` §18) và cao độ (1,98 ms mỗi bước, `pitch.md` §3), phép chấm 15 ms; công việc bắt đầu ở bước `vad` đầu;
cao độ tính liên tục thì mỗi bước tới vẫn tốn 1,98 ms của nhân 0 trong lúc đuổi kịp. Chậm sau bước chốt 🔬:

| Mở trước câu | Cao độ | Trung vị | p90 | Lớn nhất |
|---|---|---|---|---|
| 1,25 s | theo cửa sổ | 75 ms | 114 ms | 121 ms |
| 1,25 s | liên tục | 64 ms | 96 ms | 102 ms |
| 2,0 s | theo cửa sổ | 86 ms | 130 ms | 327 ms |
| 2,0 s | liên tục | 61 ms | 96 ms | 143 ms |

### 12.8 Đọc lại

- Âm tiết đầu yếu ở mọi người nói và mọi lệnh vì mạng bắt đầu mỗi cửa sổ từ bộ đệm rỗng mà trí nhớ của nó dài khoảng 2 s
  hữu hiệu; đó là phần lớn nhất của lỗi "tắt" và "đóng cửa" trên B1.
- Cao độ không nhìn sau làm mức âm tiết đầu dịch theo phiên tới 0,6–0,8, lớn hơn khoảng cách "tắt"–"bật"; nhìn sau bỏ
  được độ dịch ấy nhưng ở mạng 8 000 bước chỉ thêm một câu Cửa 3.
- Mức và màu sau chuỗi của phiên thật khác mô phỏng: `agc` theo lịch sử, đáp tuyến micro và vỏ coi là phẳng.
- Ballast của bộ dò cao độ chia NCCF theo năng lượng trung bình của dòng từ lần đặt lại. Cao độ chạy liền thì trên board
  dòng ấy là cả giờ phòng yên, lúc học và ở Cửa 3 là một phiên: cho 300 s các bước im của chính phiên chạy trước, model
  `20261004_acde692` trên Cửa 3 từ 86 xuống 82/112; log cao độ chuẩn hoá trên bước `vad` lệch p50/p90/p99 0,07 / 1,14 /
  2,25, 42% bước lệch quá 0,1; POV lệch p90 0,07. Ballast trên 2,0 s gần nhất (`listen.yaml` v5): cùng model 85/112 khi
  bộ dò đặt lại đầu phiên, 86/112 khi 300 s phòng yên chạy trước; ba chiều trên bước `vad` lệch p90 0, p99 0,10 ở log
  cao độ, 1% bước lệch quá 0,1.
- `vad` của board đọc mức trước `agc`, nên với người nói nhỏ hay xa nó tắt khi tiếng chưa hết: mẫu học cắt trên nó mất lời
  mà nhãn đòi, cắt trên `vad` của riêng tiếng người nói thì không (§12.6). Kéo cửa sổ board thêm sau `vad` không giúp Cửa 3.

### 12.9 Cửa 3 với chuỗi chạy liền cả buổi thu

Board chạy chuỗi và bộ dò cao độ liền từ lúc bật; Cửa 3 trước `2120246` dựng chuỗi mới mỗi phiên. Cùng model
`20261004_acde692` (bề rộng 128), float, ballast cao độ 2,0 s, lệnh điểm cao nhất không ngưỡng, các phiên được đếm của
28/09 và 01/10; "liền" là một chuỗi qua mọi phiên của buổi thu (các dòng manifest liền nhau cùng board, firmware,
`pcm_shift`, kể cả phiên không đếm). Đo 05/10 bằng script chẩn đoán chạy một lần.

| Chuỗi | Cửa sổ lệnh | Lệnh điểm cao nhất đúng | Độ lợi `agc` trung bình trên bước `vad`, phiên lệnh 28/09 / 01/10 |
|---|---|---|---|
| mới mỗi phiên | 112 | 85 | +5,5 … +9,4 dB / +1,6 … +7,0 dB |
| liền cả buổi | 130 | 88 | +19,8 … +26,2 dB / +5,7 … +12,2 dB |

Chạy liền, `agc` vào mỗi phiên lệnh với độ lợi phiên trước để lại, nên lời nói ra cao hơn chừng 15 dB; mô phỏng
`command/v5` rút độ lợi đầu phiên ngẫu nhiên, trung vị +22 dB lúc có tiếng (§12.6), tức đúng vùng này. `vad` sau nhiều
phiên của cùng người nói cắt câu dài hơn 10–15 bước, và ở 12/30 phiên lệnh bắt thêm 1–3 đoạn ngắn 17–36 bước giữa hai
câu (hơi thở, tiếng động) hay tách một câu làm hai: 18 cửa sổ thừa, Cửa 3 cũ tính chúng là câu lệnh. Từ đây Cửa 3 chạy
liền, và trong phiên lệnh cửa sổ không chồng câu nào chuỗi tìm thấy khi chạy riêng phiên ấy từ đầu là cửa sổ thừa, phải
bị từ chối như tiếng động (`32d8a89`). Với nhãn ấy, cùng model: câu lệnh đúng 85/112, như chuỗi mới mỗi phiên; cả 18
cửa sổ thừa có lệnh điểm cao nhất là "mở cửa", điểm 684–819‰, hơn lệnh nhì 12–36‰, cách vòng tự do 175–379‰, nên
`δ₂` 50 từ chối hết ở mọi `δ₁`.

Shard board của tập học (10 phiên 04/10) cắt bằng chuỗi chạy liền giữ cửa sổ chứa đúng một câu của chuỗi chạy riêng
phiên, cả hai dài 0,8–2,4 s: 105 câu, đúng 105 câu thật của §11; 10 cửa sổ ngoài khoảng dài (có 5 câu §11 bỏ vì dài,
chuỗi liền cắt ngắn lại nên chỉ xét độ dài cửa sổ thì chúng lọt vào), 2 không chứa câu nào; ba lượt tiếng động của §11
không còn thành cửa sổ riêng.

`vad` của chuỗi so với `vad` của riêng tiếng người nói (§12.6) trên 198 phiên `val` mô phỏng của `command/v5`, 1 609
câu của người nói: chuỗi mới mỗi phiên không chạm 68 câu (4,2%); chuỗi đã nghe chính phiên ấy một lượt rồi mới chấm
lượt thứ hai không chạm 77 câu (4,8%). Quen phòng và người nói không đưa `vad` tới câu nó bỏ sót: chỗ thiếu là mức
(E7-T6), không phải trạng thái.

### 12.10 Kiểm bản dựng `command/v5`

Script chẩn đoán đọc mọi shard đã xong như bộ học sẽ đọc, 05/10 22:50, khi `train_bud500` xong 1 250/2 536 shard. Bảy
split cùng `config`, luật cắt (`cut` listen, `cut_vad` talker), `listen_hash` 0x354b1ae6, cao độ và bộ phòng; khác nhau
đúng chỗ định: split học lưu float16, đọc ở tốc độ 0,9 / 1,0 / 1,1 (chia đều, mỗi tốc độ một phần ba), `val` và `test`
float32, không đổi tốc độ. Không split nào có offset đứt, log-mel hay cao độ không hữu hạn, mẩu ngoài split hay sai thứ
tự, tiếng ra ngoài cửa sổ; mọi mẩu đủ khung CTC cho đơn vị của nó. Log-mel −13,82 … 5,64 (sàn ln 10⁻⁶ = −13,82). Cửa sổ
cắt trên `vad` của người nói không gộp mẩu nào.

| Split | Cửa sổ | Trung vị, s | Đủ 2,0 s trước câu | Dài hơn `train.max_s` | Không ra đơn vị | Không bước `vad` chuỗi nào |
|---|---|---|---|---|---|---|
| `val` | 1 583 | 5,49 | 75,2% | 2 | 3 | 37 (2,3%) |
| `test` | 2 000 | 5,01 | 74,7% | 1 | 36 | 45 (2,3%) |
| `train_bud500`, 1 250 shard | 320 000 | 4,58 | 41,5% | 0 | 71 | 17 730 (5,5%) |
| `train_vlsp` | 55 687 | 7,31 | 53,4% | 5 065 | 6 268 | 757 (1,4%) |
| `train_fpt_open` | 25 432 | 5,63 | 61,7% | 262 | 2 777 | 512 (2,0%) |
| `train_common_voice_vi` | 18 502 | 5,14 | 83,5% | 14 | 417 | 626 (3,4%) |
| `train_vivos` | 10 266 | 5,63 | 70,6% | 161 | 33 | 52 (0,5%) |

Trước câu ngắn hơn 2,0 s khi cửa sổ trước chặn, như board. Cửa sổ dài hơn `train.max_s` và mẩu không ra đơn vị bộ học bỏ
qua (`load_role`, `shards_of`). Cửa sổ không có bước `vad` nào của chuỗi là câu
board sẽ không cắt (E7-T6, `afe/vad.md` §6); bộ học vẫn học chúng vì cửa sổ cắt trên `vad` của người nói.

### 12.11 Cửa 3 của `command/v5` ở bước 70 000

Run `20261006_7e65d01-dirty_2897b3` dừng ở bước 70 129 theo lời chủ repo; mạng float đã lưu ở bước 70 000, chấm như Cửa 3
(`make command-eval`) trên phiên 28/09 ở 1 m và 3 m, không ngưỡng: ô là số câu có lệnh điểm cao nhất đúng. Cột cuối là bộ
lệnh mặc định với "mở đèn", "mở quạt" thay "bật đèn", "bật quạt".

| Lệnh | CTC | RNN-T | CTC, "mở" thay "bật" |
|---|---|---|---|
| bật đèn / bật quạt | 10/12 / 11/11 | 10/12 / 11/11 | — |
| tắt đèn / tắt quạt | 0/11 / 0/11 | 0/11 / 0/11 | 10/11 / 8/11 |
| mở cửa / đóng cửa | 10/10 / 5/11 | 10/10 / 6/11 | 10/10 / 5/11 |
| tăng / giảm âm lượng | 10/13 / 11/11 | 8/13 / 11/11 | 10/13 / 11/11 |
| dừng lại / chụp ảnh | 11/11 / 10/11 | 11/11 / 11/11 | 11/11 / 10/11 |
| tổng, bộ mặc định | 78/112 | 78/112 | — |

Có ngưỡng (δ₂ 50 ‰, δ₁ 200 … 1 000 ‰): lệnh nhận đúng 45% (CTC), 22% (RNN-T); từ chối 117/120 và 120/120 cửa sổ tiếng
động, cửa sổ thừa và câu gần âm. Run B1 ở bước 40 000 cùng thước được 88/112, "tắt" có "bật" trong bộ 5/22. Mọi câu "tắt"
sai thành "bật" cùng vật, gần nửa "đóng cửa" thành "mở cửa"; bỏ "bật" khỏi bộ thì "tắt" đúng 18/22: mạng nhận phần sau của
lệnh, âm tiết đầu không đủ để tách. Cao độ chuẩn hoá so âm tiết đầu với nền trước nó (§12.3) vẫn nguyên trong v5.

Cùng thước, cùng phiên, mạng float của hai run cũ (B1 không còn `feature_stats.npz` nên không chấm lại được; con số
88/112 của nó đo sáng 05/10 với Cửa 3 cũ, mỗi phiên một chuỗi mới):

| Lệnh | `20261002_128545c` (đang khoá) | `20261004_acde692`, 100 000 | v5, 70 000 |
|---|---|---|---|
| tắt đèn / tắt quạt | 0/11 / 0/11 | 0/11 / 0/11 | 0/11 / 0/11 |
| đóng cửa | 11/11 | 10/11 | 5/11 |
| tổng | 84/112 | 84/112 | 78/112 |
| nhận đúng có ngưỡng, δ₁ 200 ‰ | 56% | 62% | 45% |

Với chuỗi chạy liền cả buổi thu như board, không mạng nào nhận "tắt" khi có "bật" trong bộ: 5–10/22 của B1 là của thước
cũ. v5 ở bước 70 000 thua hai run đủ bước 6 câu, gần hết ở "đóng cửa" → "mở cửa".

### 12.12 "tắt" trên board: mạng nghe được, bản thu thì không (06/10)

Cùng Cửa 3 hiện tại, cùng bước: run `20261004_acde692` (v4) và v5 ở 40 000 bước được 84 và 83/112 câu đúng, ở 44 000
bước 82 và 79; có ngưỡng (δ₁ 200 ‰) v4 56–58%, v5 37–39%: v5 đoán ngang v4 nhưng biên giữa lệnh nhất và nhì hẹp hơn.

Giọng người thật nói lệnh qua đường mô phỏng board (`val_commands`), v5 ở 70 000 bước: "tắt đèn" 28/29, "đóng cửa" 50/52,
"mở cửa" 56/60, "dừng lại" 60/60, "bật đèn" 6/10; âm tiết đầu của "tắt" ra thanh sắc. Trên phiên 28/09 cả v2, v4, v5 đều
0/22 "tắt": giải tự do âm tiết đầu ra "-ặt" thanh nặng, phụ âm đầu mất hay thành "h", "v", "k".

F0 bằng bộ dò YIN riêng trên micro thô ch0, trung vị âm tiết đầu / sau: 28/09 "tắt đèn" 215 / 136 Hz (tỉ số 1,57, 7
câu), "bật đèn" 142 / 139 Hz (1,02); `val_commands` "tắt đèn" 239 / 193 Hz (1,32, 17 câu), "bật đèn" 205 / 205 Hz
(1,13). Thanh sắc của chủ repo rõ hơn cả các giọng mạng nhận đúng. Ba chiều cao độ mạng nhận, trung bình 15 bước từ bước
`vad` đầu: log F0 chuẩn hoá của âm tiết đầu không tách "tắt" với "bật" ở cả hai nơi (board +0,03…+0,08 so với −0,07…−0,02;
`val_commands` +0,05 so với +0,14), vì nó so với nền ngay trước: mạng đọc thanh âm tiết đầu từ log-mel.

Dời log-mel từng phiên 28/09, từng dải, cho trung bình trên các bước `vad` bằng trung bình của `val` v5: v5 ở 70 000 bước
vẫn 0/22 "tắt", 6/11 "đóng cửa", có ngưỡng 48%. Màu phổ và mức cố định của board không phải nguyên nhân.

Ba cách đưa cửa sổ 28/09 vào mạng, Cửa 3 không ngưỡng / có ngưỡng δ₁ 200 ‰; "tắt" 22 câu:

| Cách | v5, 70 000 | v4 (`acde692`), 100 000 |
|---|---|---|
| như Cửa 3 | 78 / 45%, "tắt" 0 | 84 / 62%, "tắt" 0 |
| log F0 chuẩn hoá theo trung bình log F0 có tiếng của cả phiên (không nhân quả) | 79 / 44%, "tắt" 1 | 85 / 62%, "tắt" 0 |
| cửa sổ mở 0,5 s trước bước `vad` đầu thay 2,0 s | 55 / 10%, "tắt" 0 | 85 / 54%, "tắt" 0 |

Cao độ lấy giọng người nói làm mốc không đưa "tắt" về ở cả hai mạng. v5 học toàn cửa sổ có đủ 2,0 s trước câu nên
phụ thuộc vào đó: còn 0,5 s nó mất 23 câu, v4 (học mẩu cộng 0,3 s) không mất câu nào; lệnh nói sát nhau trên board có
khoảng trước câu ngắn như thế.

Tám phiên bật/tắt của chủ repo ngày 04/10 (80 cm, mười lần mỗi câu; v2, v4 chưa học chúng, v5 học chúng trong shard
board), chấm như Cửa 3 với bộ mặc định cộng "bật/tắt ti vi", "bật/tắt điều hòa"; ô là top-1 đúng / cửa sổ, trong ngoặc
số được nhận ở δ₁ 200 ‰, δ₂ 50 ‰:

| Câu | v2 (`128545c`) | v4 (`acde692`) | v5, 70 000 |
|---|---|---|---|
| tắt đèn | 4/14 (0) | 2/14 (0) | 6/14 (0) |
| tắt quạt | 10/12 (0) | 10/12 (0) | 3/12 (0) |
| tắt ti vi | 6/11 (0) | 4/11 (0) | 10/11 (0) |
| tắt điều hòa | 6/11 (0) | 4/11 (0) | 2/11 (0) |
| bật đèn / quạt / ti vi / điều hòa | 12/12 / 12/12 / 10/11 / 10/10 (43) | 12/12 / 12/12 / 10/11 / 10/10 (42) | 12/12 / 12/12 / 8/11 / 9/10 (0) |

Không mạng nào nhận một câu "tắt" nào của chủ repo qua board khi có ngưỡng; câu sai luôn ra "bật" cùng vật. Phiên 28/09
không phải trường hợp lẻ. v5 học chính các phiên này mà không nhận câu nào, cả "bật": biên của nó trên bản thu board
không qua ngưỡng.

Giọng chủ repo co giãn theo trục tần số (VTLP trên log-mel: mỗi dải lấy phổ ở tần số tâm chia α; ba chiều cao độ giữ vì
là tỉ số), cửa sổ "tắt" / "bật" như Cửa 3, bộ bật/tắt; "tắt" đúng trên 28/09 (22 câu) và 04/10 (48 câu):

| α | v4 (`acde692`), 28/09 / 04/10 | v5, 70 000, 28/09 / 04/10 |
|---|---|---|
| 0,90 | 0 / 13 | 0 / 25 |
| 1,00 | 0 / 20 | 0 / 21 |
| 1,10 | 3 / 34 | 1 / 22 |
| 1,20 | 9 / 41 | 2 / 35 |

"bật" giữ 21/23 và 44–45/45 ở mọi α của v4. Nâng giọng chủ repo lên phía giọng cao hơn đưa "tắt" về gần đủ ở 04/10 (20 → 41
trên 48) mà không lấy mất "bật": giọng trầm của chủ repo nằm ở rìa các giọng mạng đã học. Tăng cường VTLP lúc học, phủ
cả giọng trầm, là hướng sửa cho mọi người nói.

### 12.13 Lượt `command/v6`: khoảng trước câu rút ngẫu nhiên và VTLP (06/10)

Run `20261006_a16c615-dirty_20b454`: split `command/v6` (không Bud500, 102 765 câu, 171,3 giờ cùng shard board), cửa sổ
mở ở khoảng trước câu rút đều trong 0,3–2,0 s, VTLP α rút log-đều trong 0,8–1,2, 40 000 bước; phần còn lại như v5.
`val` trùng byte với v5 nên hai cột val so thẳng được.

Bud500 trong các split: `command/v2` đặt trần 25 giờ (35 070 câu), v3, v4, v5 lấy đủ 461,97 giờ (649 010 câu). Run v2
(`20261002_128545c`) và run `20261004_acde692` (split v3) cùng 84/112 ở Cửa 3 (§12.11): 437 giờ Bud500 thêm vào không
đổi số câu đúng trên board.

Nhịp học 3,26 bước/s, v5 4,46. Phần tăng cường mới chỉ thêm 2,2 ms mỗi lô (7,7 → 9,9 ms, đo trên một shard `train_vlsp`).
Bước chậm hơn vì câu dài hơn: Bud500 là 86% số câu của v5, trung bình 2,56 s lời; câu của v6 trung bình 5,38 s. Trên
shard ấy lô 32 câu rộng 747 bước, câu trung bình ~450 bước sau khi cắt khoảng trước: chừng 40% khung GPU tính là phần đệm.

Một bước học trên GPU (RTX 3050 Laptop, 4 GB; lô 32 câu ngẫu nhiên, CTC cộng RNN-T, phạt luồng, Adam), theo bề rộng lô:

| Bề rộng lô, bước | fp32 | bf16 cho encoder, loss fp32 |
|---|---|---|
| 752 | 287 ms | 260 ms |
| 456 | 205 ms | 199 ms |
| 304 | 128 ms | 123 ms |

bf16 chỉ bớt 3–9%: torchaudio tính loss RNN-T trên fp32 hoặc fp16, mạng nhỏ. Bề rộng lô quyết định thời gian: lô gom
câu cùng độ dài đệm tới câu dài nhất của nhóm thay cho cả vòng.

Cửa 3 (float, không ngưỡng; cột cuối hai ở δ₁ 200 ‰, δ₂ 50 ‰), cạnh v2 chấm lại cùng lúc, được đúng số của §12.11:

| Mạng | val loss / UER | Câu đúng | "tắt" | "bật" | "đóng cửa" | Nhận đúng có ngưỡng | Từ chối |
|---|---|---|---|---|---|---|---|
| v2 (`128545c`, đang khoá) | — | 84/112 | 0/22 | 21/23 | 11/11 | 56% | 117/120 |
| v5, bước 4 000 | 3,351 / 0,952 | — | — | — | — | — | — |
| v5, bước 12 000 | — | 13/112 | 0/22 | 0/23 | 0/11 | 0% | 120/120 |
| v6, bước 4 000 | 2,151 / 0,630 | 54/112 | 17/22 | 7/23 | 3/11 | 0% | 120/120 |
| v6, bước 6 000 | 1,824 / 0,558 | 92/112 | 16/22 | 21/23 | 5/11 | 27% | 119/120 |
| v6, bước 8 000 | 1,685 / 0,507 | 86/112 | 8/22 | 21/23 | 6/11 | 36% | 117/120 |

Từ bước 6 000 v6 nhận "tắt" của chủ repo trên phiên 28/09 mà vẫn giữ "bật": lần đầu một mạng tách được hai lệnh ấy
qua board. "tắt" còn dao động giữa các checkpoint lúc tốc độ học còn cao.

Run dừng ở bước 10 970 theo lời chủ repo, khi "tắt" đi xuống ba checkpoint liền. Ở bước 10 000: 88/112 câu đúng, "tắt"
7/22, "bật" 21/23, "đóng cửa" 7/11, có ngưỡng 41%, từ chối 119/120; val 1,595 / 0,475. Từ bước 6 000 tới 10 000,
lệnh nhất và lệnh nhì của gần mọi câu "tắt" cách nhau 0–50 ‰ (v2: "bật" hơn "tắt" 50–200 ‰): mạng đứng ở ranh giới hai
lệnh, mỗi checkpoint lật một phần.

Giọng chủ repo co giãn theo trục tần số như §12.12, v6 ở bước 10 000, bộ bật/tắt:

| α | 28/09 "tắt" / "bật" | 04/10 "tắt" / "bật" |
|---|---|---|
| 0,90 | 9/22 / 21/23 | 45/48 / 44/45 |
| 1,00 | 7/22 / 21/23 | 45/48 / 44/45 |
| 1,10 | 8/22 / 21/23 | 45/48 / 44/45 |
| 1,20 | 13/22 / 21/23 | 45/48 / 44/45 |
| 1,30 | 11/22 / 21/23 | 45/48 / 44/45 |

Phiên 04/10 nằm trong tập học (shard board); v6 nhận "tắt" ở đó ở mọi α, v5 học cùng shard chỉ đúng 21/48. Phiên 28/09,
không học, gần như không đổi theo α: giọng trầm không còn là chỗ thiếu chính, mở rộng `train.augment.vtlp` không sửa
được 28/09. Mạng mang giọng chủ repo của ngày, phòng, khoảng cách đã học, chưa mang sang ngày khác; ở 28/09 phiên 1 m
và 3 m hỏng như nhau.

### 12.14 Lượt `command/v7`: thêm giọng thật (07/10)

Run `20261007_d625146-dirty_21fd14`: split `command/v7`, tập học của v6 cộng LSVSC (100,63 giờ), Speech-MASSIVE (5,29
giờ) và đoạn ViMD (97,02 giờ), 204 140 câu, 392,3 giờ cùng shard board; công thức của v6 (§12.13) cộng lô gom theo 16
nhóm độ dài; 40 000 bước, 01:29–05:13. `val` trùng byte với v5, v6.

Cửa 3 (float, không ngưỡng; hai cột cuối ở δ₁ 200 ‰, δ₂ 50 ‰), v2 cùng thước: 84/112, "tắt" 0/22, "bật" 21/23, "đóng
cửa" 11/11, 56%, 117/120:

| Bước | val loss / UER | Câu đúng | "tắt" | "bật" | "đóng cửa" | Nhận đúng có ngưỡng | Từ chối |
|---|---|---|---|---|---|---|---|
| 2 000 | 2,535 / 0,735 | 68/112 | 7/22 | 21/23 | 5/11 | 17% | 120/120 |
| 4 000 | 1,933 / 0,584 | 97/112 | 19/22 | 21/23 | 10/11 | 31% | 118/120 |
| 6 000 | 1,750 / 0,536 | 88/112 | 8/22 | 21/23 | 7/11 | 37% | 116/120 |
| 8 000 | 1,616 / 0,498 | 86/112 | 9/22 | 21/23 | 7/11 | 49% | 116/120 |
| 10 000 | 1,525 / 0,471 | 98/112 | 13/22 | 21/23 | 10/11 | 56% | 118/120 |
| 12 000 | 1,515 / 0,454 | 98/112 | 15/22 | 21/23 | 9/11 | 54% | 118/120 |
| 14 000 | 1,435 / 0,436 | 97/112 | 16/22 | 21/23 | 7/11 | 63% | 116/120 |
| 16 000 | 1,386 / 0,421 | 91/112 | 8/22 | 21/23 | 10/11 | 51% | 118/120 |
| 18 000 | 1,359 / 0,419 | 91/112 | 5/22 | 21/23 | 11/11 | 51% | 118/120 |
| 20 000 | 1,275 / 0,393 | 84/112 | 0/22 | 21/23 | 11/11 | 55% | 118/120 |
| 22 000 | 1,253 / 0,383 | 88/112 | 2/22 | 21/23 | 11/11 | 54% | 118/120 |
| 24 000 | 1,244 / 0,380 | 88/112 | 3/22 | 21/23 | 11/11 | 53% | 117/120 |
| 26 000 | 1,177 / 0,363 | 86/112 | 2/22 | 21/23 | 10/11 | 55% | 116/120 |
| 28 000 | 1,151 / 0,353 | 85/112 | 1/22 | 21/23 | 11/11 | 54% | 119/120 |
| 30 000 | 1,139 / 0,353 | 90/112 | 6/22 | 21/23 | 11/11 | 50% | 117/120 |
| 32 000 | 1,110 / 0,342 | 87/112 | 2/22 | 21/23 | 11/11 | 54% | 118/120 |
| 34 000 | 1,094 / 0,337 | 85/112 | 0/22 | 21/23 | 11/11 | 56% | 118/120 |
| 36 000 | 1,101 / 0,341 | 85/112 | 0/22 | 21/23 | 11/11 | 54% | 118/120 |
| 38 000 | 1,088 / 0,338 | 85/112 | 0/22 | 21/23 | 11/11 | 54% | 118/120 |
| 40 000 | 1,079 / 0,335 | 86/112 | 1/22 | 21/23 | 11/11 | 55% | 118/120 |

Giọng chủ repo, bộ bật/tắt như §12.12, bước 40 000: phiên 04/10 (trong tập học) "tắt" 45/48, "bật" 44/45; phiên 28/09
"tắt" 1/22, "bật" 21/23; co giọng α 1,2 thì 28/09 "tắt" 2/22, 04/10 không đổi.

"tắt" của 28/09 lên sớm (19/22 ở bước 4 000), rồi đi về 0–3 khi `val` còn giảm: mạng học tiếng Việt càng khớp càng
nghe "tắt" của chủ repo ở phiên ấy thành "bật". Ba thứ đã thử không đổi điểm đến ấy: thêm 220 giờ giọng thật, VTLP, và
chính các phiên 04/10 của chủ repo trong tập học. Cuối lượt v7 ngang v2 trên Cửa 3 (86 so với 84 câu đúng, 55% so với
56% có ngưỡng). Nhịp học 2 000 bước mỗi ~11 phút, không nhanh hơn v6: đổi nhịp 0,8–1,6 lần sau khi gom nhóm kéo câu dài
thêm tới 25%, lô đệm tới câu kéo dài nhất của nó, và câu của v7 dài hơn (trung bình 6,9 s so với 6,0 s).

### 12.15 Mức thu của phiên 28/09 so với 04/10 (07/10)

Câu "tắt"/"bật" của chủ repo, chuỗi chạy liền cả buổi thu như Cửa 3, trung vị theo câu: mức nền và mức tiếng (phân vị 90)
của micro 0 thô, độ lợi `agc` lúc tiếng bắt đầu, và độ lợi của chuỗi ở 4 bước đầu tiếng so với bước 8–20:

| Buổi | Từ | Câu | Nền thô | Tiếng thô | `agc` | Chuỗi ở đầu tiếng |
|---|---|---|---|---|---|---|
| 04/10, 80 cm | "bật" / "tắt" | 45 / 44 | −53 dBFS | −29 / −31 dBFS | +7 / +8 dB | −8 / −6 dB |
| 28/09, 1 m | "bật" / "tắt" | 12 / 10 | −63 dBFS | −43 / −45 dBFS | +22 / +23 dB | −3 / −9 dB |
| 28/09, 3 m | "bật" / "tắt" | 10 / 11 | −63 dBFS | −45 / −46 dBFS | +24 / +23 dB | −3 / −7 dB |

Tiếng 28/09 ở micro nhỏ hơn 04/10 chừng 15 dB, nền nhỏ hơn 10 dB (firmware thu `368` và `868`, cùng `pcm_shift` 13).
Đổi mức tiếng thô trước chuỗi, v7 ở bước 40 000, bộ bật/tắt, đúng (nhận ở δ₁ 200 ‰):

| Buổi | Đổi mức | "tắt" | "bật" |
|---|---|---|---|
| 28/09 | +0 / +6 / +12 / +15 dB | 1/22 (0) ở cả bốn | 21/23 (20) ở cả bốn |
| 04/10 | 0 / −6 / −12 / −15 dB | 45/48, 45/47, 46/46, 46/46 (44) | 44/45, 44/45, 44/46, 44/46 (44) |

Mức thu không đổi quyết định: 28/09 nâng tới mức 04/10 vẫn 1/22 "tắt", 04/10 hạ tới mức 28/09 vẫn đúng. Chỗ khác của
"tắt" 28/09 nằm ở tiếng nói hôm ấy, khoảng cách và vang (1–3 m so với 80 cm) hoặc firmware thu; §12.16 và §12.17 tách
ba thứ ấy.

Âm tiết đầu của chủ repo, micro 0 thô, mốc từ của bộ căn §3.11, F0 theo YIN (khung 40 ms, bước 10 ms), trung vị theo câu
có ít nhất 3 khung có thanh:

| Buổi | Từ | Câu | Dài | F0 | F0 / âm tiết hai |
|---|---|---|---|---|---|
| 04/10, 80 cm | "bật" / "tắt" | 38 / 11 | 150 / 140 ms | 169 / 222 Hz | 1,06 / 1,29 |
| 28/09, 1 m | "bật" / "tắt" | 4 / 5 | 195 / 150 ms | 141 / 213 Hz | 1,01 / 1,52 |
| 28/09, 3 m | "bật" / "tắt" | 3 / 5 | 200 / 130 ms | 144 / 219 Hz | 1,03 / 1,64 |

"tắt" của 28/09 cao và tách khỏi âm tiết sau rõ hơn của 04/10; độ dài như nhau. Cao độ và độ dài hôm ấy không làm
"tắt" giống "bật" hơn; nguyên âm của nó ở §12.17.

### 12.16 "tắt" ở 1 m và 3 m trên firmware thu hiện tại (07/10)

Tám phiên mới của chủ repo (`20261007_home_001`–`008`, `host/plans/tat_far.tsv`): "tắt"/"bật" đèn, quạt ở 1 m và 3 m,
cùng phòng và hướng như 28/09, firmware thu `contract-v1-1008-g62d9ab5`; phiên 6 và 8 nói đổi chỗ nhau nên đổi nhãn,
câu đầu của phiên 8 ("bật quạt" trong phiên "bật đèn") bỏ. Chuỗi liền cả buổi như Cửa 3, bộ bật/tắt; đúng (nhận đúng ở
δ₁ 200 ‰, δ₂ 50 ‰; nhận nhầm là lệnh khác qua ngưỡng):

| Buổi | Mạng | 1 m "tắt" | 3 m "tắt" | 1 m "bật" | 3 m "bật" |
|---|---|---|---|---|---|
| 07/10, firmware hiện tại | v2 (`128545c`) | 2/24 (0; nhầm 5) | 0/23 (0; nhầm 6) | 26/27 (26; 0) | 23/23 (20; 0) |
| 07/10, firmware hiện tại | v7, 40 000 | 20/24 (8; nhầm 0) | 15/23 (2; nhầm 0) | 26/27 (26; 0) | 23/23 (23; 0) |
| 28/09, firmware `368` | v2 (`128545c`) | 0/11 (0; nhầm 4) | 0/11 (0; nhầm 5) | 11/13 (7; 0) | 10/10 (6; 0) |
| 28/09, firmware `368` | v7, 40 000 | 1/11 (0; nhầm 0) | 0/11 (0; nhầm 5) | 11/13 (11; 0) | 10/10 (9; 0) |

Cùng khoảng cách, v7 nghe đúng "tắt" 35/47 câu mới so với 1/22 của 28/09: khoảng cách không phải chỗ thiếu; chỗ khác
còn lại nằm trong bản thu 28/09 (§12.17). v2 trên bản thu mới vẫn 2/47 và nhận nhầm "tắt" thành lệnh khác 11/47 lần; v7 không
nhận nhầm lần nào, nhưng chỉ 10/47 câu "tắt" qua ngưỡng. Mọi câu "tắt" của Cửa 3 (§12.9) là phiên 28/09.

Phổ dài hạn của micro 0 thô, đoạn có tiếng so với dải 500–1000 Hz: 28/09 có 100–250 Hz ở −4,3 / −8,6 dB (1 m / 3 m)
so với −13,1 / −16,1 dB của 07/10, 250–500 Hz −8,1 / −11,0 so với −12,4 / −15,8 dB; nền hai buổi gần như nhau ở dải
trầm, dải cao không bị cắt, hai micro lệch nhau như nhau (−9,4 … −10,0 dB): không có dấu bộ lọc của firmware, tiếng
28/09 nặng trầm hơn 5–9 dB. Chỉnh dải trầm (kệ thấp RBJ ở 500 Hz) trước chuỗi, v7 ở bước 40 000, "tắt" đúng (nhận nhầm):

| Buổi | Kệ thấp | 1 m | 3 m |
|---|---|---|---|
| 28/09 | 0 / −4 / −8 dB | 1/11, 3/11, 5/11 (0) | 0/11 ở cả ba (nhầm 5, 4, 3) |
| 07/10 | +4 / +8 dB | 20/24, 21/24 (0) | 14/23, 13/23 (0) |

Dải trầm của 28/09 giải thích một phần ở 1 m, không giải thích 3 m; thêm trầm cho 07/10 không làm hỏng "tắt".

Cửa 3 từ đây gồm các phiên 07/10 (firmware thu hiện tại), bỏ câu đầu phiên `20261007_home_008`
(`eval.board.left_out_utterances`): 209 câu lệnh, 126 cửa sổ phải từ chối. Float, không ngưỡng; hai cột cuối ở δ₁ 200 ‰:

| Mạng | Câu đúng | "tắt đèn" | "tắt quạt" | "bật đèn" / "bật quạt" | Nhận đúng có ngưỡng | Từ chối |
|---|---|---|---|---|---|---|
| v2 (`128545c`, đang khoá) | 135/209 | 1/35 | 1/34 | 35/37 / 35/36 | 61% | 123/126 |
| v7, 40 000 | 170/209 | 16/35 | 20/34 | 35/37 / 35/36 | 59% | 124/126 |

### 12.17 "tắt" của chủ repo: firmware thu, nguyên âm và thanh (07/10)

Firmware thu của 28/09 (`contract-v1-368-gc786e8f`) và 07/10 (`contract-v1-1008-g62d9ab5`) có cùng đường mẫu:
`git diff c786e8f 62d9ab5` không đổi dòng nào ở `drv_audio`, `bsp_board`, `firmware/test_apps/capture/main` và
`sdkconfig.defaults` của app thu; IDF 6.0.2 ở cả hai; khoá phụ thuộc chỉ thêm `esp-dl` và `esp_new_jpeg`, app thu không
gọi. Chỗ khác của 28/09 nằm trong bản thu hôm ấy.

Âm tiết đầu của chủ repo, micro 0 thô, mốc từ của bộ căn §3.11. Nhân là các khung 10 ms trong 6 dB của khung to nhất
của từ đầu; mức là năng lượng trung bình của nhân; F0 theo YIN như §12.15; F1 theo LPC bậc 12 ở 10 kHz, khung 25 ms mỗi
5 ms, cực trên 200 Hz có băng dưới 400 Hz; trung vị theo câu. F1 đo cả sau kệ thấp RBJ 500 Hz −8 và +8 dB, vì tiếng
28/09 nặng trầm hơn 5–9 dB (§12.16):

| Buổi | Từ | Câu | Mức nhân | F0 âm tiết đầu / hai | F1, kệ −8 / 0 / +8 dB |
|---|---|---|---|---|---|
| 28/09, 1 m | "bật" | 13 | −41,1 dBFS | 141 / 140 Hz | 701 / 688 / 697 Hz |
| 28/09, 1 m | "tắt" | 11 | −40,3 dBFS | 213 / 139 Hz | 758 / 725 / 706 Hz |
| 28/09, 3 m | "bật" | 10 | −43,0 dBFS | 144 / 137 Hz | 695 / 685 / 682 Hz |
| 28/09, 3 m | "tắt" | 11 | −41,3 dBFS | 217 / 135 Hz | 694 / 680 / 657 Hz |
| 04/10, 80 cm | "bật" | 45 | −23,7 dBFS | 169 / 138 Hz | 715 / 709 / 706 Hz |
| 04/10, 80 cm | "tắt" | 48 | −26,9 dBFS | 222 / 148 Hz | 792 / 773 / 753 Hz |
| 07/10, 1 m | "bật" | 27 | −38,1 dBFS | 131 / 126 Hz | 709 / 696 / 681 Hz |
| 07/10, 1 m | "tắt" | 24 | −36,0 dBFS | 200 / 126 Hz | 777 / 766 / 756 Hz |
| 07/10, 3 m | "bật" | 23 | −37,4 dBFS | 131 / 130 Hz | 713 / 699 / 682 Hz |
| 07/10, 3 m | "tắt" | 23 | −34,6 dBFS | 198 / 129 Hz | 775 / 769 / 756 Hz |

Ở 04/10 và 07/10, F1 của "tắt" cao hơn "bật" 60–80 Hz (ă mở hơn â): dấu hiệu phổ duy nhất của nguyên âm, và nhỏ. Cùng
dải trầm (28/09 qua kệ −8 dB, 07/10 như thu), F1 của "tắt" 1 m ngày 28/09 là 758 so với 766 Hz: ở 1 m chỗ khác là dải
trầm, khớp với kệ −8 dB cứu 5/11 câu (§12.16). Ở 3 m F1 của "tắt" 28/09 là 694 Hz, bằng "bật" (695): hôm ấy ở 3 m chủ
repo nói "tắt" với nguyên âm của "bật", và kệ không cứu câu nào. Tiếng 28/09 nhỏ hơn 07/10 3–6 dB ở cùng khoảng cách;
giọng 07/10 còn trầm hơn 28/09 mà vẫn được nhận.

v7 có nghe thanh của âm tiết đầu không: các phiên 07/10, từ đầu của mọi câu được đếm đổi F0 bằng overlap-add của Praat
(giữ formant) trên cả hai kênh thô, rồi chạy như Cửa 3, bộ bật/tắt, v7 ở bước 40 000. Hệ số 1 là đối chứng của chính
phép tổng hợp lại; sau nó chuỗi cắt 1 m "tắt" thành 23 cửa sổ thay 24. Đúng / cửa sổ (nhận đúng ở δ₁ 200 ‰, δ₂ 50 ‰):

| F0 từ đầu | 1 m "tắt" | 3 m "tắt" | 1 m "bật" | 3 m "bật" |
|---|---|---|---|---|
| như thu (§12.16) | 20/24 (8) | 15/23 (2) | 26/27 (26) | 23/23 (23) |
| tổng hợp lại, × 1 | 16/23 (5) | 9/23 (0) | 26/27 (26) | 23/23 (22) |
| "tắt" × 0,65, tới F0 của "bật" | 14/23 (0) | 6/23 (0) | 26/27 (26) | 23/23 (22) |
| "bật" × 1,53, tới F0 của "tắt" | 16/23 (5) | 9/23 (0) | 26/27 (24) | 23/23 (17) |

F0 đo lại sau khi đổi ra đúng × 0,65 và × 1,53 (trung vị). So với đối chứng, hạ F0 của "tắt" xuống mức "bật" lật 5 trên
46 câu; nâng "bật" lên mức "tắt" không lật câu nào trên 50, chỉ hạ biên. Ở ba hàng tổng hợp lại, câu "tắt" sai đều bị
nghe thành "bật" (3 m tổng hợp lại: 14 "bật", 9 "tắt"; hạ F0: 17 "bật", 6 "tắt"), còn "bật" sai một lần thành "mở"; bộ
giải chỉ chấm trên bộ lệnh nên không bao giờ ra "tặt". v7 tách "tắt" với "bật" gần như chỉ bằng phổ của phụ âm đầu và
nguyên âm, không bằng thanh, dù thanh là chỗ hai từ khác nhau nhiều nhất ở giọng chủ repo (200 so với 131 Hz). Phép đổi
này chỉ đổi mức F0, giữ đường đi lên; đổi cả đường nét sang khuôn của "bật" thì v7 lật 27/32 câu "tắt" (§12.20), nên
kết luận ấy sai. Riêng phép tổng hợp lại, chưa đổi F0, đã lấy 4 và 6 câu "tắt" mà không lấy câu "bật" nào: dấu hiệu
mạng dùng cho "tắt" mảnh. Đầu vào cho thấy vì sao: ba chiều cao độ của âm tiết đầu so với nền ngay trước câu, nên không
mang thanh (§12.12).

"tắt" 28/09 hỏng vì mạng chỉ dựa vào nguyên âm, mà nguyên âm ấy hôm 28/09 ở 3 m trùng "bật" và ở 1 m bị dải trầm kéo
xuống; thanh vẫn rõ (213–217 so với 141–144 Hz) nhưng mạng không dùng. Firmware thu, khoảng cách, mức thu và giọng trầm
không phải nguyên nhân.

### 12.18 Cao độ của âm tiết đầu trên board (07/10)

v7 ở bước 40 000 trên `val` (1 578 câu): thanh đúng ở âm tiết đầu 75,8%, ở các âm tiết sau 83,7%; giữ ba chiều cao độ ở
trung bình thì còn 64,4% và 74,8%. Mạng dùng cao độ, và âm tiết đầu vẫn kém hơn 8 điểm như A ngày 05/10 (§12.3).
`make ctc-tone-flip CHECK=places` cho lại đúng các số ấy, cùng lỗi đơn vị trên `val`: 0,335 như mô phỏng, 0,392 khi giữ
cao độ ở trung bình.

Bộ dò của board (Kaldi chạy dòng, `dsp_spec/pitch`) trên từng từ của chủ repo, mốc từ của bộ căn §3.11, so với YIN trên
micro 0 thô (trung vị theo từ). Lỗi là phần khung có POV trên 0,3 mà F0 lệch quá nửa quãng tám khỏi trung vị YIN của từ;
"+0,4 s" là F0 của khung ấy trên đường Viterbi dò lại 0,4 s sau; "thô" là cùng bộ dò chạy trên micro 0 thô:

| Buổi | Từ | YIN âm tiết đầu / hai | Lỗi âm tiết đầu: chuỗi / +0,4 s / thô | Lỗi âm tiết hai | POV âm tiết đầu / hai |
|---|---|---|---|---|---|
| 28/09, 1 m | "bật" | 141 / 138 Hz | 0,05 / 0,00 / 0,10 | 0,05 | 0,43 / 0,61 |
| 28/09, 1 m | "tắt" | 213 / 140 Hz | 0,45 / 0,12 / 0,43 | 0,16 | 0,34 / 0,54 |
| 28/09, 3 m | "bật" | 144 / 137 Hz | 0,50 / 0,40 / 0,50 | 0,12 | 0,51 / 0,63 |
| 28/09, 3 m | "tắt" | 216 / 136 Hz | 0,55 / 0,00 / 0,28 | 0,03 | 0,36 / 0,51 |
| 04/10, 80 cm | "bật" | 162 / 140 Hz | 0,52 / 0,47 / 0,51 | 0,07 | 0,49 / 0,61 |
| 04/10, 80 cm | "tắt" | 221 / 151 Hz | 0,16 / 0,11 / 0,24 | 0,02 | 0,44 / 0,62 |
| 07/10, 1 m | "bật" | 131 / 126 Hz | 0,43 / 0,35 / 0,29 | 0,00 | 0,38 / 0,61 |
| 07/10, 1 m | "tắt" | 200 / 128 Hz | 0,58 / 0,57 / 0,54 | 0,03 | 0,35 / 0,50 |
| 07/10, 3 m | "bật" | 130 / 130 Hz | 0,23 / 0,09 / 0,16 | 0,02 | 0,38 / 0,62 |
| 07/10, 3 m | "tắt" | 198 / 129 Hz | 0,44 / 0,39 / 0,44 | 0,16 | 0,43 / 0,44 |

Bộ dò sai 16–58% khung ở âm tiết đầu và 0–16% ở âm tiết hai; trên micro thô sai như trên đầu ra của chuỗi, nên lỗi nằm
ở bộ dò, không ở chuỗi. Đường Viterbi chạy dòng nối từ nền trước câu (nền phiên `20261007_home_001` dò ra quanh 100 Hz)
vào âm tiết đầu, và "tắt" 200 Hz hay bị dò thành nửa tần số; dò lại 0,4 s sau bớt lỗi ở vài phiên, không phải mọi phiên. Trên `val` (mô
phỏng, nhiều giọng) POV của từ đầu cũng thấp hơn từ hai ở mọi thanh (trung vị 0,40–0,66 so với 0,49–0,76).

Log F0 chuẩn hoá của từ đầu, trung bình theo POV trên các bước của từ, tách hai nhóm được tới đâu (AUC; 0,5 là đoán).
Mốc như board: trung bình theo POV 0,75 s trước; C: 0,75 s trước và sau; "`vad`": chỉ các bước `vad` của chuỗi vào
mốc, đường dò lại ở cuối cửa sổ:

| Nhóm | Như board | C 0,75 / 0,75 s | `vad` 0,75 / 0 s | `vad` 0,75 / 0,4 s | `vad` 0,75 / 0,75 s |
|---|---|---|---|---|---|
| `val`, sắc / nặng vần tắc (82 / 84 câu) | 0,61 | 0,78 | 0,60 | 0,70 | 0,77 |
| chủ repo, "tắt" / "bật", theo buổi | 0,56–0,73 | 0,58–0,82 | 0,53–0,92 | 0,42–0,72 | 0,56–0,89 |

Đổi mốc chỉ nâng `val` từ 0,61 lên chừng 0,78, đúng chỗ C đã đổi ngày 05/10 mà mạng học không khá hơn (§12.3); với
giọng chủ repo không mốc nào tách được, vì giá trị F0 bộ dò đưa vào đã sai. Thanh của âm tiết đầu không đến được mạng
từ bộ dò hiện tại.

Bốn bộ dò trên đầu ra của chuỗi, từng từ theo mốc của bộ căn: F0 của từ là trung vị trên các khung bộ dò ấy gọi là có
tiếng (Kaldi POV trên 0,3; SwiftF0 0.3.0 và PESTO 2.0.1 `mir-1k_g7` độ tin trên 0,5); lỗi thô là F0 của từ lệch quá nửa
quãng tám khỏi mốc (YIN trên micro 0 thô với chủ repo, YIN trên clip gốc trước mô phỏng với `val`, nên cột YIN nghiêng về
phía mốc); đặc trưng thanh là log F0 từ đầu chia từ hai. `val`: các câu có từ đầu vần tắc thanh sắc (74) hay nặng (80):

| Bộ dò | Lỗi thô từ đầu, `val` nặng / sắc | AUC sắc / nặng, `val` (mốc 0,79) | AUC "tắt" / "bật", chủ repo, 5 buổi | Chi phí |
|---|---|---|---|---|
| Kaldi như board | 0,14 / 0,23 | 0,64 | 0,56–0,97 | 2,0 ms mỗi bước trên board |
| Kaldi, đường dò lại 0,4 s sau | 0,04 / 0,16 | 0,67 | 0,88–1,00 | như trên, vòng 25 khung thêm 🔬 |
| YIN | 0,01 / 0,06 | 0,77 | 0,55–1,00 | 🔬 |
| SwiftF0 | 0,04 / 0,08 | 0,78 | 0,80–1,00 | ~9 triệu MAC mỗi khung theo cỡ các lớp, ~0,57 tỉ MAC/s 🔬 |
| PESTO | 0,05 / 0,07 | 0,80 | 0,22–0,65, nhiều câu không khung nào đủ tin | 🔬 |
| SWIPE′ (`pysptk`, ngưỡng 0,3) | chưa đo | chưa đo | 0,22–1,00: 1,00 / 1,00 / 0,95 / 0,83, 07/10 3 m 0,22 | một FFT mỗi octave ứng viên 🔬 |
| SHS (Praat, nén 0,84, 15 hài) | chưa đo | chưa đo | 0,56–1,00 | một FFT 🔬 |

Trên `val`, ba bộ dò từng khung (YIN, SwiftF0, PESTO) đưa thanh của từ đầu về sát mốc 0,79, Kaldi dò lại 0,4 s sau chỉ
0,67. Với chủ repo, SwiftF0 và Kaldi dò lại 0,4 s sau tách "tắt" / "bật" ở mọi buổi (0,80–1,00), YIN hỏng ở hai buổi,
PESTO hỏng trên tiếng board. Mốc của chủ repo ở buổi 04/10 chỉ 0,57: YIN trên micro thô cũng sai ở "bật" thanh nặng (tiếng
kẹt). esp-dl trên board chạy 0,1–0,5 tỉ MAC/s (`latency.md` §10), nên SwiftF0 nguyên cỡ không vừa chip.

### 12.19 Thang int8 của `command/v7` (07/10)

Run `20261007_d625146-dirty_21fd14` ở bước 40 000, `make ctc-ptq`, `make ctc-qat`, `make ctc-int16` (KẾ HOẠCH §3.14: float
chưa đạt Cửa 3 nên đo đủ bốn bậc). Lỗi đơn vị trên 1 963 câu `test`; Cửa 3 gồm các phiên 07/10 (209 câu lệnh, 126 cửa
sổ phải từ chối), nhận ở δ₁ `quant.reject` 300 ‰ và δ₂ 50 ‰:

| Dòng | Lỗi đơn vị | Đúng nhất | Nhận đúng | Nhận nhầm |
|---|---|---|---|---|
| float | 0,367 | 170/209 | 119/209 | 4/126 |
| bậc 2: `minmax` | 0,450 | 172/209 | 109/209 | 2/126 |
| bậc 2: `percentile` | 0,385 | 165/209 | 115/209 | 4/126 |
| bậc 2: `mse` | 0,402 | 176/209 | 122/209 | 2/126 |
| bậc 2: `kl` | 0,395 | 177/209 | 112/209 | 3/126 |
| bậc 4: QAT trên `mse`, 2 000 bước | 0,376 | 162/209 | 114/209 | 2/126 |
| bậc 3: `mse`, int16 1 lớp | 0,402 | 181/209 | 119/209 | 3/126 |
| bậc 3: `mse`, int16 2 lớp | 0,400 | 181/209 | 122/209 | 4/126 |
| bậc 3: `mse`, int16 4 lớp | 0,401 | 176/209 | 119/209 | 3/126 |

QAT hạ lỗi đơn vị `val` từ 0,366 xuống 0,347 trong 2 000 bước nhưng nhận đúng ít hơn `mse` 8 câu. Theo luật chọn của
KẾ HOẠCH §3.14 (`gate_tie` 5), `mse`, int16 1, 2 và 4 lớp hoà ở 119–122 câu; int16 2 lớp có lỗi đơn vị thấp nhất, cần
µs trên board của hai tích chập int16 trước khi chốt.

### 12.20 Phép kiểm lật F0 trên v7 (07/10)

`make ctc-tone-flip CHECK=val` trên v7 ở bước 40 000 (KẾ HOẠCH §3.11). 1 102 câu `val` có âm tiết vần tắc thanh sắc hay
nặng, ở đầu câu hay sau đó, trong cửa sổ chỉ chứa một mẩu; bộ căn đặt mốc được 1 099 câu; Praat bỏ 84 âm tiết đầu và
436 âm tiết sau có tiếng dưới 60% khung hay dưới 50 ms. Mỗi âm tiết mang khuôn F0 của thanh kia ở cùng vị trí (trung vị
theo nửa cung so với trung vị F0 của người nói, 10 điểm), giữ formant; đối chứng là cùng phép tổng hợp lại giữ F0;
194 phiên mô phỏng lại, phiên đầu trùng từng byte bản dựng. Dịch là căn bậc hai trung bình bình phương độ dịch của ba
chiều cao độ trên quãng có tiếng, theo độ lệch chuẩn lúc học; lật là phần âm tiết mà nhãn mang thanh kia được điểm CTC
cao hơn nhãn gốc; điểm dịch là trung vị mức đổi của hiệu điểm ấy, nat:

| Vị trí | Thanh gốc | Âm tiết | Dịch | Lật: đối chứng / sau đổi | Lật thêm | Điểm dịch |
|---|---|---|---|---|---|---|
| đầu | sắc | 32 | 1,67 | 0,00 / 0,44 | +0,44 | +5,3 |
| đầu | nặng | 50 | 1,54 | 0,16 / 1,00 | +0,84 | +6,3 |
| đầu | cả hai | 82 | 1,55 | 0,10 / 0,78 | +0,68 | +5,8 |
| sau | sắc | 309 | 1,10 | 0,01 / 0,88 | +0,87 | +10,2 |
| sau | nặng | 311 | 1,09 | 0,03 / 0,99 | +0,95 | +11,5 |
| sau | cả hai | 620 | 1,09 | 0,02 / 0,93 | +0,91 | +10,9 |

Trên tiếng mô phỏng, đặc trưng Kaldi ở âm tiết đầu dịch theo F0 không kém âm tiết sau, và v7 đổi thanh theo F0 ở 78%
âm tiết đầu so với 93% âm tiết sau: lật thêm 0,68 so với 0,91, quá `follows_share` 0,5. Mạng không học đường tắt. Ở âm
tiết đầu nó dựa vào F0 bằng chừng nửa ở âm tiết sau (điểm dịch 5,8 so với 10,9) và yếu nhất khi sắc mang khuôn nặng
(0,44); còn nặng mang khuôn sắc thì lật cả 50/50, đúng chiều "tắt" cần khi nguyên âm giống "bật". Chỗ hỏng trên board vì
thế là bộ dò: trên bản thu của chủ repo Kaldi sai 16–58% khung ở âm tiết đầu, trên `val` chỉ 14–23% (§12.18). Theo luật
§3.11: học `command/v8` riêng, không hoán đổi thanh, rồi chạy lại phép kiểm trên v8.

`make ctc-tone-flip CHECK=owner`: các phiên "bật" / "tắt" của 28/09 và 07/10, bộ `host/sets/battat_vi.json`, chuỗi chạy
liền như Cửa 3. Từ đầu của mỗi câu, căn mốc trên micro 0 thô, mang khuôn F0 của từ kia lấy trên chính giọng chủ repo
(trung vị theo nửa cung so với trung vị F0 của chủ repo, 10 điểm) ở cả hai micro, giữ formant; Praat bỏ những từ có tiếng
dưới 60% khung, nhiều nhất ở "bật" thanh nặng (tiếng kẹt). Đúng (nhận đúng ở δ₁ 200 ‰, δ₂ 50 ‰):

| Buổi | Từ | Câu qua lọc | Như thu | Tổng hợp lại | Mang khuôn từ kia | Nghe ra khi mang khuôn từ kia |
|---|---|---|---|---|---|---|
| 28/09, 1 m | "tắt" | 7 | 1 (0) | 1 (0) | 0 (0) | "bật" 7 |
| 28/09, 3 m | "tắt" | 7 | 0 (0) | 0 (0) | 2 (0) | "bật" 5, "tắt" 2 |
| 07/10, 1 m | "tắt" | 19 | 15 (5) | 15 (3) | 5 (0) | "bật" 14, "tắt" 5 |
| 07/10, 3 m | "tắt" | 22 | 14 (2) | 16 (0) | 1 (0) | "bật" 21, "tắt" 1 |
| 28/09, 1 m | "bật" | 9 | 9 (9) | 9 (7) | 9 (5) | "bật" 9 |
| 28/09, 3 m | "bật" | 10 | 10 (9) | 10 (8) | 10 (6) | "bật" 10 |
| 07/10, 1 m | "bật" | 23 | 23 (23) | 23 (22) | 23 (21) | "bật" 23 |
| 07/10, 3 m | "bật" | 15 | 15 (15) | 15 (15) | 15 (11) | "bật" 15 |

Mọi câu như thu, không lọc, cho lại đúng §12.16: "tắt" 07/10 20/24 (8) và 15/23 (2), 28/09 1/11 (0) và 0/11 (0); "bật"
26/27 (26), 23/23 (23), 11/13 (11), 10/10 (9). Mang khuôn của "bật" (đi xuống, thấp), 27 trên 32 câu "tắt" đúng khi giữ
F0 thành sai, hầu hết thành "bật"; "bật" mang khuôn "tắt" thành "bất", không có trong bộ, nên 0/57 câu đổi và chỉ hạ
biên. Phép đổi riêng mức F0 ở §12.17 (× 0,65, giữ đường đi lên) chỉ lật 5/46: v7 nghe đường nét của thanh ở âm tiết
đầu, cả trên giọng chủ repo, nên kết luận "v7 tách 'tắt' với 'bật' gần như chỉ bằng phụ âm và nguyên âm" của §12.17
sai. Cùng với phép kiểm trên `val`, chỗ hỏng của "tắt" là cao độ mà bộ dò đưa vào: Kaldi sai 44–58% khung ở âm tiết đầu
của "tắt" trên các buổi này (§12.18); ngày 28/09 nguyên âm cũng nghiêng về "bật" (§12.17), nên không còn manh mối nào
cứu.

### 12.21 Lượt `command/v8`: Cửa 3 và giọng chủ repo theo mốc, phép kiểm trên trọng số cuối (07/10)

Run `20261007_439d763-dirty_f438bd`, công thức v7, ba chiều cao độ của SwiftF0 (KẾ HOẠCH §3.11), 40 000 bước: học
14:32–16:54 tới bước 24 083, dừng theo lời chủ repo, học tiếp từ `last.pt` 17:31–19:06. `make ctc-watch` chấm từng mốc
trên CPU khi lượt học vừa ghi ra, phép `owner` mỗi mốc thứ hai; phép `owner` ở các mốc còn lại chấm sau bằng
`make ctc-tone-flip CHECK=owner STEPS=…`, cùng phiên. Cửa 3 là các phiên 07/10, bộ mặc định, nhận ở δ₁ 200 ‰ và δ₂
50 ‰; "lật" là câu "tắt" đúng khi giữ F0 mà thành sai khi mang khuôn F0 của "bật" (`tone_flip owner`):

| Mốc | Lỗi đơn vị `val` | Cửa 3 đúng (nhận) | Từ chối | "tắt" / "bật" Cửa 3 | "tắt" 07/10 / 28/09 như thu | Lật |
|---|---|---|---|---|---|---|
| 2 000 | 0,854 | 157 (5) | 126/126 | 66/69 (4) / 64/73 (1) | 45/47 (2) / 21/22 (2) | 11/53 |
| 4 000 | 0,612 | 174 (114) | 124/126 | 69/69 (65) / 59/73 (39) | 47/47 (44) / 22/22 (21) | 3/55 |
| 6 000 | 0,569 | 193 (134) | 123/126 | 68/69 (58) / 70/73 (63) | 47/47 (42) / 21/22 (16) | 6/54 |
| 8 000 | 0,503 | 197 (149) | 123/126 | 69/69 (63) / 70/73 (67) | 47/47 (47) / 22/22 (16) | 8/55 |
| 10 000 | 0,506 | 195 (137) | 122/126 | 64/69 (45) / 70/73 (69) | 45/47 (36) / 19/22 (9) | 21/51 |
| 12 000 | 0,472 | 195 (149) | 124/126 | 68/69 (59) / 69/73 (60) | 47/47 (45) / 21/22 (14) | 15/53 |
| 14 000 | 0,470 | 197 (157) | 123/126 | 68/69 (65) / 67/73 (51) | 47/47 (46) / 21/22 (19) | 2/54 |
| 16 000 | 0,438 | 194 (147) | 124/126 | 65/69 (44) / 70/73 (69) | 47/47 (32) / 18/22 (12) | 31/51 |
| 18 000 | 0,428 | 190 (124) | 125/126 | 59/69 (27) / 70/73 (65) | 45/47 (26) / 14/22 (1) | 33/47 |
| 20 000 | 0,412 | 193 (175) | 123/126 | 68/69 (65) / 69/73 (67) | 47/47 (47) / 21/22 (18) | 8/53 |
| 22 000 | 0,398 | 198 (143) | 125/126 | 64/69 (38) / 70/73 (61) | 47/47 (35) / 17/22 (3) | 36/49 |
| 24 000 | 0,389 | 192 (145) | 125/126 | 59/69 (37) / 70/73 (69) | 46/47 (33) / 13/22 (4) | 42/48 |
| 26 000 | 0,378 | 199 (165) | 123/126 | 65/69 (53) / 70/73 (64) | 47/47 (43) / 18/22 (10) | 37/50 |
| 28 000 | 0,362 | 191 (145) | 124/126 | 57/69 (38) / 70/73 (69) | 45/47 (34) / 12/22 (4) | 41/48 |
| 30 000 | 0,358 | 195 (161) | 125/126 | 62/69 (48) / 70/73 (70) | 47/47 (41) / 15/22 (7) | 40/50 |
| 32 000 | 0,349 | 194 (153) | 125/126 | 60/69 (39) / 70/73 (69) | 46/47 (36) / 14/22 (3) | 41/48 |
| 34 000 | 0,345 | 191 (160) | 124/126 | 59/69 (40) / 70/73 (68) | 46/47 (33) / 13/22 (7) | 42/48 |
| 36 000 | 0,346 | 192 (158) | 125/126 | 61/69 (41) / 70/73 (68) | 47/47 (34) / 14/22 (7) | 40/49 |
| 38 000 | 0,346 | 193 (159) | 125/126 | 62/69 (43) / 70/73 (69) | 47/47 (37) / 15/22 (6) | 43/48 |
| 40 000 | 0,343 | 196 (160) | 125/126 | 63/69 (44) / 70/73 (69) | 47/47 (37) / 16/22 (7) | 41/48 |
| v7, 40 000 | 0,335 | 170 (119) | 124/126 | 36/69 / 70/73 | 35/47 (10) / 1/22 (0) | 27/32 |

Mọi câu "tắt" của chủ repo, đúng (nhận), 07/10 / 28/09, như thu và với chiều cao độ giữ ở trung bình lúc học; hai mốc
4 000 và 8 000 chấm trước khi có phép giữ:

| Mốc | Như thu | Giữ độ hữu thanh | Giữ hai chiều F0 | Giữ cả ba | Lật |
|---|---|---|---|---|---|
| 2 000 | 45 (2) / 21 (2) | 45 (1) / 21 (0) | 26 (0) / 14 (0) | 26 (0) / 9 (0) | 11/53 |
| 6 000 | 47 (42) / 21 (16) | 47 (43) / 22 (16) | 47 (37) / 18 (3) | 46 (35) / 21 (13) | 6/54 |
| 10 000 | 45 (36) / 19 (9) | 45 (36) / 18 (7) | 44 (30) / 16 (1) | 44 (29) / 16 (3) | 21/51 |
| 12 000 | 47 (45) / 21 (14) | 47 (45) / 19 (13) | 46 (34) / 18 (6) | 47 (39) / 18 (6) | 15/53 |
| 14 000 | 47 (46) / 21 (19) | 47 (46) / 22 (20) | 47 (47) / 21 (16) | 47 (46) / 21 (18) | 2/54 |
| 16 000 | 47 (32) / 18 (12) | 45 (27) / 16 (4) | 43 (17) / 15 (1) | 44 (14) / 12 (1) | 31/51 |
| 18 000 | 45 (26) / 14 (1) | 45 (29) / 11 (1) | 41 (18) / 5 (1) | 43 (25) / 9 (1) | 33/47 |
| 20 000 | 47 (47) / 21 (18) | 47 (47) / 21 (18) | 47 (47) / 19 (17) | 47 (47) / 21 (19) | 8/53 |
| 22 000 | 47 (35) / 17 (3) | 47 (39) / 17 (4) | 44 (16) / 6 (1) | 47 (31) / 10 (1) | 36/49 |
| 24 000 | 46 (33) / 13 (4) | 45 (39) / 16 (4) | 33 (10) / 6 (0) | 44 (31) / 7 (2) | 42/48 |
| 26 000 | 47 (43) / 18 (10) | 47 (46) / 16 (10) | 45 (34) / 12 (4) | 47 (44) / 14 (6) | 37/50 |
| 28 000 | 45 (34) / 12 (4) | 46 (42) / 15 (5) | 39 (21) / 4 (1) | 44 (40) / 12 (5) | 41/48 |
| 30 000 | 47 (41) / 15 (7) | 47 (44) / 16 (12) | 46 (34) / 9 (1) | 47 (45) / 16 (8) | 40/50 |
| 32 000 | 46 (36) / 14 (3) | 47 (41) / 15 (5) | 43 (26) / 5 (1) | 46 (38) / 13 (4) | 41/48 |
| 34 000 | 46 (33) / 13 (7) | 47 (40) / 16 (7) | 39 (22) / 4 (1) | 46 (39) / 13 (5) | 42/48 |
| 36 000 | 47 (34) / 14 (7) | 47 (41) / 17 (7) | 41 (20) / 4 (1) | 46 (40) / 16 (5) | 40/49 |
| 38 000 | 47 (37) / 15 (6) | 47 (41) / 17 (6) | 43 (30) / 5 (1) | 46 (42) / 16 (5) | 43/48 |
| 40 000 | 47 (37) / 16 (7) | 47 (42) / 18 (8) | 42 (29) / 6 (1) | 46 (42) / 16 (6) | 41/48 |

Ở mốc 12 000 độ hữu thanh không mang gì cho "tắt", nên giả thuyết "v8 tách 'tắt' bằng lúc /ɓ/ có tiếng" sai; F0 chỉ
thêm độ tin; không có cao độ "tắt" vẫn đúng, tức log-mel, giống hệt của v7, đủ để nhận "tắt" ở mốc này. v7 ở mốc 4 000
cũng đúng 19/22 câu "tắt" 28/09 rồi về 1/22 ở cuối (§12.14): thông tin có trong log-mel, mạng bỏ nó khi học tiếp.

Tới mốc 22 000 mạng đổi qua lại giữa hai đường: lật 2–21 câu ở các mốc tới 14 000, 31 và 33 ở 16 000 và 18 000, 8 ở
20 000, rồi 36–43 ở mọi mốc từ 22 000. Mốc 20 000 (21/22 câu "tắt" 28/09, nhận đủ 47/47 ngày 07/10) là một mốc lẻ giữa
hai mốc bám F0 (18 000: 14/22, nhận 1; 22 000: 17/22, nhận 3), không phải một đoạn. Ở các mốc bám F0 phần log-mel giữ
"tắt" 28/09 7–16/22; cao độ SwiftF0 thêm 6–7 câu ở 22 000 và 24 000, từ −2 tới 4 câu ở các mốc sau; ngày 07/10 phần
log-mel giữ 43–47/47 ở mọi mốc từ 6 000. Từ mốc 28 000 giữ riêng độ hữu thanh của SwiftF0 thêm 1–3 câu "tắt" 28/09 ở mọi
mốc: chiều ấy kéo "tắt" về "bật" như độ hữu thanh của Kaldi ở v7 (bảng dưới), yếu hơn.

`make ctc-tone-flip CHECK=places STEPS=…`, thanh đúng trên `val` theo vị trí, như mô phỏng / giữ cả ba chiều cao độ;
âm tiết đầu 1 578 (167 vần tắc), âm tiết sau 15 100 (2 118 vần tắc):

| Mốc | Lỗi đơn vị | Âm tiết đầu | Âm tiết sau | Âm tiết đầu, vần tắc | Âm tiết sau, vần tắc |
|---|---|---|---|---|---|
| 2 000 | 0,853 / 0,880 | 0,148 / 0,059 | 0,251 / 0,134 | 0,198 / 0,018 | 0,255 / 0,058 |
| 4 000 | 0,612 / 0,677 | 0,553 / 0,413 | 0,618 / 0,463 | 0,503 / 0,323 | 0,677 / 0,432 |
| 6 000 | 0,569 / 0,610 | 0,585 / 0,513 | 0,654 / 0,590 | 0,623 / 0,623 | 0,787 / 0,749 |
| 8 000 | 0,503 / 0,546 | 0,618 / 0,579 | 0,722 / 0,665 | 0,760 / 0,707 | 0,840 / 0,788 |
| 10 000 | 0,505 / 0,539 | 0,610 / 0,567 | 0,715 / 0,651 | 0,725 / 0,701 | 0,843 / 0,803 |
| 12 000 | 0,472 / 0,507 | 0,653 / 0,637 | 0,747 / 0,705 | 0,713 / 0,725 | 0,851 / 0,804 |
| 14 000 | 0,470 / 0,501 | 0,654 / 0,613 | 0,750 / 0,701 | 0,683 / 0,677 | 0,858 / 0,825 |
| 16 000 | 0,438 / 0,478 | 0,676 / 0,646 | 0,776 / 0,722 | 0,760 / 0,737 | 0,845 / 0,810 |
| 18 000 | 0,427 / 0,460 | 0,681 / 0,653 | 0,769 / 0,720 | 0,713 / 0,707 | 0,839 / 0,813 |
| 20 000 | 0,412 / 0,446 | 0,672 / 0,646 | 0,790 / 0,746 | 0,695 / 0,671 | 0,866 / 0,848 |
| 22 000 | 0,398 / 0,435 | 0,693 / 0,660 | 0,798 / 0,754 | 0,760 / 0,749 | 0,894 / 0,853 |
| 24 000 | 0,388 / 0,426 | 0,693 / 0,672 | 0,801 / 0,760 | 0,737 / 0,743 | 0,864 / 0,828 |
| 26 000 | 0,378 / 0,415 | 0,721 / 0,705 | 0,810 / 0,765 | 0,772 / 0,772 | 0,890 / 0,865 |
| 28 000 | 0,362 / 0,396 | 0,710 / 0,696 | 0,821 / 0,777 | 0,778 / 0,772 | 0,899 / 0,874 |
| 30 000 | 0,358 / 0,392 | 0,719 / 0,694 | 0,823 / 0,781 | 0,790 / 0,814 | 0,898 / 0,878 |
| 32 000 | 0,349 / 0,384 | 0,734 / 0,707 | 0,826 / 0,782 | 0,814 / 0,814 | 0,900 / 0,872 |
| 34 000 | 0,345 / 0,378 | 0,741 / 0,722 | 0,827 / 0,788 | 0,832 / 0,850 | 0,903 / 0,877 |
| 36 000 | 0,346 / 0,379 | 0,738 / 0,711 | 0,827 / 0,786 | 0,826 / 0,832 | 0,901 / 0,877 |
| 38 000 | 0,346 / 0,380 | 0,732 / 0,710 | 0,828 / 0,785 | 0,826 / 0,820 | 0,905 / 0,875 |
| 40 000 | 0,343 / 0,375 | 0,735 / 0,714 | 0,831 / 0,790 | 0,820 / 0,832 | 0,909 / 0,881 |

Trên `val` phần log-mel của thanh tốt lên suốt lượt: ở âm tiết đầu khi giữ cả ba chiều, 0,58 ở mốc 8 000 lên 0,71 ở
mốc cuối, riêng vần tắc 0,71 lên 0,83 (167 âm tiết, sai số chuẩn chừng 0,03); cao độ thêm 0,01–0,04 ở âm tiết đầu từ
mốc 8 000 và −0,02 tới 0,05 ở âm tiết đầu vần tắc, không lớn dần. Mạng không bỏ log-mel cho F0 trên tiếng như mô phỏng.
Trên board thì phần log-mel giữ "tắt" 28/09 9–21/22 tới mốc 20 000 rồi 7–16/22, trong khi lật lên 36–43: phần log-mel học
thêm về sau tốt lên trên tiếng như tập học mà không tốt lên trên tiếng board của buổi 28/09, hôm nguyên âm "tắt" trùng
"bật" và thanh là manh mối chính (§12.17).

**Trên trọng số cuối.** `make ctc-tone-flip CHECK=val`: cùng 1 102 câu, 1 099 căn được, Praat bỏ 84 âm tiết đầu và 436
âm tiết sau như §12.20; dịch là của ba chiều SwiftF0; cột như §12.20:

| Vị trí | Thanh gốc | Âm tiết | Dịch | Lật: đối chứng / sau đổi | Lật thêm | Điểm dịch |
|---|---|---|---|---|---|---|
| đầu | sắc | 32 | 1,08 | 0,00 / 0,47 | +0,47 | +5,1 |
| đầu | nặng | 50 | 1,06 | 0,14 / 1,00 | +0,86 | +7,0 |
| đầu | cả hai | 82 | 1,06 | 0,09 / 0,79 | +0,71 | +6,4 |
| sau | sắc | 309 | 1,20 | 0,01 / 0,88 | +0,86 | +8,9 |
| sau | nặng | 311 | 1,28 | 0,05 / 0,98 | +0,94 | +10,2 |
| sau | cả hai | 620 | 1,24 | 0,03 / 0,93 | +0,90 | +9,5 |

Âm tiết đầu lật thêm 0,71 so với 0,90 ở các âm tiết sau (v7: 0,68 so với 0,91), điểm dịch 6,4 so với 9,5 (v7: 5,8 so
với 10,9): mạng theo F0 ở âm tiết đầu; nặng mang khuôn sắc lật hết, sắc mang khuôn nặng lật 47%.

Cửa 3 bằng `make command-eval` trên trọng số cuối, δ₂ 50 ‰; "nhận đúng" và "lệnh kém nhất" là tỉ lệ của bảng ấy ở δ₁
200 ‰:

| Cao độ | Đúng nhất | Nhận đúng | Lệnh kém nhất | Từ chối | "tắt đèn" / "tắt quạt" | "bật đèn" / "bật quạt" |
|---|---|---|---|---|---|---|
| SwiftF0, như lúc học | 196/209 | 74% | 46% | 125/126 | 33/35 / 30/34 | 35/37 / 35/36 |
| Kaldi của board (`KALDI=1`) | 186/209 | 63% | 27% | 122/126 | 29/35 / 24/34 | 35/37 / 35/36 |
| Kaldi, độ hữu thanh giữ ở trung bình (`KALDI=1 HOLD=voicing`) | 192/209 | 73% | 27% | 124/126 | 32/35 / 30/34 | 35/37 / 35/36 |
| đầu RNN-T, Kaldi, độ hữu thanh giữ (`TRACK=rnnt KALDI=1 HOLD=voicing`) | 193/209 | 39% | 0% | 125/126 | 33/35 / 28/34 | 35/37 / 35/36 |

`tone_flip owner --kaldi-pitch`, mọi câu "tắt", đúng (nhận):

| v8, Kaldi | 07/10, 1 m | 07/10, 3 m | 28/09, 1 m | 28/09, 3 m |
|---|---|---|---|---|
| như thu | 22/24 (16) | 22/23 (12) | 6/11 (0) | 3/11 (0) |
| giữ độ hữu thanh | 23/24 (19) | 23/23 (16) | 10/11 (6) | 6/11 (0) |
| giữ hai chiều F0 | 20/24 (10) | 12/23 (6) | 3/11 (0) | 0/11 (0) |
| giữ cả ba | 23/24 (20) | 23/23 (22) | 10/11 (6) | 6/11 (0) |

"bật" như thu 49/50 (41) ngày 07/10 và 21/23 (21) ngày 28/09; lật "tắt" 26/41. Giữ cả ba chiều cho đúng số của SwiftF0
ở mốc 40 000, vì khi ấy đặc trưng không còn gì của bộ dò.

Ba điều của KẾ HOẠCH §3.11 trên trọng số cuối, cao độ như lúc học: (1) lật 41/48, từ 36 trở lên, và âm tiết đầu `val`
0,71 so với 0,90 ở các âm tiết sau, từ một nửa trở lên; (2) "tắt" 07/10 47/47, nhận 37; (3) Cửa 3 196/209, từ chối
125/126. Cả ba đạt; phép thử chéo "tắt" 28/09 16/22 (7) so với 1/22 của v7. Lỗi đơn vị `val` 0,343 so với 0,335 của
v7 là dấu hiệu "kém v7" của dòng D; độ lệch giữa hai lượt học cùng công thức chưa đo. Nghe bằng Kaldi của board: (2)
44/47, nhận 28, đạt; (3) 186/209, từ chối 122/126, đạt đúng ngưỡng; (1) lật 26/41, dưới ba phần tư; "tắt" 28/09 9/22,
không câu nào qua ngưỡng. Độ hữu thanh của Kaldi kéo "tắt" về "bật" như ở v7: giữ nó ở trung bình, 07/10 46/47 (35),
28/09 16/22 (6).

Cao độ của board theo luật KẾ HOẠCH §3.11, trần là v8 nghe bằng SwiftF0 (196/209, từ chối 125/126, "tắt" 07/10 47/47):
Kaldi như hiện nay kém trần 10 câu Cửa 3, không chạm; Kaldi với độ hữu thanh giữ ở trung bình được 192/209, từ chối
124/126, "tắt" 07/10 46/47, chạm trần. Board chạy v8 bằng `dsp_spec/pitch`, chiều độ hữu thanh là hằng số trung bình
lúc học. Ở cả hai cách Kaldi, lệnh kém nhất nhận đúng 27% so với 46% của SwiftF0; "đóng cửa" đúng 8/11 so với 10/11. Đầu
RNN-T học kèm của v8 đúng nhất ngang CTC nhưng chỉ nhận đúng 39%, lệnh kém nhất 0%: CTC vẫn là đường chạy trên board.

Cùng phép giữ trên v7 ở bước 40 000 (Kaldi), mọi câu "tắt", đúng (nhận):

| v7 | 07/10, 1 m | 07/10, 3 m | 28/09, 1 m | 28/09, 3 m |
|---|---|---|---|---|
| như thu | 20/24 (8) | 15/23 (2) | 1/11 (0) | 0/11 (0) |
| giữ độ hữu thanh | 18/24 (11) | 21/23 (5) | 5/11 (0) | 0/11 (0) |
| giữ hai chiều F0 | 20/24 (3) | 11/23 (0) | 1/11 (0) | 0/11 (0) |
| giữ cả ba | 20/24 (4) | 17/23 (2) | 4/11 (0) | 0/11 (0) |

"bật" không đổi ở mọi cách. Độ hữu thanh của Kaldi ở âm tiết đầu kéo "tắt" về "bật": bỏ nó, 07/10 3 m từ 15 lên 21/23,
28/09 1 m từ 1 lên 5/11. F0 của Kaldi vẫn giúp một ít: bỏ nó, 07/10 3 m còn 11/23 và gần như không câu nào qua ngưỡng.
Bỏ cả ba, 28/09 vẫn 4/22: ở trọng số cuối, chính phần log-mel của v7 nghe "tắt" 28/09 thành "bật", trong khi v8 ở mốc
12 000 không cao độ vẫn đúng 47/47 và 18/22 trên cùng log-mel.

`make ctc-tone-flip CHECK=owner STEPS=…` trên các checkpoint của chính v7 (Kaldi), một lượt nghe phiên cho mọi
checkpoint; dừng sau mốc 12 000 vì đã đủ để so. Mọi câu "tắt", đúng (nhận), như thu / giữ cả ba chiều cao độ; lật là
câu "tắt" đúng khi giữ F0 mà khuôn "bật" làm sai:

| Mốc | v7 07/10 | v7 28/09 | v7 lật | v8 28/09 |
|---|---|---|---|---|
| 2 000 | 23 (4) / 0 (0) | 7 (0) / 1 (0) | 14/24 | 21 (2) / 9 (0) |
| 4 000 | 44 (12) / 43 (7) | 19 (4) / 21 (5) | 14/47 | 22 (21) / |
| 6 000 | 44 (35) / 36 (19) | 8 (5) / 13 (6) | 19/42 | 21 (16) / 21 (13) |
| 8 000 | 46 (40) / 46 (40) | 9 (6) / 19 (14) | 17/44 | 22 (16) / |
| 10 000 | 46 (27) / 45 (27) | 13 (7) / 18 (9) | 18/43 | 19 (9) / 16 (3) |
| 12 000 | 47 (27) / 47 (33) | 15 (5) / 18 (10) | 17/44 | 21 (14) / 18 (6) |

Ngày 07/10 cả hai mạng đúng gần hết ở mọi mốc. Ngày 28/09, ở cùng mốc học, phần log-mel của hai mạng ngang nhau (giữ cả
ba chiều, mốc 6 000–12 000: v7 13–19/22, v8 16–21/22); cao độ Kaldi kéo v7 xuống 8–15/22, cao độ SwiftF0 đưa v8 lên
19–22/22. Cái hơn của v8 ở các mốc ấy là bộ dò đưa vào lúc chạy, không phải một nhánh log-mel tốt hơn.

### 12.22 Thang int8 của `command/v8` trên cao độ của board (07/10)

Run `20261007_439d763-dirty_f438bd` ở bước 40 000, nghe bằng Kaldi của board, độ hữu thanh gập vào `front.proj` ở trung
bình lúc học (KẾ HOẠCH §3.11, §3.14): `make ctc-ptq`, `make ctc-int16`, `make ctc-qat` cùng `KALDI=1 HOLD=voicing`, thang
ở `<run>/int8_kaldi_voicing/`. Hiệu chuẩn, 1 963 câu `test` và QAT đọc shard của v7, cùng mục và log-mel, cao độ Kaldi
của hợp đồng; Cửa 3 gồm các phiên 07/10, nhận ở δ₁ `quant.reject` 300 ‰ và δ₂ 50 ‰, cùng thước §12.19:

| Dòng | Lỗi đơn vị | Đúng nhất | Nhận đúng | Nhận nhầm |
|---|---|---|---|---|
| float | 0,407 | 192/209 | 151/209 | 3/126 |
| bậc 2: `minmax` | 0,467 | 192/209 | 133/209 | 4/126 |
| bậc 2: `percentile` | 0,430 | 194/209 | 145/209 | 3/126 |
| bậc 2: `mse` | 0,437 | 191/209 | 131/209 | 3/126 |
| bậc 2: `kl` | 0,430 | 193/209 | 142/209 | 3/126 |
| bậc 3: `percentile`, int16 1 lớp | 0,427 | 193/209 | 146/209 | 3/126 |
| bậc 3: `percentile`, int16 2 lớp | 0,427 | 193/209 | 146/209 | 3/126 |
| bậc 3: `percentile`, int16 4 lớp | 0,424 | 195/209 | 142/209 | 3/126 |
| bậc 4: QAT trên `percentile`, 2 000 bước | 0,391 | 184/209 | 137/209 | 3/126 |

Float đúng 192/209 như `make command-eval KALDI=1 HOLD=voicing` (§12.21), nhận nhầm 3/126 so với 2: hai phép chỉ khác
nhau ở các bước đệm cuối cửa sổ, nơi phép gập bỏ cả chiều ấy còn phép giữ trên đặc trưng thì không. Sai số từng lớp của
ESP-PPQ trên đồ thị `percentile` xếp ba tích chập 2D của phần đầu trước (`front/convs.1` 0,0054, `convs.2` 0,0054,
`convs.0` 0,0038), rồi tích chập theo chiều sâu đầu tiên (0,0011). Mọi dòng int8 nhận đúng hơn dòng tốt nhất của
v7 (122/209) 9–24 câu và đúng nhất 191–195 so với 162–181. QAT hạ lỗi đơn vị `val` của đồ thị `percentile` từ
0,390 xuống 0,361 trong 2 000 bước nhưng mất 10 câu đúng nhất và 8 câu nhận đúng so với nó, như ở v7. Theo luật của
KẾ HOẠCH §3.14 (`gate_tie` 5), `percentile`, `kl` và ba dòng int16 hoà ở 142–146 câu; int16 4 lớp có lỗi đơn vị thấp
nhất, cần µs trên board của bốn tích chập int16 trước khi chốt. `make ai-unit` với dòng ấy dừng lúc dựng: app thử
3 439 KB, quá `ota_0` 3 MB của `partitions_unit.csv` 367 KB, vì các probe của run đã học nhúng vào app (cửa sổ `ctc` 701 KB,
`kws` 647 KB, `ns` 498 KB). Mọi dòng giữ int8 ở hai đầu, kể cả int16 4 lớp. Bản demo 07/10 chạy `percentile`: hoà,
không lớp int16 nào, nên µs là của mạng cùng cỡ đã đo (`latency.md` §18, 10,2 ms mỗi 32 ms).

### 12.23 "Tăng/giảm âm lượng" trên board B với `command/v8` (08/10)

Tối 08/10 chủ repo thấy board hay bỏ qua "tăng âm lượng" và "giảm âm lượng". Board B chạy bản demo với dòng `percentile`
của v8 nghe bằng cao độ Kaldi, độ hữu thanh gập (§12.22), δ₁ 100 ‰ (gap 96 nhận, 104 từ chối), δ₂ 25 ‰ ở NVS, một bộ
14 lệnh như `host/sets/battat_vi.json`. Log UART 19:25–19:30 lúc chủ repo nói thử: "bật đèn" nhận 6/6 với gap 0, các
lệnh khác nhận với gap 15–97 ‰; hai lệnh âm lượng nhận 8 lần (tăng 6, giảm 2), khoảng 20 lượt `REJECT LOW_SCORE` với
gap 104–190 ‰.

Phát lại các phiên 28/09 qua chuỗi sản phẩm và đồ thị int8 ấy, quyết như chip trên `battat_vi.json` ở cùng δ₁, δ₂, câu
gán như Cửa 3; đo bằng script chẩn đoán chạy một lần, nhận đúng / câu:

| Lệnh | 1 m | 3 m |
|---|---|---|
| bật đèn | 5/7 | 4/5 |
| tăng âm lượng | 5/8 | 3/5 |
| giảm âm lượng | 1/5 | 0/6 |

Bản thu cho đúng tỉ lệ của board, và chip trùng Python 336/336 cửa sổ (`latency.md` §22): chỗ yếu ở mạng, không ở
firmware. Mạng vẫn xếp đúng lệnh đầu ở 11/11 câu "giảm" và 10/13 câu "tăng", như v5 float ở §12.11; câu bị bỏ là vì gap
vượt δ₁. Vòng tự do mạng nghe, so với cách đọc của `lang_vi`: "giảm âm lượng" `z a: m T4 @ m T1 l M@ N T6`, "tăng"
`t a N T1`:

| Câu | Mạng nghe |
|---|---|
| giảm âm lượng, 1 m | `s a: T3 b_< O N T1 n M@ T5` · `z a: T3 b_< o N T1 n M@ T6` · `d_< a: T4 m o N T1 n M@ T6` |
| giảm âm lượng, 3 m | `d_< a: T3 o N T1 n M@ T5` · `z a: T3 h o m T1 n M@ k T5` · `d_< a: T4 o m T1 v @: T5` |
| tăng âm lượng, 1 m | `t a N T1 o n T1 n M@ T5` · `t a N T1 o T1 n M@ k T6` · `a N T1 h o N T1 n M@ T6` |

Ba chỗ lệch lặp lại: "âm" mở bằng nguyên âm nên mạng gắn cho nó phụ âm đầu (`b_<`, `m`, `h`, `v`), nghe `o` thay `@` và
cuối `N` hay `n` thay `m`; "lượng" nghe `n` thay `l` sau `m` của "âm", mất `N` cuối hay thành `k`, thanh nặng `T6`
thành sắc `T5` ở gần nửa số câu; "giảm" nghe thanh ngã `T3` thay hỏi `T4` ở 5/11 câu và mất `m` cuối. Đường Viterbi
của cách đọc tốt nhất kém vòng tự do trên từng âm tiết, ‰ của T_W, trung bình trên 11 câu mạng nghe ra tiếng mỗi lệnh:

| Lệnh | Gap trung bình | Âm tiết 1 | Âm tiết 2 | Âm tiết 3 |
|---|---|---|---|---|
| bật đèn | 48 | 12 | 39 | — |
| tăng âm lượng | 82 | 21 | 36 | 27 |
| giảm âm lượng | 126 | 39 | 37 | 51 |

Không âm tiết nào gánh cả gap: ba âm tiết mỗi cái hụt 20–50 ‰, cộng lại quá δ₁. Mười phiên board của tập học (§11,
`eval.board.train`) chỉ có bật, tắt, mở đèn, quạt, ti vi, điều hoà, đúng các lệnh board nhận với gap 0–45 ‰ tối 08/10;
lệnh âm lượng chưa có phiên board nào trong tập học. Nới δ₁ không gỡ được: bảng chọn ngưỡng của dòng ấy
(`int8_kaldi_voicing/percentile/thresholds.yaml`) cho δ₁ 200 ‰ nhận 83,3% thay 80,6% câu `val_commands` mà nhận nhầm
49 thay 2 (δ₂ 0), 13 thay 1 (δ₂ 25).

### 12.24 v2 và v8 theo từng lệnh, bốn cách nghe cao độ (08/10)

Chủ repo thấy "đóng cửa", "mở cửa", "chụp ảnh" và cặp âm lượng trên board kém bản v2 của buổi demo 02/10. `make
command-eval` trên trọng số float, 209 câu lệnh và 126 cửa sổ phải từ chối của Cửa 3; nhận ở δ₁ 100 ‰, δ₂ 25 ‰ như board
B tối 08/10. v2 là run `20261002_128545c-dirty_64e7a4`, không có chiều cao độ; bản deploy 02/10 của nó không mang δ₁ trong
ảnh model nên board chạy mặc định của `Kconfig` `svc_listen`, δ₁ 300 ‰, δ₂ 50 ‰. Đúng nhất / nhận đúng:

| Lệnh | v2 | v8, SwiftF0 như lúc học | v8, Kaldi, giữ độ hữu thanh (board) | v8, Kaldi, giữ cả ba chiều |
|---|---|---|---|---|
| bật đèn | 35 / 29 | 35 / 35 | 35 / 34 | 35 / 35 |
| tắt đèn | 1 / 0 | 33 / 27 | 32 / 29 | 32 / 26 |
| bật quạt | 35 / 35 | 35 / 34 | 35 / 35 | 35 / 35 |
| tắt quạt | 1 / 0 | 30 / 25 | 30 / 25 | 30 / 25 |
| mở cửa | 10 / 10 | 10 / 10 | 10 / 10 | 10 / 10 |
| đóng cửa | 11 / 10 | 10 / 8 | 8 / 6 | 10 / 8 |
| tăng âm lượng | 10 / 5 | 10 / 8 | 10 / 8 | 10 / 7 |
| giảm âm lượng | 11 / 3 | 11 / 6 | 11 / 5 | 11 / 6 |
| dừng lại | 10 / 9 | 11 / 10 | 10 / 9 | 10 / 10 |
| chụp ảnh (chưa học) | 11 / 9 | 11 / 10 | 11 / 10 | 11 / 10 |
| **Cả bộ** | **135 / 110** | **196 / 173** | **192 / 171** | **194 / 172** |
| Nhận nhầm / 126 | 1 | 1 | 1 | 0 |

Ở δ₂ 50 ‰ nhận đúng là 101, 160, 147 và 161 câu. Ở cùng ngưỡng, v8 nhận hơn v2 ở mọi lệnh trừ "đóng cửa". Cặp âm lượng
của v2 cũng chỉ qua 5/13 và 3/11 ở δ₁ 100 ‰; ở δ₁ 300 ‰, δ₂ 50 ‰ của buổi 02/10, v2 nhận 7/13 và 9/11. "Đóng cửa"
kém đi vì cao độ của Kaldi: ba câu nghe bằng Kaldi chuyển sang "mở cửa" với khoảng cách nhất nhì 2–21 ‰, giữ cả ba
chiều thì trả lại 10/11 như SwiftF0. Giữ cả ba chiều, phần cao độ không vào mạng nữa, nên cách ấy không theo bộ dò nào.

### 12.25 Cao độ với v8: thanh theo từng cách nghe, trên `val` và trên lệnh của chủ repo (08/10)

Mạng float của v8 ở bước 40 000, năm cách đưa ba chiều cao độ vào: SwiftF0 như lúc học; Kaldi như board dò; Kaldi với độ
hữu thanh giữ ở trung bình, như board B chạy v8; Kaldi với hai chiều F0 giữ; không cao độ, cả ba chiều giữ. Thanh đúng là
thanh của vòng tự do, căn theo khoảng cách sửa với đơn vị của câu, như `tone_flip places`; đo bằng script chẩn đoán
chạy một lần. Trên `val` (1 578 câu), bản dựng v8 cho SwiftF0 và bản dựng v7 cho Kaldi: cùng mục, log-mel trùng từng
byte, chỉ cao độ khác:

| Cách nghe | Lỗi đơn vị | Âm tiết đầu | ngang | huyền | ngã | hỏi | sắc | nặng | Âm tiết sau | ngang | huyền | ngã | hỏi | sắc | nặng |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SwiftF0 | 0,343 | 73,5% | 82,0 | 69,9 | 52,6 | 42,7 | 92,7 | 53,5 | 83,1% | 88,3 | 86,9 | 49,0 | 70,4 | 92,7 | 72,8 |
| Kaldi | 0,392 | 63,4% | 82,8 | 61,2 | 34,7 | 14,5 | 79,0 | 34,8 | 77,3% | 86,5 | 87,8 | 45,8 | 46,4 | 82,6 | 67,9 |
| Kaldi, giữ độ hữu thanh | 0,372 | 69,9% | 78,9 | 66,3 | 49,5 | 34,7 | 92,4 | 44,9 | 79,9% | 82,3 | 87,3 | 45,3 | 63,4 | 92,1 | 69,2 |
| Kaldi, giữ hai chiều F0 | 0,411 | 64,1% | 87,4 | 68,1 | 30,5 | 10,5 | 65,9 | 44,4 | 69,8% | 89,7 | 69,8 | 37,0 | 38,3 | 75,2 | 53,9 |
| không cao độ | 0,375 | 71,5% | 78,5 | 71,6 | 45,3 | 33,1 | 92,7 | 52,4 | 79,0% | 82,8 | 85,6 | 43,1 | 61,7 | 92,0 | 66,4 |
| Số âm tiết | | 1 578 | 494 | 335 | 95 | 124 | 343 | 187 | 15 100 | 4 331 | 2 863 | 775 | 1 420 | 3 501 | 2 210 |

Trên 209 câu lệnh của Cửa 3 qua board B, cửa sổ gán như Cửa 3; thanh so với cách đọc miền Bắc của lệnh:

| Cách nghe | Đúng nhất | Thanh âm tiết đầu | Thanh các âm tiết sau |
|---|---|---|---|
| SwiftF0 | 196/209 | 167/209 (80%) | 179/233 (77%) |
| Kaldi | 186/209 | 141/209 (67%) | 150/233 (64%) |
| Kaldi, giữ độ hữu thanh | 192/209 | 160/209 (77%) | 153/233 (66%) |
| Kaldi, giữ hai chiều F0 | 170/209 | 130/209 (62%) | 148/233 (64%) |
| không cao độ | 194/209 | 166/209 (79%) | 153/233 (66%) |

So với cách board chạy v8, bỏ cao độ ngang ngửa: trên `val` âm tiết đầu hơn 1,6 điểm (nặng hơn 7,5 điểm, vì Kaldi dò
sai âm tiết đầu như §12.18), âm tiết sau kém 0,9 điểm; trên lệnh của chủ repo đúng nhất 194 so với 192, thanh âm tiết
đầu 79% so với 77%, âm tiết sau bằng nhau. So với SwiftF0 thì kém 2–4 điểm trên `val`, dồn ở hỏi, nặng, ngã, và 11
điểm ở thanh các âm tiết sau của chủ repo ("quạt" 20 so với 28/36, "đèn" 22 so với 29/35); quyết định của bộ mười lệnh
gần như không đổi, vì các lệnh còn khác nhau ở phụ âm và vần. Độ hữu thanh của Kaldi hại cả hai nơi: giữ riêng hai
chiều F0, để độ hữu thanh vào, thì "tắt" chỉ còn 17/35 và 18/34. Cặp âm lượng không theo cao độ: ở cả SwiftF0, thanh
các âm tiết sau của nó chỉ đúng 11/26 và 15/22. Hỏi và ngã yếu ở cả SwiftF0, Kaldi giữ độ hữu thanh lẫn không cao độ: âm tiết đầu 33–53%, hỏi
hay thành sắc.

Ứng viên đầu của KẾ HOẠCH §3.11 trên cùng 209 câu: Kaldi của board với trạng thái của khung t lấy trên đường Viterbi
truy ngược từ khung t + 25 (0,4 s), log F0 trừ trung bình theo POV từ 0,75 s trước tới 0,4 s sau, delta hai khung mỗi
phía; bản thử dựng trên lớp bộ dò của bản soi gương, vòng con trỏ lùi nới đủ, đo bằng script chẩn đoán chạy một lần:

| Cách nghe | Đúng nhất | Thanh âm tiết đầu | Thanh các âm tiết sau | "đóng cửa": đúng nhất / thanh âm tiết đầu |
|---|---|---|---|---|
| Kaldi dò lại 0,4 s | 188/209 | 150/209 (72%) | 164/233 (70%) | 9/11 / 2/11 |
| Kaldi dò lại 0,4 s, giữ độ hữu thanh | 192/209 | 160/209 (77%) | 159/233 (68%) | 8/11 / 2/11 |

Dò lại nâng Kaldi 2–6 điểm thanh nhưng không tới SwiftF0 (196/209, 80%, 77%) và không hơn không cao độ (194/209):
mạng học trên SwiftF0 chỉ lấy được lợi của cao độ từ chính bộ dò ấy. Thanh của "đóng" còn kém đi ở cả hai cách.

Cùng phép chấm của §12.23 (bộ `battat_vi.json`, δ₁ 100 ‰, δ₂ 25 ‰, quyết như chip, câu gán như Cửa 3) trên đồ thị int8
`percentile` của thang giữ cả ba chiều cao độ (`int8_kaldi_pitch/`), phiên 28/09, nhận đúng / câu:

| Lệnh | giữ độ hữu thanh (board 08/10) | giữ cả ba chiều |
|---|---|---|
| bật đèn | 9/12 | 10/12 |
| tăng âm lượng | 8/13 | 8/13 |
| giảm âm lượng | 1/11 | 5/11 |

Gap của các câu "giảm" hạ: nhận ở 73–95 ‰, ba câu từ chối ở 101–102 ‰ và hai ở 149, 157 ‰.

### 12.26 v8 giữ cả ba chiều cao độ trên board B, δ₁ 200 ‰ (08/10)

`make ctc-deploy ROW=percentile KALDI=1 HOLD=pitch` (thang `int8_kaldi_pitch/`: `percentile` nhận đúng 163/209 ở δ₁
300 ‰, δ₂ 50 ‰ của thang, so với 145/209 của dòng giữ độ hữu thanh; `make ctc-thresholds` chọn δ₁ 100 ‰, δ₂ 0 ‰, ở đó
Cửa 3 nhận đúng 184/209, nhận nhầm 1/126), rồi `make models-flash` 20:43–20:47. Board B khởi động: ảnh 3 370 KB ở PSRAM,
mạng dựng 347 ms, NVS giữ δ₁ 100 ‰, δ₂ 25 ‰ của chủ repo.

Chủ repo thử ngay trên bộ 301 lệnh rồi bộ 47 lệnh: nhiều câu bị từ chối `LOW_SCORE` với gap 103–153 ‰, "chụp ảnh" không
qua. Trên bản thu 28/09, đồ thị int8 mới nhận "chụp ảnh" 10/11 so với 9/11 của đồ thị cũ ở bộ battat, 9/11 so với 8/11 ở
bộ 301 lệnh, gap 55–89 ‰; ở bộ 301 lệnh, "tăng âm lượng" và "giảm âm lượng" 1 m chỉ qua 2/8 và 1/5 ở cả hai đồ thị, vì
"tăng một độ", "giảm một độ" chỉ kém 0–21 ‰ (`LOW_MARGIN`). Đánh đổi của δ₁ ở δ₂ 25 ‰ trên bảng chọn ngưỡng của dòng
mới (`val_commands`, câu `val` nhận nhầm) và trên Cửa 3 float giữ cả ba chiều (δ₂ 50 ‰):

| δ₁ | `val_commands` nhận đúng | `val` nhận nhầm | Cửa 3 nhận đúng | Cửa 3 từ chối |
|---|---|---|---|---|
| 100 ‰ | 75,0% | 2 | 73% | 126/126 |
| 200 ‰ | 78,2% | 11 | 78% | 124/126 |
| 300 ‰ | 78,2% | 46 | 78% | 123/126 |

δ₁ 200 ‰ lấy hết phần nhận thêm; 300 ‰ chỉ thêm nhận nhầm. Đặt `kws/cmd_reject` 200 qua console lúc 20:56 (`nvs set kws
cmd_reject 200 -t u16`), khởi động lại: `listening: … delta1 200, delta2 25`.

### 12.27 Câu ngoài bộ: "bật đèn" trên bộ 47 lệnh của chủ repo (08/10)

Board B chạy v8 giữ cả ba chiều cao độ ở δ₁ 200 ‰, δ₂ 25 ‰ (§12.26) với bộ 47 lệnh của chủ repo, có "mở đèn", "tắt
đèn", "bạn bè" mà không có "bật đèn". 21:11 chủ repo nói "bật đèn": log UART nhận `ban_be` (gap 51, lead 38) rồi `tat_den`
(gap 138, lead 42); cửa sổ ngay trước mỗi câu bị từ chối (`LOW_SCORE` gap 347, `LOW_MARGIN` gap 132). Ở δ₁ 100 ‰
`tat_den` đã bị từ chối, `ban_be` thì không; "bạn bè" cùng thanh với "bật đèn" (nặng, huyền).

Phát lại các phiên của Cửa 3 qua `make command-eval SET=host/sets/demo_vi.json` (float, bước 40 000), ba cách nghe cao
độ. Cửa sổ của các phiên "bật đèn" và "bật quạt", lệnh không có trong bộ, nhận thành "tắt …" ở δ₂ 25 ‰, tính từ
score/lead/gap của từng cửa sổ, chưa xét luật phần; cột cuối là 14 câu na ná của `20260928_home_032` lọt thành lệnh:

| Cách nghe | "bật" → "tắt", δ₁ 100 ‰ | δ₁ 200 ‰ | "tắt đèn", "tắt quạt" nhận đúng, δ₁ 100 ‰ | Câu na ná lọt, δ₁ 100 / 200 ‰ |
|---|---|---|---|---|
| SwiftF0 như lúc học | 16/79 | 35/79 | 68/69 | 0 / 5 |
| Kaldi, giữ độ hữu thanh | 24/79 | 38/79 | 67/69 | 0 / 4 |
| giữ cả ba chiều (board B) | 15/79 | 36/79 | 67/69 | 0 / 4 |

Bỏ cao độ không làm "bật" lọt thành "tắt" nhiều hơn SwiftF0; δ₁ 200 ‰ làm lọt gấp đôi ở mọi cách nghe. Chọn δ₁ 200 ‰ ở
§12.26 đã đọc cột từ chối của Cửa 3 ở δ₂ 50 ‰, không phải δ₂ 25 ‰ board chạy: trên bộ 10 lệnh của Cửa 3, float giữ cả
ba chiều, 14 câu na ná lọt 0 ở δ₁ 100 ‰ và 5 ở δ₁ 200 ‰.

Trên đồ thị int8 `percentile` của board B, cùng bộ 47 lệnh, mọi cửa sổ của các phiên đếm của Cửa 3, quyết như chip;
đo bằng script chẩn đoán chạy một lần. Ở 63/80 cửa sổ "bật", lệnh điểm cao nhất là "tắt …", mà vòng tự do vẫn nghe
`b_<` đầu ở 57/63 và thanh nặng ở âm tiết đầu ở 58/63: mạng nghe ra "bật", mà âm tiết kém nhất của đường "tắt" chỉ kém
vòng tự do 51–162 ‰ (trung vị 98). Ở cửa sổ có lệnh đúng điểm cao nhất, âm tiết kém nhất kém vòng tự do 0–201 ‰,
trung vị 28, phân vị 90 là 60. Thêm một trần cho âm tiết kém nhất của lệnh thắng, δ₂ 25 ‰; cột đầu tính mọi cửa sổ của
các phiên nói lệnh có trong bộ, kể cả hơi thở và tiếng động:

| δ₁ | Trần âm tiết kém nhất | Lệnh trong bộ nhận đúng | "bật" lọt | Câu na ná, nói tự do lọt |
|---|---|---|---|---|
| 100 ‰ | — | 116/154 | 23/80 | 0/25 |
| 100 ‰ | 60 ‰ | 114/154 | 8/80 | 0/25 |
| 200 ‰ | — | 122/154 | 42/80 | 3/25 |
| 200 ‰ | 70 ‰ | 120/154 | 12/80 | 0/25 |
| 200 ‰ | 60 ‰ | 117/154 | 8/80 | 0/25 |
| 200 ‰ | 50 ‰ | 102/154 | 0/80 | 0/25 |

Luật trần âm tiết đổi cách quyết của KẾ HOẠCH §3.12, nên chưa làm; chủ repo để sau.

### 12.28 Trần âm tiết kém nhất `δ₃` cho đồ thị int8 của board B (08/10)

Luật `δ₃` của KẾ HOẠCH §3.12 trên dòng `percentile` của v8 giữ cả ba chiều cao độ (`int8_kaldi_pitch/`), quyết như chip.
`make ctc-thresholds` quyết mỗi cửa sổ `val_commands` (216 cửa sổ, tám lệnh) thêm một lần trên các lệnh đã học trừ chính
lệnh nó nói, thành câu bộ lệnh không có. Mục tiêu đầu `out_of_set_accept` 0,05 chọn δ₁ 100 ‰, δ₂ 0 ‰, δ₃ 50 ‰: câu ngoài
bộ bị nhận 8/216, nhưng Cửa 3 nhận đúng chỉ 152/209 so với 184/209 không trần, nên chưa deploy. Cửa 3 trên lưới ba
ngưỡng, cùng đồ thị; "nhận sai lệnh" là câu lệnh trong bộ nhận thành lệnh khác, "câu lạ" là 126 cửa sổ phải từ chối,
"bật" lọt là 80 cửa sổ "bật đèn", "bật quạt" của các phiên Cửa 3 quyết trên bộ 47 lệnh của chủ repo (§12.27):

| δ₁ | δ₂ | δ₃ | Cửa 3 nhận đúng | Nhận sai lệnh | Câu lạ nhận | `val_commands` nhận đúng | `val` nhận nhầm | Ngoài bộ nhận | "bật" lọt |
|---|---|---|---|---|---|---|---|---|---|
| 100 ‰ | 25 ‰ | — | 172/209 | 5 | 0/126 | 75,0% | 2 | 17/216 | 23/80 |
| 100 ‰ | 0 ‰ | 50 ‰ | 152/209 | chưa đo | 0/126 | 65,3% | 2 | 8/216 | 0/80 |
| 100 ‰ | 0 ‰ | 60 ‰ | 172/209 | 6 | 0/126 | 74,1% | 3 | 16/216 | 11/80 |
| 200 ‰ | 0 ‰ | 60 ‰ | 175/209 | 6 | 0/126 | 75,0% | 4 | 17/216 | 11/80 |
| 200 ‰ | 0 ‰ | 70 ‰ | 179/209 | 6 | 1/126 | 78,2% | 5 | 23/216 | 15/80 |
| 200 ‰ | 0 ‰ | 80 ‰ | 184/209 | 7 | 1/126 | 80,6% | 10 | 29/216 | 22/80 |
| 200 ‰ | 25 ‰ | 60 ‰ | 163/209 | 5 | 0/126 | 71,3% | 1 | 10/216 | 8/80 |
| 200 ‰ | 25 ‰ | — | 182/209 | 5 | 5/126 | 78,2% | 11 | 49/216 | 42/80 |
| 200 ‰ | 0 ‰ | — | 194/209 | 7 | 6/126 | 84,3% | 45 | 72/216 | 66/80 |

Dòng δ₁ 200 ‰, δ₂ 25 ‰ không trần là board B lúc 21:11. Trần thay được phần việc của δ₂ với câu na ná: ở δ₂ 0 ‰ và δ₃ 60 ‰,
Cửa 3 nhận đúng hơn δ₁ 100 ‰, δ₂ 25 ‰ không trần 3 câu, câu ngoài bộ bằng (17/216), "bật" lọt 11 so với 23, nhận sai lệnh
thêm 1. `out_of_set_accept` đặt 0,08, đúng mức câu ngoài bộ của cặp không trần ấy: luật chọn ra δ₁ 200 ‰, δ₂ 0 ‰, δ₃ 60 ‰
(lệnh kém nhất của `val_commands` 0,5, cả bộ 75,0%). So với board B lúc 21:11 (δ₁ 200 ‰, δ₂ 25 ‰), bộ ba ấy mất 7 câu
nhận đúng của Cửa 3 mà "bật" lọt từ 42 xuống 11/80 và câu lạ nhận từ 5 xuống 0/126. Đo bằng script chẩn đoán chạy một lần.

### 12.29 Lượt thử nhìn trước: lượt A và B, cả ba chiều cao độ giữ (08/10)

KẾ HOẠCH §3.12, hai lượt học tinh chỉnh từ v8, mỗi lượt 10 000 bước, lr 3·10⁻⁴, cả ba chiều cao độ giữ ở trung bình
lúc học, cùng seed và split: A không nhìn trước (run `20261008_fd6fe76-dirty_dd5966`, 22:12–23:16), B một lớp nhìn
trước 12 khung, 0,384 s (run `20261008_818da00-dirty_2f0bcb`, 21:09–22:12). Lỗi đơn vị `val` theo mốc 2 000 … 10 000:
A 0,374, 0,368, 0,361, 0,354, 0,350; B 0,369, 0,359, 0,357, 0,347, 0,343; v8 nghe SwiftF0 0,343, v8 giữ cả ba chiều
không học lại 0,375 (§12.25). Cửa 3 float, Kaldi, cả ba chiều giữ, δ₂ 25 ‰, chưa xét luật phần:

| Run | Đúng nhất | Nhận đúng δ₁ 100 ‰ | Nhận đúng δ₁ 200 ‰ | Câu lạ nhận δ₁ 100 / 200 ‰ | "tắt đèn" đúng nhất | "tắt quạt" đúng nhất |
|---|---|---|---|---|---|---|
| v8 (board B) | 194/209 | 172/209 | 181/209 | 0 / 7 | 32/35 | 30/34 |
| A | 170/209 | 137/209 | 149/209 | 0 / 4 | 17/35 | 21/34 |
| B | 181/209 | 149/209 | 161/209 | 0 / 6 | 23/35 | 27/34 |

`tone_flip places` trên `val`, cả ba chiều giữ: thanh đúng âm tiết đầu A 72,7%, B 73,2%, âm tiết sau 81,8% và 82,0%; riêng
vần tắc, âm tiết đầu 76,0% và 79,6% (v8 không học lại: 71,5% và 79,0% cho âm tiết đầu và sau, §12.25). `tone_flip owner`
cả ba chiều giữ, "tắt" đúng nhất (qua ngưỡng của phép kiểm) trên 69 câu của 28/09 và 07/10: v8 62 (48), B 50 (21), A 38
(9); "bật" 70/73 ở cả ba.

Theo luật của §3.12, B thắng A: Cửa 3 nhận đúng hơn 12 câu ở δ₁ 100 ‰, từ chối bằng nhau; thanh âm tiết đầu trên `val`
chỉ hơn 0,5 điểm. Nhưng cả hai kém v8: học tinh chỉnh với cả ba chiều giữ kéo "tắt" của chủ repo xuống dù `val` khá lên,
như v6 ở §12.13, nên không lượt nào thay v8. Lượt thử chạy tiếp với cao độ SwiftF0 như v8 học (C, D của §3.12).

### 12.30 Lượt thử nhìn trước với cao độ SwiftF0: lượt C và D (09/10)

KẾ HOẠCH §3.12 sau §12.29: học tinh chỉnh từ v8 10 000 bước, lr 3·10⁻⁴, cao độ SwiftF0 như v8 học, không giữ chiều nào.
Lượt C có lớp nhìn trước 12 khung (run `20261008_d1c4974-dirty_ff7794`, 23:26–00:30); lượt D, đối chứng không nhìn
trước (run `20261009_346b687-dirty_96faa0`, 00:31–01:32). Lỗi đơn vị `val`, nghe SwiftF0, theo mốc 2 000 … 10 000: C
0,353, 0,344, 0,340, 0,332, 0,329; D 0,357, 0,349, 0,346, 0,339, 0,335 (v8: 0,343). Cửa 3 float như board nghe, Kaldi,
cả ba chiều giữ, δ₂ 25 ‰, chưa xét luật phần:

| Run | Đúng nhất | Nhận đúng δ₁ 100 ‰ | Nhận đúng δ₁ 200 ‰ | Câu lạ nhận δ₁ 100 / 200 ‰ | "tắt đèn" đúng nhất | "tắt quạt" đúng nhất |
|---|---|---|---|---|---|---|
| v8 (board B) | 194/209 | 172/209 | 181/209 | 0 / 7 | 32/35 | 30/34 |
| C | 191/209 | 177/209 | 183/209 | 0 / 5 | 31/35 | 30/34 |
| D | 191/209 | 173/209 | 181/209 | 0 / 7 | 33/35 | 29/34 |

`tone_flip places` trên `val`, cả ba chiều giữ: lỗi đơn vị C 0,364, D 0,370, thanh đúng âm tiết đầu 71,5% và 71,8%, âm
tiết sau 79,4% và 79,3% (v8: 0,375, 71,5%, 79,0%); nghe SwiftF0, C 0,329, 73,9%, 83,8%. `tone_flip owner`, cả ba chiều
giữ: "tắt" đúng nhất C 61/69 (48 qua ngưỡng của phép kiểm), D 62/69 (45), v8 62/69 (48). Học tinh chỉnh với cao độ như
lúc học giữ "tắt" của chủ repo, khác A và B (§12.29). Theo luật của KẾ HOẠCH §3.12, C không thắng D: Cửa 3 nhận đúng
chỉ hơn 4 câu, thanh âm tiết đầu trên `val` kém 0,3 điểm; cả C lẫn D đều trong nhiễu đếm của v8. Lớp nhìn trước không
đổi được Cửa 3 của chủ repo khi học với cao độ, nên không làm phần firmware của nó và giữ v8.

### 12.31 Vì sao học tinh chỉnh với cao độ giữ làm mất "tắt" của chủ repo (09/10)

Các câu "tắt" (69) và "bật" (74) của chủ repo trong các phiên Cửa 3 28/09 và 07/10, cửa sổ gán như Cửa 3, float, cả ba
chiều cao độ giữ như board chạy. Mỗi cửa sổ chấm lệnh nó nói ("t a t T5 …", "b_< @ t T6 …") so với cùng lệnh đổi một đơn
vị sang của từ kia (phụ âm đầu `t`↔`b_<`, nguyên âm `a`↔`@`, thanh `T5`↔`T6`); mỗi ô là trung vị của hiệu điểm, ‰ của
T_W, và tỉ lệ câu mạng ưa đơn vị đúng; "đúng" là lệnh nói hơn ba lệnh kia. Dòng "không dải thấp" đặt 12 dải mel có cạnh
trên ≤ 500 Hz ở trung bình lúc học, nơi có hoạ âm của giọng (chủ repo: 200 Hz ở "tắt", 131 Hz ở "bật", KẾ HOẠCH §3.11).
Đo bằng script chẩn đoán chạy một lần:

| Câu nói | Run | Phổ | Đúng | Phụ âm đầu | Nguyên âm | Thanh |
|---|---|---|---|---|---|---|
| tắt | v8 | đủ | 62/69 | 32 (88%) | 24 (90%) | 33 (87%) |
| tắt | C | đủ | 61/69 | 37 (88%) | 28 (91%) | 34 (88%) |
| tắt | B | đủ | 50/69 | 8 (77%) | 5 (62%) | 13 (70%) |
| tắt | A | đủ | 38/69 | 6 (74%) | −3 (36%) | 5 (61%) |
| tắt | v8 | không dải thấp | 64/69 | 33 (93%) | 33 (97%) | 42 (93%) |
| tắt | A | không dải thấp | 32/69 | −6 (39%) | −7 (28%) | 10 (68%) |
| bật | v8 | đủ | 71/74 | 55 (96%) | 15 (95%) | 41 (93%) |
| bật | A | đủ | 70/74 | 54 (96%) | 17 (93%) | 50 (96%) |
| bật | v8 | không dải thấp | 59/74 | 29 (93%) | −1 (47%) | 1 (57%) |
| bật | C | không dải thấp | 59/74 | 27 (95%) | 0 (53%) | 3 (55%) |
| bật | A | không dải thấp | 70/74 | 33 (96%) | 11 (92%) | 19 (88%) |
| bật | B | không dải thấp | 70/74 | 30 (96%) | 8 (84%) | 23 (89%) |

A và B mất "tắt" ở cả ba đơn vị cùng lúc, không riêng thanh: cả âm tiết trôi về "bật", nguyên âm của A còn ngả hẳn về `â`.
v8 và C tách "bật" khỏi "tắt" một phần bằng các dải dưới 500 Hz: bỏ chúng thì "bật" của cả hai tụt 71 → 59 và 70 → 59,
nguyên âm và thanh về quanh 0, tức hai mạng đọc độ cao giọng ngầm từ hoạ âm trong log-mel. A và B không dùng các dải ấy
cho "bật" (70/74 có hay không). Học tinh chỉnh với cao độ giữ đổi manh mối tách hai từ sang các dải cao hơn, học từ
giọng của kho; với chủ repo, `ă` và `â` gần trùng nhau (§12.17), nên các manh mối ấy chỉ về "bật".

Khi giữ cả ba chiều, phần log-mel của A đổi nhiều hơn của D: lỗi đơn vị `val` 0,375 ở v8, 0,350 ở A, 0,370 ở D; thanh
âm tiết sau 79,0%, 81,8% và 79,3% (§12.29, §12.30). A bắt đầu từ lỗi 0,375 của v8 giữ cao độ, D từ 0,343 của v8 nghe
SwiftF0, nên lượt A học lại phần log-mel nhiều hơn. Dải thấp chỉ giúp "bật": v8 bỏ chúng vẫn nhận "tắt" 64/69, tức nhận
nó bằng phổ trên 500 Hz, nơi `ă` và `â` của chủ repo gần trùng. Ở đó "tắt" nằm sát ranh giới với "bật": trong lượt v8,
"tắt" 28/09 với cao độ giữ lên xuống 7–21/22 giữa các mốc từ 6 000 (§12.21). Học lại phần log-mel dời ranh giới ấy về
phía "bật" ở A (nguyên âm `â` ở 64% câu "tắt"); học tiếp với SwiftF0 giữ nó như v8. v7, học với cao độ Kaldi, ngang v8 ở
mốc 6 000–12 000 rồi còn 4/22 ở mốc cuối so với 16/22 của v8.

### 12.32 Phần mở trước câu: mạng nguội trên board B khi chủ repo nói trực tiếp (09/10)

Log UART của board B ngày 08/10 (3 192 cửa sổ, mọi bộ lệnh và ngưỡng của ngày) theo độ dài cửa sổ: dưới 1,5 s nhận 8/484,
1,5–2,5 s 57/1 094, 2,5–3,5 s 205/1 032, từ 3,5 s 36/582. 1 446 cửa sổ (45%) mở ngay sau cửa sổ trước, phần mở trước câu
bị chặn dưới 2 s theo luật cắt (§5.4), nhận 6,8%; 1 746 cửa sổ đủ phần ấy nhận 11,9%.

Trên các câu lệnh của Cửa 3 (210 câu), v8 float, cả ba chiều cao độ giữ, bộ 10 lệnh, δ₁ 200 ‰, δ₂ 25 ‰; "nguội": cửa sổ
mở trước câu đúng chừng ấy, mạng bắt đầu từ bộ đệm rỗng như board; "ấm": mạng chạy qua đủ 2 s trước câu, chỉ các khung từ
chỗ mở ngắn hơn được chấm. Đo bằng script chẩn đoán chạy một lần:

| Mở trước câu | Nguội: đúng nhất | Nguội: nhận đúng | Ấm: đúng nhất | Ấm: nhận đúng |
|---|---|---|---|---|
| 0 s | 139 | 9 | 197 | 172 |
| 0,25 s | 193 | 160 | 197 | 172 |
| 0,5 s | 193 | 173 | 197 | 172 |
| 1 s | 195 | 171 | 197 | 172 |
| 2 s | 196 | 174 | 196 | 174 |

v8 cần chừng 0,5 s trước câu; `vad` gộp hai đoạn cách nhau dưới `utterance.gap_s` 0,4 s, nên cửa sổ bị chặn vẫn còn chừng
0,4 s trước câu và chỉ mất vài câu. Mạng nguội không giải thích được tỉ lệ nhận thấp khi nói trực tiếp.
