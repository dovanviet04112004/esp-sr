# Parity C ↔ Python

Kết quả `test_apps/parity` trên board, đọc `contracts/golden/`. Ngưỡng lấy từ `tolerance.yaml` của từng khối.

| Khối | Số ca | Sai số tuyệt đối lớn nhất | SNR nhỏ nhất dB | Ngưỡng | Đối chứng âm đỏ | Commit | Ngày |
|---|---|---|---|---|---|---|---|

## Bản tham chiếu Python: STFT phân tích rồi tổng hợp (E6-T1)

`srpipe.dsp.spec.stft`, float32, lưới §3.1 (512 / 256, căn Hann tuần hoàn), tín hiệu dài 64 bước. Ra trễ vào đúng **một bước (256 mẫu)**; trễ thuật toán từ lúc một mẫu tới cho tới lúc nó ra là tới một cửa sổ (32 ms).

| Tín hiệu | SNR dựng lại dB | Sai số tuyệt đối lớn nhất |
|---|---|---|
| ồn trắng ±0,5 | 138,8 | 1,79e-7 |
| sin 440 Hz biên độ 0,5 | 138,6 | 1,79e-7 |
| chirp 50 Hz → 7,9 kHz | 139,0 | 2,09e-7 |
| chuỗi xung 120 Hz biên độ 0,9 | 139,2 | 2,38e-7 |

Đây là trần float32 của chính bản tham chiếu; bộ vàng C ↔ Python (E6-T4) so với bảng trên.
