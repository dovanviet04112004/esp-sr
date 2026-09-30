# ADR-0014 — `ns` học được có hai ứng viên, RNNoise-16k và NSNet-16k cỡ L, lấy giá của `nsnet2` ESP-SR trên board B làm mốc

- **Trạng thái**: Chấp nhận
- **Ngày**: 2026-10-01
- **Liên quan**: KẾ HOẠCH §0.2 (dòng 12), §1.1, §3.3, §3.9, §5.1, §5.6, §6.1, §6.5, §6.6; TASKS E9-T3, E9-T4, E9-T10,
  E9-T12; `docs/FREERTOS.md` 11.3; `docs/measurements/latency.md` §12

---

## Bối cảnh

KẾ HOẠCH cũ chỉ có một mạng cho khe `ns`: RNNoise dựng lại cho 16 kHz, ~90 KB int8, vì TỔNG QUAN §3.1 cho rằng mạng
tính gain từng vạch đắt. Bàn so của §3.16 (E9-T9) chạy AFE của ESP-SR trên board B, cùng lối vào `gsc` của `dsp_afe`
[1]. Đường dìm nhiễu `nsnet2` của họ tốn **4 182 µs trung bình, 4 444 µs đỉnh mỗi 256 mẫu, 395 760 B PSRAM, 10 624 B
RAM nội**, tức một mạng dìm nhiễu cỡ ấy chạy được trên đúng chip này. Mạng ấy không phải NSNet: file model của ESP-SR
2.5.5 cho thấy một encoder tích chập trên dải ERB, LSTM 128 int16, decoder tích chập chuyển vị và một mặt nạ [2].
Hai mạng tham chiếu trên máy tính, NSNet2 của Microsoft [3] và RNNoise 0.2 của xiph [4], có ~2,7 và ~2,9 triệu tham số,
gấp hàng chục lần mọi thứ vừa chip, nên chỉ là mốc trên của chất lượng.

Chủ repo chốt ngày 30/09 dựng hai ứng viên, và ngày 01/10 chốt cỡ, dữ liệu và cách chấm dưới đây.

## Quyết định

| Điểm | Chốt |
|---|---|
| Ứng viên | **RNNoise-16k**: RNNoise [5] dựng lại dải cho 16 kHz, dày 24 → GRU 24 / 48 / 96 → gain 18–22 dải (bản đầu 18) cộng xác suất tiếng nói, ~88 k tham số, ~90 KB int8. **NSNet-16k**: họ NSNet2 [3, 6], log công suất 256 vạch → dày H → GRU H → GRU H → dày H → 256 gain sigmoid |
| Cỡ NSNet-16k | **L**: H = 144, 324 688 tham số, 337 KB `.espdl` int8. Probe có trạng thái trên board B (E9-T10) đo L 5 054 µs mỗi bước, hơn `nsnet2` 21%, và 394 KB PSRAM, dưới 395 760 B của nó; S (H = 96) 3 019 µs, M (H = 128) 4 414 µs. Chủ repo chọn L để lấy chất lượng; S và M học cùng lượt làm đường lùi khi E9-T7 thấy nhân 1 vượt nhịp |
| Học | **một lượt** trên GPU cho cả bốn mạng, cùng batch trộn lúc học, cùng seed, cùng số epoch, cùng loss |
| Dữ liệu | mọi mẩu tiếng sạch qua luật sạch và mọi file nhiễu đã sàng lọc, **không trần giờ**; **Common Voice không vào tập học**, vì người nói của nó là giọng thử của `wake` và `command`; `val`, `test` là của `command/v1` |
| Chọn | bản hơn sàn bằng thước của §3.15 thì giữ, không bản nào hơn thì giữ sàn; hai bản ngang nhau trong sai số của tập thử thì giữ bản rẻ hơn |
| Thước | chạy với `wake` và `command` đã học trên chuỗi có sàn, trên phiên thu qua board có nhiễu, cho cả ba biến thể; chỉ bản thắng được học lại `wake` và `command` để xác nhận |
| Thứ tự | probe có trạng thái của cả hai ứng viên (E9-T10) chạy với trọng số ngẫu nhiên trong lúc học, vì nó quyết định ứng viên nào chạy được |

## Hệ quả

Ngân sách phải nới, ghi ở đây theo §6.1 ("không lặng lẽ nới"):

| Chỗ | Trước | Sau |
|---|---|---|
| §5.6, nhân 1, trường hợp nặng nhất | ~4 800 µs ≈ 30% (`ns` 800–1 800 µs) | ~8 100 µs ≈ 51% (`ns` 4 809–5 054 µs đo), nhỉnh hơn mục tiêu trung bình 50% |
| §5.1, tải nhân 1 | ~27% | ~27% với sàn, tới ~51% với NSNet-16k L; `FREERTOS.md` 11.3 theo |
| §6.1, slot model | `ns` ~90 KB, còn ~2,8 MB cho `command` | `ns` 337 KB với L, còn ~2,5 MB cho `command`, vẫn trên ~2,1 MB của ADR-0013 |
| §6.5, trọng số kéo về RAM nội | ~430–470 KB | ~440–720 KB: lý do để mọi model ở PSRAM càng mạnh |
| §6.6, PSRAM | `ns` + `wake` ~140 + ~100 KB, cộng ~4,3 MB | `ns` 187–394 KB đo, cộng tới ~4,6 MB trên 8 MB |

NSNet-16k cỡ L đọc 337 KB trọng số mỗi khung, ~21 MB/s qua PSRAM, gấp đôi mức làm suy luận ở nhân kia chậm đi 16,6%
ở repo face attendance (§6.6). E9-T7 và E14-T6 đo độ trễ đỉnh một khung với Wi-Fi chạy; vượt nhịp thì lùi về M hay S,
không kéo trọng số về RAM nội.

Dữ liệu không cắt nên lượt học dài: ~136 giờ tiếng sạch, ~64 nghìn bước, ~5,5–8 giờ, giới hạn bởi tốc độ đọc E: của
bộ trộn 🔬.

## Phương án đã loại

| Phương án | Vì sao loại |
|---|---|
| NSNet-16k cùng trần ~90 KB với RNNoise-16k | so hai ứng viên cùng giá và không dòng ngân sách nào phải đổi, nhưng mạng theo vạch khi ấy nhỏ hơn hẳn mạng đã chạy được trên chính board này; chủ repo chọn chất lượng |
| Cỡ lớn nhất còn trong giá `nsnet2` (S, 3,0 ms) | giữ nhân 1 dưới 50% chắc chắn, nhưng chủ repo chọn chất lượng: L đo 5,1 ms, nhân 1 tới ~51% ở trường hợp nặng nhất; S và M vẫn học để lùi |
| Chép kiến trúc `nsnet2` của Espressif (ERB, tích chập, LSTM) | là một thiết kế khác hẳn cần probe riêng; lấy giá của nó làm trần là đủ để so cùng giá |
| Cho Common Voice vào tập học | `ns` sẽ học trên chính người nói mà thước về sau dùng để chấm `wake` và `command` |
| Học lại `wake` và `command` cho từng biến thể trước khi chấm | công bằng nhất nhưng gấp ba lần học; chấm trước rồi chỉ học lại cho bản thắng |

## Nguồn

1. `interim/scenes/afe_compare/board_read_1m/board.json`, biến thể `board_gsc_espsr_nsnet2` của app
   `firmware/test_apps/espsr_compare`, board B, 30/09; thời gian tính trên mỗi 256 mẫu (`job_format.h`).
2. ESP-SR 2.5.5 trên ESP Component Registry, `model/nsnet_model/nsnet2/`: cấu hình nhúng và bảng chỉ mục tensor.
3. Braun, Tashev — *Data augmentation and loss normalization for deep noise suppression*, SPECOM 2020
   (arXiv:2008.06412).
4. [RNNoise](https://github.com/xiph/rnnoise) 0.2 trong pyrnnoise 0.4.5, bản chạy ở bàn so trên máy tính.
5. Valin — *A hybrid DSP/deep learning approach to real-time full-band speech enhancement*, 2018.
6. Xia và cộng sự — *Weighted speech distortion losses for neural-network-based real-time speech enhancement*,
   ICASSP 2020.
