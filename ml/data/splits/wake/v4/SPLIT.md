# wake/v4

Dựng bằng `python -m srpipe.tasks.wake.data` (`make splits`), seed 20260928, cấu hình mục `split` của
`ml/configs/models/wake.yaml`. Luật ở KẾ HOẠCH §1.3:

- Chỉ mẩu qua sàng lọc (`interim/screen/rejects.tsv`); không âm bản nào có lời đọc như "trợ lý" theo giọng
  Bắc, dù viết cách nào.
- Dương: mẩu TTS có `kept` của `interim/wake/synth_pos`, cắt bỏ khoảng lặng sau từ (`@0-cuối`), và mẩu người thật nói từ ấy của kho trích `hf_extract` (`raw/speech/hf_extract/`), cắt
  đúng hai tiếng; kho ấy không có mã người nói nên mẩu chỉ vào `train`; âm bản gần
  âm: của `interim/wake/synth_neg`. Mẩu dương nào cũng chỉ có từ đánh thức và dừng ở âm cuối của nó.
- 20% người nói VIVOS train và 20% giọng có sẵn của VieNeu vào `val`
  trọn vẹn, cùng mọi giọng nhân bản từ họ; giọng nhân bản từ kho không có mã người nói chỉ vào `train`.
- `train_neg` rút 100 giờ lời nói ngẫu nhiên từ speech/vivos/train/, speech/fpt_open/, speech/vlsp/, speech/bud500/.
- `test_neg` là speech/common_voice_vi/, speech/vivos/test/: không kho nào đã làm giọng mẫu cho TTS. Đủ 24 giờ khi thêm nền phòng
  thu qua board (E11-T6); `test_pos` cũng chờ bản thu ấy.
- 40% người nói của speech/common_voice_vi/ rời `test_neg` sang `val_neg` trọn vẹn, rút theo seed, để mục tiêu báo nhầm của
  `val` dựa trên nhiều lần vượt (KẾ HOẠCH §3.11).

| File | Mẩu | Giờ | Mẩu TTS |
|---|---|---|---|
| `train_pos.txt` | 2243 | 0.42 | 2219 |
| `train_neg.txt` | 122229 | 100.01 | 51 |
| `val_pos.txt` | 74 | 0.01 | 74 |
| `val_neg.txt` | 8532 | 9.45 | 1 |
| `test_neg.txt` | 14669 | 16.82 | 0 |

- train_pos.txt: 7d2915191cfcf1fd6689e166c1674ed783adf47f00cbef7dc1cb7c5e6c316659
- train_neg.txt: 7d21edf14326df1a016a51c7521222101d98a3ea2692676688afc5226c4cf0a9
- val_pos.txt: 427f32fd5545ae6b14e78d9e1be2cab1f98381d5b9cda9e3ff5fd6d6747e1ded
- val_neg.txt: 45530069a576d93a0279a8ed84bb0ca1dd206308a2e2db4abb69f986feee7e6b
- test_neg.txt: 3c20564d6cf0cef04c32b49c9fec4fbe80a099aeda37f14787d04d84ba425f69
