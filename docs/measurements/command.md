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
QAT thấp nhất. QAT học trên đồ thị lô 16, tốc độ học 3e-5 giảm cosine về 1e-6; lỗi đơn vị `val` của đồ thị ấy 31,38% ở
bước 0, 31,54%, 31,63%, 31,33% ở bước 500, 1 000, 1 500 và 31,38% ở bước 2 000, loss 0,977 xuống 0,969: phần học gần như
không đổi `val`, và chưa tách được phần 0,59 điểm QAT hơn percentile trên `test` đến từ việc học hay từ thang hiệu chuẩn
trên lô 16 chép sang đồ thị lô 1.

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
