# Parity C ↔ Python

Kết quả `test_apps/parity` trên board, đọc `contracts/golden/`. Ngưỡng lấy từ `tolerance.yaml` của từng khối.
Chạy: `pytest firmware/test_apps/parity/pytest_parity.py --target esp32s3 --embedded-services esp,idf --port /dev/ttyUSB0 -p no:cacheprovider`.

| Khối | Số ca | Sai số tuyệt đối lớn nhất | SNR nhỏ nhất dB | Ngưỡng | Đối chứng âm đỏ | Commit | Ngày |
|---|---|---|---|---|---|---|---|
| `stft` — phổ phân tích | 4 | 7,6e-6 | 137,2 | ≤ 1e-4, ≥ 115 dB | — | 8cd377f | 26/09 |
| `stft` — tổng hợp từ phổ vàng | 4 | 3,0e-7 | 137,4 | ≤ 1e-5, ≥ 115 dB | lệch một mẫu: 0,96, −3,1 dB → đỏ | 8cd377f | 26/09 |
| `mel` — log-mel, ba cấu hình (40, 80, 24 dải) | 3 | 1,9e-6 | 139,9 | ≤ 1e-4, ≥ 115 dB | lệch một dải: 2,24, 3,0 dB → đỏ | e7de26f+ | 26/09 |
| `mel` — MFCC (13, 20, 24 hệ số) | 3 | 1,5e-5 | 134,8 | ≤ 1e-3, ≥ 110 dB | — | e7de26f+ | 26/09 |
| `chain` — `pcm` ra của mặt tiền `dsp_afe`, mọi module tắt (int16) | 4 | 1 LSB | 77,9 | ≤ 1 LSB, ≥ 60 dB | lệch một mẫu: 15 090 LSB, −2,9 dB → đỏ | ecaf140 | 26/09 |
| `chain` — `seq`, `doa_deg`, `doa_conf`, `vad`, `level_dbfs`, `gain_db`, `flags` | 4 | 0 | — | khớp tuyệt đối (`level_dbfs` ≤ 1) | — | ecaf140 | 26/09 |

## Bản tham chiếu Python: STFT phân tích rồi tổng hợp (E6-T1)

`srpipe.dsp.spec.stft`, float32, lưới §3.1 (512 / 256, căn Hann tuần hoàn), tín hiệu dài 64 bước. Ra trễ vào đúng **một bước (256 mẫu)**; trễ thuật toán từ lúc một mẫu tới cho tới lúc nó ra là tới một cửa sổ (32 ms).

| Tín hiệu | SNR dựng lại dB | Sai số tuyệt đối lớn nhất |
|---|---|---|
| ồn trắng ±0,5 | 138,8 | 1,79e-7 |
| sin 440 Hz biên độ 0,5 | 138,6 | 1,79e-7 |
| chirp 50 Hz → 7,9 kHz | 139,0 | 2,09e-7 |
| chuỗi xung 120 Hz biên độ 0,9 | 139,2 | 2,38e-7 |

Đây là trần float32 của chính bản tham chiếu; bộ vàng C ↔ Python (E6-T4) so với bảng trên.

## Chuỗi mặt tiền `dsp_afe` (E3-T4)

`pcm` lệch tới 1 LSB là bản chất của phép so, không phải lỗi: trung bình hai mẫu int16 có tổng lẻ rơi đúng vào nửa
LSB, và sai số float32 của hai thư viện FFT (`dl_fft` trên board, numpy ở Python) quyết định làm tròn lên hay xuống.
Ca có tổng hai kênh luôn chẵn (chirp giống nhau hai kênh, tiếng gần im) khớp tuyệt đối. Trên máy tính: 78,1 dB.

## Dựng lại trên phiên thu thật của board (E5-T11)

App khung rỗng (`main` bản `dev`, mọi module `dsp_afe` tắt), luồng `mode 5` (`ch0 ch1 clean`) 10 phút về
`srhost.stream_rx`; `srhost.score` chạy `srpipe.dsp.afe.chain` trên `ch0 ch1` rồi so với `clean` của board, bỏ hai
bước đầu, phán theo `contracts/golden/chain/tolerance.yaml`.

| Phiên | Bản dựng | Bước so | Sai số lớn nhất | Mẫu vượt ngưỡng | Mẫu lệch 1 LSB | SNR dB | Ngày |
|---|---|---|---|---|---|---|---|
| `20260926_home_002` | `dev` @ `3d3335d` | 37 498 | 1 LSB | 0 | 21,1 % | 25,1 | 26/09 |

Phòng yên, không nguồn âm: `clean` chỉ khoảng −72 dBFS (RMS ~8 LSB), nên sai số làm tròn 1 LSB của mục trên chiếm phần
lớn và kéo SNR xuống 25 dB. Ngưỡng SNR 60 dB của bộ vàng áp cho tín hiệu có biên độ của bộ vàng, không áp ở đây; phép
phán của phiên thật là sai số lớn nhất ≤ 1 LSB.
