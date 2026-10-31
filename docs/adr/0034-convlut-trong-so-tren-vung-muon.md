# ADR-0034 — `ConvLut` đọc trọng số từ vùng mượn RAM nội theo lát kênh, và kernel stem RGB

- **Trạng thái**: Chấp nhận theo KẾ HOẠCH §3.18 bước 6 và 7 (chủ repo giao tối ưu tiếp YOLO26n 31/10); chủ repo chưa
  xem riêng ADR này
- **Ngày**: 2026-10-31
- **Liên quan**: KẾ HOẠCH §3.14 bước 2.5, §3.18, §6.5; ADR-0025, ADR-0026, ADR-0031; TASKS E15-T13;
  `docs/measurements/detect.md` §8

---

## Bối cảnh

YOLO26n ở S = 512 chạy 2 629 ms một ảnh trên board B, sau `ConvLut` (ADR-0031) và hai bản vá softmax. 82 `ConvLut` chiếm
1 839 ms. Trọng số đọc thẳng từ flash qua cache (§3.18), và tích chập int8 của esp-dl chạy 0,2–0,27 chu kỳ một
nhân-cộng. Lệnh nhân-cộng vector của S3 chạy một chu kỳ khi dữ liệu đã sẵn (`latency.md` §25), nên phần lớn thời gian là
chờ bộ nhớ.

Tích chập đầu `model.0` đọc ảnh RGB 3 kênh. esp-dl chạy nó trên kernel không căn: 136 ms, 1,15 chu kỳ một nhân-cộng.

Speaker đã có sẵn hai cơ chế cho việc này:
- vùng RAM nội mượn mỗi lượt chạy, cho trọng số của op riêng (ADR-0025);
- bộ nạp trước dcache của ROM (ADR-0026).

## Các phương án

Đo trên board B, khớp từng giá trị trừ chỗ ghi khác (`detect.md` §8).

| Phương án | Mỗi ảnh | Mất |
|---|---|---|
| Như cũ | 2 629 ms | — |
| Nạp trước dải sau vào dcache (ADR-0026) | dải 1 hàng 2 800 ms; dải như cũ chậm thêm 18 ms | không lợi |
| Trọng số cả lớp chép vào vùng mượn khi vừa 64 KB | 2 255 ms | lớp lớn hơn 64 KB không được gì |
| **Lát kênh ra vừa vùng mượn, chép lát vào vùng; kernel stem RGB** | vùng 64 KB 2 118 ms, 32 KB 2 157 ms, 16 KB 2 359 ms | RAM nội lúc chạy |
| Chép trọng số sang PSRAM | không đo | ~2,6 MB PSRAM, phần để dành cho YOLO |

## Quyết định

1. **Vùng mượn.** `ConvLut` xin lúc dựng số byte trọng số của nó, tối đa 32 KB. Mỗi lượt, `EspdlNet::step` cấp vùng như
   cho speaker (§6.5).
   - Vùng chứa cả filter thì op chép filter vào vùng rồi chạy như ADR-0031.
   - Filter lớn hơn vùng thì op chạy theo lát kênh ra: mỗi lát là số nhóm 16 kênh (8 với int16) nhiều nhất mà vùng
     chứa. Op chép trọng số của lát vào vùng, rồi chạy cả các dải hàng của lát trên đúng kernel và đúng phép làm tròn của
     esp-dl, như vòng chia lát của esp-dl dời con trỏ trọng số, bias và đầu ra.
   - Bảng tra áp lên từng lát ngay sau dải, đúng số kênh của lát.
   - Không được cấp vùng, hay lớp không chia lát được, thì op chạy như ADR-0031, đọc trọng số tại chỗ. Lớp không chia lát
     được gồm depthwise, hệ số theo kênh, và số kênh ra lẻ nhóm.
2. **Kernel stem RGB** (`kernels/stem_s8_esp32s3.S`). Tích chập int8 3 × 3, bước 2, 3 kênh vào, 16 kênh ra, có bias,
   cửa sổ không chạm biên dưới và biên phải, chạy trên kernel PIE của repo:
   - mỗi hàng cửa sổ là 9 byte liền, một lần nạp không căn lấy cả hàng;
   - 27 phép nhân-cộng vector một điểm ảnh;
   - bias nạp vào QACC và làm tròn half-up bằng đúng macro của `pie_s8_esp32s3.inc`;
   - cột trái đọc phần đệm trên một bản filter có cột đầu bằng 0;
   - hàng trên cùng chạy biến thể hai hàng cửa sổ.
3. Op không nạp trước dải vào dcache.
4. App thử cấp vùng mượn cho lượt đo từng module, như một lượt chạy. `make ai-unit-detect KEEP=1` dựng và chạy lại riêng
   firmware trên ảnh xuất có sẵn trên board.

## Hệ quả

- **Mỗi giá trị không đổi** ở mọi cỡ vùng: chỉ thứ tự các pass và chỗ đọc trọng số khác, phép tính vẫn của esp-dl.
  Bản dò khớp từng giá trị ở vùng 16, 32 và 64 KB.
- **RAM nội:** tối đa 32 KB trong lúc `detect` chạy, nhả ngay sau. Trong sản phẩm, khối liền lớn nhất còn trống quyết
  định cỡ vùng. Lúc heartbeat, cả RAM nội còn 32 495 B (`ram.md` §5), nên vùng thật nhỏ hơn và lợi ít hơn, nằm giữa
  2 629 và 2 359 ms. Không cấp được gì thì `detect` chạy như cũ, trừ phần stem: E15-T7 đo 🔬.
- **PSRAM không tăng.**
- Phụ thuộc thêm vào bố cục nội bộ của esp-dl 3.3.14: trọng số theo nhóm làn liền nhau, bias 4 byte một kênh với int8
  và 8 byte với int16. Nâng esp-dl thì bản dò kiểm lại.
- Còn lại: Slice, Concat và RequantizeLinear chiếm ~310 ms. Tích chập int16 của đầu dò chạy 0,3–0,46 chu kỳ một
  nhân-cộng, depthwise int16 tới 2,8.
