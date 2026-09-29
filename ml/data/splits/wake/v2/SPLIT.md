# wake/v2

Dựng bằng `python -m srpipe.tasks.wake.data` (`make splits`), seed 20260928, cấu hình mục `split` của
`ml/configs/models/wake.yaml`. Luật ở KẾ HOẠCH §1.3:

- Chỉ mẩu qua sàng lọc (`interim/screen/rejects.tsv`); không âm bản nào có lời chứa "chào mi na".
- Dương: mẩu TTS có `kept` của `interim/wake/synth_pos`; âm bản gần âm: của `interim/wake/synth_neg`.
- 20% người nói VIVOS train và 20% giọng có sẵn của VieNeu vào `val`
  trọn vẹn, cùng mọi giọng nhân bản từ họ; giọng nhân bản từ kho không có mã người nói chỉ vào `train`.
- `train_neg` rút 100 giờ lời nói ngẫu nhiên từ speech/vivos/train/, speech/fpt_open/, speech/vlsp/, speech/bud500/.
- `test_neg` là speech/common_voice_vi/, speech/vivos/test/: không kho nào đã làm giọng mẫu cho TTS. Đủ 24 giờ khi thêm nền phòng
  thu qua board (E11-T6); `test_pos` cũng chờ bản thu ấy.
- `train_hard`, `val_hard` (KẾ HOẠCH §3.11): mẩu TTS có `kept` của `interim/wake/synth_hard`, âm bản gần âm của
  `interim/wake/synth_neg`, và câu của kho học có lời khớp một mẫu của `split.hard`; không câu nào chứa
  "chào mẹ", "chào minh", "chào bạn", để phiên gần âm thu qua board đo được mô hình tổng quát hoá. `val_hard` bỏ mẩu đã có trong
  `val_neg`, vì `val` chấm báo nhầm trên cả hai file và mỗi mẩu chỉ được đếm một lần.

| File | Mẩu | Giờ | Mẩu TTS |
|---|---|---|---|
| `train_pos.txt` | 3466 | 1.00 | 3466 |
| `train_neg.txt` | 122483 | 100.11 | 370 |
| `val_pos.txt` | 118 | 0.03 | 118 |
| `val_neg.txt` | 2497 | 3.33 | 14 |
| `test_neg.txt` | 20717 | 22.94 | 0 |
| `train_hard.txt` | 2590 | 4.19 | 611 |
| `val_hard.txt` | 8 | 0.00 | 8 |

- train_pos.txt: 678456b24aa1a1ffef11cf787e5541468c4c1e470643fbc07eea98318a5ce38d
- train_neg.txt: 886c1714ab815352deed83b18a0fd7cf45c727f9721708fa54e4695850f32534
- val_pos.txt: 2581d0b7ac5cef937afd984ba9878d0ce793b2c7ff947b7ab0a0bbd4cb1b4a58
- val_neg.txt: 73be4cbeda8fd5751d8cd1c40c8af3d361c72a0b92a07d4ff7b01ab4d15152ed
- test_neg.txt: 504ff4f30d672dc6bb318e26c91805a78f21aea9abcedcb1dea17c637280169a
- train_hard.txt: 0bac482c584cb1562bb658fc7d4df8ec2d6abb0fb8363bffe286ac60963bfe5c
- val_hard.txt: 555040a915f93bfa27ced60a913a1570803cdc3661439115f0654a9fc5c0d5ee
