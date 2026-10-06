# command/v6

Dựng bằng `python -m srpipe.tasks.command.ctc.data` (`make splits`), seed 20260928, cấu hình mục `split` của
`ml/configs/models/command_ctc.yaml` và `unseen` của `ml/configs/models/command.yaml`. Luật ở KẾ HOẠCH §1.3:

- Chỉ mẩu qua sàng lọc (`interim/screen/rejects.tsv`).
- Theo người nói: mỗi kho trao cho `val` và `test` phần người nói của nó, còn lại vào `train`; kho không có mã người
  nói chỉ vào `train`.
  - `speech/common_voice_vi/`: {'val': 0.05, 'test': 0.1}
  - `speech/vivos/train/`: {'val': 0.1}
  - `speech/vivos/test/`: {'test': 1.0}
  - `speech/fpt_open/`: chỉ train
  - `speech/vlsp/`: chỉ train
- Lệnh chưa học (E11-T13) không có trong `train` và `val`: bỏ "chụp ảnh" 63 mẩu có lời chứa lệnh ấy. Ở `test` thì giữ.
- Kho có trần giờ chỉ giữ phần rút theo seed tới trần: không kho nào.
- `train` chia một file mỗi kho; vai của file là phần tên trước dấu `_` đầu tiên.
- Phiên thu qua board vào `train` (KẾ HOẠCH §1.3, `eval.board.train` của `command.yaml`): 20261004_home_001, 20261004_home_002, 20261004_home_003, 20261004_home_004, 20261004_home_005, 20261004_home_006, 20261004_home_007, 20261004_home_008, 20261004_home_009, 20261004_home_010; cắt như Cửa 3, giữ câu từ 0.8 đến 2.4 s, shard lặp 75 lần trong thứ tự shard.
- `val_commands.txt`: tới 60 mẩu mỗi lệnh đã học của kho trích `hf_extract`, người thật nói đúng lời lệnh, rút theo seed; không học, chỉ để chọn δ₁, δ₂ (KẾ HOẠCH §1.3, §3.12).

| File | Mẩu | Giờ | Người nói |
|---|---|---|---|
| `test.txt` | 2000 | 1.98 | 59 |
| `train_common_voice_vi.txt` | 18502 | 20.73 | 342 |
| `train_fpt_open.txt` | 25432 | 29.44 | 0 |
| `train_vivos.txt` | 10266 | 13.25 | 41 |
| `train_vlsp.txt` | 55687 | 100.82 | 0 |
| `val.txt` | 1583 | 1.88 | 25 |
| `val_commands.txt` | 216 | 0.05 | 0 |

- test.txt: 6cd80f62b2a45544e3352d14586224ad21d2cb6b9a9cbb2e86e12ab95401355f
- train_common_voice_vi.txt: 341fb26c92159d914a40151370760c646f9443c175938b046ac10e4febd3732a
- train_fpt_open.txt: 475bfd70a8b38c711d7e82f137fc038108fa9e211ff1dbd79a02ab2858592d30
- train_vivos.txt: 5ef518155363071203e5928cc4aa27b19272c869e0a0e28c881a3ebd080539f7
- train_vlsp.txt: 0a6a8226698d806e6233413b260a002f65f750f6c20b515f5d26c62128fe70b3
- val.txt: a424db43e9ca376aaafc1efe651f6464db1c83876548154328175a0249da4e4d
- val_commands.txt: fc6f05df819ec4961a9880726dcc758d0e937af802b257a7ecdc0995af6a93ee
