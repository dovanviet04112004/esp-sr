# Báo cáo tuần 11 — thực tập sinh kỹ thuật

Tuần 11 (28/09 – 02/10/2026)

## 1. Thông tin chung

| Mục | Nội dung |
|---|---|
| Họ và tên | Đỗ Văn Việt |
| Phòng ban | Thiết kế điện tử |
| Người hướng dẫn (Mentor) | Anh Chung |
| Tuần báo cáo | Tuần 11 (28/09 – 02/10/2026) |
| Phạm vi tuần | Dự án esp-sr: tự dựng lại chuỗi nhận lệnh tiếng Việt kiểu ESP-SR trên ESP32-S3, từ xử lý tín hiệu tới mô hình. Mọi số đo trên board B: ESP32-S3, hai micro INMP441 cách 4,5 cm |

## 2. Tóm tắt tuần

ESP-SR của Espressif chỉ phát hành thư viện đã biên dịch và không có tiếng Việt, nên dự án tự dựng lại toàn bộ: xử lý
tín hiệu, luật đọc tiếng Việt và mô hình; khối nào cũng đi đủ bốn bước, từ bản Python, bộ vàng có đối chứng âm, bản C
khớp Python, tới đo trên board. Chuỗi xử lý tín hiệu chạy trên board 2,6 ms mỗi khung 16 ms, và chính bản Python của nó
tạo đặc trưng cho mọi lượt học. Mô hình nhãn cứng (wake, kws) chạy tốt khi đủ dữ liệu người thật, đã chứng minh bằng
bộ tiếng Anh, nhưng tiếng Việt chưa có đủ dữ liệu ấy. Mô hình âm vị ctc đã học xong, chạy trên chip 5,7 ms mỗi 32 ms
tiếng; sau khi sửa một lỗi mô phỏng RMSNorm của ESP-PPQ, quyết định của chip trùng Python ở cả 198/198 cửa sổ. Tuần
sau học lại ctc với nhiều giờ và nhiều bước hơn, rồi học rnnt và hai mạng giảm nhiễu.

## 3. ESP-SR đóng mã, nên phải dựng lại từ đầu

ESP-SR có đủ chuỗi tiền xử lý, từ đánh thức và nhận lệnh, nhưng không dùng thẳng được:

| Vấn đề | Chi tiết |
|---|---|
| Không có mã nguồn | `lib/esp32s3/` gồm 12 tệp `.a`, 14,7 MB; `src/` chỉ có 4 tệp `.c`, 68 KB, để đọc cấu hình. Mọi thuật toán nằm trong khối đã biên dịch |
| Không kiểm được | số ra từ một tệp `.a` không dựng lại được, không làm bộ vàng được |
| Không có tiếng Việt | từ đánh thức, bộ lệnh, tổng hợp tiếng nói đều không có bản tiếng Việt |

Vì vậy dự án tự viết toàn bộ:

- xử lý tín hiệu: `dsp_spec` (STFT, log-mel, cao độ) và `dsp_afe` (lọc, cân hai micro, dò hướng, chùm, giảm nhiễu, dò
  tiếng nói, cân mức);
- luật đọc tiếng Việt `lang_vi`: chuẩn hoá chữ và số, đổi âm tiết ra đơn vị âm có thanh, sinh biến thể giọng Bắc,
  Trung, Nam;
- mô hình và phần quyết định sau mô hình trong `ai_engine`.

ESP-SR chỉ được link trong đúng một app so sánh, để đo AFE của họ cạnh bản tự viết trên cùng bản thu.

Mỗi khối đi đủ bốn bước:

```
1. Bản Python float32   viết theo thuật toán đã chốt, chạy đúng trên dữ liệu có nhãn
2. Bộ vàng              cặp vào–ra sinh từ Python, kèm đối chứng âm: cố ý sai một chỗ thì phép kiểm phải đỏ
3. Bản C                khớp Python trên máy tính và trên board, từng bit hoặc trong ngưỡng ghi sẵn
4. Đo trên board        µs trung bình và đỉnh, RAM, cộng vào bảng ngân sách
```

Hiện có 18 bộ vàng, bộ nào cũng có đối chứng âm đã thấy đỏ:

| Nhóm | Bộ vàng |
|---|---|
| `dsp_spec` | stft, mel, pitch |
| `dsp_afe` | hpf, balance, doa, gsc, ns_omlsa, vad, agc, cả chuỗi (hai bộ) |
| `lang_vi` | normalize, g2p (16 742 âm tiết, mỗi vùng một lượt), lexicon |
| phần quyết định sau mạng | command_kws, command_ctc, command_rnnt |

Ví dụ đối chứng âm: trễ một mẫu ở hpf, lệch một dải ở mel, đọc "d" giọng Nam thành "z" ở g2p, bỏ hệ số hiệu chuẩn micro
ở cả chuỗi. Phần mạng nơ-ron không kiểm bằng bộ vàng mà bằng mô phỏng int8: đầu ra của chip phải trùng mô phỏng của
ESP-PPQ (mục 5.1).

## 4. Chuỗi xử lý tín hiệu: dsp_spec và dsp_afe

Khối này làm xong từ dự án trước (tinyai-signal) và có trong repo ngay từ đầu, nên ở đây chỉ nói lại nó gồm gì và dùng
vào việc gì.

```
2 micro INMP441, I2S 16 kHz, int16                                  thời gian mỗi khung 16 ms
   │
   ▼
hpf       lọc thông cao 80 Hz: bỏ thành phần một chiều và tiếng ù   41 µs
   ▼
stft      cửa sổ 512 mẫu (32 ms), bước 256 (16 ms), 257 vạch         286 µs, hai kênh
   ▼
balance   bù lệch độ nhạy và pha giữa hai micro, hệ số riêng board   18 µs
   ▼
doa       GCC-PHAT, dò hướng người nói theo lưới 2°                  22 µs, 641 µs ở bước dò
   ▼
gsc       chùm lái theo hướng, triệt tiếng từ hướng khác             141 µs
   ▼
khe ns    OM-LSA, hoặc một mạng giảm nhiễu (mục 5.4)                 1,69 ms
   ▼
istft → vad (GMM sáu dải) → agc (đưa mức nói về −26 dBFS)            165 + 138 + 258 µs
   ▼
đặc trưng, nhân 0: log-mel 40 dải + 3 chiều cao độ (Kaldi pitch)     63 µs + 1,98 ms
   ▼
wake, command
```

Cả chuỗi `dsp_afe` 2,62 ms trung bình, 3,30 ms đỉnh mỗi khung 16 ms, tức 16% nhân 1; đặc trưng 13% nhân 0. Khử vọng loa
(aec) và tách mù (bss) chưa xong.

Vai trò trong học và tạo đặc trưng:

- Không thu được tiếng hàng trăm người qua board, nên dữ liệu học lấy từ kho công khai và TTS. Chúng đi qua đường mô
  phỏng board: đặt người nói và nguồn nhiễu vào phòng mô phỏng trước dàn hai micro, áp độ nhạy và độ lệch micro theo
  hiệu chuẩn của board, cộng nền ồn thật thu từ board, lượng tử như driver, rồi chạy đúng bản Python của chuỗi trên.
- Bản Python khớp bản C từng bit (cả chuỗi lệch tối đa 1 LSB), nên đặc trưng lúc học trùng thứ board tính ra lúc chạy.
  Bản thu thật qua board để chấm, không để học.
- wake đọc 40 log-mel; kws và ctc đọc 40 log-mel cộng 3 chiều cao độ, vì thanh điệu tiếng Việt nằm ở cao độ. Thống kê
  chuẩn hoá lúc học nằm trong ảnh model, nên model và thống kê không lệch nhau.

## 5. Mô hình

### 5.1. Bộ chạy chung: esp-dl và ESP-PPQ

Mọi mạng chạy bằng esp-dl, runtime của Espressif có kernel assembly cho S3. ESP-PPQ là công cụ của Espressif đổi mạng
float sang int8 và mô phỏng int8 trên máy tính.

```
mạng torch (float)
   ▼
ONNX
   ▼
ESP-PPQ: lượng tử int8, mô phỏng int8 trên máy tính ──────────┐
   ▼                                                           │
.espdl → ảnh model trong flash → chép vào PSRAM lúc nạp        │ so từng phần tử:
   ▼                                                           │ chip phải trùng mô phỏng
esp-dl chạy trên chip ─────────────────────────────────────────┘
```

Lợi:

- Nhanh. Tuần 10 đo cùng ba mạng trên board điểm danh: esp-dl 234,2 ms, TFLite Micro cộng esp-nn 1 270,5 ms, nhanh 5,42
  lần. Luật lượng tử luỹ thừa 2, đối xứng, cho phép nhân 8 bit thẳng và dịch bit thay phép chia.
- Có sẵn bộ đệm chạy dòng, nên TCN và encoder ctc đẩy từng khối mới, không tính lại cả cửa sổ.
- Trọng số và vùng làm việc để hết ở PSRAM: mọi mạng của dự án lấy 0 B RAM nội.

Hại:

- Mỗi tensor chỉ có một số mũ luỹ thừa 2, không có hệ số riêng từng kênh, nên mất độ chính xác. ctc float lỗi đơn vị
  34,45%, int8 thường 36,3–39,9% tuỳ cách hiệu chuẩn; phải đi thang bốn bậc (cân bằng lớp, chọn cách hiệu chuẩn, int16
  cho lớp nhạy, QAT) mới kéo về 35,75%.
- ESP-PPQ 1.3.11 có lỗi làm mô phỏng lệch chip. Lỗi chỉ lộ ra khi chạy trên board, mỗi lỗi một bản vá kèm test:

| Lỗi của ESP-PPQ | Triệu chứng trên board B |
|---|---|
| đầu vào đồ thị bị hai phép đọc ở hai thang mà không đổi thang | một lớp encoder ctc lệch 8 bước so với mô phỏng |
| bộ đệm chạy dòng lấy nhầm trục kênh khi có `Slice` đứng trước tích chập | esp-dl dừng ngay lúc dựng mạng |
| `Slice` đọc thẳng đầu vào được số mũ riêng, còn esp-dl chép nguyên số nguyên | 3 chiều cao độ trên chip chỉ còn một nửa |
| RMSNorm làm tròn khác nhân esp-dl | 2/198 cửa sổ quyết định lệch Python |

Ngoài bốn bản vá còn ba chỗ phải đi vòng: chuẩn hoá phải viết đúng dạng ESP-PPQ gộp được thành một phép RMSNormalization,
để dạng chia int8 thì sai số một lớp lớn gấp 1,5 lần trung bình, 2,3 lần lớn nhất; trạng thái GRU phải chép ra vùng tạm
vì esp-dl đặt trạng thái ra đè lên trạng thái vào; ONNX của ESP-PPQ ghim lô 1, nên QAT học trên đồ thị lô lớn rồi chép
sang đồ thị lô 1.

So với TFLite Micro cộng esp-nn:

| | esp-dl + ESP-PPQ | TFLite Micro + esp-nn |
|---|---|---|
| Tốc độ | nhanh hơn 5,42 lần trên cùng ba mạng (tuần 10) | chậm hơn; esp-nn chỉ là thư viện kernel dưới TFLite Micro, nhanh 5,5 lần so với kernel tham chiếu (DS-CNN 3,7 triệu MAC: 380 → 68,5 ms) |
| Lượng tử | một số mũ luỹ thừa 2 cho cả tensor | hệ số thực riêng từng kênh, mất ít độ chính xác hơn |
| Khớp mô phỏng | phải vá 4 lỗi mới trùng từng bit | ít phải vá hơn |
| Bộ nhớ | thêm 858 KB flash, 13,9 KB RAM nội tĩnh (tuần 10) | nhẹ hơn |

Dự án chọn esp-dl vì thời gian là ràng buộc chính: mạng giảm nhiễu đã tốn 4,8–5,1 ms mỗi khung 16 ms, chậm 5 lần như
TFLite Micro thì ước vượt cả khung. Cái giá là mọi mạng phải qua phép so chip với mô phỏng trước khi dùng.

### 5.2. Mô hình nhãn cứng: wake và kws

Nhãn cứng nghĩa là mỗi từ là một lớp riêng của mạng: muốn nhận từ nào thì phải có nhiều bản ghi người thật nói đúng từ
ấy để học.

wake, TCN chạy dòng:

```
log-mel 40 dải mỗi 16 ms
   │  chuẩn hoá bằng thống kê lúc học
   ▼
TCN nhân quả int8, đẩy từng khung
   6 tầng tích chập giãn nở, kernel 3, giãn 1, 2, 4, 8, 16, 32
   64 kênh, trường nhìn 127 khung ≈ 2 s
   ▼
xác suất có từ đánh thức, mỗi khung
   │  trung bình trượt 5 khung
   ▼
so ngưỡng (lưu ở NVS) → thức
```

Thông số: 1,95 ms mỗi khung 16 ms (12% một nhân), dựng mạng 24 ms, PSRAM 31 KB, đầu ra chip trùng mô phỏng từng bit.

kws, DS-CNN một lần mỗi câu:

```
vad báo hết câu
   ▼
cửa sổ 94 khung (1,5 s) kết thúc ở chỗ vad tắt
43 chiều mỗi khung: 40 log-mel + 3 cao độ
   │  chuẩn hoá
   ▼
DS-CNN cỡ S, int8
   tích chập đầu 10×4, bước 2, 64 kênh
   4 khối tách chiều sâu: 3×3 theo kênh + 1×1, 64 kênh
   gộp trung bình → lớp ra
   ▼
các lệnh đã học + other + silence
   ▼
lớp thắng phải là lệnh, xác suất qua ngưỡng, cách lớp nhì đủ xa → lệnh; không thì từ chối
```

Thông số: 22 triệu phép nhân cộng mỗi câu, 41,9 ms trên nhân 0 (ngân sách 100 ms), `.espdl` 40,6 KB, PSRAM 148 KB,
trùng mô phỏng từng bit. Cỡ M 277 ms và L 2,03 s quá ngân sách.

Minh chứng bằng tiếng Anh. Giữ nguyên đường xử lý, mô hình và cách chấm, chỉ đổi dữ liệu sang Speech Commands v0.02
(2 618 người nói), rồi thử trên board B. Thử nhiều, không chỉ vài từ: 10 từ tiếng Anh, 10 lệnh tiếng Việt ở 1 m và 3 m,
24 câu gần âm, nói tự do, nhiễu; báo nhầm của wake đo trên 53 phiên không có từ.

| Thử | Dữ liệu học mỗi từ | Board: nhận đúng | Board: từ chối đúng / báo nhầm |
|---|---|---|---|
| kws, 4 lệnh tiếng Việt từ một kho github | 200 mẩu, vài người nói | 10/45 (bật quạt 10/11, ba lệnh kia 0) | 88/93 |
| kws, 10 từ tiếng Anh | 200 mẩu, ~181 người | 25/60 | 109/138 |
| kws, yes và no tiếng Anh | ~3 200 mẩu, ~1 300 người | 12/13 (yes 5/6, no 7/7) | 173/185 |
| wake "yes" | 3 228 mẩu người thật | 4/5 câu trọn vẹn qua ngưỡng | 2,07 lần/giờ |
| wake "trợ lý" | TTS + 24 mẩu người thật | 1/104 câu qua ngưỡng | 4,5 lần/giờ |

Cùng từ "no", cùng phiên board: 200 mẩu nhận 2/7, 3 130 mẩu nhận 7/7. Đường xử lý và mô hình không hỏng; chỗ quyết định
là dữ liệu người thật.

Điểm mạnh:

- Có lớp other và silence nên từ chối tốt thứ không phải lệnh: tiếng ồn, nói chuyện thường, câu gần âm (từ chối đúng
  173/185, câu gần âm 22/24).
- Nhẹ: wake 1,95 ms mỗi khung, kws 41,9 ms một lần mỗi câu.
- Mỗi câu một quyết định, dễ đặt ngưỡng.

Điểm yếu:

- Cần hàng nghìn mẩu người thật, hàng trăm người cho mỗi từ; TTS không thay được (wake "trợ lý" học từ TTS chỉ bắt
  1/104).
- Thêm một lệnh là thu dữ liệu, học lại, nạp lại model; bộ lệnh càng lớn càng khó mở rộng.
- Câu gần âm phải đưa vào học làm âm bản khó, không thì bắt nhầm (bản wake đầu chấm câu đọc số "sáu mươi bốn sáu mươi
  lăm" tới 0,994).

### 5.3. Mô hình âm vị: ctc và rnnt

Hướng này không học từng lệnh. Mạng nghe ra chuỗi đơn vị âm (44 đơn vị có thanh của `lang_vi`); lệnh viết bằng chữ,
`lang_vi` đổi chữ ra chuỗi đơn vị ngay trên máy lúc nạp bộ lệnh, rồi so chuỗi nghe được với từng lệnh. Thêm lệnh là
thêm một dòng chữ, không thu, không học lại. MultiNet của ESP-SR cũng đi hướng này.

Encoder dùng chung cho cả hai đường, dựng theo MultiNet7:

```
40 log-mel + 3 cao độ mỗi 16 ms
   │  chuẩn hoá; đẩy từng khối 16 khung (256 ms)
   ▼
3 tích chập 2D trên thời gian × dải mel: 8, 32, 48 kênh
cao độ đi vòng, nhập lại ở phép chiếu
   │  còn 31,25 khung/s, mỗi khung 32 ms
   ▼
6 lớp kiểu MultiNet7, bề rộng 128: feedforward, bộ trộn, 2 khối LiGLU
(tích chập theo chiều sâu, kernel 17, 9, 5), RMSNorm, đường tắt
   ▼
128 chiều mỗi khung; cả mạng 1,98 triệu tham số
```

ctc:

```
encoder
   ▼
đầu CTC: 45 lớp mỗi khung (44 đơn vị + blank) → log-softmax
   ▼
chấm từng lệnh: cộng mọi cách đặt chuỗi đơn vị của lệnh lên các khung, mọi biến thể giọng
chấm đường tự do: chuỗi tốt nhất không ràng buộc
   ▼
quyết
   δ₁: lệnh tốt nhất kém đường tự do không quá 300‰ mỗi khung
   δ₂: hơn lệnh nhì ít nhất 50‰ mỗi khung
   luật phần: không đoạn âm tiết nào ngắn hơn lệnh được điểm bằng hay hơn cả lệnh
   ▼
lệnh, hoặc từ chối
```

Luật phần có từ buổi demo 02/10: nói mỗi chữ "chụp" mà máy nhận cả "chụp ảnh", vì ctc chấm từng khung độc lập nên ép
được đơn vị thiếu vào vài khung. Có luật phần thì hai câu nói một chữ ấy bị từ chối.

| ctc | Số |
|---|---|
| Dữ liệu học | 103,4 giờ (VIVOS, Common Voice, FPT đủ; VLSP 15 giờ, Bud500 25 giờ), qua đường mô phỏng board |
| Học | 40 000 bước, lô 32, 46 phút trên RTX 3050 4 GB |
| Lỗi đơn vị, float | 29,5% trên val, 34,45% trên 2 000 câu thử |
| Lỗi đơn vị, int8 | 36,3–39,9% tuỳ cách hiệu chuẩn; int16 vài lớp 36,1–36,4%; QAT 35,75%, chọn QAT, int8 thuần |
| Trên phiên thu qua board, float | lệnh điểm cao nhất đúng 85/112, nhận đúng 74/112, nhận nhầm 2/86 câu không phải lệnh |
| Trên phiên thu qua board, QAT | lệnh điểm cao nhất đúng 86/112, nhận đúng 68/112, nhận nhầm 3/86; chip đếm ra đúng như vậy |
| Chỗ yếu nhất | "tắt" hay nghe thành "bật": 21/22 câu "tắt" ra lệnh "bật" |
| Encoder trên chip | 46,0 ms mỗi khối 256 ms tiếng, tức 5,7 ms mỗi 32 ms, khoảng 18% một nhân, chỉ trong cửa sổ lệnh |
| Chấm trên chip | 56,3 ms trung bình, 64,9 ms đỉnh mỗi câu; câu 3 s: 16,3 ms với 10 lệnh, 79,7 ms với 64 lệnh |
| Bộ nhớ | PSRAM 274 KB, RAM nội 0, dựng mạng 308 ms |
| Chip so với Python | 198/198 cửa sổ Cửa 3 trùng từng trường; trước bản vá RMSNorm là 196/198 |

rnnt, như MultiNet7 giải trên chip:

```
encoder → chiếu khung 384
2 đơn vị phát gần nhất → mạng dự đoán không trạng thái:
   nhúng 384 → tích chập theo chiều sâu, ngữ cảnh 2 → ReLU → chiếu 384
   ▼
bộ nối: tanh(khung + tiền tố) → 45 lớp
   ▼
tìm chùm 4 giả thuyết, mỗi khung tối đa một đơn vị,
chỉ đi trong FST tối giản của mọi biến thể lệnh và các phần của lệnh
   ▼
quyết như ctc: δ₁, δ₂, luật phần
```

| | ctc | rnnt |
|---|---|---|
| Sau encoder | một lớp ra 45 | mạng dự đoán và bộ nối 384 chiều, như trọng số MultiNet7 |
| Biết đơn vị đã nói chưa | không, mỗi khung độc lập | có, mạng dự đoán đọc 2 đơn vị cuối |
| Chấm lệnh | cộng đủ mọi cách đặt, chính xác | tìm chùm gần đúng theo FST |
| Học | CTC, 46 phút | RNN-T cộng 0,2 lần CTC; lưới khung × đơn vị tốn bộ nhớ GPU: chạy thử 177 ms mỗi bước, đỉnh 1 974 MiB của 4 096 MiB |
| Trạng thái | học xong, chạy trên chip | mã học, tìm chùm Python và C xong, C trùng Python từng bit trên máy tính; lượt học đầu dừng vì nhãn lệch một lớp, đã sửa |
| Thời gian trên chip | như trên | encoder như ctc; bộ nối mỗi khung nhân số giả thuyết: chưa đo |

### 5.4. Giảm nhiễu: OM-LSA và hai mạng

```
phổ công suất 257 vạch, lối ra của gsc
   ▼
khe ns, chọn một trong ba
   OM-LSA + IMCRA   thuật toán thuần: nhiễu ước bằng cực tiểu trượt ~1 s, gain mỗi vạch, sàn −12 dB
   RNNoise-16k      đặc trưng 18–22 dải → dày 24 → GRU 24, 48, 96 → gain từng dải, nội suy ra 257 vạch
   NSNet-16k L      log công suất 256 vạch → dày 144 → GRU 144 → GRU 144 → dày → sigmoid mỗi vạch
   ▼
gain 0..1 mỗi vạch × phổ → istft
```

| | OM-LSA | RNNoise-16k | NSNet-16k L |
|---|---|---|---|
| Loại | thuật toán thuần | mạng | mạng |
| Tham số | — | 82 603 | 324 688 |
| Trên board, mỗi khung 16 ms | 1,69 ms | 4,8 ms | 5,1 ms |
| Trạng thái | xong, C trùng Python từng bit | chạy trên chip trùng mô phỏng với trọng số ngẫu nhiên; đang học | như RNNoise-16k; riêng cỡ L lệch 1 bước từ khung 112, chưa rõ nguyên nhân |

Mốc giá: mạng nsnet2 của ESP-SR trên cùng board tốn 4,18 ms mỗi khung.

Vì sao cần OM-LSA:

- Là sàn: chạy ngay không cần học, rẻ (1,69 ms), khớp C–Python từng bit. Mạng chỉ được giữ khi hơn sàn bằng thước của bộ
  nhận dạng; không hơn thì giữ sàn.
- Là đường lùi khi mạng chưa học xong hay gặp lỗi.
- Nhiễu dừng xử lý tốt: phòng yên, ồn trắng, ồn hồng, vòi nước giảm 10–12 dB trong quãng nghỉ, tiếng nói mất tối đa 1,2 dB.

Vì sao yếu:

- Nó học nhiễu từ cực tiểu trượt khoảng 1 s, nên thứ gì đổi nhanh hơn bị coi là tiếng nói: tiếng rửa bát chỉ giảm
  khoảng 3 dB, tiếng mèo dưới 2 dB, nhạc thu qua board chỉ 4 dB.
- Nhiễu tăng đột ngột thì sau khoảng 2,4 s mới bám; trong lúc ấy nhiễu mới đi qua gần nguyên vẹn.
- Sàn −12 dB giới hạn độ dìm. Dìm sâu hơn thì tiếng méo, hại bộ nhận dạng.

Hai mạng nhắm đúng chỗ yếu ấy. Lượt học đầu (01/10) dừng sau epoch 2: hàm mất mát phạt nhiễu sót quá nhẹ, mạng L chỉ dìm
14 dB khi bỏ sàn và 10 dB với sàn −12 dB, ngang OM-LSA. Thử hàm trên phổ nén thì sâu thêm 2,7 dB nhưng tiếng mất 4,2 dB.
Lượt tới học lại từ đầu với hàm khớp phổ nén kiểu NSNet2, đích là tỉ lệ biên độ tiếng trên hỗn hợp; với gain lý tưởng,
đích ấy dìm 37,6 dB trong quãng nghỉ mà tiếng chỉ mất 0,15 dB.

## 6. Kế hoạch tuần 12

| Việc | Kết quả mong đợi | Ưu tiên |
|---|---|---|
| Học lại ctc trên nhiều giờ và nhiều bước hơn | từ 103,4 lên khoảng 300 giờ, lấy thêm VLSP và Bud500 trong 626 giờ đã có; từ 40 000 lên khoảng 120 000 bước, ước 2,3 giờ GPU, vì lỗi val còn giảm tới bước cuối (31,1% ở bước 28 000, 29,5% ở 40 000); nhận đúng hơn 74/112, bớt lỗi "tắt" thành "bật" | cao |
| Học rnnt trên encoder ấy | Cửa 3 sau int8 của rnnt cạnh ctc; chọn đường bằng số, ghi thành quyết định | cao |
| Học hai mạng giảm nhiễu với hàm mới, ước 6 giờ GPU | dìm sâu hơn OM-LSA ở nhiễu không dừng (nhạc, rửa bát) mà không làm Cửa 3 kém đi | trung bình |
| Chạy phần tìm chùm của rnnt trên board | C trùng Python trên board B như trên máy tính | trung bình |

## 7. Phiên bản công cụ

| Thành phần | Phiên bản |
|---|---|
| ESP-IDF | 6.0.2 |
| esp-dl | 3.3.11 |
| ESP-PPQ | 1.3.11, cộng 4 bản vá của dự án |
| PyTorch, torchaudio | 2.14.0, 2.11.0 |
| Python | 3.12.3 |
| GPU học | RTX 3050 Laptop, 4 GB |
| Board | board B: ESP32-S3, hai micro INMP441 cách 4,5 cm |

## 8. Tài liệu tham khảo

1. Espressif, ESP-SR 2.5.5, ESP-DL 3.3.11, ESP-PPQ 1.3.11.
2. I. Cohen, B. Berdugo, Speech enhancement for non-stationary noise environments, Signal Processing, 2001; I. Cohen,
   Noise spectrum estimation in adverse environments: improved minima controlled recursive averaging, IEEE TSAP, 2003.
3. J.-M. Valin, A hybrid DSP/deep learning approach to real-time full-band speech enhancement (RNNoise), MMSP 2018.
4. S. Braun, I. Tashev, Data augmentation and loss normalization for deep noise suppression (NSNet2), SPECOM 2020.
5. Y. Zhang và cộng sự, Hello Edge: keyword spotting on microcontrollers (DS-CNN), 2017.
6. P. Warden, Speech Commands: a dataset for limited-vocabulary speech recognition, 2018.
7. A. Graves và cộng sự, Connectionist temporal classification, ICML 2006; A. Graves, Sequence transduction with
   recurrent neural networks, 2012.
8. M. Ghodsi và cộng sự, RNN-Transducer with stateless prediction network, ICASSP 2020.
9. P. Ghahremani và cộng sự, A pitch extraction algorithm tuned for automatic speech recognition, ICASSP 2014.

## 9. Nhận xét của Mentor

- Đánh giá chung:
- Điểm mạnh:
- Điểm cần cải thiện:
- Hành động cần thực hiện:
