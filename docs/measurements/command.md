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
