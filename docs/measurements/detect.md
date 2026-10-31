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

**mAP trên `test`**, 2 500 ảnh, cùng cách chấm, 11 phút 10: float 0,3645, int8 0,3492, tụt 0,0153, cùng cỡ với `val`.

**Đối chứng âm của cửa** (KẾ HOẠCH §3.18 bước 4). `make detect-coco-broken` dựng gói giữ nguyên ảnh, `model.pt` và
`data.yaml`, nhưng dời nhãn mọi khung sang lớp kế tiếp, mỗi tập 300 ảnh. Run `20261030_0929027-dirty_4e3632`:
- import cho qua: tên lớp của `model.pt` vẫn khớp `data.yaml`, nên bước này không thể thấy lỗi;
- float trên `val` 0,0058, bằng 0,0145 lần mAP 0,40116 mà Ultralytics ghi trong `model.pt` (học ở 640), dưới mức 0,5
  của `eval.min_float_share`: `passes: false`;
- `make detect-deploy` từ chối run.

Gói thật giữ 0,906 lần mAP ấy (0,3635 trên 0,40116): khoảng hở do S = 512 và letterbox của chip.

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
- Lần chạy đầu xuất kèm cặp thử cho `model->test()`: ảnh vào int8 1×512×512×3 cùng sáu đầu ra, nặng 4,474 MiB.
- Không kèm cặp thử: 2,862 MiB (3 001 360 B); bản COCO của Espressif nặng 2,93 MiB. Chỉ bản này vừa khe `vision_0`
  3,25 MiB, nên `ptq` xuất không kèm cặp thử (KẾ HOẠCH §3.18 bước 3). E15-T5 đưa ảnh vào chip theo đường khác.
- Ảnh vào lượng tử với exponent −6: 0..1 thành 0..64, mỗi bậc gom 4 mức điểm ảnh. Chưa đo ảnh hưởng 🔬.
- Softmax trong khối Attention ra float: log xuất không có thông tin lượng tử cho `Softmax_output`. speaker phải vá chỗ
  này (`softmax_as_espdl`) để chip khớp mô phỏng, nên đây là chỗ đầu tiên cần soi ở E15-T5.

**Lock.** Chuỗi chạy lại bằng code đã chốt ở `f3fd898`: run `20261030_f3fd898-dirty_a82130`. Trên `val`, float 0,3635,
int8 0,3504, tụt 0,0131; trên `test`, int8 0,3485. Lock và `meta.json` lấy từ run này.

Chưa làm: int8 chấm bằng letterbox song tuyến.

## 3. Chip so với mô phỏng ESP-PPQ (E15-T5, 30/10)

`make ai-unit-detect` trên board B với run đang khoá, `20261030_f3fd898-dirty_a82130`. Thử 8 ảnh `val`: 2 ảnh giữ cỡ
gốc, 6 ảnh thu về cạnh dài 256 px.
- Nạp ảnh thị giác mất 3 461 ms, gồm kiểm sha256 của 2,86 MiB đọc từ flash. Mỗi ảnh 3 102–3 160 ms, gồm tiền xử lý và
  hậu xử lý.
- Khung `decode.c` ra trùng bản Python trên cả 8 ảnh. Ảnh có sha256 sai bị `load` từ chối.
- 46 trên 48 tensor ra có CRC khác mô phỏng; chỉ `one2one_p4_box` của ảnh 1 và 5 khớp. Tám giá trị đầu mỗi tensor in
  ra đều trùng, nên chỗ lệch nằm rải rác.

**Dò bằng điểm đo.** `make ai-unit-detect TAPS="0 1 2 4 6 8 9 10 13 16 19 22"` lấy thêm đầu ra của 12 khối làm đầu ra
của đồ thị:
- khối 0–9 khớp trên cả 8 ảnh;
- đầu (khối 23, int16) lệch ở mọi ảnh, kể cả ảnh 1, 2 và 5, nơi cả ba đầu vào của nó (khối 16, 19, 22) đều khớp;
- khối 10–22 lệch lác đác. Ảnh 4 lệch từ khối 10, khối Attention, trở đi. Ảnh 0, 3, 6, 7 lệch ở một trong ba lối ra
  int16 của cổ: khối 16, 19 hoặc 22.

**Hai chỗ ESP-PPQ tính khác chip.**
1. Tích chập int16. Nhân S3 của esp-dl nạp bias vào QACC, cộng từng tích 16 × 16 bit đúng tuyệt đối trên làn 40 bit,
   rồi làm tròn nửa lên một lần (`tie728_s16_vector_round_result`). ESP-PPQ cộng bằng float32, chỉ giữ 24 bit, trong
   khi các tích chập của đầu dịch 11–17 bit ra int16, nên tổng lên tới 2^32. Gần nửa bước của lưới ra, hai bên làm tròn lệch nhau một bậc. Bản vá
   `conv_s16_as_espdl` cộng bằng float64, đúng tới 53 bit, rồi làm tròn một lần như chip.
2. Softmax của Attention. Đây đúng là lỗi speaker đã vá bằng `softmax_as_espdl`: ở ảnh 4, một xác suất rơi sát nửa
   bước.

Swish int16 qua bảng tra bước 32 không lệch: ESP-PPQ 1.3.11 đã chép đúng `lut_s16_nearest_neighbor` của S3, làm tròn
nửa lên.

Run ấy mô phỏng lại trên PC, so với CRC chip đã báo (8 ảnh × 18 tensor: 6 đầu ra, 12 điểm đo):

| Bản vá | Khớp chip |
|---|---|
| không | 89/144 |
| `conv_s16_as_espdl` | 133/144; ảnh 4 còn lệch từ khối 10 |
| `conv_s16_as_espdl` + `softmax_as_espdl` | **144/144** |

Hai bản vá khai ở `esp_ppq_patches` của `detect.yaml`, áp cho `ptq`, `eval` và bản dò. Hiệu chuẩn và TQT chạy trên mô
phỏng đã vá, nên số nguyên của lượng tử có thể đổi; chuỗi chạy lại thành run mới.

**Run mới** `20261030_7bf6e8a-dirty_7d38c2`, chuỗi chạy lại ở `7bf6e8af` dưới hai bản vá, lock và `meta.json` lấy từ
run này:

| Bước | Thời gian | Đỉnh RAM | Kết quả |
|---|---|---|---|
| `make detect-ptq` | 19 phút 12 | 3,71 GB | `.espdl` 3 001 424 B |
| `make detect-eval SPLIT=val` | 13 phút 05 | 2,23 GB | float 0,3635, int8 **0,3511**, tụt 0,0124: qua cửa |
| `make detect-eval SPLIT=test` | 13 phút 30 | 2,23 GB | float 0,3645, int8 0,3496, tụt 0,0149 |

PTQ chậm thêm 2 phút và mỗi lượt chấm thêm 2,5 phút: các tích chập int16 tính bằng float64, mà GPU laptop tính
float64 chậm. Run cũ có int8 0,3504 trên `val`.

**Board B**, `make ai-unit-detect`: 4 phút 45 gồm dựng ảnh thử, build, ghi flash và chạy.
- Cả 8 ảnh: 6 tensor ra và các khung khớp mô phỏng từng giá trị; ảnh sha256 sai bị từ chối.
- Nạp 3 469 ms. Mỗi ảnh trung bình 3 140 ms, đỉnh 3 166 ms, gồm letterbox, mạng và `decode.c`; ảnh 640×427 lâu
  nhất. Hai lượt chạy cách nhau 6 phút ra cùng số tới 1 ms.
- Bộ nhớ, đo bằng heap còn trống:

| | RAM nội | PSRAM |
|---|---|---|
| đồ thị đã nạp giữ | 6 072 B | 2 707 852 B |
| một lần chạy dùng thêm, tối đa | 320 B | 11 152 B |
| còn giữ sau lần gỡ đầu | 5 748 B | 0 |
| còn giữ sau mỗi lần nạp và gỡ về sau | 0 | 0 |

- 5 748 B RAM nội chỉ cấp ở lần nạp đầu, không rò theo mỗi lần đổi model (E15-T8). Ca thứ hai của bản dò giữ điều này.
- E15-T2 dựng mạng thẳng bằng esp-dl, tốn 109 KB RAM nội. Qua `ai_engine` chỉ còn 6 KB, vì `ExternalMallocGuard` đẩy
  các khoản cấp phát của esp-dl sang PSRAM.

`make parity-board` có thêm bộ vàng `detect_decode`. 4 ca khớp tuyệt đối trên chip; ca đối chứng âm lệch 3 nên đỏ, đúng
như phải thế. Bộ ấy làm dòng `plan` của bản dựng mặc định dài quá 160 B, nên app dừng trước ca đầu tiên; app giờ in
mỗi khối một dòng `plan`. Hai bản dựng qua: 128 và 61 ca, 4 phút 28.

## 4. Thời gian từng toán tử trên board B (E15-T9, 30/10)

`profile_module` của esp-dl chạy lại từng module trên ảnh cuối của bản dò, một nhân: run `20261030_7bf6e8a-dirty_7d38c2`,
S = 512, trọng số đọc thẳng từ flash. `make ai-profile` xếp 298 module, tổng 3 097,9 ms; lượt chạy thường mất
3 140 ms vì gồm cả letterbox và `decode.c`.

| Loại op | Module | ms |
|---|---|---|
| Conv | 102 | 2 211,6 |
| Swish int8 | 66 | 249,7 |
| Concat | 19 | 160,2 |
| LUT (Swish int16) | 21 | 120,4 |
| Slice | 16 | 79,9 |
| RequantizeLinear | 20 | 67,7 |
| MatMul | 4 | 51,1 |
| Add | 20 | 35,2 |
| Softmax | 2 | 33,1 |
| QuantizeLinear | 2 | 28,6 |
| Transpose | 10 | 23,6 |
| Resize | 2 | 18,8 |
| Mul, Split, MaxPool, Reshape | 14 | 18,0 |

Hai đường chậm của esp-dl chiếm một phần ba thời gian.

**Trọng số lớn hơn cache dữ liệu 64 KB:** 8 tích chập, 767 ms (25%).

| Tích chập | Kiểu | Trọng số | ms | Chu kỳ một nhân-cộng |
|---|---|---|---|---|
| `/model.23/one2one_cv2.2/one2one_cv2.2.0` | 3 × 3, int16, 256 → 16, P5 | 72 KB | 291,9 | 7,42 |
| `/model.5/conv` | 3 × 3 bước 2, int8 | 144 KB | 164,2 | 0,26 |
| `/model.7/conv` | 3 × 3 bước 2, int8 | 288 KB | 79,4 | 0,25 |
| `/model.22/cv2` | 1 × 1, int16 | 192 KB | 75,4 | 0,72 |
| `/model.9/cv2` | 1 × 1, int8 | 128 KB | 46,0 | 0,33 |
| `/model.20/conv` | 3 × 3 bước 2, int8 | 144 KB | 40,1 | 0,25 |
| `/model.22/cv1` | 1 × 1, int8 | 96 KB | 34,9 | 0,33 |
| `/model.8/cv2` | 1 × 1, int8 | 96 KB | 34,6 | 0,33 |

- Nặng nhất là tích chập đầu tiên của nhánh khung ở P5: một module chiếm 9,4% cả mạng. Tích chập cùng chỗ ở P3 và P4
  có trọng số 18 và 36 KB, vừa cache, chạy 0,25 và 0,38 chu kỳ. Ở P5 nó chậm hơn 20–30 lần: trọng số đọc lại từ flash
  ở mỗi điểm ảnh ra.
- Ba tích chập 3 × 3 int8 lớn chỉ chậm hơn `model.3` (0,19 chu kỳ, vừa cache) chừng một phần ba.
- `cache_sized_convolutions` hiện chỉ cắt tích chập 1 × 1 int8 theo kênh ra, nên trong 8 tích chập trên chỉ áp được
  cho 3.

**Số kênh không chia hết cho 16:** 3 tích chập, 258 ms (8%).
- `/model.2/m.0/cv1`, 16 → 8 kênh: 116,5 ms, 1,48 chu kỳ.
- `/model.0/conv`, ảnh RGB 3 → 16 kênh: 108,9 ms, 0,92 chu kỳ.
- `/model.2/m.0/cv2`, 8 → 16 kênh: 32,4 ms.

## 5. Tối ưu ở bước xuất (E15-T10, 30/10)

Ba bản vá ở bước xuất, giữ nguyên mọi số nguyên của đồ thị. Mỗi bước, `make ai-unit-detect` khớp mô phỏng từng giá trị
trên 8 ảnh. Hai bản vá sau đo bằng `PATCHES=` trên run có bản vá đầu, không lượng tử lại.

| Bước | Mỗi ảnh, trung bình / đỉnh | Các module, một nhân | PSRAM của đồ thị |
|---|---|---|---|
| run `20261030_7bf6e8a-dirty_7d38c2`, chưa vá | 3 140 / 3 166 ms | 3 097,9 ms, 298 module | 2 707 852 B |
| `cache_sized_convolutions` cho int16 mọi kernel: run `20261030_8b3b0e4-dirty_001351` | 2 845 / 2 869 ms | 2 757,8 ms, 324 module | 2 757 412 B |
| thêm `lean_quantizers` và `aligned_channels` | 2 719 / 2 745 ms | 2 633,6 ms, 319 module | 2 852 444 B |
| run chính thức với cả ba: `20261030_4ae2501-dirty_1e05ac`, int8 `val` 0,3506 | **2 722 / 2 750 ms** | 2 635,1 ms, 318 module | 2 852 444 B |

**Cắt tích chập theo kênh ra.** 5 tích chập thành 21 phần, mỗi phần có trọng số vừa nửa cache, thêm 5 Concat:

| Tích chập | Trước | Sau, cộng các phần | Chu kỳ một nhân-cộng |
|---|---|---|---|
| `/model.23/one2one_cv2.2/one2one_cv2.2.0`, int16 3 × 3, 2 phần 8 kênh | 291,9 ms | 23,1 ms | 7,42 → 0,59 |
| `/model.22/cv2`, int16 1 × 1, 6 phần 40 kênh và 1 phần 16 | 75,4 ms | 61,2 ms | 0,72 → 0,57–0,68 |
| `/model.9/cv2`, int8 1 × 1, 4 phần 64 kênh | 46,0 ms | 29,8 ms | 0,33 → 0,21–0,22 |
| `/model.22/cv1`, int8 1 × 1, 4 phần | 34,9 ms | 20,2 ms | 0,33 → 0,19–0,20 |
| `/model.8/cv2`, int8 1 × 1, 4 phần | 34,6 ms | 19,8 ms | 0,33 → 0,19 |

- Gần hết phần lợi đến từ tích chập int16 ở P5.
- Ba tích chập int8 3 × 3 có trọng số 144–288 KB (`model.5`, `7`, `20`) để nguyên. Chúng chạy 0,25 chu kỳ, gần mức 0,19 của
  tích chập vừa cache; cắt ra thì phần nào cũng phải đọc lại cả ảnh vào.
- Bảy phần của `/model.22/cv2` mỗi phần có một RequantizeLinear riêng, chuyển cùng Concat sang int16, mỗi cái 5,2 ms:
  RequantizeLinear tăng từ 67,7 lên 98,5 ms. `lean_quantizers` gộp chúng làm một, về 67,7 ms.
- Các phần thêm 49 560 B PSRAM.

**Đệm khối 8 kênh của `model.2`.** `aligned_channels` đệm vùng 8 kênh giữa hai tích chập 3 × 3, qua Swish, lên 16 kênh
bằng kênh 0, để cả hai đi đường vector của esp-dl:
- `cv1` 16 → 8 kênh: 116,5 → 21,9 ms;
- `cv2` 8 → 16 kênh: 32,5 → 21,9 ms;
- Swish của vùng ấy: 4,1 → 8,2 ms;
- thêm 95 032 B PSRAM.

## 6. Cỡ ảnh vào S (E15-T11, 30/10)

Mỗi S là một run riêng từ cùng gói COCO và cùng `model.pt`, có cả ba bản vá ở bước xuất. Run 416 và 320 tạo bằng
`make detect-import SET="size=<S>"`, đo trên board bằng `make ai-unit-detect RUN=<run>`, không deploy. Cả ba khớp mô phỏng
từng giá trị trên 8 ảnh.

| S | Run | mAP50-95 `val` float / int8 | Mỗi ảnh, trung bình / đỉnh | Nạp | PSRAM của đồ thị |
|---|---|---|---|---|---|
| **512** (đang khoá) | `20261030_4ae2501-dirty_1e05ac` | 0,3635 / **0,3506** | **2 722 / 2 750 ms** | 3 075 ms | 2 852 444 B |
| 416 | `20261030_c73d011-dirty_1ee057` | 0,3291 / 0,3202 | 1 815 / 1 849 ms | 2 177 ms | 1 962 892 B |
| 320 | `20261030_c73d011-dirty_d298a9` | 0,2897 / 0,2776 | 1 048 / 1 085 ms | 1 420 ms | 1 256 708 B |

- Thời gian theo S²: 416 lấy 67% thời gian của 512 (S² là 66%), 320 lấy 39% (S² là 39%).
- mAP int8 so với 512: 416 mất 0,030, tức 8,7%; 320 mất 0,073, tức 20,8%. Phần mất do lượng tử ở cả ba cỡ đều dưới cửa
  0,02: 0,0129, 0,0089, 0,0121.
- `model.pt` học ở 640. Mọi S ở đây chấm trên cùng 2 500 ảnh `val`, cùng letterbox của chip.
- Chọn S là việc của chủ repo; sản phẩm giữ 512 tới khi chọn.

**PointwiseS8 của speaker, thử 30/10.** `make ai-unit-detect PATCHES="pointwise_s8"` trên run chính thức: khớp từng giá
trị, nhưng mỗi ảnh 2 807 ms thay vì 2 722, nên không dùng.
- 40 tích chập 1 × 1 int8 thành PointwiseS8, tốn 568,2 ms. Phần esp-dl chúng thay, gồm cả Add và Concat ghép theo, tốn
  khoảng 485 ms.
- 23 chỗ PointwiseS8 nhanh hơn, mỗi chỗ chỉ 0,6–2,4 ms.
- Chậm nhất là ba tích chập 256 → 256 kênh trên lưới 16 × 16 (`model.8/cv1`, `model.10/cv1`, `model.10/cv2`), 20,6 →
  56,6 ms mỗi cái: trọng số 64 KB, đúng bằng cache dữ liệu.
- Các phần đã cắt mà PointwiseS8 ghép lại thành một op cũng chậm hơn, ví dụ 7,6 → 29,1 ms.
- Kernel 1 × 1 của esp-dl chạy các tích chập này ở 0,19–0,22 chu kỳ một nhân-cộng. Các kernel nhanh của PointwiseS8 viết
  cho K 24–192 của speaker.

## 7. ConvLut: tích chập cùng bảng tra của hàm kích hoạt (E15-T13, ADR-0031, 30/10)

`conv_lut` gộp mỗi tích chập với Swish int8 hay bảng tra int16 đọc riêng nó thành một op ConvLut. Op chạy kernel tích chập
của esp-dl theo dải hàng, mỗi dải ra 16 KB, rồi tra bảng lên dải ấy khi còn trong cache. 82 cặp được gộp.

| Bước | Mỗi ảnh, trung bình / đỉnh | Các module, một nhân | PSRAM của đồ thị |
|---|---|---|---|
| run `20261030_4ae2501-dirty_1e05ac`, ba bản vá ở bước xuất | 2 722 / 2 750 ms | 2 635,1 ms, 318 module | 2 852 444 B |
| thêm `conv_lut` (`PATCHES=`) | 2 662 / 2 694 ms | 2 626,3 ms, 237 module | 2 825 888 B |
| run chính thức `20261030_38a8738-dirty_37f714`, int8 `val` 0,3503 | **2 658 / 2 690 ms** | | 2 825 888 B |

Cả ba khớp mô phỏng từng giá trị trên 8 ảnh.

- **Lợi nhỏ hơn ước lượng.** 82 ConvLut nhanh hơn cặp tích chập và kích hoạt cũ chỉ 44,5 ms: 57 cặp nhanh hơn, 20 cặp chậm
  hơn. `model.0`, chẳng hạn, đi từ 142,1 xuống 136,3 ms, dù lượt Swish riêng của nó từng tốn 32,9 ms.
- **Vì sao.** Lượt bảng tra nghẽn ở chính phép tra, không ở PSRAM: S3 không có lệnh tra byte theo vectơ, mỗi byte tốn vài
  chu kỳ dù dữ liệu đã ở trong cache. Chép bảng ra vùng căn 16 byte để `lut_s8` đi đường vectơ không đổi gì: 2 663 so với
  2 662 ms.
- **Một Add chậm đi.** `/model.2/m.0/Add` từ 11,7 lên 56,3 ms, dù nút, số mũ và đầu vào y hệt hai bên. Bỏ các tensor trung
  gian làm bộ xếp bộ nhớ của esp-dl đặt lại các tensor trong PSRAM.

**Các op chỉ chép dữ liệu còn lại**, theo bảng ConvLut, cộng 253 ms:
- Slice và Concat trong các khối C3k2: ~177 ms; `model.2` (128 × 128) 65,5 ms, `model.16` 32,2, `model.4` 27,0;
- Concat của cổ mạng (`model.12`, `15`, `18`, `21`): ~55 ms;
- Concat của các tích chập đã cắt: 10,1 ms.

## 8. Trọng số trên vùng mượn RAM nội và kernel stem RGB (E15-T13, ADR-0034, 31/10)

Mọi bước đo trên board B bằng `make ai-unit-detect`, run chính thức `20261030_38a8738-dirty_37f714`, khớp mô phỏng từng
giá trị trên 8 ảnh trừ chỗ ghi khác. Ảnh xuất có `PATCHES="softmax_quant softmax_scale"`; các bước firmware sau đó chạy
`KEEP=1` trên cùng ảnh ấy.

**Bản vá ở bước xuất.**

| Bước | Mỗi ảnh, trung bình / đỉnh | Ghi chú |
|---|---|---|
| ConvLut (§7) | 2 658 / 2 690 ms | |
| thêm `softmax_quant`, `softmax_scale` | **2 629 / 2 661 ms** | gốc của các bước dưới |
| thêm `lean_transposes`, `attention_core` | 2 636 / 2 667 ms | lấy thêm 5 712 B RAM nội; không dùng |

`attention_core` viết cho bố cục [1, T, H × D] của speaker, không bắt được attention của khối PSA.

**ConvLut, các cách chạy thử.**

| Bước | Mỗi ảnh | Khớp |
|---|---|---|
| lát kênh ≤ 16 KB trọng số, dải 1 hàng, nạp trước dải sau | 2 607 ms | **sai** 63 chỗ |
| dải 1 hàng, nạp trước dải sau, kernel stem RGB | 2 800 ms | khớp |
| dải như §7, nạp trước, trọng số ≤ 64 KB chép vào vùng mượn, kernel stem | 2 273 ms | khớp |
| như trên, bỏ nạp trước | 2 255 ms | khớp |
| **lát kênh vừa vùng mượn, chép lát vào vùng mượn, kernel stem**, vùng 64 KB | **2 118 / 2 144 ms** | khớp |
| như trên, vùng 32 KB | 2 157 / 2 188 ms | khớp |
| như trên, vùng 16 KB | 2 359 / 2 390 ms | khớp |

- **Lỗi của lượt sai:** lát cuối hẹp hơn các lát trước (48, 48, 32 kênh), mà bảng tra vẫn đi đủ 48 giá trị một điểm ảnh, lấn
  sang 16 kênh đầu của điểm ảnh sau vốn đã tra rồi. Tra đúng số kênh của lát thì khớp.
- **Nạp trước dải sau không lợi:** dải 1 hàng làm tích chập 3 × 3 lớn chậm hẳn (`model.5` 169 → 227 ms), vì vòng của esp-dl
  trong mỗi dải đọc lại toàn bộ trọng số từ flash. Giữ dải như §7 thì nạp trước vẫn thêm 3–8 ms ở các lớp nhỏ, và bỏ nó
  nhanh hơn 18 ms.
- **Trọng số trong RAM nội là đòn bẩy chính.** Đọc qua cache từ flash, tích chập int8 của esp-dl chạy 0,2–0,27 chu kỳ một
  nhân-cộng; trọng số trong vùng mượn thì ~0,11, kể cả khi đầu vào vẫn ở PSRAM. Lớp lớn hơn vùng chạy theo lát kênh ra,
  mỗi lát chép vào vùng rồi đọc lại đầu vào; vùng nhỏ thì lát hẹp, đầu vào đọc lại nhiều lần hơn.
- Lượt đo từng module trước đây chạy ngoài `EspdlNet::step`, nên không có vùng mượn. Nay app thử cấp vùng như một lượt
  chạy.

Thời gian từng module, ms (một nhân, lượt đo trên ảnh cuối):

| Module | Trọng số | Gốc | Vùng 16 KB | 32 KB | 64 KB |
|---|---|---|---|---|---|
| `/model.0/conv`, stem RGB | 0,4 KB | 136,2 | 59,3 | 59,3 | 59,3 |
| `/model.3/conv` | 36 KB | 124,6 | 97,4 | 76,8 | 69,1 |
| `/model.5/conv` | 144 KB | 169,1 | 169,2 | 90,6 | 91,9 |
| `/model.7/conv` | 288 KB | 76,7 | 76,8 | 52,1 | 42,9 |
| `/model.20/conv` | 144 KB | 41,3 | 41,3 | 26,3 | 25,3 |
| `/model.13/cv1` | 48 KB | 55,3 | 34,3 | 28,8 | 24,4 |
| `/model.19/cv2`, int16 | 48 KB | 65,2 | 45,6 | 35,9 | 31,0 |
| `/model.8/cv1` | 64 KB | 24,7 | 12,3 | 11,3 | 10,5 |
| ConvLut, 82 module | | 1 839,0 | 1 522,9 | 1 318,2 | 1 289,0 |
| Cả đồ thị, 235 module | | 2 592,8 | 2 276,1 | 2 071,8 | 2 043,2 |

- Ở 16 KB, `model.5`, `7`, `20` không được lát nào: một lát 16 kênh của chúng cần 18 KB.
- **Kernel stem RGB** (`stem_s8_esp32s3.S`): 136,2 → 59,3 ms, ~45 chu kỳ một điểm ảnh cho 27 phép nhân-cộng vector, phần
  còn lại là bảng tra Swish 16 byte một điểm ảnh.
- Chưa đổi: `model.1` (16 → 32 kênh, 59,7 ms) và `model.2/cv2` (48 → 64 kênh 1 × 1 trên 128 × 128, 62,2 ms) nghẽn ở
  vòng mỗi tap và lượt đọc ghi PSRAM, không ở trọng số.
- **Một module chậm gấp bốn mà không rõ vì sao:** `/model.4/m.0/cv2` 58,7 ms ở firmware gốc của bước này, 13,5 ms ở mọi
  firmware sau, trên cùng một ảnh xuất. `/model.2/m.0/Add` từng như thế ở §7 (11,7 → 56,3 ms) giữa hai ảnh xuất.
  Nguyên nhân chưa rõ 🔬.
