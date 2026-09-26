# ADR-0003 — `hpf` lọc bằng biquad có sẵn của `esp-dsp`, dạng trực tiếp II

- **Trạng thái**: Thay bởi ADR-0004
- **Ngày**: 2026-09-26
- **Liên quan**: KẾ HOẠCH §3.3, §3.4, §4.5.1; TASKS E7-T1; ADR-0002; `docs/measurements/latency.md` §5, `budget.md`, `parity.md`

---

## Bối cảnh

`hpf` là biquad Butterworth bậc hai ở đầu chuỗi, mỗi micro một bộ, 256 mẫu mỗi bước. KẾ HOẠCH ghi dạng trực tiếp
II chuyển vị, viết tay. Chủ dự án đặt luật: có kernel thư viện thì dùng. `dl_fft` và `esp-dl` không có biquad; `esp-dsp`
1.8.2, bản repo đã đo ở E6-T3 rồi bỏ cho FFT (ADR-0002), có đủ hai kernel: `dsps_biquad_gen_hpf_f32` (công thức RBJ)
và `dsps_biquad_f32`, có bản hợp ngữ cho S3. Kernel ấy là dạng trực tiếp II, không phải dạng chuyển vị.

## Các phương án

| Phương án | Được | Mất | Số đo |
|---|---|---|---|
| **`esp-dsp` 1.8.2, `dsps_biquad_f32`, dạng II** | kernel có sẵn; không phải bảo trì vòng lọc | sai số làm tròn float32 lớn hơn ~10–20 dB | 36,9 µs hai kênh; so với float64: tiếng nói −30 dBFS + một chiều 0,5 LSB sai tối đa 0,48 LSB, hum 50 Hz −10 dBFS + một chiều −20 dBFS sai 7,7 LSB (70 dB dưới tín hiệu) |
| `esp-dsp` `dsps_biquad_sf32`, hai kênh xen kẽ | kernel có sẵn | `dsp_afe` giữ kênh tách rời: chép vào và ra tốn thêm 15 µs | 36,6 µs chỉ kernel, 51,7 µs kèm chép; sai số như dạng II |
| Dạng II chuyển vị viết tay | sai số nhỏ hơn: 0,13 LSB và 0,7 LSB ở hai tín hiệu trên | vòng lọc tự viết | 36,5 µs hai kênh |

## Quyết định

Dùng `dsps_biquad_f32` của `esp-dsp`. Tốc độ ba cách ngang nhau (~17 chu kỳ mỗi mẫu: biquad là đệ quy nên độ trễ của bộ
tính dấu phẩy động quyết định), nên kernel hợp ngữ không mua được thời gian; nó được giữ theo luật của chủ dự án — có
kernel thư viện thì dùng — vì sai số của nó ở tín hiệu thật dưới một bước int16 và ở trường hợp xấu vẫn dưới SNR 61 dBA
của INMP441. Cái giá là 10–20 dB độ chính xác so với dạng chuyển vị, ghi rõ ở đây để lần xét lại có số.

## Hệ quả

- `dsp_afe` khai `espressif/esp-dsp ==1.8.2` trong `idf_component.yml`; bản dựng máy tính lấy mã C thuần của hai kernel.
- `srpipe.dsp.afe.hpf` soi gương đúng dạng II và đúng công thức hệ số ở float32.
- Ước "< 20 µs hai kênh" của KẾ HOẠCH §3.3 sai; số đo 36,9 µs nằm ở `budget.md`, 0,2% một bước.
- Xét lại khi: `esp-dsp` đổi bản; một phòng đo cho tiếng trầm mạnh tới mức sai số dạng II lên trên nền micro — khi
  ấy dạng chuyển vị viết tay cho cùng tốc độ và chính xác hơn 10–20 dB.
