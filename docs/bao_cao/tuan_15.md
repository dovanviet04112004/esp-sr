# Báo cáo tuần 15 — thực tập sinh kỹ thuật

Tuần 15 (26/10 – 30/10/2026)

## 1. Thông tin chung

| Mục | Nội dung |
|---|---|
| Họ và tên | Đỗ Văn Việt |
| Phòng ban | Thiết kế điện tử |
| Người hướng dẫn (Mentor) | Anh Chung |
| Tuần báo cáo | Tuần 15 (26/10 – 30/10/2026) |
| Phạm vi tuần | Dự án esp-sr. Chốt chuỗi xử lý âm thanh đầy đủ đang chạy trên board: tiền xử lý, đặc trưng, từ đánh thức, nhận lệnh, xác minh người nói, gửi kết quả về máy tính. Trong tuần: đưa mạng xác minh người nói b0, b3 và bộ dò cao độ SwiftF0 lên chip bằng các op riêng, thay từ đánh thức bằng từ tạm "on". Bắt đầu nhánh nhận diện vật thể YOLO26n. Mọi số đo trên board B: ESP32-S3, hai micro INMP441 cách 4,5 cm |

## 2. Tóm tắt tuần

Chuỗi nghe lệnh tiếng Việt giờ chạy trọn trên board B. Hai micro qua khối tiền xử lý `dsp_afe` ở nhân 1, tốn 16,3% thời
gian. Nhân 0 lo phần còn lại:
- tính log-mel và cao độ SwiftF0;
- dò từ đánh thức "on";
- mở cửa sổ nhận lệnh bằng mạng CTC;
- xác minh người nói bằng ReDimNet2 b0 chấm AS-norm.

Kết quả gửi về máy tính qua MQTT. Từ lúc hết câu tới khi có lệnh mất khoảng 1,66 s. Trong đó 514 ms là
quyết định lệnh (trung vị) và 504 ms là xác minh người nói.

Ba thay đổi lớn nhất trong tuần:
- **Xác minh người nói nhanh hơn.** 11 op riêng của `ai_engine` thay các op chậm của esp-dl, kết quả khớp từng bit. Mạng b0
  từ 1 173,7 xuống 342,5 ms. Lần xác minh trong sản phẩm từ 1,34 s xuống 504 ms. Mạng b3, nhiều ngôn ngữ, từ 7,09 s xuống
  1,40 s.
- **Cao độ SwiftF0 thay bộ dò Kaldi.** Mạng CNN chạy theo dòng 1,76 ms mỗi bước 16 ms, khớp mô phỏng từng bit.
- **Từ đánh thức tạm "on"** (ADR-0029), học lại ba vòng. Ở ngưỡng 850‰ chủ repo chọn: bắt 28/41 lần "on" của chủ repo
  qua board, 7,4 lần nhận nhầm mỗi giờ trên tập thử.

Nhánh nhận diện vật thể YOLO26n đi được từ gói model của server tới chip:
- Lượng tử theo quy trình của Espressif. mAP50-95 int8 0,35 trên 2 500 ảnh COCO, tụt 0,013 so với float.
- Chip khớp mô phỏng từng giá trị, sau khi vá hai chỗ ESP-PPQ tính khác chip.
- Đã tối ưu tốc độ một vòng: mỗi ảnh 512×512 từ 3,14 s còn 2,72 s, kết quả không đổi một số nguyên nào.

## 3. Chuỗi xử lý âm thanh đầy đủ (30/10)

### 3.1. Sơ đồ

```
2 micro INMP441 cách 4,5 cm, I2S 16 kHz ──► thu_task (nhân 1)
   ▼
sach_task (nhân 1): dsp_afe, mỗi bước 16 ms
   hpf 80 Hz → stft 512/256 mỗi micro → balance (bảng hiệu chuẩn) → doa (GCC-PHAT 2–8 kHz, lưới 2°)
   → khe không gian: trộn trần (trung bình hai kênh) → ns (OM-LSA + IMCRA, sàn −12 dB) → istft
   → vad (GMM của WebRTC, chế độ 2, kéo dài 240 ms) → agc (−26 dBFS, chặn đỉnh −3 dBFS, nhìn trước 4 ms)
   ▼  q_clean, 128 bước ≈ 2 s, PSRAM
nhan_task (nhân 0): svc_listen
   mỗi bước: log-mel 80 dải + cao độ SwiftF0 (swift_front → CNN mỗi 16 bước → decode → swift_dims)
   NGHE: mạng wake mỗi 8 bước → vượt ngưỡng → WAKE, sang LENH
   LENH: cửa sổ lệnh, mạng command_ctc theo dòng → COMMAND hoặc REJECT
   ▼  q_verify
xac_task (nhân 0): spk_fbank + ReDimNet2 b0 + AS-norm → COMMAND kèm người nói, hoặc REJECT SPEAKER
   ▼  q_event_up
gui_task → MQTT: up/event, up/telemetry mỗi giây, up/heartbeat mỗi 30 s, up/speakers → broker EMQX → máy tính
```

Sản phẩm hiện không có loa phát, nên không dựng `aec`. Khe không gian chọn bằng Kconfig giữa `gsc`, `bss` và trộn trần;
bản dựng sản phẩm đang dùng trộn trần.

### 3.2. Tiền xử lý `dsp_afe`, nhân 1

Thời gian là trung bình / đỉnh mỗi bước 16 ms, đo ngày 29/10 ở profile `bench`. Mọi khối nằm trong RAM nội.

| Khối | µs mỗi bước | Chất lượng đã đo |
|---|---|---|
| hpf, 2 kênh | 41,1 / 45,3 | — |
| stft, 2 kênh | 283,7 / 287,0 | — |
| balance | 18,4 / 22,7 | — |
| doa | 640,9 / 642,6 ở bước dò; 22,1 ở bước khác | cảnh dựng VIVOS, một người nói ở 30–150°: sai trung bình 2,7°, 99% trong ±10°; bản thu board lệch 0–28° |
| ns (OM-LSA) | 1 687,6 / 1 875,5 | nhiễu dừng giảm 10–11,6 dB ở chỗ ngắt, tiếng nói mất ≤ 1,2 dB; trên board: phòng và quạt 11–12 dB, riêng nhạc 4 dB |
| istft | 158,0 / 162,0 | — |
| vad, chế độ 2 | 138,0 / 175,3 | F1 0,791, ngưỡng năng lượng tốt nhất 0,717 (VIVOS test, ≈ 2,2 h) |
| agc | 257,2 / 261,1 | mọi mức vào −50 đến −10 dBFS ra trong ±3 dB, lệch nhất 1,56 dB |
| **Cả chuỗi** | **2 606,2 / 3 291,5** | **16,3% bước, đỉnh cộng dồn 20,6%** (mục tiêu ≤ 50% / ≤ 80%) |

### 3.3. Đặc trưng và các mạng, nhân 0

| Khối | Model và tham số | Chi phí | Bộ nhớ | Chất lượng đã đo |
|---|---|---|---|---|
| log-mel | 80 dải, 20–7 600 Hz | 87,7 / 93,3 µs mỗi bước | 3 680 B | — |
| cao độ SwiftF0 | 25 949 tham số, int8, chạy theo dòng 16 khung một lần | front end 2,37 ms; CNN và decode 1,90 ms; swift_dims 44 µs; ≈ 4,6 ms mỗi bước | CNN 246 632 B PSRAM | int8 so với float lệch 2,5 / 6,8 / 22,4 cent (trung vị / p90 / p99); 2,1% khung đổi hữu thanh |
| từ đánh thức | "on", mạng TCN 64 kênh, trường nhìn 127 khung ≈ 2 s; run "on3"; làm trơn 5 bước; ngưỡng NVS 850‰ | 2 245 µs mỗi lần chạy 8 khung, ≈ 0,3 ms mỗi bước | 128 880 B | val: bắt 92,4%, 5,73 lần nhận nhầm/giờ; test: 7,40 lần/giờ; 28/41 lần "on" của chủ repo qua board |
| lệnh | `command_ctc` v8: 44 đơn vị âm có thanh điệu, 83 chiều vào, hàng lượng tử percentile, đồ thị đọc thẳng từ flash | 121,6 ms mỗi lượt 16 bước; 9,8 ms mỗi bước khi có cửa sổ | 322 768 B tensor | Gate 3 (bản thu board của chủ repo ngày 07/10) ở ngưỡng của ảnh 100/0/70: 172/209 đúng, 1/126 nhận nhầm; lỗi đơn vị trên test 0,393. Board đang đặt ngưỡng 300/10/65535, chưa có số Gate 3 ở mức ấy |
| người nói | ReDimNet2 b0, int8, đọc từ flash; AS-norm K = 100 trên nhóm chuẩn 380 hàng, 27 người; đăng ký từ 10 lệnh | mạng 380,4 ms; spk_fbank 79,1 ms; **cả lần xác minh 504 ms** | 1 429 600 B tensor | EER 4,00%; giữ 95,8% lệnh ở 1% kẻ mạo danh, 98,2% ở 5%; chủ repo 98,9% ở 1 m, 97,5% ở 3 m |

### 3.4. Luồng nghe `svc_listen`

- **NGHE**: mỗi bước chấm từ đánh thức; vượt ngưỡng thì báo WAKE và sang LENH từ bước kế tiếp.
- **LENH**: mỗi câu nói mở đúng một cửa sổ lệnh, đuôi của từ đánh thức bị bỏ. Có quyết định, hoặc 1,5 s không có câu nào,
  thì về NGHE. Trong LENH, các lần vượt ngưỡng đánh thức không tính.
- **Đăng ký giọng**: mỗi câu đều mở cửa sổ, không cần từ đánh thức.
- **Mã từ chối** gửi lên máy tính: LOW_SCORE, LOW_MARGIN, LOW_SYLLABLE, PART, FRAME_GAP, SPEAKER. Payload chỉ có mã, máy
  tính đổi mã thành câu.
- **Lượt chạy thật 29/10**, 14 phút sau các bản sửa: 59 WAKE, 36 COMMAND, 9 PART, 7 LOW_SCORE, 7 SPEAKER.

### 3.5. Tài nguyên và độ trễ

**CPU.**
- Nhân 1: 16,3% trung bình.
- Nhân 0: bảng ngân sách còn ghi 13,7%, nhưng số ấy tính bộ dò Kaldi cũ. Với SwiftF0, các khối theo nhịp ước khoảng 26%
  bước 🔬.
- Lúc có cửa sổ lệnh, nhân 0 gần như bận cả bước, ước 15/16 ms 🔬.

**Độ trễ quyết định**, đo 29/10 trên board B, tính từ lúc câu nói đóng lại:

| Kết quả | Trung vị | p90 | Lớn nhất |
|---|---|---|---|
| COMMAND | 514 ms | 678 ms | 5 856 ms |
| REJECT | 695 ms | 4 956 ms | 6 584 ms |

Cộng 240 ms kéo dài của `vad`, 400 ms khoảng nghỉ đóng câu và 504 ms xác minh người nói, từ lúc hết tiếng tới COMMAND mất
khoảng 1,66 s. Lượt đo dùng từ đánh thức on2 ở 900‰, chưa phải on3.

**Bộ nhớ và phân vùng flash 16 MB:**

| Phân vùng | Cỡ | Nội dung |
|---|---|---|
| ota_0 | 3 MiB | firmware, 2 137 584 B |
| models_0 | 9 MiB | ảnh model tiếng nói: 5 425 984 B với b0, 9 347 264 B với b3 |
| vision_0 | 3,25 MiB | ảnh model thị giác (mục 5) |
| storage | 0,5 MiB | LittleFS: bộ lệnh, cấu hình |

PSRAM còn trống khoảng 4,9 MiB khi sản phẩm khởi động xong. RAM nội còn ít: khối trống lớn nhất sau `dsp_afe` là
49 152 B.

## 4. Việc trong tuần ở chuỗi âm thanh

### 4.1. Xác minh người nói: op riêng trên chip

Esp-dl chạy mạng ReDimNet2 qua nhiều op nhỏ, mỗi op đọc ghi cả tensor qua PSRAM, một số đi đường chậm vì số kênh 24 không
chia hết cho 16. Tuần này viết 11 op riêng trong `ai_engine`, mỗi op một module Python cùng tên để xuất model. Bước nào
cũng giữ nguyên số nguyên của đồ thị, kiểm bằng phép so từng bit trên board.
- **Các op**: SoftmaxQuant, LayerNormS8, StageSum, ConvBlock, MatMulS8, AttentionCore, StemConv, StrideDepthwise,
  WeightedStats, GlobalStats, PointwiseS8. Sau đó thêm GroupNormS8, TimeDepthwise cho b3 và họ op Band* cho SwiftF0.
- **b0**: đồ thị trong PSRAM 1 173,7 → 342,5 ms, tensor 2 134 984 → 1 419 476 B; đồ thị đọc từ flash 408,5 → 380,4 ms.
  Lần xác minh trong sản phẩm 1 340 → 504 ms.
- **b3** (đa ngôn ngữ, EER 2,99%): 7 089,2 → 1 399,6 ms trên một nhân. Ảnh model 9,35 MB, nên khe model nới lên 11 MB
  ngày 28/10. Sản phẩm chọn b0 hay b3 bằng `make product SPEAKER=`. Ngày 29/10 chủ repo đặt lại b0.
- **Mượn RAM nội** (ADR-0025): op mượn RAM nội trong lúc mạng chạy, để giữ trọng số và hàng đệm. ConvBlock 2 428 → 919 ms.
  Trên heap thật của sản phẩm, khối trống lớn nhất chỉ 49 152 B, nhỏ hơn mức xin 79 680 B, nên op mượn khối trống lớn
  nhất. Ở mức ấy, b3 từ 3 424,0 ms không mượn còn 1 870,2 ms.
- **Nạp trước dcache** (ADR-0026): đọc trước hàng kế tiếp vào cache trong lúc tính hàng hiện tại. ConvBlock 795,2 →
  746,6 ms. Kết quả vẫn đúng từng bit khi nhân 1 đang ghi NVS.
- **Hai nhân** (ADR-0027): chia op của b3 cho hai nhân chỉ được 1 401,6 → 1 310,1 ms, không phải 0,9–1,0 s như ước tính.
  Khi phần chạy song song đọc lại PSRAM nó vừa ghi, kết quả lệch tới 21. Chạy cả mạng người nói ở nhân 1 song song với các
  mạng ở nhân 0 thì 2/6 kết quả sai, b0 chậm từ 468 lên 1 139 ms. Sản phẩm giữ một nhân.

### 4.2. Cao độ SwiftF0 lên chip

Mạng lệnh tiếng Việt cần cao độ để phân biệt thanh điệu. Bốn bộ dò cao độ đã được đo trên tín hiệu board. SwiftF0
(Nieradzik 2025) giữ thanh của âm tiết đầu tốt nhất, cả trên giọng chủ repo lẫn tập `val`. Bản gốc 95 842 tham số nặng
gấp chục lần sức esp-dl trên board. Bản viết lại cho chip còn 25 949 tham số, 3,3 triệu phép nhân-cộng mỗi bước.
- **Front end** `swift_front` trong `dsp_spec`, khớp bản Python: 3 107 → 2 367 µs mỗi bước, sau khi ghép cửa sổ vào lúc
  nạp FFT.
- **Mạng CNN**: đệm 12 kênh lên 16 cho đường vector, rồi viết các op BandBlock, BandHead, BandStem, BandChain. Từ 26,3 ms
  xuống 1,675 ms mỗi bước; chạy theo dòng 16 khung một lần, 1,76 ms.
- **Làm tròn đầu vào**: ESP-PPQ làm tròn nửa lên. Sửa chip theo đúng cách ấy, bài kiểm `listen-unit` từ 104/105 lên 336/336
  cửa sổ khớp.
- Chủ repo chọn SwiftF0 cho sản phẩm ngày 29/10; bộ dò Kaldi ra khỏi đường nghe. Mạng lệnh v8 nghe trên cao độ mới: lỗi
  đơn vị trên val 0,345, không cao độ là 0,375; thanh của âm tiết sau đúng 77,3%.

### 4.3. Từ đánh thức tạm "on"

Model "trợ lý" cũ (`wake/v4`) chỉ bắt 1/104 câu trên board, vì thiếu mẫu dương thật. Chủ repo không dùng giọng tổng
hợp làm dữ liệu học, còn các kho mở chỉ cho vài chục mẩu "trợ lý" sạch. Cùng mạng và cùng đường board, pilot "yes" của
Speech Commands bắt 4/5 câu với 2–4 lần báo nhầm mỗi giờ: đó là dữ liệu thật của nhiều nghìn người nói. Vì vậy dùng tạm
"on" của Speech Commands (ADR-0029), để cả chuỗi chạy được với từ đánh thức trong lúc chờ đủ bản thu "trợ lý".

| Run | Thay đổi | Ngưỡng | Bắt trên val | Nhận nhầm/giờ trên test | "on" của chủ repo qua board |
|---|---|---|---|---|---|
| on1 | lần học đầu | 960‰ | 78,5% | 3,73 | 17/41 |
| on2 | thêm 27 000 mẫu âm khó | 920‰ | 83,6% | 3,52 | 6/41 |
| on3 | bốc mẫu tiếng Anh theo cao độ, tăng mức to của từ 0–20 dB | 920‰ | 85,1% | 4,29 | 22/41 |
| on3 | ngưỡng chủ repo chọn | **850‰** | 92,4% | 7,40 | **28/41** |

- on2 bắt kém giọng chủ repo vì tập học thiên lệch. Mẫu tiếng Anh dương thì nhỏ và trầm; mẫu âm tiếng Việt qua `agc` thì
  to và cao. Kết quả là "on" nói to, giọng cao bị chấm thấp hơn.
- Ảnh model định dạng 11 mang luôn ngưỡng và độ làm trơn. Điểm trên chip là số nguyên phần nghìn, khớp bộ vàng của Python.

### 4.4. Các thay đổi khác

- **`svc_listen`**: thêm trạng thái NGHE và LENH quanh từ đánh thức. Sửa lỗi kẹt sau 7 phút chạy thật: hàng đợi cửa sổ
  đầy. Báo FRAME_GAP khi cao độ không tới kịp cửa sổ đã đóng.
- **Đăng ký giọng**: thử 5 lệnh. Ngưỡng b0 thành 1 359…2 923; chủ repo giữ 98,7 / 97,8%, so với 98,9 / 97,5% khi đăng ký
  10 lệnh. Quay lại 10 lệnh khi chủ repo chọn b3.
- **esp-dl 3.3.11 → 3.3.14**, cần cho YOLO26: mọi phép kiểm chip khớp Python vẫn xanh, các mạng nhanh lên 0–4%. Lệnh
  126,3 → 121,6 ms, từ đánh thức 2 345 → 2 245 µs.

## 5. Nhận diện vật thể YOLO26n (sơ lược)

### 5.1. Mục tiêu

Một máy chủ học model nhận diện vật thể và gửi xuống board; board nhận ảnh, trả khung và tên vật (ADR-0030).
- **Gói của server**: `model.pt` gốc của Ultralytics và dữ liệu khuôn YOLO, chia `train`, `val`, `test`. Repo tự lượng
  tử, kiểm chất lượng rồi nạp vào khe riêng `vision_0`, không đụng ảnh model tiếng nói.
- **Model**: YOLO26n, đầu ra one-to-one không cần NMS. Esp-dl 3.3.14 có sẵn các op của nó.
- **Gói thử**: model COCO 80 lớp của Ultralytics; 1 024 ảnh train2017 để hiệu chuẩn; val2017 chia đôi thành `val` và
  `test`, mỗi bên 2 500 ảnh.

### 5.2. Bản dựng sẵn của Espressif trên board B

| S | Trọng số | Suy luận | PSRAM khi dựng xong | PSRAM đỉnh |
|---|---|---|---|---|
| 512 | đọc từ flash | 3 014–3 024 ms | 2,57 MiB | 3,25 MiB |
| 512 | chép sang PSRAM | 2 468–2 478 ms | 5,27 MiB | 5,91 MiB |
| 640 | đọc từ flash | 4 876–4 887 ms | 3,98 MiB | 4,65 MiB |

Sản phẩm còn ~4,9 MiB PSRAM, nên chỉ bản 512 đọc trọng số từ flash là vừa. Chủ repo chọn S = 512.

### 5.3. Chuỗi lượng tử và cửa chất lượng

```
gói server ─► import: model.pt → ONNX, so với model.pt trên 8 ảnh val (lệch tối đa 2,84e-05)
   ─► ptq: hiệu chuẩn percentile 64 lô × 16 ảnh qua đúng letterbox của chip, TQT 106 khối trên 256 ảnh
          int8 cho thân mạng, int16 cho đầu ra và lối ra của cổ, Swish int16 bằng bảng tra bước 32
   ─► eval: mAP50-95 float và int8 trên val ─► cửa ─► deploy: lock, ảnh thị giác, vision_0
```

- **mAP50-95 trên `val`**, 2 500 ảnh qua letterbox của chip: float 0,3635, int8 0,3506, tụt 0,0129. Cửa cho phép tụt tới
  0,02. Trên `test`: float 0,3645, int8 0,3496.
- **Đối chứng âm của cửa**: gói cố ý dời nhãn mọi khung sang lớp kế tiếp. Float chỉ còn 0,0145 lần mAP mà `model.pt` ghi
  lúc học, dưới mức 0,5, nên cửa chặn và lệnh `deploy` từ chối.
- **So với Espressif** (0,363 công bố): họ chấm bằng letterbox song tuyến trên 5 000 ảnh với bộ chấm của Ultralytics,
  chip thì làm letterbox láng giềng gần nhất. Float của repo trên đường chấm của chip cũng chỉ 0,3635, nên phần lớn khoảng
  cách nằm ở cách chấm, không ở lượng tử.
- Chạy trên RTX 3050 Laptop 4 GB: ptq 16–19 phút, mỗi lượt chấm 2 500 ảnh 11–14 phút.

### 5.4. Ảnh thị giác và nhánh `detect` trên chip

- **Phân vùng**: models_0 9 MiB, vision_0 3,25 MiB, storage 0,5 MiB. Hai bản sản phẩm b0 và b3 đều khởi động; NVS giữ
  nguyên ngưỡng và mẫu giọng.
- **Ảnh thị giác**: header 4 KB (magic, phiên bản, S, số lớp, ngưỡng điểm, số khung tối đa, sha256, tên lớp), rồi tới
  `.espdl`. Header ghi sau cùng, nên mất điện giữa chừng thì ảnh không được nhận. `make vision-flash` mất 54 s.
- **`ai_engine_detect_*`**: ánh xạ `vision_0`, kiểm sha256, dựng đồ thị với trọng số đọc thẳng từ flash. Letterbox bằng
  bộ tiền xử lý của esp-dl. Hậu xử lý `decode.c` không cấp phát bộ nhớ mỗi lần chạy, khớp bản Python từng giá trị trên bộ
  vàng có ca đối chứng âm, cả trên máy tính lẫn trên board.

### 5.5. Chip khớp mô phỏng

Bản dò chạy 8 ảnh COCO trên board, so CRC32 của 6 tensor ra với mô phỏng ESP-PPQ. Lần đầu 46/48 tensor lệch, dù khung ra
vẫn đúng. Gắn thêm 12 điểm đo vào giữa mạng thì thấy các khối 0–9 khớp, còn phần đầu ra int16 lệch ở mọi ảnh. Hai chỗ ESP-PPQ
tính khác chip:

1. **Tích chập int16.** Chip nạp bias vào thanh ghi tích luỹ 40 bit, cộng từng tích 16 × 16 bit đúng tuyệt đối, rồi làm
   tròn nửa lên một lần. ESP-PPQ cộng bằng float32, chỉ giữ 24 bit, trong khi tổng lên tới 2^32, nên gần nửa bước của lưới
   ra hai bên làm tròn lệch nhau một bậc. Bản vá mới `conv_s16_as_espdl` cộng bằng float64 rồi làm tròn như chip.
2. **Softmax của khối Attention.** Đây đúng là lỗi đã vá cho speaker (`softmax_as_espdl`).

Mô phỏng lại trên PC, đối chiếu CRC chip đã báo:

| Bản vá | Khớp chip |
|---|---|
| không | 89/144 |
| chỉ `conv_s16_as_espdl` | 133/144 |
| cả hai | **144/144** |

Lượng tử lại với hai bản vá. Trên board, cả 6 tensor và các khung khớp từng giá trị trên 8 ảnh; ảnh sai sha256 bị từ chối.

**Chi phí trên board**, trọng số đọc từ flash, chưa tối ưu:

| | Thời gian | RAM nội | PSRAM |
|---|---|---|---|
| nạp ảnh thị giác | 3 469 ms | 6 072 B | 2 707 852 B |
| một ảnh: letterbox, mạng, decode | 3 140 ms trung bình, 3 166 ms đỉnh | thêm 320 B | thêm 11 152 B |

- **RAM nội khi dựng mạng**: dựng bằng esp-dl trực tiếp tốn 109 KB, qua `ai_engine` chỉ 6 KB, vì các khoản cấp phát của
  esp-dl được đẩy sang PSRAM.
- **Nạp và gỡ nhiều lần**: lần nạp đầu giữ lại 5 748 B RAM nội; các lần sau trả lại đủ. Việc đổi model qua Wi-Fi sau này
  vì vậy không rò bộ nhớ.

### 5.6. Tối ưu tốc độ

Đo thời gian từng op trên board: 298 module, 3 098 ms. Tích chập chiếm 2 212 ms. Hai chỗ chậm lớn của esp-dl:
- **Trọng số lớn hơn cache dữ liệu 64 KB**: 8 tích chập, 767 ms.
  - Nặng nhất là tích chập int16 3 × 3 đầu nhánh khung ở P5: 292 ms, 7,42 chu kỳ một phép nhân-cộng.
  - Cùng tích chập ấy ở P3 và P4 có trọng số vừa cache, chỉ 0,25 và 0,38 chu kỳ.
  - Ở P5, mỗi điểm ảnh ra phải đọc lại 72 KB trọng số từ flash.
- **Số kênh không chia hết cho 16**: 3 tích chập, 258 ms.

Ba bản vá ở bước xuất, không đổi số nguyên nào, mỗi bước đều kiểm khớp từng giá trị trên board:

| Bản vá | Làm gì | Mỗi ảnh | PSRAM thêm |
|---|---|---|---|
| — | | 3 140 ms | — |
| `cache_sized_convolutions`, mở rộng cho int16 mọi cỡ kernel | cắt tích chập theo kênh ra, mỗi phần có trọng số vừa nửa cache | 2 845 ms | 49 560 B |
| `lean_quantizers` | gộp 7 lần lượng tử lại trùng nhau do việc cắt sinh ra | | — |
| `aligned_channels`, mở rộng qua Swish | đệm khối 8 kênh của `model.2` lên 16 kênh | **2 719 ms (−13,4%)** | 95 032 B |

Hai bản vá sau đo chung một lượt; bảng thời gian từng op tách được phần của từng bản: `lean_quantizers` −31 ms,
`aligned_channels` −105 ms. Run chính thức lượng tử lại với cả ba bản vá: 2 722 ms mỗi ảnh, khớp từng giá trị, int8 trên
`val` 0,3506. Phần còn lại, theo ước lượng 🔬:
- **Ghép Swish và bảng tra vào tích chập** (~370 ms đọc ghi PSRAM) và **bỏ chép Concat, Slice** (~240 ms) cần op riêng
  như đã làm cho speaker, đưa mỗi ảnh xuống khoảng 2,2 s mà không mất chất lượng.
- **Giảm S** sẽ mất mAP, cần đo bảng mAP theo ms để chủ repo chọn: S = 416 khoảng 1,4–1,5 s; S = 320 khoảng 0,9 s.
- **Thời gian thực** (≥ 10 khung/giây) không làm được trên ESP32-S3. Các tích chập của mạng có 1,72 tỉ phép nhân-cộng ở
  512, tính từ bảng đo. Cả khi mọi kernel chạy 0,15 chu kỳ một phép, mỗi ảnh vẫn mất khoảng 1 s. Cách dùng hợp lý là chụp
  định kỳ hoặc chạy khi camera thấy chuyển động.

## 6. Các vấn đề đã gặp và cách giải quyết

| # | Vấn đề | Biểu hiện đo được | Cách giải quyết | Kết quả |
|---|---|---|---|---|
| 1 | ESP-PPQ cộng tích chập int16 bằng float32 | 46/48 tensor ra của YOLO lệch chip | bản vá mô phỏng `conv_s16_as_espdl`, cộng float64 rồi làm tròn một lần như chip | cùng `softmax_as_espdl`: 144/144 CRC khớp; board khớp từng giá trị |
| 2 | App kiểm parity dừng trước ca đầu | thêm bộ vàng `detect_decode` làm dòng `plan` dài 171 B, vượt giới hạn 160 B | in mỗi khối một dòng `plan`; máy tính vốn gộp mọi dòng | hai bản dựng qua: 128 và 61 ca |
| 3 | Công cụ xếp hạng op bỏ sót tích chập chậm nhất | tính trọng số int16 một byte, nên 72 KB thành 36 KB | lấy độ rộng theo `quant_type` của tích chập | tích chập 292 ms được gắn nhãn đúng |
| 4 | Tích chập int16 đọc lại trọng số từ flash | 292 ms, 7,42 chu kỳ một phép nhân-cộng | cắt theo kênh ra, mỗi phần 8 kênh, 36 KB | mỗi ảnh −295 ms |
| 5 | Cắt tích chập sinh lượng tử lại trùng | RequantizeLinear 67,7 → 98,5 ms | `lean_quantizers` gộp lại | về 67,7 ms |
| 6 | Hai nhân làm sai kết quả | phần song song đọc lại PSRAM nó vừa ghi: lệch tới 21; mạng người nói ở nhân 1: 2/6 kết quả sai | luật mới cho phần song song; sản phẩm giữ một nhân | đúng từng bit |
| 7 | Khoản mượn RAM nội không có chỗ trên heap thật | khối trống lớn nhất 49 152 B, nhỏ hơn mức xin 79 680 B | mượn khối trống lớn nhất | b3: 3 424,0 → 1 870,2 ms |
| 8 | Làm tròn đầu vào SwiftF0 khác ESP-PPQ | `listen-unit` 104/105 cửa sổ khớp | chip làm tròn nửa lên như ESP-PPQ | 336/336 |
| 9 | Chuỗi nghe kẹt sau 7 phút chạy thật | hàng đợi cửa sổ đầy | mỗi câu một cửa sổ; không đếm vượt ngưỡng đánh thức trong LENH | lượt 14 phút chạy liền |
| 10 | "on" nói to, giọng cao bị chấm thấp | on2 bắt 6/41 lần "on" của chủ repo | bốc mẫu tiếng Anh theo cao độ, tăng mức to của từ (on3) | 22/41 ở 920‰, 28/41 ở 850‰ |

## 7. Các hướng đang xử lý

| Hướng | Trạng thái 30/10 | Điều kiện hay kỳ vọng |
|---|---|---|
| Từ đánh thức: thử thật và Gate 2 | on3 chạy trên board B ở 850‰ | bắt ≥ 95% ở 1 m, ≤ 1 lần nhận nhầm mỗi giờ trong ≥ 24 h; học lại "trợ lý" khi đủ bản thu board |
| Tải nhân 0 với SwiftF0 | chỉ có ước tính ~26% bước | đo trực tiếp trên board, cập nhật bảng ngân sách |
| Độ trễ quyết định | mạng lệnh đọc 3,1 MB trọng số từ flash mỗi lượt; front end SwiftF0 2,4 ms mỗi bước | mạng nhanh hơn là đòn bẩy còn lại |
| b3 trong sản phẩm | chưa đo thời gian thật khi đăng ký, chưa đo RAM nội khi Wi-Fi bật | giữ mẫu giọng của cả hai model cần đổi khuôn NVS |
| Việc treo từ tuần 14 | lượt phòng không ai nói, lượt hai người thật, so ba đường không gian: tuần này chưa làm | như kế hoạch tuần 14 |
| YOLO26n: tối ưu tiếp | 2 722 ms mỗi ảnh với ba bản vá ở bước xuất, khớp từng giá trị | op riêng cho tích chập + Swish, có ADR |
| YOLO26n: hợp đồng và luồng ảnh | chưa làm | topic gửi ảnh, nhận khung, đẩy model qua Wi-Fi; task `nhin_task` ở nhân 0, ưu tiên thấp nhất, không làm chậm lệnh |

## 8. Kế hoạch tuần 16

| Việc | Kết quả mong đợi | Ưu tiên |
|---|---|---|
| YOLO26n: chốt run tối ưu, bảng mAP theo ms cho S = 320, 416, 512 | chủ repo chọn S trên bảng đo | cao |
| YOLO26n: op riêng ghép tích chập với Swish | ADR, khớp từng giá trị, số trước và sau | cao |
| YOLO26n: hợp đồng MQTT và `nhin_task` | ảnh gửi từ máy tính ra cùng khung như trên PC; độ trễ lệnh không tăng khi đang chạy ảnh | trung bình |
| Từ đánh thức: lượt thử thật dài | số bắt và nhận nhầm mỗi giờ trên board | trung bình |
| Đo tải nhân 0 với SwiftF0, kiểm bộ lệnh lấy lại từ tin retained | bảng ngân sách đúng với sản phẩm hiện tại | trung bình |

## 9. Phiên bản công cụ

| Thành phần | Phiên bản |
|---|---|
| ESP-IDF | 6.0.2 |
| esp-dl | 3.3.14 |
| dl_fft | 0.8.0 |
| ESP-PPQ | 1.3.11 |
| Ultralytics (chỉ để xuất ONNX) | 8.4.193 |
| Python, NumPy, PyTorch | 3.12.3, 2.5.3, 2.14.0 |
| Board | board B: ESP32-S3, 8 MB PSRAM, 16 MB flash, hai micro INMP441 cách 4,5 cm |

## 10. Tài liệu tham khảo

1. L. Nieradzik, SwiftF0: Fast and Accurate Monophonic Pitch Detection, 2025 (bộ dò cao độ).
2. I. Yakovlev và cộng sự, Reshape Dimensions Network for Speaker Recognition, Interspeech 2024 (ReDimNet; b0, b3 là các
   cỡ của ReDimNet2).
3. P. Matějka và cộng sự, Analysis of Score Normalization in Multilingual Speaker Recognition, Interspeech 2017 (AS-norm).
4. A. Graves và cộng sự, Connectionist Temporal Classification, ICML 2006 (mạng lệnh CTC).
5. P. Warden, Speech Commands: A Dataset for Limited-Vocabulary Speech Recognition, 2018 (từ đánh thức tạm "on").
6. I. Cohen, B. Berdugo, Speech enhancement for non-stationary noise environments, Signal Processing, 2001; I. Cohen, Noise
   spectrum estimation in adverse environments: improved minima controlled recursive averaging, IEEE Trans. Speech and
   Audio Processing, 2003 (`ns`: OM-LSA, IMCRA).
7. C. Knapp, G. Carter, The generalized correlation method for estimation of time delay, IEEE Trans. ASSP, 1976
   (GCC-PHAT của `doa`).
8. Ultralytics, YOLO26, bản 8.4.193.
9. S. R. Jain, A. Gural, M. Wu, C. H. Dick, Trained Quantization Thresholds for Accurate and Efficient Fixed-Point
   Inference of Deep Neural Networks, MLSys 2020 (TQT).
10. T.-Y. Lin và cộng sự, Microsoft COCO: Common Objects in Context, ECCV 2014.
11. Espressif, ESP-IDF 6.0.2, ESP-DL 3.3.14, ESP-PPQ 1.3.11.

## 11. Nhận xét của Mentor

- Đánh giá chung:
- Điểm mạnh:
- Điểm cần cải thiện:
- Hành động cần thực hiện:
