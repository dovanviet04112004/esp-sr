# Nhận diện vật thể (`detect`, E15)

Số đo của nhánh `detect` (KẾ HOẠCH §3.18, ADR-0030).

## 1. YOLO26n dựng sẵn của Espressif trên board B (E15-T2, 30/10)

App dò ngoài repo, dựng từ ví dụ `yolo26_detect` của esp-dl 3.3.14 cùng `espressif/yolo26` 0.1.1:
- bảng phân vùng, flash, PSRAM, cache và `-O2` như bản dựng sản phẩm;
- model đọc qua ánh xạ từ `models_0` + 6 MiB, sau ảnh tiếng nói;
- hai ảnh thử của ví dụ, `bus.jpg` 810×1080 và `person.jpg`, mỗi ảnh 3 lần;
- tiếng nói không chạy.

App không ghi NVS. Sau đó `make product` nạp lại sản phẩm, và board đọc đúng các ngưỡng của chủ repo.

| S | Trọng số | Suy luận | Giải JPEG | Tiền xử lý | Hậu xử lý | PSRAM khi mạng dựng xong | PSRAM đỉnh khi chạy | Dựng |
|---|---|---|---|---|---|---|---|---|
| 512 | đọc từ flash | **3 014–3 024 ms** | 87–100 ms | 32–33 ms | 23 ms | 2,57 MiB | 3,25 MiB | 333 ms |
| 512 | chép sang PSRAM | 2 468–2 478 ms | 87–100 ms | 32–33 ms | 23 ms | 5,27 MiB | 5,91 MiB | 510 ms |
| 640 | đọc từ flash | 4 876–4 887 ms | 88–100 ms | 48 ms | 36 ms | 3,98 MiB | 4,65 MiB | 362 ms |
| 640 | chép sang PSRAM | 3 989–4 004 ms | 88–100 ms | 48 ms | 36 ms | 6,68 MiB | 7,31 MiB | 538 ms |

- Hai cách đặt trọng số ra cùng khung, cùng điểm. Ở 512:
  - `bus.jpg` ra 5 khung: xe buýt 0,82; người 0,85, 0,82, 0,78, 0,54.
  - `person.jpg` ra 3 khung: người 0,83; xe đạp 0,75, 0,53.
- Trọng số đọc từ flash chậm hơn 22% ở cả hai cỡ. Số chép sang PSRAM gần số Espressif công bố (2 348 và 3 793 ms).
- PSRAM đỉnh gồm cả ảnh đã giải: 810×1080 RGB là 2,5 MiB, giải phóng sau tiền xử lý.
- Sản phẩm còn trống ~4,9 MiB PSRAM lúc khởi động. Vừa được bản 512 trọng số đọc từ flash, kèm ảnh cỡ camera. Bản 640
  hoặc bản chép trọng số sang PSRAM thì không vừa.
- Dựng mạng tốn 109 KB RAM nội (đáy 54 555 B còn trống), và 4,4 KB không trả lại sau khi xoá mạng. Sản phẩm không có chỗ
  ấy, nhưng `ai_engine` dựng mạng trong `ExternalMallocGuard`, đẩy các khoản cấp phát này sang PSRAM; kiểm lại ở E15-T5.
- Chưa đo: khi tiếng nói cùng chạy (đo cùng `nhin_task` ở E15-T7) và S = 320 (cần lượng tử của repo, E15-T3).
