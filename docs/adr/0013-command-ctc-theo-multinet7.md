# ADR-0013 — Mạng của `command` `ctc` dựng theo MultiNet7: encoder nhiều tầng tốc độ khung với tích chập có cổng, học CTC cộng RNN-T

- **Trạng thái**: Chấp nhận; thay phần kiến trúc của ADR-0010
- **Ngày**: 2026-09-30
- **Liên quan**: KẾ HOẠCH §3.3, §3.12, §4.1; TASKS E11-T12; ADR-0010, ADR-0012

---

## Bối cảnh

ADR-0010 chọn CRNN (tích chập rồi GRU một chiều) vì tài liệu ESP-SR tả MultiNet là mô hình nhẹ dựa trên CRNN và CTC
(nguồn 6 của ADR-0010). Tài liệu hiện hành của Espressif không tả kiến trúc, chỉ ghi chi phí và độ chính xác [2]. Chính
các file model của ESP-SR 2.5.5 thì có: mỗi file dữ liệu mang cấu hình JSON của mạng, và bảng chỉ mục cho tên, cỡ, số mũ
fixed-point của từng tensor [1]. MultiNet6 và MultiNet7, hai mạng nhận lệnh mới nhất của Espressif cho ESP32-S3, không có
GRU hay LSTM nào.

## MultiNet7 đọc từ file

`mn7_index` là dãy bản ghi 24 byte: tên 16 byte, hai số 32 bit, số sau là cỡ khối. `mn7_data` là các khối nối nhau theo
đúng thứ tự ấy, tổng cỡ khớp từng byte (2 739 172); mỗi khối mở đầu bằng 20 byte ghi hai chiều của tensor và số mũ.
Thông số đặc trưng đọc từ mã máy của `get_mfcc_opts_kaldi` trong `libc_speech_features.a`, tên từng trường theo
`print_mfcc_opts` của chính thư viện.

| Phần | Nội dung | Tham số |
|---|---|---|
| Đặc trưng | fbank kiểu Kaldi: cửa sổ povey 25 ms, bước 10 ms, FFT 512, 80 mel từ 20 tới 7600 Hz, nhấn tần 0,97, 16 kHz; cấu hình mạng ghi `kaldifbank`, 80 chiều | |
| Giảm khung | ba tích chập 2D 3×3 với 8, 32, 48 kênh, lớp đầu nhận hai mặt phẳng; trải 48 × 9 = 432 rồi chiếu xuống 128; thời gian giảm ×2 (`subsampling_rate` 2) | 106 nghìn |
| Encoder | bề rộng 128; 6 lớp chia 4 tầng (1, 2, 2, 1 lớp), các tầng sau chạy chậm hơn tầng đầu ×2, ×4, ×2, có nâng khung trở lại và hệ số ghép giữa các tầng; mỗi lớp ba khối feedforward 128 → 256 → 128, hai khối LiGLU (chiếu 128 → 256 thành nhánh cổng và nhánh lọc, tích chập theo chiều sâu nhân 17, 9, 5, 9 theo tầng, chiếu ra 128), một khối `pool` là ma trận 128 × 128 đứng ở chỗ self-attention, một hệ số chuẩn hoá và một hệ số tắt; hạ khung ×2 ở đầu ra | 1,90 triệu |
| Đầu CTC | 128 → 496 | 64 nghìn |
| Mạng dự đoán và bộ nối RNN-T | nhúng 496 × 384, tích chập ngữ cảnh 2 theo chiều sâu, chiếu phía mã hoá và phía dự đoán về 384, ra 496 | 580 nghìn |
| Đơn vị | 496 mảnh SentencePiece dựng trên bảng âm vị tiếng Anh, có `<blk>` (`phoneme_label` 1) | |
| Số | trọng số int8; bias, tích chập 2D và nhân theo chiều sâu int16; một số mũ cố định mỗi tensor | tổng 2,65 triệu |

Cấu hình ghi `rnnt_ctc_2.0`: mạng học với cả RNN-T và CTC. Trên chip, `libmultinet.a` giải bằng tìm chùm CTC bị ràng buộc
bởi FST hay cây tiền tố của bộ lệnh (`ctc_beam_search_with_fst`). Espressif công bố trên S3: 11 ms mỗi khung 32 ms,
2 920 KB PSRAM, 18 KB RAM trong; lệnh tiếng Anh ở 3 m đúng 97,2% khi yên, 92,3% với nhiễu dừng, 90,6% với tiếng người
[2].

Bản tiếng Trung `mn7_cn` cùng cấu hình `rnnt_ctc_2.0`, cùng fbank 80 chiều, **không có chiều cao độ nào**, và đơn vị là
408 âm tiết pinyin **không mang thanh** (`▁a`, `▁ai`, `▁ba`, `▁bai`…): với một ngôn ngữ có thanh, MultiNet7 bỏ thanh và
phân biệt lệnh chỉ bằng chuỗi âm tiết.

Chưa đọc ra: vai trò của `in_quant` (199 × 80) và `in_encproj` (48 × 384); 80 chiều có xếp thành hai mặt phẳng 40 dải
không; đơn vị của `chunk_size` 32; phép tính cụ thể của khối `pool`.

Bộ khung này là Zipformer của k2 [3] — các tầng nhiều tốc độ khung, ba feedforward và hai khối tích chập mỗi lớp, chuẩn
hoá một hệ số, nhánh tắt — với self-attention thay bằng một khối trộn rẻ; phần RNN-T theo kiểu mạng dự đoán không trạng
thái [4].

## Các phương án

| Phương án | Được | Mất |
|---|---|---|
| CRNN, tích chập rồi GRU một chiều (ADR-0010) | nhỏ | MultiNet6 và 7 không dùng GRU; GRU int8 chạy dòng qua esp-dl chưa từng chạy trong repo |
| TCN nhân quả tách chiều sâu | đã chạy khớp từng bit trên board (E11-T10) | không có mốc nhận lệnh nào trên S3 cho nó |
| **Encoder kiểu MultiNet7** | chính bộ khung Espressif chạy nhận lệnh trên S3 trong 11 ms mỗi 32 ms; chỉ có tích chập, feedforward, nhân, cộng, những phép esp-dl đã chạy dòng với TCN | nhiều khối phải xuất và khớp hơn; khối trộn phải tự chọn phép tính |

## Quyết định

- **Mạng**: bộ khung MultiNet7 của bảng trên — giảm khung bằng ba tích chập 2D (8, 32, 48 kênh), bề rộng 128, 6 lớp chia
  4 tầng (1, 2, 2, 1) ở ×1, ×2, ×4, ×2, mỗi lớp ba feedforward, hai LiGLU với nhân theo chiều sâu 17, 9, 5, 9, một khối
  trộn thay self-attention, chuẩn hoá một hệ số, nhánh tắt; đầu CTC.
- **Khác MultiNet7 ở ba chỗ**: đặc trưng là log-mel 40 cộng ba chiều cao độ trên lưới 16 ms, và đầu ra là 44 đơn vị có
  nhãn thanh cộng khoảng trắng, vì tiếng Việt có sáu thanh và lệnh có thể chỉ khác nhau ở thanh, điều MultiNet7 tiếng
  Trung bỏ qua (ADR-0010, KẾ HOẠCH §3.1, §3.12); đầu ra giữ 31,25 khung mỗi giây, bỏ lần hạ ×2 cuối của MultiNet7, vì
  chuỗi 44 đơn vị có thanh dày hơn chuỗi mảnh âm vị tiếng Anh ra ở 25 khung mỗi giây 🔬.
- **Cỡ**: trần 1,8 MB int8 của `ctc` (KẾ HOẠCH, bảng mô hình) nhỏ hơn 2,1 MB mà giảm khung, encoder và đầu CTC của
  MultiNet7 chiếm, nên khối feedforward hẹp lại; bề rộng chốt ở E11-T12 sau khi đo µs 🔬.
- **Khối trộn**: một ma trận 128 × 128 như `pool` của MultiNet7, đặt trên trung bình nhân quả trong khúc 🔬, vì phép tính
  của họ không đọc được từ trọng số.
- **Học**: CTC cộng RNN-T phụ trợ (mạng dự đoán không trạng thái và bộ nối, bỏ khi xuất), so với CTC trơn cùng seed, cùng
  split, cùng số epoch; giữ bản có lỗi đơn vị thấp hơn sau int8 (CLAUDE.md §4.3).
- **Chạy**: int8 qua esp-dl với `StreamingCache`; phép thử đầu tiên là một lớp encoder xuất qua ESP-PPQ, chạy dòng trên
  board B, khớp mô phỏng và đo µs, như TCN của E11-T10.

## Hệ quả

- KẾ HOẠCH §3.12, bảng mô hình và cây thư mục §4.1, TASKS E11-T12 theo ADR này. ADR-0010 giữ đơn vị và đặc trưng; ADR-0012
  giữ hai đường.
- GRU int8 qua esp-dl vẫn phải thử trên board cho mạng dìm nhiễu (E9-T5).
- Xét lại khi lớp encoder xuất qua esp-dl không khớp mô phỏng hoặc vượt 18 ms mỗi 32 ms của KẾ HOẠCH §3.3: lùi về TCN của
  E11-T10.

## Nguồn

1. ESP-SR 2.5.5: `model/multinet_model/mn7_en/{mn7_data, mn7_index, vocab}`, `model/multinet_model/mn6_en/mn6_data`,
   `lib/esp32s3/{libmultinet.a, libc_speech_features.a}`; gói `espressif/esp-sr` ghim ở
   `firmware/test_apps/espsr_compare/main/idf_component.yml`, chỗ duy nhất được link nó (CLAUDE.md §6).
2. Espressif, ESP-SR, *Benchmark*, ESP32-S3, mục MultiNet,
   docs.espressif.com/projects/esp-sr/en/latest/esp32s3/benchmark/README.html, đọc 30/09.
3. Z. Yao và cộng sự, *Zipformer: A faster and better encoder for automatic speech recognition*, ICLR 2024,
   arXiv:2310.11230.
4. M. Ghodsi, X. Liu, J. Apfel, R. Cabrera, E. Weinstein, *RNN-Transducer with Stateless Prediction Network*, ICASSP
   2020.
