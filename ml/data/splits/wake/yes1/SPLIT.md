# wake/yes1

Dựng bằng `python -m srpipe.tasks.wake.data split`, seed 20260928, mục `split` của `ml/configs/models/wake.yaml`
(KẾ HOẠCH §1.3). Pilot kiểm đường wake trên kho nhiều người nói, từ đánh thức "yes" của Speech Commands v0.02:

- `train_pos`, `val_pos`: mọi mẩu của từ ấy ở `raw/speech/speech_commands`, cắt ở cuối tiếng nói như mọi mẩu dương; `val` lấy từ
  `testing_list.txt`, `train` từ mẩu không nằm ở danh sách nào của bộ, nên người nói tách như bộ tách.
- `train_hard`, `val_hard`: các từ còn lại của bộ, rút theo seed 3000 và
  600 mẩu.
- `train_neg`, `val_neg`, `test_neg`: nguyên văn của `wake/v4`.

| File | Mẩu | Giờ | Mẩu TTS |
|---|---|---|---|
| `test_neg.txt` | 14669 | 16.82 | 0 |
| `train_hard.txt` | 3000 | 0.82 | 0 |
| `train_neg.txt` | 122229 | 100.01 | 51 |
| `train_pos.txt` | 3228 | 0.78 | 0 |
| `val_hard.txt` | 600 | 0.16 | 0 |
| `val_neg.txt` | 8532 | 9.45 | 1 |
| `val_pos.txt` | 419 | 0.10 | 0 |

- test_neg.txt: 3c20564d6cf0cef04c32b49c9fec4fbe80a099aeda37f14787d04d84ba425f69
- train_hard.txt: 55ae69526c0f3988d11b55c9b54c1bf988c8a7f8e357164b94d424defb6ed786
- train_neg.txt: 7d21edf14326df1a016a51c7521222101d98a3ea2692676688afc5226c4cf0a9
- train_pos.txt: e2316eaceafc3a7674b67cdc7a0cd610d36d11fc06ff0dbb6a1f232700fe8bed
- val_hard.txt: f59bdaaed4992adab671bd51f711ade36ba70ca850d4ade933579b2c2eebfdcf
- val_neg.txt: 45530069a576d93a0279a8ed84bb0ca1dd206308a2e2db4abb69f986feee7e6b
- val_pos.txt: 2f02b04f39b557474f27254d93f0397c7d762d318b75367cfefd8a280d6e82a7
