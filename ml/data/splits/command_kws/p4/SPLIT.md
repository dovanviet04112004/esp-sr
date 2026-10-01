# command_kws/p4

Dựng bằng `python -m srpipe.tasks.command.kws.data split`, seed 20260930, mục `split` của
`ml/configs/models/command_kws.yaml` (KẾ HOẠCH §1.3, §3.12). Mỗi file một vai, một lớp, một nguồn:
`<vai>_<lớp>_<nguồn>.txt`; lớp theo thứ tự đầu ra của mạng: `yes`, `no`, `up`, `down`, `left`, `right`, `on`, `off`, `stop`, `go`, `other`, `silence`.

- `real`: mẩu của Speech Commands v0.02 ở `raw/speech/speech_commands`, mỗi từ khoá {'train': 200, 'val': 100} mẩu một vai, các từ còn
  lại vào `other`, {'train': 1200, 'val': 300} mẩu một vai; `val` lấy từ `testing_list.txt`, `train` từ mẩu không nằm ở danh
  sách nào của bộ, nên người nói tách như bộ tách. Pilot kiểm đường kws trên kho nhiều người nói.
- `noise`: đoạn 1 tới 3 s của nhiễu MUSAN và DEMAND cho
  `silence`; mỗi file nhiễu chỉ ở một vai.
- Tập thử là phiên thu qua board (E11-T6), chưa có.

| File | Lớp | Mẩu | Giờ |
|---|---|---|---|
| `train_down_real.txt` | `down` | 200 | 0.05 |
| `train_go_real.txt` | `go` | 200 | 0.05 |
| `train_left_real.txt` | `left` | 200 | 0.05 |
| `train_no_real.txt` | `no` | 200 | 0.05 |
| `train_off_real.txt` | `off` | 200 | 0.05 |
| `train_on_real.txt` | `on` | 200 | 0.05 |
| `train_other_real.txt` | `other` | 1200 | 0.33 |
| `train_right_real.txt` | `right` | 200 | 0.05 |
| `train_silence_noise.txt` | `silence` | 600 | 0.32 |
| `train_stop_real.txt` | `stop` | 200 | 0.05 |
| `train_up_real.txt` | `up` | 200 | 0.05 |
| `train_yes_real.txt` | `yes` | 200 | 0.05 |
| `val_down_real.txt` | `down` | 100 | 0.03 |
| `val_go_real.txt` | `go` | 100 | 0.03 |
| `val_left_real.txt` | `left` | 100 | 0.03 |
| `val_no_real.txt` | `no` | 100 | 0.03 |
| `val_off_real.txt` | `off` | 100 | 0.03 |
| `val_on_real.txt` | `on` | 100 | 0.03 |
| `val_other_real.txt` | `other` | 300 | 0.08 |
| `val_right_real.txt` | `right` | 100 | 0.03 |
| `val_silence_noise.txt` | `silence` | 100 | 0.06 |
| `val_stop_real.txt` | `stop` | 100 | 0.03 |
| `val_up_real.txt` | `up` | 100 | 0.03 |
| `val_yes_real.txt` | `yes` | 100 | 0.03 |

- train_down_real.txt: fc54abd7a3b91489b121c1a636235ede34d79e2d6294c0f617850bde067af500
- train_go_real.txt: 0628b80a69b023f3f754391b74b384cbdf3e9445aa73d9438e9e3601fabf8a8f
- train_left_real.txt: 3cbe1a917299ce157f8a926755a278fd96d29dfef0bfdf3a766a93dc2648a2aa
- train_no_real.txt: 89ce07812f07657fa660f78bacad014afe0e421850856a19347d617bbfc56a22
- train_off_real.txt: 2c9ec13861ab7b3411df66adbf19590fc8628cdc504e4226009f8402b436327f
- train_on_real.txt: 5df103453be03a1466005fd7ed779b3580855b82231bfb62449b4c9c787c3ce8
- train_other_real.txt: 297cb8ec105c7294e2506708c0ce7f824bd91e48df33ff36dae1d8b8f9525e14
- train_right_real.txt: 55d97483725a9863820d0c4245d32d4ff77e814b6c2a59918a65dd1b525e6f0c
- train_silence_noise.txt: a1b7d8645e13a5dc93adccc83d558f1d22782ad1d353a57b7067438dc1e07e5f
- train_stop_real.txt: 82e6747cc50f09d8bc656f6d40a7cf5f0ab5ce77f9163411353733c867530b96
- train_up_real.txt: 770e184ed9f7caac3a504d665fa96170f38db8c40008824bb92fae16e3185523
- train_yes_real.txt: 6cdb140182551a5ec70375eb98179fe3392fc5f0e1582fd6a5e967593f426967
- val_down_real.txt: b7eb791e65d4b35c7a4f99643d39e76647a266a17825e44941ca4ecb023b9bb8
- val_go_real.txt: 6474123962873ea54297b9941dbc3e141f2a340bc68a1978588253d324326b54
- val_left_real.txt: 12d772b1988aa6c7199a9a6f3929c51201d692a4f73152868b291a2c6646ef90
- val_no_real.txt: 619ff8595edfc47775b888175e56b702ec6a6650df99fad0c60509906ccc667b
- val_off_real.txt: 6d0da5fd1369b9f6ad0b3f952b62f1b2b2e991688a148d534a79c1d5becca862
- val_on_real.txt: 4c5d58d4f33f7407d5d473019602e425c83180da13d0985118e42b287a531fce
- val_other_real.txt: 040371786919442a6ec7133c575e4cfe1433480b304dac055664465719edff1b
- val_right_real.txt: 11a79b0f9d31cab87cecfbe831a35655e1f498ece830b6f399a775d5609dd4a7
- val_silence_noise.txt: bff1ed3031d5b8f18d4f504d8e3f0ba72ddb1f67e7a213da9fe2f37065f4704c
- val_stop_real.txt: 18be98734a472406066b071b63c47c39db1c5d640b5e8dcd6ed1db1155502aa4
- val_up_real.txt: 8bd6a179a5c1da7fbb4376de24f067547c2f4b1b8d6808ac07d23848570ee1bf
- val_yes_real.txt: baf44b3e160ab26a8ad32a44a71423ddf3a8de8f09d974399193b5cc7746762e
