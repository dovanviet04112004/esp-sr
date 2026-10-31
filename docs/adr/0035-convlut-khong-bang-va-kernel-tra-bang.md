# ADR-0035 — Conv không bảng tra cũng chạy như `ConvLut`, và kernel tra bảng của repo

- **Trạng thái**: Chấp nhận theo KẾ HOẠCH §3.18 bước 6 và 7 (chủ repo giao tối ưu tiếp YOLO26n 31/10); chủ repo chưa
  xem riêng ADR này
- **Ngày**: 2026-10-31
- **Liên quan**: KẾ HOẠCH §3.18, §6.5; ADR-0031, ADR-0034; TASKS E15-T13; `docs/measurements/detect.md` §8

---

## Bối cảnh

Sau ADR-0034, YOLO26n chạy 2 157 ms một ảnh trên board B với vùng mượn 32 KB. Còn ba chỗ chưa nhận vùng mượn hay còn
chạy chậm (`detect.md` §8):
- 36 Conv không có bảng tra theo sau vẫn là Conv của esp-dl, đọc trọng số từ flash qua cache: 255 ms. Gồm các lớp ra của đầu
  dò, các Conv trong attention và các phần mà `cache_sized_convolutions` cắt;
- bảng tra int8 của esp-dl ~6 chu kỳ một byte, bảng int16 ~9 chu kỳ một giá trị: 243 ms trong 99 ConvLut;
- dải của pass có trọng số trong vùng mượn vẫn cỡ theo đầu ra; với tích chập bước 2, hàng vào và hàng ra của một dải chiếm
  tới 56–72 KB, quá dcache.

## Các phương án

Đo trên board B, mỗi ảnh, khớp từng giá trị ở mọi dòng.

| Phương án | Mỗi ảnh | Ghi chú |
|---|---|---|
| Như ADR-0034 | 2 157 ms | |
| **Conv không bảng xuất thành ConvLut không bảng (`conv_lut_plain`)** | 2 080 ms | |
| như trên, bỏ cắt tích chập ở bước xuất | 2 352 ms | một nhóm làn của khung P5 cần 36 KB, hơn vùng |
| **dải có trọng số trong vùng: hàng vào cộng hàng ra ≤ nửa dcache** | −6 ms | đo khi IDLE0 ra khỏi watchdog |
| **bảng int8 và int16 trên kernel vô hướng của repo** | 1 988 ms | int8 4 lệnh một byte, int16 6 lệnh một giá trị |

## Quyết định

1. **ConvLut không bảng.** Nút ConvLut có thể không mang thuộc tính `lut`. Khi ấy op không xin bảng từ trình dựng của
   esp-dl, đầu ra ở lưới của tích chập, và op không tra bảng. Mọi phần khác như ADR-0031 và ADR-0034.
2. **Bản vá xuất `conv_lut_plain`**, chạy sau `conv_lut`, đổi mọi Conv int8 hay int16 không mang hàm kích hoạt mà `conv_lut`
   để lại thành ConvLut không bảng, giữ đầu vào, thuộc tính và số mũ đầu ra. `cache_sized_convolutions` vẫn chạy trước:
   lớp mà một nhóm làn đã lớn hơn vùng mượn vẫn cần cắt ở bước xuất.
3. **Cỡ dải.** Pass có trọng số trong vùng mượn chọn số hàng ra sao cho hàng vào và hàng ra của dải không quá nửa dcache,
   và không quá cỡ của ADR-0031. Pass đọc trọng số tại chỗ giữ cỡ của ADR-0031: dải nhỏ làm vòng chia lát của esp-dl
   đọc lại toàn bộ trọng số từ flash ở mỗi dải.
4. **Kernel tra bảng** (`kernels/lut_s8_esp32s3.S`, `kernels/lut_s16_esp32s3.S`), cùng phép của esp-dl 3.3.14 trên S3:
   - int8: giá trị v thành `table[v + 128]`. Kernel tra byte không dấu của v trên bản bảng xoay 128 byte mà op dựng trên
     stack, 4 lệnh một byte, bốn giá trị một bước xen nhau;
   - int16, bước 2^s: `table[((v ^ 0x8000) + 2^(s-1)) >> s]`, làm tròn half-up như S3, viết lại thành
     `middle[(v + 2^(s-1)) >> s]` với dịch số học, `middle = table + 2^(15-s)`; 6 lệnh một giá trị.
5. App thử gỡ IDLE0 khỏi watchdog của task trong lúc đo detect. Mạng giữ nhân 0 nhiều giây, và backtrace watchdog in qua
   UART ~50 ms rơi vào ảnh hay module đang đo.

## Hệ quả

- Mỗi giá trị không đổi: bản dò khớp ESP-PPQ từng giá trị trên 8 ảnh ở mọi bước.
- `conv_lut_plain` hiện mới đo bằng `PATCHES=`. Ảnh `detect` của lock chỉ có nó khi `detect.yaml` thêm nó và run chính
  thức xuất lại.
- Không thêm PSRAM hay RAM nội ngoài vùng mượn; bảng xoay 256 B nằm trên stack của task chạy mạng.
- `nhin_task` (E15-T7) giữ nhân 0 nhiều giây mỗi ảnh nên cũng làm watchdog của IDLE0 kêu; phải tính khi dựng task ấy.
- Còn lại: Slice, Concat, RequantizeLinear ~310 ms; khối C3k2 ở 128 × 128 nghẽn ở đọc ghi PSRAM; depthwise int16 của đầu dò.
