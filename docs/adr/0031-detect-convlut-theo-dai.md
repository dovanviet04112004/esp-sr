# ADR-0031 — Op `ConvLut`: tích chập của esp-dl theo dải hàng, bảng tra của hàm kích hoạt áp ngay trong cache

- **Trạng thái**: Chấp nhận (chủ repo, 2026-10-30)
- **Ngày**: 2026-10-30
- **Liên quan**: KẾ HOẠCH §3.14 bước 2.5, §3.18; ADR-0026, ADR-0030; TASKS E15-T13; `docs/measurements/detect.md` §4, §5

---

## Bối cảnh

YOLO26n ở S = 512, sau ba bản vá ở bước xuất, chạy 2 722 ms một ảnh trên board B. Bảng thời gian từng module (2 635 ms,
một nhân) cho thấy hàm kích hoạt chạy thành lượt riêng sau mỗi tích chập:
- Swish int8: 66 lượt, 253,6 ms;
- bảng tra int16 của Swish ở phần đầu: 20 lượt, 117,9 ms.

Mỗi lượt đọc lại cả tensor mà tích chập vừa ghi ra PSRAM, đổi qua bảng tra rồi ghi lại. Ví dụ Swish của `model.0`: 1 MiB mất
32,9 ms, khoảng 7,5 chu kỳ một byte, nghẽn ở băng thông PSRAM chứ không ở bảng tra.

esp-dl chỉ ghép Linear, ReLU, LeakyReLU và PReLU vào tích chập, không ghép Swish. PointwiseS8 của speaker chạy các tích
chập 1 × 1 của YOLO chậm hơn esp-dl 83 ms, nên không dựng tiếp trên nó (`detect.md` §5).

esp-dl đã tự chạy tích chập trên một phần các hàng: khi chia việc cho hai nhân, `ConvOpArgs` đặt lại con trỏ vào và ra,
`input_height`, `output_height`, phần đệm trên cho nửa đầu và phần đệm dưới cho nửa sau (`dl_base_conv_args.hpp`).

## Các phương án

| Phương án | Lợi ước | Công | Rủi ro |
|---|---|---|---|
| **ConvLut theo dải**: kernel tích chập của esp-dl chạy từng dải hàng, bảng tra áp lên dải khi còn trong cache | phần lớn 371,5 ms 🔬 | nửa ngày, không assembly | dựa vào API nội bộ của esp-dl |
| Kernel tích chập mới có bảng tra lúc ghi, op cho cả khối C3k2 | 400–600 ms 🔬 | nhiều ngày | phải tự giữ đúng từng phép làm tròn của esp-dl |
| Dừng ở 2 722 ms | 0 | 0 | — |

## Quyết định

1. Bản vá xuất `conv_lut` (`esp_ppq_patches/ops/conv_lut.py`) chạy sau `prepare_graph` của ESP-PPQ, lúc trọng số đã ở bố
   cục của esp-dl và hàm kích hoạt đã có bảng. Nó gộp mỗi Conv thoả đủ các điều kiện sau thành một nút `ConvLut`:
   - đầu ra chỉ một op đọc, và op ấy có bảng tra (`lut`);
   - op kích hoạt cùng quant type với Conv;
   - Conv không ghép sẵn hàm kích hoạt nào;
   - đầu ra của Conv không phải đầu ra của đồ thị.

   Nút mới giữ nguyên đầu vào, trọng số và bias của Conv, cùng các thuộc tính đệm, bước, giãn và nhóm. Thêm hai thuộc
   tính: `lut` của op kích hoạt và `conv_exponent` là số mũ đầu ra của tích chập. Đầu ra của nút là đầu ra của op kích hoạt.
2. Op `ConvLut` của `ai_engine` (`ops/espdl_conv_lut.cpp`):
   - dựng `ConvOpArgs` như Conv của esp-dl, với số mũ đầu ra của tích chập, rồi chọn kernel bằng `dl_conv_ensure_kernels`;
   - chạy `base::conv2d` hay `base::depthwise_conv2d` theo từng dải hàng ra, mỗi dải ra vừa một phần tư cache dữ liệu;
   - sau mỗi dải, `base::lut_s8` hay `base::lut_s16_nearest_neighbor` của esp-dl đổi dải ấy tại chỗ.

   Op chạy một nhân, như `Model::run` mặc định, và tự báo là module có bảng tra, nên trình dựng của esp-dl gắn bảng làm đầu
   vào cuối như với op kích hoạt.
3. Kernel tích chập, phép làm tròn và hàm tra bảng đều là của esp-dl, nên mỗi giá trị bằng chuỗi Conv rồi kích hoạt. Bản dò
   vẫn so chip với mô phỏng đồ thị gốc, như mọi bản vá ở bước xuất. Bản vá được giữ khi bản dò khớp và giờ giảm (KẾ HOẠCH
   §3.14 bước 2.3).

## Hệ quả

- **Đo trên board B**: bản dò ra 2 662 ms một ảnh, run chính thức trước đó 2 722 ms, khớp từng giá trị; PSRAM bớt 26,5 KB.
  Lợi kém xa ước lượng, vì giả định ban đầu sai: lượt bảng tra nghẽn ở chính phép tra chứ không ở PSRAM. S3 không có
  lệnh tra byte theo vectơ, nên mỗi byte tốn vài chu kỳ dù dữ liệu đã ở trong cache. Chép bảng ra vùng căn 16 byte cho
  `lut_s8` đi đường vectơ cũng không đổi gì (2 663 so với 2 662 ms).
- Trong số đó, 82 ConvLut nhanh hơn cặp Conv và kích hoạt cũ 44,5 ms. Riêng `/model.2/m.0/Add` lại chậm từ 11,7 lên 56,3
  ms dù nút y hệt: tensor được xếp lại trong PSRAM khi tensor trung gian biến mất.
- Tensor trung gian của tích chập biến mất, nên không thêm PSRAM.
- Op dựa vào API nội bộ của esp-dl (`ConvOpArgs`, `ConvArgsType`, `dl_conv_ensure_kernels`, `lut_s8`,
  `lut_s16_nearest_neighbor`). Nâng esp-dl thì bản dò kiểm lại.
- Các tích chập 1 × 1 và 3 × 3 vẫn chạy kernel của esp-dl. Lợi thêm từ chính phép tích chập phải chờ phương án kernel mới.
