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
thanh ra đúng sắc, phụ âm đầu và nguyên âm vẫn sai. Ba hướng chưa tách: tiếng bật hơi của "t" chìm dưới nền ồn của board
(`mic_array.md` §4), cao độ đặt lại ở đầu cửa sổ chưa có quá khứ cho âm tiết đầu, giọng riêng của một người nói.

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
