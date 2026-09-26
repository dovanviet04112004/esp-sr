# Parity C ↔ Python

Kết quả `test_apps/parity` trên board, đọc `contracts/golden/`. Ngưỡng lấy từ `tolerance.yaml` của từng khối.
Chạy: `pytest firmware/test_apps/parity/pytest_parity.py --target esp32s3 --embedded-services esp,idf --port /dev/ttyUSB0 -p no:cacheprovider`.

| Khối | Số ca | Sai số tuyệt đối lớn nhất | SNR nhỏ nhất dB | Ngưỡng | Đối chứng âm đỏ | Commit | Ngày |
|---|---|---|---|---|---|---|---|
| `stft` — phổ phân tích | 4 | 7,6e-6 | 137,2 | ≤ 1e-4, ≥ 115 dB | — | 8cd377f | 26/09 |
| `stft` — tổng hợp từ phổ vàng | 4 | 3,0e-7 | 137,4 | ≤ 1e-5, ≥ 115 dB | lệch một mẫu: 0,96, −3,1 dB → đỏ | 8cd377f | 26/09 |

## Bản tham chiếu Python: STFT phân tích rồi tổng hợp (E6-T1)

`srpipe.dsp.spec.stft`, float32, lưới §3.1 (512 / 256, căn Hann tuần hoàn), tín hiệu dài 64 bước. Ra trễ vào đúng **một bước (256 mẫu)**; trễ thuật toán từ lúc một mẫu tới cho tới lúc nó ra là tới một cửa sổ (32 ms).

| Tín hiệu | SNR dựng lại dB | Sai số tuyệt đối lớn nhất |
|---|---|---|
| ồn trắng ±0,5 | 138,8 | 1,79e-7 |
| sin 440 Hz biên độ 0,5 | 138,6 | 1,79e-7 |
| chirp 50 Hz → 7,9 kHz | 139,0 | 2,09e-7 |
| chuỗi xung 120 Hz biên độ 0,9 | 139,2 | 2,38e-7 |

Đây là trần float32 của chính bản tham chiếu; bộ vàng C ↔ Python (E6-T4) so với bảng trên.
