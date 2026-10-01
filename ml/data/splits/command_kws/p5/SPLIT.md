# command_kws/p5

Dựng bằng `python -m srpipe.tasks.command.kws.data split`, seed 20260930, mục `split` của
`ml/configs/models/command_kws.yaml` (KẾ HOẠCH §1.3, §3.12). Mỗi file một vai, một lớp, một nguồn:
`<vai>_<lớp>_<nguồn>.txt`; lớp theo thứ tự đầu ra của mạng: `yes`, `no`, `other`, `silence`.

- `real`: mẩu của Speech Commands v0.02 ở `raw/speech/speech_commands`, mỗi từ khoá {'train': 5000, 'val': 1000} mẩu một vai, các từ còn
  lại vào `other`, {'train': 2500, 'val': 300} mẩu một vai; `val` lấy từ `testing_list.txt`, `train` từ mẩu không nằm ở danh
  sách nào của bộ, nên người nói tách như bộ tách. Pilot kiểm đường kws trên kho nhiều người nói.
- `noise`: đoạn 1 tới 3 s của nhiễu MUSAN và DEMAND cho
  `silence`; mỗi file nhiễu chỉ ở một vai.
- Tập thử là phiên thu qua board (E11-T6), chưa có.

| File | Lớp | Mẩu | Giờ |
|---|---|---|---|
| `train_no_real.txt` | `no` | 3130 | 0.85 |
| `train_other_real.txt` | `other` | 2500 | 0.68 |
| `train_silence_noise.txt` | `silence` | 600 | 0.32 |
| `train_yes_real.txt` | `yes` | 3228 | 0.88 |
| `val_no_real.txt` | `no` | 405 | 0.11 |
| `val_other_real.txt` | `other` | 300 | 0.08 |
| `val_silence_noise.txt` | `silence` | 100 | 0.06 |
| `val_yes_real.txt` | `yes` | 419 | 0.11 |

- train_no_real.txt: d931216667ae661417ee77c9a383eaa61a2f6e6a0e38bc814fc94c55082575ac
- train_other_real.txt: 06e7ea94c4f66d0818e445a35689b0615ad8665e4e1dc52379f4db97147fd63c
- train_silence_noise.txt: a1b7d8645e13a5dc93adccc83d558f1d22782ad1d353a57b7067438dc1e07e5f
- train_yes_real.txt: 4d28502370d63e0f447fe1dbe2af15ba3d63c5daf77136c2c158f4d07d8d0dad
- val_no_real.txt: 6a66ae1f07aec7b60e168225f9e27986ef9cfec6b1ef2d01ea2f95b4c22f667f
- val_other_real.txt: d066edccf27c1a7b085583e662ebbb5cf99d3944447de01cf0b0e69efdec6020
- val_silence_noise.txt: bff1ed3031d5b8f18d4f504d8e3f0ba72ddb1f67e7a213da9fe2f37065f4704c
- val_yes_real.txt: 183864e7f291f0f312c3a67cc00c7429bbba6bfc0e4326e02355a5a1a261117e
