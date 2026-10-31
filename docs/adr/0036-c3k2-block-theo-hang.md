# ADR-0036 — Op `C3k2Block`: khối C3k2 của YOLO chạy theo hàng, mọi map giữa nằm trong cache

- **Trạng thái**: Chấp nhận theo KẾ HOẠCH §3.18 bước 6 và 7 (chủ repo giao tối ưu tiếp YOLO26n 31/10); chủ repo chưa
  xem riêng ADR này
- **Ngày**: 2026-10-31
- **Liên quan**: KẾ HOẠCH §3.18, §6.5; ADR-0031, ADR-0034, ADR-0035; TASKS E15-T13; `docs/measurements/detect.md` §8

---

## Bối cảnh

Sau ADR-0035, YOLO26n chạy 1 988 ms một ảnh trên board B. Các lớp độ phân giải cao nghẽn ở đọc ghi PSRAM, không ở phép
tính. Khối C3k2 `model.2` ở 128 × 128 tốn ~210 ms:
- cv1 1 × 1 ra y, hai Slice tách y0 và y1;
- bottleneck: m1 3 × 3 rồi m2 3 × 3, Add với y1;
- Concat [y0, y1, z], cv2 1 × 1.

Mỗi map giữa đi qua PSRAM một lần ghi, một lần đọc. Ghi một dòng cache còn phải nạp dòng ấy trước (ADR-0026). Cộng lại
khối đọc ghi ~11 MB mỗi ảnh, ~147 ms ở ~75 MB/s. `model.4` ở 64 × 64 cùng dạng, ~105 ms. Ở cả hai, y, y1, z và Concat
cùng một số mũ, nên Add là cộng bão hoà và Concat là chép.

## Các phương án

| Phương án | Lợi ước | Mất |
|---|---|---|
| Op chép Slice, Concat nhanh hơn | nhỏ: chép đã gần trần băng thông | — |
| Producer ghi thẳng vào Concat qua buffer in-place của esp-dl | Concat và Slice | bộ xếp bộ nhớ của esp-dl, mọi op đọc ghi cửa sổ kênh; 3 × 3 của esp-dl không đọc cửa sổ kênh được |
| **Một op cho cả khối, chạy theo hàng** | đọc x và ghi y mỗi thứ một lần: đo 1 988 → 1 913 ms một ảnh | một op mới, vòng hàng |

## Quyết định

1. **Bản vá xuất `c3k2_block`**, chạy sau `conv_lut`, thay mỗi khối C3k2 một bottleneck bằng một nút `C3k2Block` khi:
   - cv1 và cv2 là ConvLut 1 × 1 có bảng, m1 và m2 là ConvLut 3 × 3 bước 1 đệm 1 có bảng, cùng int8;
   - hai Slice cắt y ở 0:c và c:2c, Add đọc y1 và m2, Concat nối [y0, y1, z], cv2 đọc riêng Concat;
   - mọi map giữa chỉ khối đọc, và y, y1, z, Concat cùng một số mũ.

   Nút đọc x rồi filter và bias của cv1, m1, m2, cv2; thuộc tính là bảng (`lut1`–`lut4`) và số mũ tích chập của từng cái.
   ESP-PPQ chỉ ghi các bảng mà một thuộc tính `lut` trỏ tới, nên bản vá ghi thêm bốn bảng của khối theo đúng cách ấy.
2. **Op `C3k2Block`** (`ops/espdl_c3k2_block.cpp`). Bước j làm ba việc:
   - **cv1, hàng j:** ghi thẳng y0 và y1 vào hàng Concat (mỗi điểm ảnh 3c giá trị), tra bảng, chép y1 vào vòng y1;
   - **m1, hàng j − 1:** chạy từ vòng y1;
   - **hàng j − 2 của khối:** m2 từ vòng của m1, z = y1 + u cộng bão hoà vào hàng Concat, cv2 ghi hàng ra.

   Vòng hàng của 3 × 3 ghi mỗi hàng hai lần, nên ba hàng liền nhau luôn nằm kề nhau cho kernel của esp-dl. Mọi tích chập
   là kernel và phép làm tròn của esp-dl, bảng tra là kernel `lut` của repo (ADR-0035). Trọng số cả khối chép vào vùng mượn
   khi vừa (§6.5). Các hàng giữa nằm ở một vùng PSRAM chung của mọi khối, dựng lúc dựng đồ thị.
3. Chỉ khối một bottleneck với Add, Concat cùng số mũ. C3k (`c3k=True`) và khối có Requantize giữ đường cũ.

## Hệ quả

- Board B, vùng 32 KB: một ảnh 1 913 ms, từ 1 988, khớp ESP-PPQ từng giá trị trên 8 ảnh. Khối `model.2` 157,3 ms, từ ~210;
  `model.4` 79,5 ms, từ ~105 (`detect.md` §8). Phần còn lại của khối là tính: bốn tích chập ở ~0,13 chu kỳ một
  nhân-cộng và bốn lượt bảng tra.
- PSRAM của đồ thị 2 602 488 B, từ 2 824 384, kể cả vùng hàng chung 45 072 B: các map giữa của hai khối không còn.
- Khối `model.2` và `model.4` không còn từng module riêng trong bảng thời gian.
