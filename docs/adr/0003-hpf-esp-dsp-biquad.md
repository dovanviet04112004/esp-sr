# ADR-0003 — `hpf` lọc bằng biquad có sẵn của `esp-dsp`, dạng trực tiếp II

- **Trạng thái**: Đề xuất — chờ số đo chi phí trên board (E7-T1 bước 4)
- **Ngày**: 2026-09-26
- **Liên quan**: KẾ HOẠCH §3.3, §3.4, §4.5.1; TASKS E7-T1; ADR-0002; `docs/measurements/budget.md`, `parity.md`

---

## Bối cảnh

`hpf` là biquad Butterworth bậc hai ở đầu chuỗi, mỗi micro một bộ, 256 mẫu mỗi bước. KẾ HOẠCH ghi dạng trực tiếp
II chuyển vị, viết tay. Chủ dự án đặt luật: có kernel thư viện thì dùng. `dl_fft` và `esp-dl` không có biquad; `esp-dsp`
1.8.2, bản repo đã đo ở E6-T3 rồi bỏ cho FFT (ADR-0002), có đủ hai kernel: `dsps_biquad_gen_hpf_f32` (công thức RBJ)
và `dsps_biquad_f32`, có bản hợp ngữ cho S3. Kernel ấy là dạng trực tiếp II, không phải dạng chuyển vị.

## Các phương án

| Phương án | Được | Mất | Số đo |
|---|---|---|---|
| **`esp-dsp` 1.8.2, dạng II** | kernel có sẵn, có bản hợp ngữ cho S3; không phải bảo trì vòng lọc | sai số làm tròn float32 lớn hơn ~10–20 dB | so với float64: tiếng nói −30 dBFS + một chiều 0,5 LSB sai tối đa 0,48 LSB; hum 50 Hz −10 dBFS + một chiều −20 dBFS sai 7,7 LSB (70 dB dưới tín hiệu); chi phí: chờ E7-T1 bước 4 |
| Dạng II chuyển vị viết tay | sai số nhỏ hơn: 0,13 LSB và 0,7 LSB ở hai tín hiệu trên | vòng lọc tự viết, không có bản tối ưu | chi phí: chờ E7-T1 bước 4 |

## Quyết định

Dùng `esp-dsp`: sai số của nó ở tín hiệu thật dưới một bước int16, và ở trường hợp xấu vẫn dưới SNR 61 dBA của
INMP441. Quyết định chốt khi số đo chi phí trên board không cho thấy dạng viết tay nhanh hơn.

## Hệ quả

- `dsp_afe` khai `espressif/esp-dsp ==1.8.2` trong `idf_component.yml`; bản dựng máy tính lấy mã C thuần của hai kernel.
- `srpipe.dsp.afe.hpf` soi gương đúng dạng II và đúng công thức hệ số ở float32.
- Xét lại khi: `esp-dsp` đổi bản; một phòng đo cho tiếng trầm mạnh tới mức sai số dạng II lên trên nền micro; hoặc
  số đo chi phí cho dạng viết tay nhanh hơn.
