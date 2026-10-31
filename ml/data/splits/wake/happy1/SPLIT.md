# wake/happy1

Dựng bằng `python -m srpipe.tasks.wake.data split`, seed 20260928, mục `split` của `ml/configs/models/wake.yaml`
(KẾ HOẠCH §1.3). Pilot kiểm đường wake trên kho nhiều người nói, từ đánh thức "happy" của Speech Commands v0.02:

- `train_pos`, `val_pos`: mọi mẩu của từ ấy ở `raw/speech/speech_commands`, cắt ở cuối tiếng nói như mọi mẩu dương; `val` lấy từ
  `testing_list.txt`, `train` từ mẩu không nằm ở danh sách nào của bộ, nên người nói tách như bộ tách.
- `train_hard`, `val_hard`: các từ còn lại của bộ, rút theo seed 3000 và
  600 mẩu, rồi thêm 27000 mẩu cho `train` sau khi rút `val`, nên mọi lần rút trước đó giữ nguyên.
- `val_neg`, `test_neg`: lời nói công khai của `wake/v4`, không mẩu TTS nào. `train_neg`: 100 giờ lời nói rút theo seed từ speech/vivos/train/, speech/fpt_open/, speech/vlsp/, speech/lsvsc/, speech/vimd/, không câu nào đọc ra từ đánh thức, không người nói nào của `val_neg`, `test_neg`.

| File | Mẩu | Giờ | Mẩu TTS |
|---|---|---|---|
| `test_neg.txt` | 14669 | 16.82 | 0 |
| `train_hard.txt` | 30000 | 8.18 | 0 |
| `train_neg.txt` | 59560 | 100.00 | 0 |
| `train_pos.txt` | 1632 | 0.40 | 0 |
| `val_hard.txt` | 600 | 0.16 | 0 |
| `val_neg.txt` | 8531 | 9.45 | 0 |
| `val_pos.txt` | 203 | 0.05 | 0 |

- test_neg.txt: 3c20564d6cf0cef04c32b49c9fec4fbe80a099aeda37f14787d04d84ba425f69
- train_hard.txt: 819c10585941a915db39bce49bd7839fb7fbddf4933fc2b77f9d6715b9768863
- train_neg.txt: 7a7161770d17f0143f4a06cf03fd7f9a721845e8a438af5aa01eaeb0f589d04a
- train_pos.txt: 637c45ad79f46894fe37a0765aa637b83c33fcbf71b5137644ca067cac440bd6
- val_hard.txt: 0e2685a59ba8f5013c61a25cb53ab1bea7e6df40aec1aafde1ab48c0d744d204
- val_neg.txt: 3096d705f7be6db9f4dbe2fb805dc81f8de6bfb82c1f3c844070e28223c14d7d
- val_pos.txt: 4c31b834691b9721c8c860dc08164d33ac0a6442d80807c00e39eabd59a2e07a
