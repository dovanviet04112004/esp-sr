# ADR-0004 — `hpf` lọc bằng dạng II chuyển vị viết tay, bỏ biquad của `esp-dsp`

- **Trạng thái**: Chấp nhận
- **Ngày**: 2026-09-26
- **Liên quan**: KẾ HOẠCH §3.3, §3.4, §4.5.1; TASKS E7-T1; ADR-0003 (bị thay); `docs/measurements/latency.md` §5, `budget.md`

---

## Bối cảnh

ADR-0003 chọn biquad `dsps_biquad_f32` của `esp-dsp` cho `hpf` theo luật dùng kernel thư viện, rồi đo trên board: kernel
ấy không nhanh hơn vòng lặp viết tay, mà kém chính xác hơn. Chủ dự án chốt (26/09): kernel thư viện mà tệ hơn thì phải
lùi, không giữ chỉ vì luật. Luật được hiểu đúng là: thử kernel thư viện trước, đo, và chỉ giữ khi nó không thua.

## Các phương án

Số đo ở `latency.md` §5: `bench_afe`, profile `bench`, board B, hai kênh × 256 mẫu; sai số so với float64 ở `srpipe`.

| Phương án | Được | Mất | Số đo |
|---|---|---|---|
| `esp-dsp` `dsps_biquad_f32`, dạng II | kernel có sẵn | phụ thuộc thêm một thư viện; sai số làm tròn lớn hơn 10–20 dB | 36,9 µs; hum 50 Hz −10 dBFS + một chiều −20 dBFS sai 7,7 LSB |
| `esp-dsp` `dsps_biquad_sf32`, hai kênh xen kẽ | kernel có sẵn | như trên, cộng 15 µs chép dữ liệu | 51,7 µs kèm chép |
| **Dạng II chuyển vị viết tay** | chính xác hơn 10–20 dB; không phụ thuộc thư viện | vòng lọc sáu dòng tự bảo trì | 36,5 µs; cùng tín hiệu sai 0,7 LSB |

## Quyết định

Viết tay dạng II chuyển vị, hệ số RBJ tự tính bằng float32. Cùng tốc độ, chính xác hơn, bớt một phụ thuộc; bản Python soi
gương đúng thứ tự phép tính nên máy tính khớp từng bit.

## Hệ quả

- `dsp_afe` bỏ `idf_component.yml` và phụ thuộc `esp-dsp`; bản dựng máy tính bỏ hai tệp C của thư viện.
- `srpipe.dsp.afe.hpf` đổi sang dạng chuyển vị; bộ vàng `hpf` sinh lại, ngưỡng khớp đo lại trên board.
- `bench_afe` bỏ các dòng so sánh; số của lần so sánh nằm ở `latency.md` §5.
- Xét lại khi: một bản `esp-dsp` sau có biquad dạng chuyển vị hay nhanh hơn rõ trên S3.
