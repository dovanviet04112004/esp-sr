# command/v9_esp32s31

Mọi file `.txt` chép nguyên byte từ `command/v9`: cùng mẩu, cùng vai, cùng seed 20260928 và cùng luật như
`command/v9/SPLIT.md`. Phiên bản riêng này chỉ để bản dựng của nó, chiều cao độ tính bằng hàng `pitch` int8 lượng tử cho
`esp32s31`, có thư mục riêng ở `processed/command/` và không bao giờ đứng thay `v9` của S3 (KẾ HOẠCH §3.14).

- Phiên thu qua board vào `train` (KẾ HOẠCH §1.3, `eval.board.train` của `command.yaml`): 20261004_home_001, 20261004_home_002, 20261004_home_003, 20261004_home_004, 20261004_home_005, 20261004_home_006, 20261004_home_007, 20261004_home_008, 20261004_home_009, 20261004_home_010; cắt như Cửa 3, giữ câu từ 0.8 đến 2.4 s, shard lặp 75 lần trong thứ tự shard.

- test.txt: 6cd80f62b2a45544e3352d14586224ad21d2cb6b9a9cbb2e86e12ab95401355f
- train_common_voice_vi.txt: 341fb26c92159d914a40151370760c646f9443c175938b046ac10e4febd3732a
- train_fpt_open.txt: a3d12d1ca418553ac8e70451322b6b91ce4dc84e7bfc98153e817d06cbde546f
- train_lsvsc.txt: fe53c0c7edf5575e1e1ca1e2ccd0508316870a03d57b711978f4f10a69418f5a
- train_speech_massive_vi.txt: f9737cb339471c43134ff9dde847a9ca780e1b62178ed93de5331da7b622bd42
- train_vimd.txt: 6386fdd5510bcf46ac18181544349b3942d93cfef231f3466a3850788774c0a7
- train_vivos.txt: 5ef518155363071203e5928cc4aa27b19272c869e0a0e28c881a3ebd080539f7
- train_vlsp.txt: 0a6a8226698d806e6233413b260a002f65f750f6c20b515f5d26c62128fe70b3
- val.txt: a424db43e9ca376aaafc1efe651f6464db1c83876548154328175a0249da4e4d
- val_commands.txt: fc6f05df819ec4961a9880726dcc758d0e937af802b257a7ecdc0995af6a93ee
