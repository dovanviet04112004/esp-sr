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

## 2. Chuỗi lượng tử của repo trên gói thử COCO, S = 512 (E15-T3, 30/10)

Máy: RTX 3050 Laptop 4 GB, WSL ~10 GB RAM. Mọi số mAP ở đây là mô phỏng trên PC, chưa phải số chip.

**Gói** `interim/detect/coco2017_yolo26n` (`make detect-coco`, 3 phút 06, đỉnh RAM 3,6 GB):
- `model.pt` là `yolo26n.pt` của Ultralytics học trên COCO, đóng vai model server đưa xuống;
- `train`: 1 024 ảnh train2017 rút theo seed 20261030 trong các ảnh có khung, 7 409 khung;
- `val` và `test`: val2017 chia đôi theo seed, mỗi bên 2 500 ảnh; 18 305 và 18 030 khung; 28 và 20 ảnh không khung.
  Cộng lại 36 335 khung và 48 ảnh nền, đúng như Ultralytics đếm val2017 khi bỏ khung crowd.

**Run** `20261030_87a6589-dirty_144a65`:

| Bước | Thời gian | Đỉnh RAM | Kết quả |
|---|---|---|---|
| `make detect-import` | 26 s | 0,88 GB | ONNX lệch `model.pt` tối đa 2,84e-05 trên 8 ảnh `val` (ngưỡng 1e-3) |
| `make detect-ptq` | 16 phút 29 | 3,95 GB | hiệu chuẩn 64 lô × 16 ảnh ~6 phút; TQT 106 khối trên 256 ảnh ~10 phút |
| `make detect-eval SPLIT=val` | 10 phút 39 | 2,14 GB | bảng dưới |

**mAP trên `val`**, 2 500 ảnh qua letterbox của chip (láng giềng gần nhất, viền 114), chấm bằng pycocotools:

| | mAP50-95 | mAP50 |
|---|---|---|
| float (onnxruntime) | 0,3635 | 0,5151 |
| int8 (mô phỏng ESP-PPQ) | **0,3496** | 0,5003 |
| Tụt | **0,0139** | 0,0148 |

Cửa `eval.gate` 0,02 của cấu hình: qua.

**So với số của Espressif.** Bảng S3 ghi 0,363 ở 512 (ADR-0030). Notebook lượng tử của họ in 0,365 cho bản P4 ở 512,
mô phỏng int8. Hai bên chấm khác nhau:

| | Espressif | Repo |
|---|---|---|
| Ảnh chấm | 5 000 ảnh val2017 | 2 500 ảnh `val` |
| Tiền xử lý lúc chấm | letterbox song tuyến của Ultralytics; chip không làm thế | letterbox láng giềng gần nhất, như chip |
| Thước | bộ chấm của Ultralytics | pycocotools |
| Hiệu chuẩn | 1 536 ảnh kéo giãn thành S×S, không letterbox | 1 024 ảnh qua letterbox của chip |
| TQT | 2 560 ảnh | 256 ảnh: bộ đệm TQT 7,3 MB RAM một ảnh ở các bản đồ 256×256 |

int8 của repo thấp hơn 0,363 là 0,0134, ngoài mức 0,01 của E15-T3. Nhưng float của repo trên đường chấm của chip cũng
chỉ 0,3635, ngang số int8 của họ: phần lớn khoảng cách nằm ở cách chấm, không ở lượng tử. Muốn tách hai phần thì chấm
thêm int8 bằng letterbox song tuyến 🔬.

**Loss từng khối TQT**, sai số bình phương giữa đầu ra int8 và float của khối, trước → sau khi học. Notebook của
Espressif lưu đủ 106 khối, cùng thứ tự với repo. Hai bên học trên ảnh khác nhau, nên chỉ so được cỡ:

| Khối | Espressif | Repo |
|---|---|---|
| 1, `model.0` | 0,0797 → 0,0676 | 0,0879 → 0,0794 |
| 2, `model.1` | 0,3177 → 0,2789 | 0,3314 → 0,2688 |
| 30, `model.8` | 0,0104 → 0,0102 | 0,0082 → 0,0076 |
| 90, conv lớp của P3 | 0,3924 → 0,3899 | 0,3135 → 0,3114 |
| 106, conv lớp của P5 | 6,0225 → 5,9623 | 5,2152 → 5,1696 |

Loss lớn ở conv lớp của P4 và P5 có ở cả hai bên: logit lớp có biên độ lớn.

**`.espdl`.**
- `int8/model.espdl` nặng 4,474 MiB vì kèm cặp thử cho `model->test()`: ảnh vào int8 1×512×512×3 cùng sáu đầu ra.
- Xuất lại không kèm cặp thử: 2,862 MiB (3 001 360 B); bản COCO của Espressif nặng 2,93 MiB. Chỉ bản này vừa khe
  `vision_0` 3,25 MiB (KẾ HOẠCH §3.18). Bản khoá xuống sản phẩm vì thế xuất không kèm cặp thử. Cặp thử dùng cho test app
  của E15-T5, vốn có bảng phân vùng riêng.
- Ảnh vào lượng tử với exponent −6: 0..1 thành 0..64, mỗi bậc gom 4 mức điểm ảnh. Chưa đo ảnh hưởng 🔬.
- Softmax trong khối Attention ra float: log xuất không có thông tin lượng tử cho `Softmax_output`. speaker phải vá chỗ
  này (`softmax_as_espdl`) để chip khớp mô phỏng, nên đây là chỗ đầu tiên cần soi ở E15-T5.

Chưa làm: chấm `test`, đối chứng âm của cửa mAP, lock và `meta.json`, int8 chấm bằng letterbox song tuyến.
