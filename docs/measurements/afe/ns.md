# `ns` sàn — OM-LSA cộng IMCRA (E9-T1)

## 1. Nhiễu bị dìm và tiếng nói mất, trên cảnh VIVOS

`make eval-ns` (`srpipe.scenes.ns`, `ml/configs/afe/ns_omlsa.yaml` trên nền `vad.yaml`) tại `e8f4a6f`, bản Python soi gương.

| Mục | Giá trị |
|---|---|
| Cảnh | cảnh VIVOS `test` của `vad.md` §1: 6 nhiễu × SNR 20, 10, 5, 0 dB = 24 cảnh, mỗi cảnh 120 s tiếng đọc, quãng nghỉ 0,5–2 s |
| Mức | tiếng nói −26 dBFS; OM-LSA làm việc trên tỉ số công suất nên một mức là đủ |
| Chuỗi | mỗi phần qua `hpf` như trong chuỗi, rồi STFT của lưới; gain của `ns_omlsa` ở giá trị gieo (sàn −12 dB) |
| Chấm | gain tính trên hỗn hợp áp riêng vào phần tiếng nói và phần nhiễu, so với chính phần ấy ở gain 1; bỏ 3 s đầu |

Ô: nhiễu giảm dB (mọi bước) / nhiễu giảm dB (quãng nghỉ) / tiếng nói giảm dB (bước nói) / SNR tăng dB (bước nói).

| Nhiễu | SNR 20 dB | SNR 10 dB | SNR 5 dB | SNR 0 dB |
|---|---|---|---|---|
| doing_the_dishes | 2,4 / 3,4 / 0,0 / 1,4 | 2,9 / 2,9 / 0,3 / 2,6 | 3,1 / 3,2 / 0,5 / 2,5 | 3,6 / 3,6 / 1,2 / 2,4 |
| dude_miaowing | 0,5 / 0,5 / 0,0 / 0,5 | 0,5 / 0,5 / 0,1 / 0,5 | 0,6 / 1,7 / 0,1 / 0,2 | 0,6 / 0,6 / 0,3 / 0,2 |
| exercise_bike | 5,0 / 7,3 / 0,0 / 3,4 | 6,3 / 7,6 / 0,3 / 4,8 | 6,6 / 7,5 / 0,6 / 5,1 | 6,8 / 6,9 / 1,1 / 5,5 |
| pink_noise | 5,2 / 10,2 / 0,0 / 2,7 | 7,3 / 11,1 / 0,2 / 4,8 | 8,5 / 11,4 / 0,5 / 6,0 | 9,3 / 11,6 / 1,1 / 6,6 |
| running_tap | 7,7 / 10,3 / 0,0 / 5,8 | 9,2 / 10,4 / 0,2 / 7,9 | 9,6 / 10,6 / 0,4 / 8,4 | 9,9 / 10,4 / 0,8 / 8,5 |
| white_noise | 7,8 / 11,3 / 0,1 / 5,6 | 9,6 / 11,6 / 0,2 / 7,9 | 10,2 / 11,6 / 0,5 / 8,6 | 10,7 / 11,6 / 1,0 / 9,0 |
| **trung bình** | 4,8 / 7,2 / 0,0 / 3,2 | 6,0 / 7,4 / 0,2 / 4,8 | 6,4 / 7,7 / 0,4 / 5,1 | 6,8 / 7,5 / 0,9 / 5,4 |

## 2. Đọc bảng

- Nhiễu dừng (ồn trắng, ồn hồng, vòi nước) xuống 10–11,6 dB trong quãng nghỉ, sát sàn 12 dB. Trên mọi bước ít hơn, vì
  trong lúc nói các vạch có tiếng nói giữ gain gần 1 và nhiễu nằm chung vạch đi theo.
- Nhiễu không dừng giảm ít: tiếng rửa bát ~3 dB, tiếng mèo kêu dưới 2 dB. OM-LSA học nhiễu từ cực tiểu trượt ~1 s; thứ gì
  đổi nhanh hơn thế thì bị coi là tiếng nói. Đây là chỗ bản mạng (E9-T4) phải hơn sàn.
- Tiếng nói mất tối đa 1,2 dB, ở SNR 0 dB; ở SNR 20 dB gần như không mất.
- Nhiễu tăng đột ngột thì ước lượng bám sau ~2,4 s: cực tiểu thô cần một cửa sổ (8 × 8 bước = 1,02 s) để quên mức cũ,
  lượt thứ hai chỉ cập nhật trên vạch được coi là vắng tiếng nên chờ thêm một cửa sổ nữa (Cohen 2003). Trong khoảng ấy
  nhiễu mới đi qua gần như nguyên vẹn. Nhiễu giảm thì bám ngay theo cực tiểu.

## 3. Độ trung thành với `omlsa.m`

Bản soi gương chạy ở đúng khung của bài (Hamming 512, bước 128, hằng số gốc, `G_min` −18 dB như `omlsa.m`), so với bản chép
float64 của `omlsa.m` 2003 (`tone_flag` 0, `broad_flag` 1, `medium`) trên cùng khung công suất; bản chép không nằm trong
repo vì mã gốc giữ bản quyền. Tiếng VIVOS `test` (8 câu của `VIVOSDEV01`) trộn nhiễu:

| Nhiễu, SNR | Khung | Lệch gain trung vị (tương đối) | Lệch gain lớn nhất | Vạch lệch > 1e-3 |
|---|---|---|---|---|
| running_tap, 5 dB | 4 465 | 5e-8 | 1,9e-5 | 0 % |
| pink_noise, 0 dB | 4 465 | 5e-8 | 1,3e-5 | 0 % |
| doing_the_dishes, 10 dB | 4 465 | 5e-8 | 1,5e-5 | 0 % |

Cả hai bên chặn gain ở 1 (KẾ HOẠCH §3.9); không chặn thì lệch lớn nhất 6,6e-3, nằm hết ở các vạch có gain trên 1. Đối chiếu
với chính `omlsa.m` trong Octave 🔬 chưa làm: máy chưa cài Octave.

## 4. Chi phí trên board B

`make bench-board`, hàng `dsp_afe ns_omlsa`: công suất mỗi bước dựng từ ồn, cứ 40 bước đổi giữa trầm và to (lệch 20 dB),
2 000 bước sau 16 bước làm nóng, profile `bench` (`-O2`, không gộp nhân-cộng, ADR-0006). Trạng thái 33 280 B ở RAM nội.

| Bản | µs trung bình | µs đỉnh | Việc đã làm |
|---|---|---|---|
| `cac33d9` | 2 488 | 15 640 | bản đầu; đỉnh là task watchdog in backtrace giữa một bước đo, vì vòng đo chạy quá 5 s không nhường |
| `b8149cc` | 2 531 | 2 920 | vòng đo nhường mỗi 100 bước, ngoài vùng đo |
| `e949651` | 2 530 | 2 920 | `frexpf`, `ldexpf`, `floorf` thay bằng thao tác bit: không đổi, xem dòng `5636ed9` |
| `1b24269` | 2 532 | 2 848 | mọi phép chia thành nhân với nghịch đảo Newton: không đổi, cùng lý do |
| `5636ed9` | **1 690** | **1 876** | đọc bit float qua `union`: GCC cho ESP32-S3 để `memcpy` 4 byte thành lời gọi hàm, hai lần mỗi nghịch đảo, `log2`, `exp2` |

Giá từng việc đo trên board (chu kỳ ở 240 MHz): nhân float ~4, nhân-cộng ~6–12, chia ~60, nghịch đảo Newton 48,
`log2` 159, `exp2` 124, làm trơn Hann 31 nhịp một vạch 270. FPU của ESP32-S3 không chạy chồng các lệnh float độc lập: bốn
chuỗi nhân-cộng độc lập tốn đúng bốn lần một chuỗi. Vì thế thời gian gần như tỉ lệ với số lệnh float, và các cách bẻ
chuỗi phụ thuộc đều không lợi, đã đo rồi bỏ: làm trơn cả phổ theo từng nhịp 1 817 µs, buộc gộp hàm con 1 855 µs, mở vòng
lặp theo vạch 1 690 µs, bốn tổng song song cho làm trơn cùng đa thức Estrin 1 811 µs. Chia theo đoạn ở `5636ed9`: SNR tiên
nghiệm 43 k chu kỳ, IMCRA 114 k, xác suất vắng tiếng 102 k, gain 121 k.

Dự trù ~350 µs của KẾ HOẠCH §3.3 thấp gần năm lần: 257 vạch, mỗi vạch cỡ trăm lệnh float, mỗi lệnh 4–6 chu kỳ. Còn một
đòn bẩy chưa dùng: `fmaf` (một lệnh `madd.s` thay cho nhân rồi cộng) ở mọi chỗ nhân-cộng, cỡ 25–35%, với bản soi gương
mô phỏng FMA làm tròn đúng để giữ khớp từng bit.

Trên `main` bản `dev` của board B (`c2f97b5`), `ns` trong chuỗi sản phẩm: heartbeat 5 phút có Wi-Fi và MQTT, 0 tràn DMA,
0 khung bỏ, RAM nội thấp nhất 36,1 KiB (`ram.md` §3).
