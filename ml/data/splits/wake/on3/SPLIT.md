# wake/on3

Dựng bằng `python -m srpipe.tasks.wake.data split`, seed 20260928, mục `split` của `ml/configs/models/wake.yaml`
(KẾ HOẠCH §1.3). Pilot kiểm đường wake trên kho nhiều người nói, từ đánh thức "on" của Speech Commands v0.02:

- `train_pos`, `val_pos`: mọi mẩu của từ ấy ở `raw/speech/speech_commands`, cắt ở cuối tiếng nói như mọi mẩu dương; `val` lấy từ
  `testing_list.txt`, `train` từ mẩu không nằm ở danh sách nào của bộ, nên người nói tách như bộ tách.
- `train_hard`, `val_hard`: các từ còn lại của bộ, rút theo seed 3000 và
  600 mẩu, rồi thêm 27000 mẩu cho `train` sau khi rút `val`, nên mọi lần rút trước đó giữ nguyên.
- `val_neg`, `test_neg`: lời nói công khai của `wake/v4`, không mẩu TTS nào. `train_neg`: 100 giờ lời nói rút theo seed từ speech/vivos/train/, speech/fpt_open/, speech/vlsp/, speech/lsvsc/, speech/vimd/, không câu nào đọc ra từ đánh thức, không người nói nào của `val_neg`, `test_neg`.

| File | Mẩu | Giờ | Mẩu TTS |
|---|---|---|---|
| `test_neg.txt` | 14669 | 16.82 | 0 |
| `train_hard.txt` | 30000 | 8.17 | 0 |
| `train_neg.txt` | 59403 | 100.00 | 0 |
| `train_pos.txt` | 3086 | 0.73 | 0 |
| `val_hard.txt` | 600 | 0.16 | 0 |
| `val_neg.txt` | 8531 | 9.45 | 0 |
| `val_pos.txt` | 396 | 0.09 | 0 |

- test_neg.txt: 3c20564d6cf0cef04c32b49c9fec4fbe80a099aeda37f14787d04d84ba425f69
- train_hard.txt: 536c23abf241c7c944959bf59ced53b3b420e633789f8ac73fecba0b0f2ddaa3
- train_neg.txt: 865e6f8ee7713f4f70f3264ad37e24c708c87be6474e9e74b3bf56fbdd140ea3
- train_pos.txt: 479615dde23fa234063a48b2d696bc37d57d174ac0f7a7b17cbb014c6f08799f
- val_hard.txt: cfc998e848096e851e73a435fb4561e669ac1aad1b53e347572c1cf46afab2a4
- val_neg.txt: 3096d705f7be6db9f4dbe2fb805dc81f8de6bfb82c1f3c844070e28223c14d7d
- val_pos.txt: 92699f6d5645c4dc963f8071d38f271005fe83736bb5abda12eb5ceaff4ed90c
