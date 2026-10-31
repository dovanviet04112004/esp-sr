# wake/vung1

Dựng bằng `python -m srpipe.tasks.wake.data` (`make splits`), seed 20260928, cấu hình mục `split` của
`ml/configs/models/wake.yaml`. Luật ở KẾ HOẠCH §1.3:

- Chỉ mẩu qua sàng lọc (`interim/screen/rejects.tsv`); không âm bản nào có lời đọc như "vừng ơi" theo giọng
  Bắc, dù viết cách nào.
- Dương: mẩu TTS có `kept` của `interim/wake/synth_pos`, cắt bỏ khoảng lặng sau từ (`@0-cuối`); âm bản gần
  âm: của `interim/wake/synth_neg`. Mẩu dương nào cũng chỉ có từ đánh thức và dừng ở âm cuối của nó.
- 20% người nói VIVOS train và 20% giọng có sẵn của VieNeu vào `val`
  trọn vẹn, cùng mọi giọng nhân bản từ họ; giọng nhân bản từ kho không có mã người nói chỉ vào `train`.
- `train_neg` rút 100 giờ lời nói ngẫu nhiên từ speech/vivos/train/, speech/fpt_open/, speech/vlsp/, speech/lsvsc/, speech/vimd/.
- `test_neg` là speech/common_voice_vi/, speech/vivos/test/: không kho nào đã làm giọng mẫu cho TTS. Đủ 24 giờ khi thêm nền phòng
  thu qua board (E11-T6); `test_pos` cũng chờ bản thu ấy.
- 40% người nói của speech/common_voice_vi/ rời `test_neg` sang `val_neg` trọn vẹn, rút theo seed, để mục tiêu báo nhầm của
  `val` dựa trên nhiều lần vượt (KẾ HOẠCH §3.11).
- `train_hard`, `val_hard` (KẾ HOẠCH §3.11): mẩu TTS có `kept` của `interim/wake/synth_hard`, âm bản gần âm của
  `interim/wake/synth_neg`, và câu của kho học có lời khớp một mẫu của `split.hard`; không câu nào chứa
  , để phiên gần âm thu qua board đo được mô hình tổng quát hoá. `val_hard` bỏ mẩu đã có trong
  `val_neg`, vì `val` chấm báo nhầm trên cả hai file và mỗi mẩu chỉ được đếm một lần.

| File | Mẩu | Giờ | Mẩu TTS |
|---|---|---|---|
| `train_pos.txt` | 1676 | 0.60 | 1676 |
| `train_neg.txt` | 59939 | 100.03 | 72 |
| `val_pos.txt` | 68 | 0.02 | 68 |
| `val_neg.txt` | 18494 | 27.60 | 6 |
| `test_neg.txt` | 14669 | 16.82 | 0 |
| `train_hard.txt` | 826 | 1.19 | 181 |
| `val_hard.txt` | 11 | 0.00 | 11 |

- train_pos.txt: d6eb0dc922f6748cb93e53ac9147e3201a3548de29973c53a922925eb069ecc3
- train_neg.txt: 4087b9d0044f5a8489827fce34b1872402940572ff6e0495fabab6381b22e0f7
- val_pos.txt: dad0d96abda0a33a027a5d95e0f73555e46ef212c3611013dd0fc9c5a1097aef
- val_neg.txt: 02a0a2b585088ce04f6249ecde28df567840014220ff861ec35c97b0d7336b6a
- test_neg.txt: 3c20564d6cf0cef04c32b49c9fec4fbe80a099aeda37f14787d04d84ba425f69
- train_hard.txt: 6e643e621104ab5d36f7fc1ab629c401b3c78d0dc5c585d1846eaa760aa7e233
- val_hard.txt: 3237b0b4d590018294c2549de6cf5a248c535e1469158d9962401c7018cd4eca
