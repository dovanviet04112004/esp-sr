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

## 2b. Trên bản thu thật qua board B (28/09)

`srhost.score` trên các phiên của `host/plans/levels.tsv` và `noisy.tsv`, board B trong vỏ, ở nhà,
`pcm_shift` 13. Chuỗi sản phẩm tới trước `agc` (`hpf`, `balance` của board, `ns_omlsa` sàn −12 dB, `vad` mức 2) chạy hai
lần, có và không có `ns`. Mỗi bước lấy mức vào `agc` không `ns` trừ mức có `ns`, tách theo `vad` của chuỗi có `ns`.
Ô là trung vị của phần chênh ấy. Không có bản sạch để so, nên "tiếng nói mất" gồm cả phần nhiễu nằm chung vạch với lời.

| Phiên | Nội dung | Mức vào `agc` p10 / p50 / p90 dBFS | Bước `vad = 1` | Dìm ở quãng nghỉ | Tiếng nói mất |
|---|---|---|---|---|---|
| `20260928_home_004` | phòng yên, không ai nói, 60 s | −81 / −81 / −79 | 0,4 % | 12 dB | – |
| `20260928_home_005` | đọc giọng thường 1 m | −81 / −79 / −49 | 53 % | 12 dB | 0 dB |
| `20260928_home_006` | đọc giọng nhỏ 1 m | −81 / −77 / −53 | 29 % | 11 dB | 1 dB |
| `20260928_home_007` | đọc giọng thường 3 m | −81 / −78 / −51 | 47 % | 11 dB | 1 dB |
| `20260928_home_008` | quạt, không ai nói, 60 s | −81 / −80 / −78 | 0 % | 12 dB | – |
| `20260928_home_009` | đọc 1 m, quạt chạy | −81 / −77 / −49 | 57 % | 12 dB | 1 dB |
| `20260928_home_010` | nhạc không lời, không ai nói, 60 s | −76 / −68 / −58 | 1,1 % | **4 dB** | – |
| `20260928_home_011` | đọc 1 m, nhạc nền | −70 / −55 / −45 | 81 % | **4 dB** | 0 dB |

- Nhiễu dừng của phòng và quạt xuống 11–12 dB ở quãng nghỉ, chạm sàn. Tiếng nói mất tối đa 1 dB, như trên
  cảnh VIVOS (§1).
- Nhạc chỉ xuống 4 dB: nó đổi nhanh hơn cực tiểu trượt ~1 s mà OM-LSA học nhiễu, nên bị coi là tiếng nói. Đây là chỗ
  bản mạng E9-T4 phải hơn sàn.
- Quạt đặt quá xa. Mức micro của `home_008` bằng đúng phòng yên (`ch0` −63,1 dBFS cả hai), nên phiên này chưa thử được
  nhiễu quạt. Cần thu lại với quạt gần board.

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
đòn bẩy chưa dùng: `fmaf` (một lệnh `madd.s` thay cho nhân rồi cộng) ở mọi chỗ nhân-cộng, cỡ 25–35% 🔬, với bản soi gương
mô phỏng FMA làm tròn đúng để giữ khớp từng bit. Để dành ở E9-T8: chuỗi hiện dùng 16,2% một bước (`budget.md`).

Trên `main` bản `dev` của board B (`c2f97b5`), `ns` trong chuỗi sản phẩm: heartbeat 5 phút có Wi-Fi và MQTT, 0 tràn DMA,
0 khung bỏ, RAM nội thấp nhất 36,1 KiB (`ram.md` §3).

## 5. Mạng học: dìm sâu tới đâu, và vì sao (01/10)

Đo trên máy tính, float, 980 mẫu `val` của `ns/v1` (lối vào là trung bình hai mic của đường mô phỏng board). Gain áp
lên riêng phần tiếng và riêng phần nhiễu ở khe, tính sau 3 s đầu; "quãng nghỉ" là các bước người nói im. Lượt học
`20261001_ec62294-dirty_437ed0`, hàm mất mát cũ: trọng số méo tiếng nói của Xia và cộng sự, α = 0,6, sai số trên gain
chưa nén.

| NSNet-16k L | Sàn −12 dB | Không sàn | Quãng nghỉ, không sàn | Tiếng mất |
|---|---|---|---|---|
| epoch 0 | 10,9 dB | 16,1 dB | 19,9 dB | 1,88 dB |
| epoch 1 | 9,8 dB | 12,9 dB | 16,2 dB | 0,78 dB |
| epoch 2 | 10,2 dB | 14,1 dB | 18,1 dB | 0,86 dB |

Ở epoch 2, RNNoise-16k không sàn dìm 13,3 dB (quãng nghỉ 17,7 dB); S và M 13,0 và 11,1 dB. Từ sàn −30 dB trở xuống số
gần như không đổi. Với sàn −12 dB mọi bản dừng ở 9–10 dB, ngang OM-LSA.

Gain lý tưởng là gain tính từ phần tiếng và phần nhiễu thật của từng vạch: mạng hoàn hảo của mỗi hàm mất mát sẽ cho ra đúng
nó.

| Gain lý tưởng | Sàn −12 dB | Không sàn | Quãng nghỉ | Tiếng mất |
|---|---|---|---|---|
| hàm cũ, α = 0,6 | 11,5 dB | 20,5 dB | 41,2 dB | 0,10 dB |
| Wiener | 11,6 dB | 21,3 dB | 42,5 dB | 0,13 dB |
| tỉ lệ biên độ tiếng / hỗn hợp | 11,3 dB | 18,6 dB | 37,6 dB | 0,15 dB |
| trọng số méo tiếng nói trên phổ nén, c = 0,3, α = 0,6 | 12,0 dB | 28,2 dB | 49,8 dB | 1,88 dB |
| như trên, α = 0,7 | — | 25,1 dB | 45,5 dB | 1,33 dB |
| như trên, α = 0,8 | — | 21,9 dB | 40,8 dB | 0,85 dB |
| như trên, α = 0,9 | — | 18,0 dB | 34,6 dB | 0,41 dB |

Đọc hai bảng:

- Sàn −12 dB là trần chung: cả gain lý tưởng cũng chỉ dìm 11,5–12 dB. Dưới sàn ấy không mạng nào hơn được OM-LSA về độ dìm.
- Không sàn, mạng cách xa đích của chính nó: 18 dB ở quãng nghỉ so với 41 dB. Một nửa số vạch trong quãng nghỉ đã dưới
  −35 dB, nhưng các vạch nhiễu to, nơi dồn năng lượng, vẫn để gain cao. Hàm cũ phạt nhiễu sót theo bình phương gain, nên
  để một vạch nhiễu ở −10 dB chỉ tốn một phần mười so với để nguyên, ở −20 dB một phần trăm.
- Đặt sai số lên phổ nén thì nhiễu sót bị phạt theo gain mũ 0,6: ở −20 dB vẫn tốn một phần tư so với để nguyên, và lực
  kéo gain xuống ở −40 dB còn một phần tư so với ở −20 dB, thay vì một phần trăm. Đó là cách NSNet2 (phổ nén mũ 0,3) và RNNoise (căn bậc hai gain) của bản PC học dìm sâu;
  đổi lại tiếng mất nhiều hơn, chỉnh bằng α.

Lượt `20261001_7f42622-dirty_095d54` học hàm trên phổ nén, α = 0,7, rồi dừng sau epoch 0 để đo trên cùng 980 mẫu:

| Hàm, epoch 0 | Sàn −12 dB | Sàn −20 dB | Không sàn | Quãng nghỉ | Tiếng mất |
|---|---|---|---|---|---|
| NSNet-16k L, hàm cũ | 10,9 dB | — | 16,1 dB | 19,9 dB | 1,88 dB |
| NSNet-16k L, phổ nén α = 0,7 | 11,5 dB | 16,6 dB | 18,8 dB | 22,5 dB | 4,18 dB |
| RNNoise-16k, phổ nén α = 0,7 | 11,4 dB | 16,5 dB | 18,6 dB | 23,4 dB | 3,39 dB |

Sâu hơn 2,7 dB nhưng tiếng mất gấp đôi. Đích của hàm ấy ép cả vạch tiếng đang lấn nhiễu: nén làm 10 dB SNR còn 3 dB, nên ở
+10 dB SNR gain lý tưởng với α = 0,7 lấy đi 5,6 dB tiếng, ở +25 dB vẫn lấy 2,1 dB. Hàm khớp phổ nén của đầu ra với tiếng
sạch, (S^c − (g·X)^c)² trên biên độ, có đích là tỉ lệ biên độ tiếng / hỗn hợp (bảng trên: 37,6 dB ở quãng nghỉ, tiếng mất
0,15 dB): nhiễu sót chỗ không có tiếng vẫn bị phạt theo gain mũ 0,6, còn chỗ có tiếng đích không ép tiếng.

## 6. Tiếng bật hơi đầu câu qua `ns_omlsa` (03–04/10)

Các phiên "tắt đèn", "tắt quạt", "bật đèn" của chủ repo trong tập chấm Cửa 3 (board B, dịch 13 của `board_b.csv`), chạy
qua chuỗi sản phẩm bằng Python ở `3549465`; năng lượng dải 2,5–7 kHz mỗi bước 16 ms trên cửa sổ Hann 512 mẫu. Chuỗi trả
`clean` trễ một bước so với lối vào (`CHAIN_LAG_HOPS`), nên bước h của `ch0` thô so với bước h + 1 của `clean`. Tiếng bật
hơi là bước thô nổi nhất trên nền trong khoảng sáu bước trước tới hai bước sau bước `vad` đầu của câu; nền là trung vị 14
bước trước đó. Nền của các phiên này có tiếng quạt máy tính (`mic_array.md` §4). Đo bằng script chẩn đoán chạy một lần.

| Câu | Chuỗi | Câu đo | Tiếng bật hơi nổi trên nền, thô, trung vị | Sau chuỗi, trung vị |
|---|---|---|---|---|
| "tắt đèn" | như sản phẩm | 11 | 12,3 dB | **28,1 dB** |
| "tắt quạt" | như sản phẩm | 11 | 9,7 dB | **24,9 dB** |
| "bật đèn" | như sản phẩm | 12 | 3,8 dB | 13,8 dB |
| "tắt đèn" | tắt `ns_omlsa` | 11 | 12,2 dB | 17,0 dB |
| "tắt quạt" | tắt `ns_omlsa` | 11 | 9,8 dB | 14,4 dB |

`ns_omlsa` hạ nền mà giữ tiếng bật hơi của "t", nên sau chuỗi phụ âm đầu nổi rõ hơn ở tín hiệu thô. Câu đầu của phiên
`20260928_home_013`: tiếng bật hơi −38,3 dB thô trên nền −51 dB, sau chuỗi −42,4 dB trên nền −69 dB. Bước cùng chỉ số của
`clean` (−64,4 dB) còn là nền trước câu, nên so bước h với bước h thì tiếng bật hơi trông như bị dìm 26 dB. "b" của "bật"
hữu thanh, ít năng lượng ở dải này.

Với model `command` đang khoá, học trên đúng chuỗi này (`command.md` §6):

| Chuỗi | Cửa 3 đầu đúng | Nhận đúng, `δ₁` 300 | "tắt" đầu đúng |
|---|---|---|---|
| như sản phẩm, sàn −12 dB | 84/112 | 71/112 | 0/22 |
| sàn −6 dB | 85/111 | 70/111 | 0/22 |
| tắt `ns_omlsa` | 83/111 | 54/111 | 3/22 |

Lọc ồn không phải chỗ "tắt" trượt: phụ âm đầu có trong đặc trưng mà model không đọc ra (`command.md` §6).

## 7. Bộ nạp của lượt học (08/10)

Lượt học lại của E9-T4 (`20261008_03de192-dirty_f04a9b`) trên máy học: RTX 3050 Laptop 4 GB, 20 luồng CPU, WSL 9 GB RAM,
dữ liệu ở E: đọc qua 9P. Mỗi bước là batch 32 ví dụ 10 s trộn lúc học (KẾ HOẠCH §3.9), bốn ứng viên học trên cùng batch.

Lọc trên CPU (`data.Mixer.example` rồi `data.slot_powers`, numpy float64), 10 worker: 1,1–1,3 bước/s, 35–42 ví dụ/s, GPU
chờ dữ liệu 77–81% thời gian; 7 worker 0,86 bước/s; 12 worker tràn RAM. Một ví dụ trên một tiến trình mất 336 ms (cProfile
24 ví dụ của epoch 0): chập RIR ~96 ms, phổ ở khe ~70 ms, đáp ứng micro ~60 ms, đọc nhiễu từ `raw/` ~71 ms, trong đó mở
file 19 ms. Ba phép lọc chiếm ~2/3; vòng Python không đáng kể.
