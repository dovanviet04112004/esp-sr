# ADR-0008 — `doa` dò lưới bằng xoay pha dồn viết tay, không dùng bảng pha và `esp-dsp`

- **Trạng thái**: Chấp nhận
- **Ngày**: 2026-09-28
- **Liên quan**: KẾ HOẠCH §3.3, §3.6, §6.5; TASKS E8-T1; ADR-0005, ADR-0006; `docs/measurements/latency.md` §9,
  `docs/measurements/afe/doa.md`

---

## Bối cảnh

Mỗi lần dò, `doa` tính `R(θ) = Σₖ Re{Φₖ/|Φₖ| · exp(jωₖτ_θ)}` cho 91 góc, trên 193 vạch của dải 2–8 kHz; góc θ và
180° − θ chung một lượt. Theo luật dùng kernel thư viện, kernel có sẵn được thử trước. Không thư viện được phép nào có
phép chiếu lên lưới trễ; thứ gần nhất là tích vô hướng `dsps_dotprod_f32` của `esp-dsp`, cần một bảng `cos`/`sin` dựng
sẵn cho từng góc và từng vạch. Bản viết tay của KẾ HOẠCH §3.6 không cần bảng: pha xoay dồn bằng một phép nhân phức mỗi vạch.

## Các phương án

Số đo ở `latency.md` §9: board B, profile `bench`, chỉ phần dò, dải 2–8 kHz.

| Phương án | Được | Mất | Số đo |
|---|---|---|---|
| Bảng `cos`/`sin` ở RAM nội + `dsps_dotprod_f32` | nhanh nhất; sai số nhỏ hơn (pha từng vạch làm tròn một lần) | 71 KB RAM nội cho bảng, 92 KB nếu dải từ 200 Hz; phụ thuộc `esp-dsp` mà ADR-0004 đã bỏ | 313,7 µs, sai 5,4e-6 |
| Bảng ở PSRAM + `dsps_dotprod_f32` | không tốn RAM nội | trượt cache khi bảng lớn hơn cache dữ liệu | 428,7 µs; 736,9 µs ở 250 vạch, chậm hơn viết tay |
| Viết tay, hai góc xen kẽ | giấu độ trễ FPU trên giấy | chậm gấp đôi, có lẽ tràn thanh ghi | 1 101,6 µs |
| **Xoay dồn viết tay** | 4,3 KB trạng thái, không bảng, không phụ thuộc; khớp Python từng bit | chậm hơn bảng RAM nội 1,7 lần | 527,2 µs, sai 7,0e-5 (4e-7 tương đối) |

## Quyết định

Xoay dồn viết tay. Bảng ở RAM nội chỉ tiết kiệm 214 µs ở bước dò, mà bước dò chỉ chạy mỗi hai bước khi có tiếng nói:
trung bình 107 µs mỗi bước, 0,7% một nhân. 71 KB RAM nội là gần bằng cả vùng `hot` của chuỗi (81 KB), trong khi RAM nội
còn phải giữ Wi-Fi, ngăn xếp và đệm DMA (KẾ HOẠCH §6.5). Đặt bảng ở PSRAM thì phần lợi còn 99 µs và mất hẳn khi dải rộng
hơn. Sai số của bản viết tay nhỏ hơn nhiều khoảng cách giữa hai góc kề nhau nên không đổi góc ra.

## Hệ quả

- `dsp_afe` vẫn không phụ thuộc `esp-dsp`; `srpipe.dsp.afe.doa` xoay dồn đúng thứ tự phép tính ấy, bảng pha đầu và bước
  dựng bằng chuỗi double, nên máy tính và board khớp từng bit.
- Lần so sánh chạy từ một bản sao tạm của `bench_afe`, không vào repo; số nằm ở `latency.md` §9.
- Xét lại khi: RAM nội còn dư rõ sau E10 và E11, hay chi phí đỉnh một khung của nhân 1 chạm mục tiêu KẾ HOẠCH §5.6.
