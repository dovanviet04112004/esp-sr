# Bàn so với bản ngoài — AFE của ESP-SR, NSNet2, RNNoise (E9-T9)

Bàn so của KẾ HOẠCH §3.16. Bộ mục, biến thể và thước ở `ml/configs/afe/compare.yaml`; số từng mục, từng biến thể ở
`compare.csv` cùng thư mục.

## Khối không gian ESP-SR có trên S3

ESP-SR 2.5.5, `managed_components` của `test_apps/espsr_compare`, 30/09:

| Khối | Có cho S3 không | Căn cứ |
|---|---|---|
| BSS, qua AFE (SE với hai micro) | có | chạy trên board B, mục dưới |
| GSC (`esp_gsc.h`, `gsc_core_types.h`) | không | header chỉ có ở `include/esp32p4*` và `include/esp32s31`, không ở `include/esp32s3` |
| MASE (`esp_mase.h`: hai micro thẳng hàng hoặc ba micro vòng, khung 16 ms) | không chạy được | có header và lớp bọc `esp_mase.c` trong `libesp_audio_processor.a`, nhưng link báo thiếu `mase_create_normal_mode`, `mase_create_wake_up_mode`, `mase_process_normal_mode`, `mase_process_wake_up_mode`, `mase_destroy_normal_mode`, `mase_destroy_wake_up_mode`: không thư viện S3 nào định nghĩa chúng |

## Ba kênh ra của AFE ESP-SR

Đo ngày 30/09 trên board B, ESP-SR 2.5.5, AFE loại nhận dạng, hai micro, chỉ bật SE (BSS) và VAD WebRTC, không
WakeNet, như `test_apps/espsr_compare`. `afe_fetch_result_t` trả `raw_data` ba kênh; `data` là kênh AFE tự chọn.

**Cách xếp và kênh AFE chọn.** Một bản dựng chẩn đoán so `data` với từng kênh của `raw_data` ở mỗi lần `fetch`, theo
hai cách xếp. `data` trùng từng mẫu với kênh 2 khi đọc `raw_data` **xen kẽ**, ở mọi lần `fetch`: 301/301 lần trên
`tinyai_music_voice`, 1094/1094 lần trên `board_read_music`; không kênh nào khớp khi đọc liền từng kênh.
`trigger_channel_id` luôn là 2.

**Kênh nào là gì.** Mỗi kênh được dựng lại bằng bình phương tối thiểu từ hai micro qua bộ lọc 64 nhịp mỗi micro, sau
khi canh trễ bằng tương quan chéo:

| Mục | Kênh | Mức dBFS | Phần dư của phép dựng | Năng lượng bộ lọc từ micro 0 / micro 1 |
|---|---|---|---|---|
| `board_read_music` | 0 | −56,4 | −1,9 dB | 0,736 / 0,046 |
| `board_read_music` | 1 | −37,8 | −10,3 dB | 2,175 / 1,043 |
| `board_read_music` | 2 | −47,7 | −16,8 dB | 0,907 / 0,000 |
| `tinyai_music_voice` | 0 | −28,2 | −0,2 dB | 0,560 / 0,143 |
| `tinyai_music_voice` | 1 | −19,4 | −3,1 dB | 0,743 / 0,834 |
| `tinyai_music_voice` | 2 | −24,9 | −5,6 dB | 1,066 / 0,153 |

Trên `board_read_music`, kênh 2 chỉ lấy từ micro 0 và có mức trùng `raw_ch0`: −55,9 dBFS ở đoạn chỉ có nhạc và
−47,3 dBFS ở đoạn có lời, so với −55,8 và −47,3 của `raw_ch0`. Kênh 0 và 1 không dựng lại tuyến tính được từ hai micro:
đó là hai lối ra của phép tách. Kênh 1 lên 7,7 dB khi có lời so với đoạn chỉ có nhạc, kênh 0 xuống 1,2 dB.

**Kênh giữ người nói.** Thước của `compare score` trên từng kênh, mọi mục biết đoạn (số là thay đổi so với `raw_ch0`;
nhiễu và tiếng nói đo trên đoạn chỉ nhiễu và đoạn có lời, SI-SDR và STOI so với phần sạch của mục trộn):

| Mục | Kênh | Nhiễu dB | Tiếng nói dB | SNR tăng dB | SI-SDR dB | STOI |
|---|---|---|---|---|---|---|
| `board_read_1m` | AFE chọn / 0 / 1 | −0,04 / −0,02 / +1,71 | 0,0 / −13,28 / +10,47 | +0,04 / −13,26 / +8,76 | | |
| `board_read_3m` | AFE chọn / 0 / 1 | −0,03 / +0,63 / +5,69 | 0,0 / −12,90 / +10,34 | +0,03 / −13,53 / +4,65 | | |
| `board_read_fan` | AFE chọn / 0 / 1 | −0,03 / −1,07 / +3,94 | 0,0 / −12,73 / +10,43 | +0,03 / −11,66 / +6,49 | | |
| `board_read_music` | AFE chọn / 0 / 1 | −0,12 / +0,61 / +10,55 | | | | |
| `board_cmd_bat_den` | AFE chọn / 0 / 1 | −0,04 / −0,46 / +3,93 | 0,0 / −13,15 / +10,60 | +0,04 / −12,69 / +6,67 | | |
| `board_cmd_tang_am_luong` | AFE chọn / 0 / 1 | −0,02 / −0,39 / +6,26 | 0,0 / −12,83 / +10,52 | +0,02 / −12,44 / +4,26 | | |
| `mix_fan_snr5` | AFE chọn / 0 / 1 | −0,11 / +2,24 / +5,32 | 0,0 / −5,94 / +9,38 | +0,11 / −8,18 / +4,06 | 0,44 / −27,10 / 5,12 | 0,416 / 0,072 / 0,518 |
| `mix_fan_snr0` | AFE chọn / 0 / 1 | −0,09 / +2,70 / +8,50 | 0,0 / −4,45 / +7,76 | +0,09 / −7,15 / −0,74 | −3,55 / −26,88 / 0,15 | 0,332 / 0,062 / 0,473 |
| `mix_music_snr5` | AFE chọn / 0 / 1 | −0,15 / −1,82 / +8,56 | 0,0 / −7,53 / +9,51 | +0,15 / −5,71 / +0,95 | 1,13 / −20,19 / 3,45 | 0,588 / 0,165 / 0,562 |
| `mix_music_snr0` | AFE chọn / 0 / 1 | −0,05 / −2,88 / +7,13 | 0,0 / −5,93 / +8,54 | +0,05 / −3,05 / +1,41 | −3,70 / −23,30 / −0,71 | 0,415 / 0,082 / 0,492 |

- Kênh AFE chọn khi không có từ đánh thức là micro đầu gần như thô: mọi thay đổi dưới 0,2 dB.
- Kênh 1 giữ người nói ở cả mười mục: tiếng nói to lên 8–11 dB, vì BSS không chiếu lối ra về mức của micro; kênh 0 là
  phần còn lại, tiếng nói nhỏ đi 4–13 dB.
- Vì vậy `espsr_bss` lấy kênh 1 và `espsr_bss_ch0` lấy kênh 0 (KẾ HOẠCH §3.16).
- Cả ba kênh chạy trước lối vào: kênh 2 sớm 2048 mẫu, kênh 0 và 1 sớm 1024 mẫu. Lúc đo, app nạp tới nửa ring của AFE
  trước lần `fetch` đầu và AFE bỏ hai khối 1024 mẫu đầu ("Ringbuffer of AFE(FEED) is full" hai lần mỗi lượt). Thước
  canh trễ bằng tương quan chéo nên số trên không lệch.

## Dìm nhiễu trên cùng lối vào gsc (08/10)

Bốn mục trộn: đoạn đọc `20260928_home_005` với quạt `…_008` hay nhạc không lời `…_010`, thu riêng qua board B, ở SNR 0 và
5 dB. Thước `compare.measure`: nhiễu giảm trên đoạn chỉ nhiễu, mức giảm trên đoạn có lời (tiếng cộng phần nhiễu trong đoạn
ấy), SNR tăng, STOI so với `ch0` của phần sạch. Các biến thể là tệp của bàn so trên đĩa; các ứng viên `ns` đang học (E9-T4,
lượt `20261008_dd911af-dirty_1ea86b`, trọng số cuối epoch 1, không sàn) chạy trên lối vào của khe `ns` (trung bình hai micro
sau `hpf`, `balance`), không qua `gsc`. Đo một lần bằng script gọi `scenes.compare.measure`.

| Biến thể | `mix_fan_snr0` | `mix_fan_snr5` | `mix_music_snr0` | `mix_music_snr5` |
|---|---|---|---|---|
| `pc_mean` | 5,0 / 2,4 / +2,6 / 0,41 | 5,0 / 1,3 / +3,7 / 0,48 | 1,2 / 0,9 / +0,3 / 0,47 | 1,7 / 0,7 / +1,1 / 0,62 |
| `pc_mean_omlsa` | 16,4 / 4,8 / +11,7 / 0,45 | 16,4 / 2,2 / +14,3 / 0,51 | 3,4 / 1,4 / +2,0 / 0,49 | 5,0 / 1,0 / +4,0 / 0,62 |
| `pc_gsc` | 7,6 / 2,8 / +4,8 / 0,43 | 10,1 / 1,5 / +8,6 / 0,49 | 3,7 / 1,6 / +2,1 / 0,46 | 4,2 / 1,3 / +2,9 / 0,54 |
| `pc_gsc_omlsa` | 9,8 / 3,6 / +6,1 / 0,42 | 18,6 / 2,3 / +16,3 / 0,51 | 3,8 / −0,3 / +4,1 / 0,47 | 6,4 / 1,6 / +4,8 / 0,55 |
| `pc_gsc_nsnet2` | 41,8 / 8,6 / +33,2 / 0,48 | 40,0 / 5,7 / +34,3 / 0,52 | 34,1 / 9,8 / +24,2 / 0,49 | 37,4 / 6,9 / +30,4 / 0,54 |
| `pc_gsc_rnnoise` | 20,0 / 7,5 / +12,5 / 0,45 | 57,9 / 4,5 / +53,5 / 0,51 | 27,7 / 8,1 / +19,5 / 0,49 | 47,3 / 4,9 / +42,4 / 0,52 |
| `board_gsc_espsr_nsnet2` | 23,9 / 6,7 / +17,2 / 0,48 | 26,7 / 3,9 / +22,9 / 0,52 | 14,1 / 6,9 / +7,2 / 0,50 | 21,0 / 4,6 / +16,4 / 0,54 |
| `board_gsc_espsr_webrtc_aggressive` | 14,5 / 5,2 / +9,3 / 0,44 | 21,4 / 2,4 / +18,9 / 0,50 | 7,0 / 2,9 / +4,1 / 0,45 | 11,7 / 2,8 / +8,9 / 0,51 |
| RNNoise-16k, epoch 1 | 47,9 / 7,0 / +41,0 / 0,45 | 55,5 / 3,3 / +52,2 / 0,50 | 33,5 / 13,0 / +20,5 / 0,37 | 41,0 / 4,2 / +36,8 / 0,57 |
| NSNet-16k S, epoch 1 | 47,0 / 5,5 / +41,5 / 0,44 | 41,1 / 2,6 / +38,5 / 0,50 | 35,2 / 3,2 / +32,0 / 0,48 | 41,8 / 2,1 / +39,7 / 0,57 |
| NSNet-16k M, epoch 1 | 44,3 / 5,8 / +38,5 / 0,45 | 53,0 / 2,8 / +50,2 / 0,51 | 31,3 / 3,2 / +28,1 / 0,49 | 20,7 / 2,1 / +18,6 / 0,57 |
| NSNet-16k L, epoch 1 | 46,2 / 6,3 / +39,9 / 0,46 | 45,0 / 2,9 / +42,2 / 0,50 | 26,6 / 3,1 / +23,4 / 0,48 | 17,9 / 1,8 / +16,1 / 0,57 |

Ô: nhiễu giảm dB / mức đoạn có lời giảm dB / SNR tăng dB / STOI.

- NSNet2 và RNNoise công bố, sau `gsc`, dìm quãng nghỉ 20–58 dB và hạ đoạn có lời 4,5–9,8 dB; OM-LSA dìm quạt 16 dB nhưng
  nhạc chỉ 3–5 dB. STOI của mọi bộ dìm nhiễu nằm trong ±0,05 của lối vào không dìm, trừ RNNoise-16k ở nhạc SNR 0.
- Ứng viên NSNet-16k sau epoch 1 dìm quãng nghỉ ngang NSNet2 công bố, trên lối vào chưa có phần lọc không gian, và hạ đoạn
  có lời ít hơn (1,8–6,3 dB); RNNoise-16k còn lấy cả tiếng ở nhạc SNR 0 (13 dB).
- `board_gsc_espsr_nsnet3` ra tín hiệu hỏng ở cả bốn mục (STOI 0,10, đoạn có lời giảm 43–45 dB): không đưa vào bảng, chưa
  rõ vì sao.
