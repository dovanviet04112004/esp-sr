# command_kws/p2

Dựng bằng `python -m srpipe.tasks.command.kws.data split`, seed 20260930, mục `split` của
`ml/configs/models/command_kws.yaml` (KẾ HOẠCH §1.3, §3.12). Mỗi file một vai, một lớp, một nguồn:
`<vai>_<lớp>_<nguồn>.txt`; lớp theo thứ tự đầu ra của mạng: `bat_den`, `tat_den`, `bat_quat`, `tat_quat`, `other`, `silence`.

- `tts`: mẩu TTS có `kept` của `interim/command/synth_pos` (lệnh nó nói) và `synth_neg` (`other`: cụm gần âm, cụm mở
  đầu, nửa lệnh, cụm tay). 20% giọng có sẵn của VieNeu và 20% người nói VIVOS
  làm giọng mẫu vào `val` trọn vẹn cùng mọi giọng nhân bản từ họ; giọng nhân bản từ kho không mã người nói chỉ vào
  `train`.
- `real`: mẩu người thật của kho trích `None` và của `kws_vi_command` (lệnh của bộ; "bật hết", "tắt
  hết" vào `other`; nhiễu phòng của họ vào `silence`). Không kho nào có mã người nói nên chỉ vào `train`.
- `speech`: lời nói thường cho `other`, từ các file `train_*` của `command/v1` cho `train` và `val.txt` cho
  `val`, không câu nào nói một lệnh đã học; người nói có giọng nhân bản ở `val` không vào `train`.
- `noise`: đoạn 1 tới 3 s của nhiễu MUSAN và DEMAND cho
  `silence`; mỗi file nhiễu chỉ ở một vai.
- Tập thử là phiên thu qua board (E11-T6), chưa có.

| File | Lớp | Mẩu | Giờ |
|---|---|---|---|
| `train_bat_den_real.txt` | `bat_den` | 200 | 0.06 |
| `train_bat_quat_real.txt` | `bat_quat` | 200 | 0.06 |
| `train_other_real.txt` | `other` | 400 | 0.11 |
| `train_other_speech.txt` | `other` | 1248 | 1.00 |
| `train_silence_noise.txt` | `silence` | 600 | 0.32 |
| `train_silence_real.txt` | `silence` | 260 | 0.07 |
| `train_tat_den_real.txt` | `tat_den` | 200 | 0.06 |
| `train_tat_quat_real.txt` | `tat_quat` | 200 | 0.06 |
| `val_bat_den_real.txt` | `bat_den` | 23 | 0.01 |
| `val_bat_quat_real.txt` | `bat_quat` | 22 | 0.01 |
| `val_other_real.txt` | `other` | 51 | 0.01 |
| `val_other_speech.txt` | `other` | 171 | 0.20 |
| `val_silence_noise.txt` | `silence` | 100 | 0.06 |
| `val_tat_den_real.txt` | `tat_den` | 22 | 0.01 |
| `val_tat_quat_real.txt` | `tat_quat` | 25 | 0.01 |

- train_bat_den_real.txt: f88a0e67752d11f80b40b54988074086b2c93584f27f9ceda9604c80d96c7610
- train_bat_quat_real.txt: 26c6438f6c738b269d54fe93ea8bf6b2ef2f0d61ffc93894f08a4d9597fc35f8
- train_other_real.txt: 00fae90c98f5b713187e300c6c1b296c6639b7376e4c439eb838f1b41d9b818f
- train_other_speech.txt: fb9e19ec91fac7c11ef9f1c9196fb87c2fb06e48380845ed66bfec5f9b8b2ad7
- train_silence_noise.txt: a1b7d8645e13a5dc93adccc83d558f1d22782ad1d353a57b7067438dc1e07e5f
- train_silence_real.txt: eb87715ed17971a04e8a681c9dd44cc9c0a3304f643af79a3ad2796432241d93
- train_tat_den_real.txt: 255324464e0688f8ef49109c1754ddcdb95e405d5922e0787f2063681bd4ab58
- train_tat_quat_real.txt: c6a12861cc84fcf45f77995b1fd73ca8a1da82269bc4b81bce898633b7671cdc
- val_bat_den_real.txt: 11b140b81aef85cab87ba53496dd1f9672a906618210a86068ffed1ad1c683f5
- val_bat_quat_real.txt: 68b2478757f6bfe8f67e640b2029a1da79317b540ba928c900b581a634c13358
- val_other_real.txt: 6af229c444111fb49dd15eaae4aecff8837249fb68a8037c82795c4372e38200
- val_other_speech.txt: d4c0e0affbcd875f2a958eba137818f87548a275fa1275bde741affc915a168d
- val_silence_noise.txt: bff1ed3031d5b8f18d4f504d8e3f0ba72ddb1f67e7a213da9fe2f37065f4704c
- val_tat_den_real.txt: c2b6241a5f5e3745016526738f575ab10db53620050a85601d7e1fad2d0d1477
- val_tat_quat_real.txt: d3c6f599660bc995adb8cbc2d05f99e09af9c40c4a73fa96e198ca794bd52b7b
