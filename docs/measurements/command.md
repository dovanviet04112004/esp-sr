# Cửa 3 của `command` trên phiên thu qua board

Thước của KẾ HOẠCH §3.12: mỗi lệnh ≥ 90% câu đúng lệnh, từ chối đúng ≥ 95% câu còn lại, trên phiên board B ở dịch 13
của `ml/data/manifests/device/board_b.csv`, loại `cmd`, `neg`, `noise`, trừ `20260928_home_039`. Chấm bằng
`make command-eval TRACK=<kws|ctc> RUN=<run>`.

## 1. `ctc` float, lượt CTC trơn đầu tiên (E11-T12, E11-T13)

Run `20261002_128545c-dirty_64e7a4`: 40 000 bước CTC trơn trên `command/v2` mô phỏng, `val` loss 0,91, tỉ lệ lỗi đơn vị
29,5%; chấm ở float, chưa int8. Chấm ngày 02/10 ở `0e0651b`. Một người nói (`spk_001`), phòng ở nhà, 1 m và 3 m, mỗi
phiên lệnh 25 s. Cửa sổ mỗi câu bắt đầu 0,3 s trước câu `vad` tìm ra, kết thúc ở bước `vad` tắt, dài tối đa 3 s; bộ lệnh
là 10 dòng của `default_vi.json` qua `lang_vi` ba vùng, 19 biến thể, "chụp ảnh" là lệnh chưa học. Ngưỡng chưa chỉnh,
nên bảng đầu đếm câu mà lệnh điểm cao nhất là lệnh đúng.

| Lệnh | 1 m | 3 m | Cả hai |
|---|---|---|---|
| bật đèn | 5/7 | 1/5 | 6/12 |
| tắt đèn | 0/6 | 0/5 | 0/11 |
| bật quạt | 6/6 | 1/5 | 7/11 |
| tắt quạt | 0/5 | 1/6 | 1/11 |
| mở cửa | 5/5 | 5/5 | 10/10 |
| đóng cửa | 6/6 | 3/5 | 9/11 |
| tăng âm lượng | 1/8 | 0/5 | 1/13 |
| giảm âm lượng | 3/5 | 0/6 | 3/11 |
| dừng lại | 5/6 | 2/5 | 7/11 |
| chụp ảnh (chưa học) | 6/6 | 4/5 | 10/11 |
| **Cả bộ** | **37/60 (62%)** | **17/52 (33%)** | **54/112 (48%)** |

Quét `δ₁` ở `δ₂` 50‰: phần câu lệnh được nhận đúng lệnh và phần câu còn lại bị từ chối.

| `δ₁` ‰ | Lệnh kém nhất | Trung bình các lệnh | Từ chối (cửa 95%) | `noise` | `neg` | `cmd` tiếng Anh |
|---|---|---|---|---|---|---|
| 100 | 0% | 5% | 86/86 | 2/2 | 24/24 | 60/60 |
| 200 | 0% | 12% | 85/86 | 2/2 | 23/24 | 60/60 |
| 300 | 0% | 13% | 84/86 | 2/2 | 22/24 | 60/60 |
| 400 | 0% | 23% | 83/86 | 2/2 | 21/24 | 60/60 |
| 500 | 0% | 29% | 81/86 | 2/2 | 19/24 | 60/60 |
| 600 | 0% | 32% | 81/86 | 2/2 | 19/24 | 60/60 |
| 800 | 0% | 39% | 80/86 | 2/2 | 18/24 | 60/60 |
| 1000 | 0% | 41% | 78/86 | 2/2 | 17/24 | 59/60 |

Xa Cửa 3. "Tắt đèn" và "tắt quạt" gần như luôn ra "bật đèn", "bật quạt"; ba âm tiết "tăng âm lượng" thường ra lệnh hai âm
tiết với khoảng cách nhất–nhì dưới 50‰. Cùng đường chấm ấy giải tự do câu của tập thử mô phỏng gần đúng, còn trên phiên
board ra rất ít đơn vị: chỗ hỏng là khoảng cách giữa mô phỏng và board (`mic_array.md` §4), nên mô phỏng được sửa theo
KẾ HOẠCH §1.2 trước khi học lại.
