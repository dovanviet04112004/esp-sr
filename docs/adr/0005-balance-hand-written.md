# ADR-0005 — `balance` nhân phức bằng vòng viết tay, không ghép từ `esp-dsp`

- **Trạng thái**: Chấp nhận
- **Ngày**: 2026-09-27
- **Liên quan**: KẾ HOẠCH §3.3, §3.4; TASKS E7-T2; ADR-0004, ADR-0006; `docs/measurements/latency.md` §6

---

## Bối cảnh

`balance` nhân 257 vạch phổ của `ch1` với 257 hệ số phức hiệu chuẩn mỗi bước. Theo luật dùng kernel thư viện, kernel có
sẵn được thử trước và chỉ giữ khi không thua. Không thư viện nào được phép có phép nhân phức từng phần tử: `dl_fft` chỉ có
biến đổi Fourier; `esp-dsp` chỉ có phép nhân **thực** từng phần tử `dsps_mul_f32` (có bước nhảy), cùng `dsps_add_f32`
và `dsps_sub_f32`. Phép nhân phức phải ghép từ sáu lượt của chúng.

## Các phương án

Số đo ở `latency.md` §6: profile `bench`, board B, nhân 1, 2000 bước sau 16 bước làm nóng, 257 vạch.

| Phương án | Được | Mất | Số đo |
|---|---|---|---|
| `esp-dsp`: bốn `dsps_mul_f32` bước 2 vào bốn đệm tạm, rồi `dsps_sub_f32` và `dsps_add_f32` ghi xen kẽ | kernel có sẵn | thêm lại phụ thuộc ADR-0004 vừa bỏ; 4 × 257 float đệm tạm; sáu lượt qua bộ nhớ thay vì một | 52,3 µs; khớp Python từng bit |
| **Vòng viết tay**: mỗi vạch đọc hệ số và vạch vào biến cục bộ, bốn phép nhân và hai phép cộng float32 | một lượt; không đệm, không phụ thuộc | vòng lặp năm dòng tự bảo trì | 18,4 µs không gộp nhân-cộng (ADR-0006); khớp Python từng bit |

## Quyết định

Vòng viết tay. Nhanh hơn 2,9 lần, cùng độ chính xác (cả hai làm tròn đúng các phép ấy theo cùng thứ tự), không đệm tạm, không
thêm phụ thuộc.

## Hệ quả

- `dsp_afe` vẫn không phụ thuộc `esp-dsp`; `srpipe.dsp.afe.balance.apply` làm đúng bốn phép nhân và hai phép cộng ấy.
- Lần so sánh chạy từ một bản sao tạm của `bench_afe` có thêm `esp-dsp`, không vào repo; số nằm ở `latency.md` §6.
- Xét lại khi: `esp-dsp` hay `dl_fft` có phép nhân phức từng phần tử viết bằng lệnh vector của S3.
