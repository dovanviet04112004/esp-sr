# ADR-0030 — Nhận diện vật thể bằng YOLO26n, model do server học rồi đẩy xuống board

- **Trạng thái**: Chấp nhận (chủ repo, 2026-10-30)
- **Ngày**: 2026-10-30
- **Liên quan**: KẾ HOẠCH §0.1, §1.1, §2.1, §3.18, §6.1, §6.3, §7.6; ADR-0026, ADR-0027, ADR-0028; TASKS E15

---

## Bối cảnh

Chủ repo muốn board nhận diện vật thể trên ảnh. Một server sẽ học model theo các lớp riêng rồi đẩy xuống board;
repo không học model ấy, mà làm sẵn từ bước nhập model trở xuống. Bản thử là YOLO26n học sẵn trên COCO.

- esp-dl có ví dụ `yolo26_detect` và component `espressif/yolo26` 0.1.1 (MIT). Lớp `YOLO26` của component giải JPEG,
  letterbox, lượng tử đầu vào và lấy top-K không cần NMS; số lớp đọc từ model, tên lớp do người dùng đưa.
- Component đòi esp-dl ~3.3.14, còn repo đang ghim 3.3.11. Bản 3.3.14 viết lại kernel Conv, Gemm, MatMul, Resize,
  LUT và Pooling cho S3.
- Espressif đo trên S3: 512×512 mất 2 348 ms cho model, mAP50-95 COCO 0,363; 640×640 mất 3 793 ms, mAP 0,384.
  Model nặng 3 073 040 byte.
- Board B không có camera (KẾ HOẠCH §2.1). Flash 16 MiB đã chia hết: `ota_0` 3 MiB, `models_0` 11 MiB, `storage`
  1,75 MiB. Ảnh model tiếng nói nặng 5,17 MiB với speaker b0 và 8,91 MiB với b3.
- Hai đồ thị esp-dl chạy song song trên hai nhân từng làm sai kết quả cả ở nhân 0 (ADR-0027).

## Các phương án

| Câu hỏi | Phương án | Được | Mất |
|---|---|---|---|
| Đẩy model xuống | **Wi-Fi, một khe, tải vào PSRAM trước** | b0 còn 3,8 MiB dư, b3 vẫn vừa; tải hay kiểm hỏng thì model cũ vẫn chạy | mất điện lúc ghi flash thì tắt nhận diện tới lần tải lại |
| | Wi-Fi, hai khe | đổi khe nguyên tử | `models_0` còn ~5,3 MiB: b0 dư 0,1 MiB, b3 không vào |
| | USB | không phải đổi gì ở mạng | không đúng việc server đẩy xuống |
| Ảnh vào | **host gửi ảnh, camera sau** | thử trọn đường ngay trên board hiện có | chưa nhận diện cảnh thật |
| Server giao gì | **model gốc `model.pt` và dữ liệu `train`, `val`, `test` khuôn YOLO** | server chỉ học; chuỗi xuống chip nằm trọn ở repo | repo phải chạy Ultralytics để xuất ONNX, trong một môi trường riêng |
| | ONNX đã vá đầu dò, tên lớp, ảnh hiệu chuẩn, tập kiểm | repo không chạy Ultralytics (AGPL-3.0) | server phải biết khuôn của chip |
| Trọng số | **đọc thẳng từ flash** | PSRAM chỉ chứa tensor trung gian | chậm hơn chép sang PSRAM, bù bằng tối ưu như b3 |
| | chép sang PSRAM | nhanh hơn | thêm ~3 MiB PSRAM |

## Quyết định

Chủ repo chọn các phương án in đậm.

- Mạng là YOLO26n, chạy bằng esp-dl và `espressif/yolo26`; nâng esp-dl lên 3.3.14 trước mọi việc khác của nhánh, rồi
  kiểm lại mọi model tiếng nói khớp từng bit.
- Server giao model gốc `model.pt` và dữ liệu khuôn YOLO chia sẵn `train`, `val`, `test`. Chủ repo chọn ONNX lúc đầu,
  rồi đổi trong ngày 30/10: chuỗi xuống chip phải nằm trọn ở repo.
- Repo xuất ONNX trong dự án uv riêng `ml/detect_ref/ultralytics/` (Ultralytics ghim bản), đối chiếu với `model.pt`;
  `srpipe` không import Ultralytics.
- Lượng tử theo quy trình YOLO26 của Espressif: int8 cho thân, int16 cho đầu dò, Swish int16 bằng bảng. Hiệu chuẩn
  trên mẫu `train`, chặn bằng mAP int8 trên `val`, báo số trên `test`.
- Model thị giác nằm trong ảnh riêng, khe riêng `vision_0` 3,25 MiB, header ghi sau cùng. `models_0` còn 9 MiB,
  `storage` 0,5 MiB.
- Host báo phiên bản mới qua MQTT retained; board tải HTTP vào PSRAM, kiểm sha256, rồi mới ghi khe và nạp lại.
- `nhin_task` chạy ở nhân 0, ưu tiên thấp nhất; tiếng nói luôn trước.
- Sau khi đúng thì tối ưu tốc độ theo bậc thang của §3.18, như đã làm với speaker b3.

## Hệ quả

- Bảng phân vùng đổi: lần nạp đầu tiên sau đó ghi lại `storage`, và bộ lệnh lấy lại từ tin retained của broker.
- Repo có thêm nhánh `detect` ở `ml/`, `ai_engine/src/detect/`, `host/` và các topic `down/vision`, `up/vision`,
  `down/image`, `up/detections`.
- Camera là việc sau: chọn module, chân (KẾ HOẠCH §2.2), driver.
- Model của server mang giấy phép của dữ liệu server học; `contracts/models.lock.json` và header ghi nguồn.
