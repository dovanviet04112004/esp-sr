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

Lọc trên GPU (`gpu_mix`), so với đường numpy trên hai batch 32 ví dụ thật (ví dụ 0–31 và 5 000–5 031 của epoch 0, không
nhiễu tự thân để hai đường khỏi rút khác nhau), loss với gain phẳng −20 dB và với gain lý tưởng của đích numpy chặn ở −40 dB:

| Phần tiếng nói trên GPU | Công suất hỗn hợp / tiếng lệch | Loss, gain −20 dB | Loss, gain lý tưởng | Một batch |
|---|---|---|---|---|
| float32 hết (`9a6ba64`) | −81…−86 / −119 dB | −0,44% | −1,7% | 83 ms |
| float64, phổ RIR float64 | −115 / −137 dB | +0,031…+0,039% | +0,12…+0,25% | 153 ms |
| float64, phổ RIR float32 như `scipy` (`86e715f`) | −115…−117 / −131…−133 dB | **+0,003…+0,005%** | **+0,013…+0,033%** | 140 ms |

Hàm mất mát nén công suất mũ 0,15, nên nền nằm ~120 dB dưới đỉnh tiếng cũng đổi đích: 30% ô của ví dụ có người nói nằm sâu hơn
thế. Lọc phần tiếng bằng float32 trên cả ví dụ 10 s để lại nền làm tròn ở đó, đích nén cao hơn ~19%. Bản numpy lại có nền
riêng: `scipy.signal.fftconvolve` biến đổi RIR float32 của kho ở độ chính xác đơn (đoạn im lặng của ví dụ 3: numpy
1,6·10⁻¹⁰ so với đỉnh 1,6·10⁻², GPU float64 5·10⁻¹⁹). Làm đúng như `scipy` thì hai đường khớp; STFT float32 và `dry` float32
không đổi loss. Lượt `9a6ba64` vì thế học trên đích lệch: loss trung bình bước 1–100 thấp hơn lượt CPU 0,42–0,46% ở cả
bốn ứng viên; bỏ lượt ấy.

Đọc: mỗi lần đọc E: qua 9P phải chờ, worker đọc lần lượt thì GPU chờ dữ liệu 30–53%. Đọc 8 luồng mỗi worker trên ví dụ chưa
lượt nào đọc: 28–37 ms một ví dụ, so với 110–165 ms đọc lần lượt. Mỗi luồng một vùng cấp phát glibc thì worker giữ thêm
362 MB sau 40 batch, `MALLOC_ARENA_MAX=2` còn 143 MB và đọc một batch 0,35 s thay vì 0,52 s.

Lượt `20261008_dd911af-dirty_1ea86b` (6 worker, 8 luồng đọc, 2 vùng cấp phát): **3,75–3,81 bước/s**, GPU chờ dữ liệu 0–1%,
GPU bận 87–94%: **gấp ~3,1 lần** đường CPU. Loss trung bình bước 1–100 lệch lượt CPU −0,02%, +0,01%, 0,00%, +0,08%
(RNNoise-16k, NSNet-16k S, M, L); từ bước 300 hai lượt tản ±2% theo cả hai chiều như mọi lượt học GRU.

## 8. Đích trên bản thu qua board (08/10)

Bốn mục trộn của bàn so (KẾ HOẠCH §3.16): đoạn đọc `20260928_home_005` với quạt `…_008` hay nhạc không lời `…_010`, thu
riêng qua board B, cộng ở SNR 0 và 5 dB. Thước của `eval.py bench`: nhiễu giảm trên mọi bước, tiếng mất trên bước có lời,
SNR tăng, sau 3 s đầu. Gain lý tưởng của hàm mất mát là tỉ lệ biên độ tiếng / hỗn hợp từng vạch (§5), chặn ở 1.

| Gain | Nhạc SNR 0 | Nhạc SNR 5 | Quạt SNR 0 | Quạt SNR 5 |
|---|---|---|---|---|
| lý tưởng, không sàn | 12,1 / 0,70 / +9,5 | 9,0 / 0,39 / +7,0 | 10,8 / 0,45 / +8,7 | 7,7 / 0,28 / +6,3 |
| lý tưởng, sàn −20 dB | 11,7 / 0,70 / +9,3 | 8,8 / 0,39 / +6,9 | 10,8 / 0,45 / +8,6 | 7,6 / 0,28 / +6,3 |
| lý tưởng, sàn −12 dB | 9,6 / 0,69 / +7,9 | 7,8 / 0,38 / +6,3 | 9,1 / 0,45 / +7,5 | 7,3 / 0,28 / +6,1 |
| OM-LSA | 1,1 / 0,5 / 0,0 | 1,1 / 0,3 / +0,2 | 9,6 / 0,9 / +7,8 | 8,8 / 0,5 / +7,1 |
| NSNet-16k L, epoch 2, không sàn | 6,8 / 2,2 / +2,4 | 5,5 / 1,4 / +2,1 | 13,0 / 3,0 / +7,7 | 10,0 / 1,4 / +6,3 |

Ô: nhiễu giảm dB / tiếng mất dB / SNR tăng dB.

Đoạn đọc gần như liền, nên phần lớn số bước là bước có lời: ở đó vạch nào tiếng lấn thì gain lý tưởng giữ, kéo theo nhạc
cùng vạch, nên cả gain lý tưởng cũng chỉ dìm nhạc 9–12 dB trên cả đoạn, với tiếng mất dưới 1 dB. Trên nhạc, NSNet-16k L ở
epoch 2 lấy được khoảng một phần tư SNR tăng lý tưởng và làm mất tiếng gấp ba; trên quạt nó dìm sâu hơn gain lý tưởng và
trả bằng tiếng.

Cùng bốn mục theo epoch của lượt `20261008_dd911af-dirty_1ea86b`, không sàn, sau 3 s đầu; ô: SNR tăng dB / tiếng mất dB:

| Biến thể | Epoch | Quạt SNR 0 | Quạt SNR 5 | Nhạc SNR 0 | Nhạc SNR 5 |
|---|---|---|---|---|---|
| OM-LSA | — | +7,8 / 0,9 | +7,1 / 0,5 | 0,0 / 0,5 | +0,2 / 0,3 |
| NSNet-16k L | 2 | +7,7 / 3,0 | +6,3 / 1,4 | +2,4 / 2,2 | +2,1 / 1,4 |
| NSNet-16k L | 10 | +7,6 / 1,9 | +5,9 / 0,8 | +1,5 / 1,4 | +1,4 / 0,8 |
| NSNet-16k L | 17 | +7,6 / 1,7 | +6,2 / 0,7 | +1,9 / 1,6 | +1,7 / 0,9 |
| NSNet-16k M | 17 | +7,5 / 2,4 | +5,9 / 0,9 | +2,8 / 3,0 | +2,4 / 1,6 |
| NSNet-16k S | 17 | +7,6 / 2,4 | +6,3 / 1,1 | +1,5 / 1,9 | +1,3 / 1,0 |
| RNNoise-16k | 17 | +7,1 / 1,7 | +6,1 / 0,9 | +4,0 / 5,7 | +2,3 / 1,2 |

Từ epoch 2 tới 17, loss `val` hạ (NSNet-16k L 0,045 → 0,043) mà số trên bản thu gần như đứng: trên quạt không bản nào
hơn OM-LSA, vốn đã sát gain lý tưởng, và mọi bản mất tiếng nhiều hơn; trên nhạc các bản lấy +1,5 … +2,8 dB của +9,5 dB lý
tưởng, RNNoise +4,0 dB với 5,7 dB tiếng mất. Lượt học dừng ở bước 46 412/61 891 (09/10 07:20), `make ns-resume` đi tiếp.

Cùng thước trên `val` mô phỏng (`eval.py score --set val`), theo lớp nhiễu, sau 3 s đầu; ô: SNR tăng dB / tiếng mất dB:

| Biến thể | Epoch | `music` | `music_vocals` | `stationary` | `nonstationary` | `babble` | `tone_only` |
|---|---|---|---|---|---|---|---|
| OM-LSA | — | +2,0 / 0,2 | +2,3 / 0,1 | +4,2 / 0,4 | +2,6 / 0,2 | +1,2 / 0,2 | +4,8 / 0,1 |
| NSNet-16k L | 1 | +3,3 / 2,4 | +3,6 / 2,1 | +4,1 / 2,9 | +3,9 / 2,2 | +1,9 / 3,6 | +4,3 / 0,8 |
| NSNet-16k L | 17 | +4,4 / 2,1 | +4,3 / 1,5 | +4,8 / 2,1 | +5,1 / 1,5 | +2,6 / 2,8 | +4,8 / 0,4 |
| NSNet-16k M | 1 | +3,6 / 2,8 | +3,6 / 2,3 | +4,2 / 3,3 | +4,4 / 2,5 | +2,0 / 4,2 | +4,4 / 0,9 |
| NSNet-16k M | 17 | +4,4 / 2,0 | +4,3 / 1,3 | +4,8 / 1,9 | +5,0 / 1,4 | +2,5 / 2,4 | +4,8 / 0,5 |
| RNNoise-16k | 1 | +3,8 / 1,8 | +3,9 / 1,7 | +4,1 / 2,2 | +4,1 / 1,6 | +2,4 / 3,7 | +4,3 / 0,5 |
| RNNoise-16k | 17 | +4,1 / 1,3 | +4,2 / 1,4 | +4,5 / 1,8 | +4,6 / 1,3 | +2,7 / 2,7 | +4,5 / 0,4 |

Trên `val` nhạc không phải lớp khó: NSNet-16k L ở epoch 17 kém `stationary` 0,4 dB và hơn OM-LSA 2,4 dB; khó nhất là
`babble`. Trên bản thu, cùng mạng vẫn hơn OM-LSA 1,9 dB trên nhạc, gần bằng phần hơn trên `val`, nhưng nhạc kém quạt
5,7 dB, và OM-LSA lấy 0,0 dB so với +2,0 dB trên nhạc `val`: đoạn nhạc thu qua board khó hơn nhạc mô phỏng với mọi cách.
Tới epoch 17, lớp nhạc `val` của L lên 1,1 dB so với epoch 1 mà đoạn thu của nó xuống (+2,4 dB ở epoch 2, +1,9 dB ở
epoch 17); của M lên ở cả hai (`val` +0,8 dB, đoạn thu +1,9 → +2,8 dB). Bàn so chỉ có một đoạn nhạc, nên chưa nói được
phần học thêm có sang bản thu không, và đoạn ấy khó vì đi qua loa, phòng và micro của board hay vì chính bản nhạc.

## 9. Lượt `20261008_dd911af-dirty_1ea86b`: mạng xa gain lý tưởng vì đâu (10/10)

Trọng số cuối epoch 17, float, trên GPU, đo bằng script chẩn đoán chạy một lần. Ví dụ học mới là epoch 40 của bộ trộn
`train`, lượt học chưa rút epoch ấy; mỗi tập 640 ví dụ, trải đều trên epoch; `val` là 640 ví dụ đầu của bộ đã lưu. Hàng
"tiếng / nhiễu / phòng / nền phòng của `val`" là bộ trộn `train` có đúng phần ấy lấy từ bể `val`.

| Tập | RNNoise-16k | NSNet-16k S | M | L | gain lý tưởng | gain 1 |
|---|---|---|---|---|---|---|
| `val` đã lưu | 0,0608 | 0,0450 | 0,0439 | 0,0428 | 0,0011 | 0,400 |
| `val` trộn lại bằng đường GPU hiện tại | 0,0604 | 0,0435 | 0,0422 | 0,0415 | 0,0010 | 0,446 |
| học, epoch 40 | 0,0478 | 0,0309 | 0,0295 | 0,0292 | 0,0009 | 0,503 |
| học, epoch 0 | 0,0473 | 0,0309 | 0,0296 | 0,0297 | 0,0010 | 0,489 |
| học, tiếng của `val` | 0,0549 | 0,0354 | 0,0344 | 0,0338 | 0,0009 | 0,460 |
| học, nhiễu của `val` | 0,0538 | 0,0375 | 0,0363 | 0,0356 | 0,0010 | 0,502 |
| học, phòng của `val` | 0,0481 | 0,0314 | 0,0298 | 0,0297 | 0,0010 | 0,493 |
| học, nền phòng của `val` | 0,0468 | 0,0306 | 0,0294 | 0,0289 | 0,0009 | 0,503 |

Loss của NSNet-16k L theo lớp, cùng các tập:

| Tập, epoch | `stationary` | `nonstationary` | `music` | `music_vocals` | `babble` | `tone_only` | không người nói |
|---|---|---|---|---|---|---|---|
| học, 17 | 0,0266 | 0,0311 | 0,0292 | 0,0336 | 0,0432 | 0,0226 | 0,0134 |
| học, nhiễu của `val`, 17 | 0,0297 | 0,0317 | 0,0291 | 0,0359 | 0,0487 | 0,0226 | 0,0592 |
| học, tiếng của `val`, 17 | 0,0315 | 0,0359 | 0,0363 | 0,0358 | 0,0476 | 0,0279 | 0,0134 |
| `val` đã lưu, 17 | 0,0398 | 0,0383 | 0,0438 | 0,0448 | 0,0645 | 0,0275 | 0,0361 |
| học, 0 / 2 / 17 | | | | | | | 0,0715 / 0,0294 / 0,0134 |
| học, nhiễu của `val`, 0 / 2 / 17 | | | | | | | 0,0958 / 0,0616 / 0,0592 |

- Loss `val` cao hơn loss học ~42% (L: 0,0415 so với 0,0292) vì vật liệu, không vì học thuộc: chênh đã có ở epoch 0
  (0,0638 so với 0,0483), phòng và nền phòng không góp, tiếng và nhiễu của `val` cộng lại gần đủ phần chênh.
- Phần của nhiễu nằm ~3/4 ở ví dụ không người nói. Ở đó loss là trung bình `g^0,6` có trọng số: 0,0134 là dìm tương
  đương −62 dB, 0,0592 là −41 dB, cả hai sâu quá mức nhận dạng cần. Có người nói thì nhiễu chưa gặp chỉ đổi `babble` và
  `stationary`; nhạc chưa gặp không đổi gì (0,0291 so với 0,0292).
- Phần của tiếng tăng đều mọi lớp, cả `tone_only`. Tiếng `val` là VIVOS và Common Voice, khoảng động trung vị 41,7 dB; 76%
  giờ học là bud500, 48,8 dB. Hàm nén công suất mũ 0,15 tính nền của chính bản thu là tiếng.
- Bộ `val` lưu ngày 01/10 không còn trùng bộ trộn hiện tại: ví dụ 0–3 khác lớp, mức nói, file nhiễu. Loss trên bộ trộn lại
  lệch 3% nên không đổi kết luận nào ở đây; dựng lại `data sets` trước lượt học sau.

Thước của `eval.py score` trên 980 ví dụ `val` đã lưu, sau 3 s đầu, không sàn; ô: nhiễu giảm dB / tiếng mất dB / SNR tăng dB:

| Lớp | Gain lý tưởng | NSNet-16k L | RNNoise-16k | OM-LSA |
|---|---|---|---|---|
| `music` | 9,8 / 0,19 / +7,1 | 8,9 / 2,09 / +4,4 | 7,9 / 1,31 / +4,1 | 2,5 / 0,15 / +2,0 |
| `music_vocals` | 12,2 / 0,17 / +7,2 | 8,4 / 1,46 / +4,3 | 8,0 / 1,37 / +4,2 | 2,8 / 0,14 / +2,3 |
| `nonstationary` | 10,3 / 0,18 / +7,7 | 8,6 / 1,47 / +5,1 | 7,9 / 1,29 / +4,6 | 3,3 / 0,17 / +2,6 |
| `stationary` | 9,9 / 0,31 / +6,8 | 8,9 / 2,14 / +4,8 | 8,4 / 1,83 / +4,5 | 5,6 / 0,42 / +4,2 |
| `babble` | 9,5 / 0,32 / +6,8 | 7,0 / 2,85 / +2,6 | 7,0 / 2,72 / +2,7 | 1,7 / 0,23 / +1,2 |
| `tone_only` | 8,3 / 0,06 / +5,1 | 8,5 / 0,42 / +4,8 | 8,3 / 0,41 / +4,5 | 6,5 / 0,07 / +4,8 |
| có người nói, SNR 0–5 dB | 12,7 / 0,47 / +9,5 | 11,5 / 4,89 / +5,2 | 10,1 / 3,77 / +4,8 | 2,6 / 0,47 / +2,0 |

Cùng thước, có thêm NSNet2 bản PC của §3.16 (`nsnet2-20ms-baseline.onnx`, khung 20 ms, bước 10 ms): gain của nó tính
trên hỗn hợp ở khe, đưa về −30 dBFS như dữ liệu DNS nó học, rồi áp riêng lên phần tiếng và phần nhiễu bằng STFT của nó.
Bàn so: bốn mục trộn của §8; `val`: 40 ví dụ đầu mỗi lớp.

| Tập | Ví dụ | Gain lý tưởng | NSNet-16k L | NSNet2 PC |
|---|---|---|---|---|
| bàn so, quạt SNR 0 và 5 | 2 | 9,3 / 0,37 / +7,5 | 10,3 / 1,21 / +6,9 | 15,0 / 4,21 / +8,4 |
| bàn so, nhạc SNR 0 và 5 | 2 | 10,6 / 0,54 / +8,2 | 5,1 / 1,27 / +1,8 | 12,1 / 5,50 / +4,4 |
| `val` `music` | 40 | 9,1 / 0,18 / +6,6 | 9,5 / 3,34 / +4,0 | 13,1 / 4,84 / +6,3 |
| `val` `music_vocals` | 40 | 9,7 / 0,19 / +7,3 | 8,1 / 1,92 / +4,1 | 12,6 / 5,61 / +5,6 |
| `val` `nonstationary` | 40 | 10,3 / 0,17 / +7,6 | 9,5 / 1,69 / +5,4 | 14,4 / 4,56 / +7,5 |
| `val` `stationary` | 40 | 10,1 / 0,48 / +6,4 | 9,5 / 3,78 / +4,8 | 14,4 / 5,22 / +7,1 |
| `val` `babble` | 40 | 9,9 / 0,38 / +6,9 | 7,8 / 4,22 / +2,9 | 11,7 / 8,53 / +3,1 |

Gain của L trên bước có lời của 640 ví dụ `val`, nhóm theo chính gain ấy:

| Gain của L | Phần vạch | `g^0,3` trung bình của L | `(S/X)^0,3` trung bình | `S/X` trung bình | Phần công suất tiếng | Công suất tiếng bị cắt |
|---|---|---|---|---|---|---|
| dưới −30 dB | 22,4% | 0,206 | 0,457 | −16,6 dB | 1,3% | 1,3% |
| −30 … −20 dB | 18,6% | 0,430 | 0,516 | −14,5 dB | 0,9% | 0,9% |
| −20 … −10 dB | 24,4% | 0,599 | 0,638 | −9,7 dB | 2,3% | 2,2% |
| −10 … −6 dB | 9,7% | 0,759 | 0,761 | −5,9 dB | 3,1% | 2,5% |
| −6 … −3 dB | 8,3% | 0,858 | 0,839 | −3,8 dB | 6,9% | 4,2% |
| −3 … −1 dB | 8,5% | 0,937 | 0,911 | −2,1 dB | 17,6% | 5,7% |
| −1 … 0 dB | 8,1% | 0,983 | 0,968 | −0,8 dB | 67,9% | 4,9% |

Đọc:

- Mạng làm đúng điều hàm mất mát đòi. Ở các nhóm mang 96% công suất tiếng (gain −10 … 0 dB), `g^0,3` của L bằng trung bình
  `(S/X)^0,3` trong 0,03: đó là đáp án tốt nhất của sai số bình phương trên phổ nén khi mạng không phân biệt được các vạch
  trong nhóm. Phần thiếu so với gain lý tưởng là phần mạng không đoán ra từ những gì nó nghe, không phải lỗi học.
- To hơn trong họ này không lợi: S, M, L chênh 6% loss (0,0309 / 0,0295 / 0,0292). NSNet2 PC lấy thêm ~2 dB SNR trên
  `val` nhưng cắt tiếng 4,6–8,5 dB, gấp 1,5–3 lần L: chỗ đứng khác trên cùng đường đánh đổi, không phải bản tốt hơn hẳn.
- Trên bàn so, quạt không còn gì để lấy: L +6,9 dB so với lý tưởng +7,5 dB, và OM-LSA đã sát lý tưởng (§8). Nhạc thu qua
  board kém nhạc `val` ~2 dB ở cả L (+4,0 → +1,8 dB) lẫn NSNet2 PC (+6,3 → +4,4 dB): đoạn nhạc ấy khó với mọi mạng, không
  riêng lượt này. Bàn so chỉ có một đoạn nhạc.
- Một phần ba loss của L, trên `val` lẫn ví dụ học, nằm ở vạch có gain lý tưởng dưới −30 dB mà mạng chưa đủ sâu, dù trung
  vị gain của mạng ở đó đã là −64 dB. Hàm nén công suất mũ 0,15 vẫn trả công cho việc dìm thêm dưới mức mà sàn của E9-T12
  (−12 … −30 dB) sẽ cắt đi.

## 10. GTCRN học sẵn: đi tiếp (21/11, E9-T15)

Trên bàn so (`compare.md`, mục GTCRN), GTCRN học sẵn trên DNS3 hơn OM-LSA và cả NSNet2, RNNoise công bố ở mọi thước trừ
mức hạ đoạn có lời (2,2 dB so với 1,3 dB của OM-LSA trên lối vào của khe), và chỉ lấy độ lớn mặt nạ làm gain không mất gì
đo được. Đi tiếp bước 2 của E9-T15: cắm GTCRN vào khe `ns` của chuỗi Python, chạy dòng từng bước như chip, và chấm `wake`,
`command` trên phiên board có nhiễu theo thước của KẾ HOẠCH §3.15, cạnh OM-LSA. Firmware chỉ bàn sau bước ấy.

Khe chạy GTCRN dòng từng bước trên CPU máy tính, 1 500 bước, mỗi bước 16 ms tiếng: 5,8 ms một bước ở một luồng torch,
9,3 ms ở mười luồng mặc định khi máy chạy song song một job lượng tử hoá (21/11). Tensor của một bước quá nhỏ để chia cho
nhiều luồng, nên khe chạy một luồng. Dựng model mất 0,6 s, và mỗi tiến trình chỉ dựng một lần.

### 10.1 Thước của bộ nhận dạng: GTCRN thua sàn (21/11)

Chấm theo KẾ HOẠCH §3.9 và §3.15: `wake` và `command` đã học trên chuỗi có sàn, phiên thu qua board B mà run chưa học, một
lần với OM-LSA, một lần với `SRPIPE_NS=gtcrn` (`esp-sr` `075b29b9`). Log ở `cache/logs/e9t15_*`.

`make wake-board RUN=artifacts/wake/runs/20261113_8ac386e_ffbc12 ROW=kl TH=800`:

| Khe `ns` | "vừng ơi" | 004 | 005 | 008 | Báo nhầm, 66 phiên 1,13 h |
|---|---|---|---|---|---|
| OM-LSA (sàn) | **33/35** | 9/11 | 12/12 | 12/12 | 0 |
| GTCRN | 26/37 | 3/12 | 11/12 | 12/13 | 0 |
| GTCRN, gain ≥ −12 dB | 29/35 | 5/11 | 12/12 | 12/12 | — |
| GTCRN, gain ≥ −20 dB | 30/38 | 6/12 | 12/12 | 12/14 | — |

`make command-eval TRACK=ctc RUN=artifacts/command_ctc/runs/20261007_439d763-dirty_f438bd CONTRACT=1`:

| Khe `ns` | Đúng ở lần đoán tốt nhất | Lệnh kém nhất, δ₁ 200–1000 ‰ | Mọi lệnh | Lời không phải lệnh bị từ chối |
|---|---|---|---|---|
| OM-LSA (sàn) | 192/215 | **60%** | 76% | 143–147/150 |
| GTCRN | 194/216 | 18% | 73% | 116–121/125 |

Lệnh của GTCRN lệch từng phiên: "tắt quạt" ở 3 m từ 1/6 lên 5/6, "đóng cửa" ở 3 m từ 4/5 xuống 1/5. Vậy GTCRN không hơn sàn
ở thước nào, và theo luật chọn của §3.9 thì giữ sàn. Hai dòng có sàn gain chạy bằng script một lần trên ba phiên "vừng ơi",
không có lệnh `make`.

Vì sao GTCRN thua, đo trên lối ra của chuỗi ở ba phiên ấy:
- Giữa lời, mức của hai bản ngang nhau: GTCRN kém −0,4 / −0,1 / +0,3 dB so với OM-LSA ở phiên 004 / 005 / 008.
- Ngay sau khi lời dứt, GTCRN thấp hơn hẳn. Ở phiên 004 và 005, 0–100 ms sau lời thấp hơn 9 dB, 100–200 ms thấp hơn
  14–15 dB, 200–400 ms thấp hơn 20–24 dB. Phiên 008 chỉ thấp hơn 2–3 dB ở 200 ms đầu.
- Nghĩa là checkpoint DNS3 vừa khử nhiễu vừa khử vang: nó học để ra tiếng khô. Nhãn dương của `wake` phủ từ 0,2 s trước
  đến 0,3 s sau khi lời dứt (`train.label_s`), đúng quãng vang ấy.
- Quãng lặng xuống −88 … −92 dBFS, so với −69 … −73 dBFS của OM-LSA. Sàn gain −12 dB đưa quãng lặng về −70 … −73 dBFS và
  gỡ được một phần (29/35). Phần còn lại là quãng vang bị cắt.
- Nâng lối vào của mạng 12 hay 24 dB, giữ gain là tỉ số với vạch vào, không giúp: 004 vẫn 3/11, 008 tụt xuống 7/12. Lỗi
  không nằm ở mức vào.

Bản ghi lối ra của hai khe ở `cache/listen/ns_gtcrn/<phiên>_{omlsa,gtcrn}.wav`.

### 10.2 Sửa không học lại: không bản nào tới sàn (21/11)

Chủ repo không cho học lại: dữ liệu tự dựng chưa đủ tốt. Thử hai cách không học, cùng ba phiên và cùng hàng `kl` 800‰ như
§10.1, bằng script một lần:
- checkpoint VCTK-DEMAND của cùng commit: bộ ấy chỉ cộng nhiễu vào tiếng phòng thu, không có vang;
- checkpoint DNS3 với gain mỗi vạch không tụt nhanh hơn 1 hay 2 dB một bước so với bước trước, để quãng vang tắt dần, và
  không dưới sàn −12 dB.

| Khe `ns` | "vừng ơi" | 004 | 005 | 008 | Lời, trung vị |
|---|---|---|---|---|---|
| OM-LSA (sàn, §10.1) | **33/35** | 9/11 | 12/12 | 12/12 | −47 / −44 / −54 dBFS |
| GTCRN VCTK-DEMAND | 0/15 | 0/6 | 0/9 | 0/0 | −71 / −73 / — dBFS |
| GTCRN VCTK-DEMAND, gain ≥ −12 dB | 23/32 | 8/11 | 12/12 | 3/9 | −55 / −54 / −62 dBFS |
| GTCRN DNS3, tụt ≤ 1 dB một bước, gain ≥ −12 dB | 30/35 | 6/11 | 12/12 | 12/12 | −47 / −46 / −54 dBFS |
| GTCRN DNS3, tụt ≤ 2 dB một bước, gain ≥ −12 dB | 30/35 | 6/11 | 12/12 | 12/12 | −46 / −46 / −54 dBFS |

- Bản VCTK-DEMAND coi tiếng xa micro có vang của board là nhiễu: lời xuống tới mức quãng lặng, không còn gì để bắt.
- Giới hạn tốc độ tụt gain chỉ gỡ thêm một lần so với chỉ đặt sàn (30/35 so với 29/35). Phiên 004 vẫn hụt ở lần nói thứ 5 và
  thứ 8: OM-LSA cho 0,89 / 0,88, GTCRN cho 0,44 / 0,44.
- Kết luận cho E9-T15: GTCRN học sẵn không hơn sàn, kể cả có sửa ở gain. Giữ OM-LSA ở khe `ns`, firmware không đổi.
- Ba phiên "vừng ơi" này thu trong phòng yên. Phiên board không có lời "vừng ơi" nói giữa nhiễu, nên thước chưa thấy được
  chỗ một mạng có thể hơn sàn. Trên 66 phiên không có từ ấy, cả hai khe đều không báo nhầm lần nào.

### 10.3 GTCRN lên chip: lượng tử không được, C float32 chậm trên S3 (22/11, ADR-0047)

Chủ repo quyết đưa GTCRN DNS3 lên chip, đổi được lúc chạy với OM-LSA. Bản thử là một bước chạy dòng viết lại bằng các
op esp-dl có sẵn, khớp `StreamGTCRN` tới 7,4e-6 so với đỉnh qua 400 bước của phiên `20261031_home_004`. ESP-PPQ nhận cả
đồ thị: 332 op ONNX, 14 tensor trạng thái, mỗi trạng thái vào và ra cùng số mũ. Gain lệch so với bản float, trên 600 bước
của phiên ấy, các trạng thái int8 đưa ngược lại mỗi bước:

| Cách lượng tử | Gain lệch, trung vị | p90 | Ở vạch có gain float > 0,1 |
|---|---|---|---|
| int8 `minmax` | 22,4 dB | 41,1 dB | 46,5 dB |
| int8 thang `ns` (`kl`, cân bằng trọng số, sửa bias) | 22,9 dB | 34,7 dB | 9,5 dB |
| lớp đầu và lớp ra mặt nạ int16 (14 op) | 5,6 dB | 23,0 dB | 27,7 dB |
| mọi op int16 trừ 14 GRU (318 op) | 11,9 dB | 14,8 dB | 7,6 dB |

- Cả GRU int16 thì không chạy: `GRU_quant_forward` của ESP-PPQ không có đường int16.
- Phân tích theo từng op ở thang `ns`: nhiễu lượng tử đã bằng 31% tín hiệu sau conv đầu của bộ mã hoá, rồi lên 1–24 lần
  tín hiệu qua bộ mã hoá. GTCRN nhận phổ tuyến tính, không có bước chuẩn hoá ở lối vào. Không học lại có lượng tử thì
  int8 hay int16 đều không dùng được.

Thời gian một bước C float32 có dáng GTCRN, trọng số ngẫu nhiên, trên board C (ESP32-S3, 240 MHz, `-O2`), `make
ai-unit-gtcrn BOARD=C`, 200 bước sau 20 bước khởi động:

| Vòng lặp | Trọng số ở | Trung bình | Đỉnh |
|---|---|---|---|
| viết thẳng, kiểm biên trong vòng trong | PSRAM | 52 127 µs | 52 127 µs |
| mỗi hàng vào đệm sẵn, một trọng số chạy qua mọi vị trí ra, exp xấp xỉ | PSRAM | 29 482 µs | 29 482 µs |
| như trên | RAM trong | 28 194 µs | 28 195 µs |

- Bản thử có 23 364 trọng số. Các bộ lọc ERB và SFE không có trọng số học nên không tính.
- Đổi chỗ trọng số chỉ được 4%: chỗ nghẽn là phép tính, không phải PSRAM. Bản thứ hai tốn khoảng 17 chu kỳ cho mỗi phép
  nhân-cộng.
- Mỗi bước dài 16 ms, nên bản C float32 hiện tại chưa vừa nhịp của S3.

### 10.4 GTCRN bằng C float32 trên S3 (25/11, E9-T16)

`ai_engine/src/ns_gtcrn/gtcrn.c` khớp bản soi gương `tasks/ns/gtcrn_mirror.py` từng bit: trên máy tính, ở cả hai ca của
golden và 60 bước phiên `20261031_home_004` với checkpoint thật; trên board C, 40/40 bước của `case_000`. Mỗi `fmaf` dịch
thành một lệnh `madd.s`. `make ai-unit-gtcrn BOARD=C`, 200 bước sau 20 bước khởi động, trọng số ở PSRAM:

| Bản | Trạng thái ở RAM trong | Trạng thái ở PSRAM |
|---|---|---|
| vòng lặp chung cho mọi conv | 48 680 µs | 50 205 µs |
| conv 1×1 bốn kênh ra một lượt, conv bước 2 trên hàng đệm sẵn, depthwise viết riêng, exp không gọi libm | 23 147 µs | 24 839 µs |

Chu kỳ mỗi bước của bản thứ hai, đếm tạm bằng `esp_cpu_get_cycle_count` theo khâu, trạng thái ở RAM trong:

| Khâu | Chu kỳ | Ghi chú |
|---|---|---|
| độ lớn, ERB, SFE | 152 475 | 257 căn bậc hai, mỗi căn 6 phép chia |
| `en0`, `en1` | 321 243 | 67 800 phép nhân-cộng: 4,7 chu kỳ mỗi phép |
| 3 khối mã hoá | 880 713 | |
| 2 khối GRU hai đường | 2 307 439 | 264 bước GRU 4 chiều và 66 bước GRU 8 chiều, mỗi cổng một exp và một phép chia |
| 3 khối giải mã | 892 694 | |
| 2 ConvTranspose | 857 895 | phép chia và phép dư nguyên theo bước trong vòng trong |
| gain | 142 989 | 257 căn bậc hai |

- Một phép chia float32 trên S3 tốn khoảng 90 chu kỳ: 257 căn bậc hai, mỗi căn 6 phép chia, hết 152 475 chu kỳ.
- Bản này vẫn gấp 1,4 lần nhịp 16 ms, mà nhân 1 còn chạy cả chuỗi. Các chỗ còn lấy được là sigmoid, tanh và căn bậc hai
  bỏ phép chia, ConvTranspose bỏ phép chia nguyên, và GRU duỗi vòng theo cỡ cố định.

Các vòng tối ưu sau đó, cùng phép đo, bản nào cũng khớp golden từng bit trên máy tính và trên board C:

| Vòng | Đổi gì | Trạng thái ở RAM trong | Trạng thái ở PSRAM |
|---|---|---|---|
| 2 | nghịch đảo và căn bậc hai không phép chia (cả bản soi gương), ConvTranspose riêng cho bước 2 | 20 386 µs | 22 074 µs |
| 3 | exp, nghịch đảo, căn, sigmoid, tanh inline; GRU, dense, depthwise bốn tổng độc lập một lượt | 16 317 µs | 18 033 µs |
| 4 | duỗi vòng GRU và dense, cache đúng cỡ thật (trạng thái 193 → 134 KB) | 16 364 µs | 18 111 µs |
| 5 | cache mỗi khối thành vòng đệm, bỏ `memmove` | 12 948 µs | 14 222 µs |
| 6 | cổng GRU tách thành vòng riêng, conv 1×1 và bước 2 bốn kênh × hai vị trí | 12 726 µs | 14 014 µs |
| 7 | GRU liên khung gộp 66 GRU một lượt, GRU nội tần bốn chuỗi song song | 12 386 µs | 13 838 µs |

Đếm chu kỳ theo khâu ở vòng 4, trạng thái ở RAM trong, mỗi bước 3,9 triệu chu kỳ:

| Khâu | Chu kỳ |
|---|---|
| dịch cache của 6 khối bằng `memmove`, cùng conv 1×1 thứ hai | 908 738 |
| GRU liên khung, 2 khối | 689 962 |
| GRU nội tần, 2 khối | 671 200 |
| conv 1×1 thứ nhất của 6 khối | 353 654 |
| `en0`, `en1` | 321 255 |
| 2 ConvTranspose | 248 841 |
| depthwise của 6 khối | 218 821 |
| chú ý theo thời gian của 6 khối | 151 794 |
| dense và LayerNorm, 2 khối | 188 064 |
| còn lại | 120 000 |

- Vòng 5 lấy lại gần đúng phần `memmove`: 3,1 triệu chu kỳ mỗi bước.
- Gần 5 100 sigmoid và tanh mỗi bước tốn khoảng 200 chu kỳ mỗi cái, kể cả các bước chuyển giữa thanh ghi float và
  thanh ghi nguyên. Gom chúng thành vòng dài ở vòng 7 chỉ lấy được 0,3 ms.
- Một bước 13,8 ms với trạng thái ở PSRAM, như sản phẩm sẽ đặt, cộng phần còn lại của chuỗi trên board C (5,4 ms có
  `aec`, bỏ 1,7 ms của OM-LSA), vượt nhịp 16 ms của nhân 1.

### 10.5 UL-UNAS học sẵn: gần sàn khi có sàn gain (25/11, E9-T19, ADR-0048)

UL-UNAS (Xiaobin-Rong/ul-unas, MIT, commit `00f7c700`), checkpoint DNS3 chính thức, chạy dòng từng bước trong khe `ns`
của chuỗi Python, như GTCRN ở §10.1. Nó học với cửa sổ Hann thường, nên vạch căn Hann của khe nhân 0,785, tỉ số tổng hai
cửa sổ, trước khi vào mạng. Bản chạy dòng của họ khớp bản offline tới 7,9e-7 so với đỉnh, bỏ 512 mẫu cuối, nơi bản offline
đệm số 0. Bản soi gương `tasks/ns/ulunas_mirror.py` khớp StreamULUNAS tới 2,7e-6 trên gain, qua 300 bước vạch ngẫu nhiên ở
−60 … 20 dB. Mục trọng số là 590 324 B.

`wake`, run `20261113_8ac386e_ffbc12`, hàng `kl`, 800‰, ba phiên "vừng ơi" của §10.1, script một lần
(`cache/scripts/ns_ulunas_wake.py`):

| Khe `ns` | 004 | 005 | 008 | Tổng | Lời, trung vị | Quãng lặng, trung vị |
|---|---|---|---|---|---|---|
| OM-LSA (sàn) | 9/11 | 12/12 | 12/12 | **33/35** | −47 / −44 / −54 dBFS | −70 / −69 / −73 dBFS |
| UL-UNAS, gain ≥ −12 dB | 8/11 | 12/12 | 12/12 | **32/35** | −47 / −46 / −53 dBFS | −71 / −70 / −73 dBFS |
| UL-UNAS | 6/10 | 12/12 | 12/12 | 30/34 | −49 / −50 / −55 dBFS | −94 / −91 / −99 dBFS |
| UL-UNAS, vạch không nhân 0,785 | 6/10 | 12/12 | 12/12 | 30/34 | −49 / −50 / −55 dBFS | −94 / −91 / −100 dBFS |
| GTCRN, gain ≥ −12 dB (§10.1) | 5/11 | 12/12 | 12/12 | 29/35 | — | −70 … −73 dBFS |
| GTCRN | 3/12 | 11/12 | 12/13 | 26/37 | −51 / −46 / −64 dBFS | −92 / −91 / −88 dBFS |

- Hai dòng OM-LSA và GTCRN chạy lại cùng script, ra đúng số của §10.1: script chấm như `wake-board`.
- Mẫu số đổi theo cách `vad` cắt lời trên mỗi lối ra: ở phiên 004 UL-UNAS không sàn chỉ cắt ra 10 lời.
- Không sàn, UL-UNAS dìm quãng lặng xuống −91 … −100 dBFS như GTCRN và hạ lời phiên 005 thêm 6 dB: nó vẫn khử vang muộn,
  đích học giữ 100 ms phản xạ đầu.
- Sàn −12 dB, đúng `G_min` của OM-LSA, đưa cả mức lời lẫn quãng lặng về ngang sàn; UL-UNAS kém sàn đúng một lần bắt, ở
  phiên 004, và hơn GTCRN cùng sàn 3 lần.
- Bỏ hệ số cửa sổ không đổi lần bắt nào: vào là log10 độ lớn, mức chỉ dời cả lối vào một hằng số.
- Khe `SRPIPE_NS=ulunas` của repo, với sàn chung của chuỗi (ADR-0048), ra lại đúng dòng có sàn, từng điểm đỉnh.

`command`, run `20261007_439d763-dirty_f438bd`, đường `ctc`, `--contract-pitch`, như §10.1, chạy CPU một luồng: dòng có sàn
bằng `SRPIPE_NS=ulunas` của repo, dòng không sàn bằng `cache/scripts/ns_ulunas_command.py`. Log ở
`cache/logs/ns_ulunas_{floor_,}command_20261125.log`:

| Khe `ns` | Đúng ở lần đoán tốt nhất | Lệnh kém nhất, δ₁ 200–1000 ‰ | Mọi lệnh | Lời không phải lệnh bị từ chối |
|---|---|---|---|---|
| OM-LSA (sàn, §10.1) | 192/215 | **60%** | 76% | 143–147/150 |
| UL-UNAS, gain ≥ −12 dB | **197/205** | 45% | 76% | 115–120/124 |
| UL-UNAS | 200/206 | 18% | 75% | 121–125/128 |
| GTCRN (§10.1) | 194/216 | 18% | 73% | 116–121/125 |

- Sàn đưa lệnh kém nhất từ 18% lên 45% và mọi lệnh lên ngang OM-LSA; lệnh kém nhất vẫn dưới sàn 15 điểm.
- Lời không phải lệnh bị từ chối 93–97%, so với 95–98% của OM-LSA.
- `command` học trên chuỗi có sàn: mạng nào cũng bị chấm bằng bộ nhận dạng đã quen OM-LSA.

### 10.6 UL-UNAS bằng C float32 trên S3 (25/11, E9-T20)

`ai_engine/src/ns_ulunas/ulunas.c`, trên phần float32 chung `ai_engine/src/ns_float/ns_float.h`, khớp bản soi gương từng bit:
trên máy tính ở cả hai ca của golden, trên board C ở 40/40 bước của `case_000`. Tách phần chung ra khỏi `gtcrn.c` không đổi
một bit nào của GTCRN: hai ca golden và ca checkpoint thật vẫn khớp trên máy tính. `make ai-unit-ulunas BOARD=C`, 200 bước
sau 20 bước khởi động, trọng số ở PSRAM, trạng thái 119 392 B:

| Bản | Trạng thái ở RAM trong | Trạng thái ở PSRAM |
|---|---|---|
| vòng lặp thẳng cho mọi conv, cache dịch bằng `memcpy` | 30 709 µs | 33 754 µs |
| 1: conv 1×1 bốn kênh × hai vị trí, AffinePReLU và xáo kênh lúc ghi; conv nhân quả trên hàng đệm sẵn số 0; ConvTranspose bước 1 chỉ kiểm biên ở mép, bước 2 nhân 3 lấy thẳng hai nhịp; GRU tần số của cTFA chạy xuôi và ngược cùng lúc | 24 180 µs | 27 447 µs |

Chu kỳ mỗi bước của vòng 1 theo khâu, đếm tạm bằng `esp_cpu_get_cycle_count`, trạng thái ở RAM trong:

| Khâu | Chu kỳ | Ghi chú |
|---|---|---|
| 2 khối GRU hai đường | 1 607 633 | như GTCRN, 6 336 sigmoid và tanh |
| GRU thời gian của cTFA, 10 khối | 1 581 871 | 353 KB trọng số GRU cộng 39 KB lớp dày, đọc một lần mỗi bước từ PSRAM: ~59 MB/s |
| conv 1×1 | 936 059 | 116 nghìn phép nhân-cộng, cùng hai tham số AffinePReLU mỗi lối ra |
| conv nhân quả và ConvTranspose | 836 121 | 48 nghìn phép nhân-cộng, cùng chép hàng đệm và cache |
| GRU tần số của cTFA | 819 735 | 3 120 sigmoid và tanh |
| attend, đặc trưng, mặt nạ | 231 622 | |

- Bản đầu của GTCRN cùng kiểu vòng lặp là 48 680 µs (§10.4), dù UL-UNAS đọc trọng số gấp 6 lần.
- Vòng 1 vẫn gấp 1,5 lần nhịp 16 ms. Cache dữ liệu của S3 đã ở mức lớn nhất (64 KB, 8 đường, dòng 64 B), PSRAM octal
  80 MHz, nên GRU thời gian bị chặn bởi tốc độ đọc PSRAM: mỗi trọng số chỉ dùng một lần mỗi bước.
- Gần 11 500 sigmoid và tanh mỗi bước, mỗi cái ~200 chu kỳ, là phần lớn còn lại.

Hai phép thử sau vòng 1, cùng phép đo, bản đếm chu kỳ dựng riêng:

| Thử | Trạng thái ở RAM trong | Khâu đổi | Kết luận |
|---|---|---|---|
| sigmoid và tanh bốn giá trị một lượt qua mảng trên stack, cùng phép tính từng giá trị, vẫn khớp từng bit | 29 789 µs | GRU hai đường 1,61 → 2,66 M; GRU tần số 0,82 → 1,41 M | chậm hơn: các làn nằm trong bộ nhớ thay vì thanh ghi; bỏ |
| cùng lượt ấy, thêm ROM dcache preload các trọng số GRU thời gian của mỗi khối từ đầu khối, 16 KB một vùng | (gộp ở trên) | GRU thời gian 1,58 → 1,12 M dù gánh sigmoid chậm hơn | preload có lợi, ~0,5 M chu kỳ mỗi bước 🔬 |

- Một bước của UL-UNAS chạm ~590 KB trọng số và ~120 KB trạng thái. Ở ~59 MB/s mà PSRAM của S3 đọc theo từng lần trượt cache,
  chỉ riêng phần đọc ấy đã cỡ 10 ms 🔬; tham số AffinePReLU theo từng kênh và từng vạch thêm ~96 KB mỗi bước.
- ROM preload là một bộ duy nhất cho dcache chung của hai nhân, nên `ns` trên nhân 1 dùng nó phải tránh các op esp-dl trên
  nhân 0 đang dùng (ADR-0026).

### 10.7 Hai mạng trong chuỗi thật trên board B (25/11, E9-T18)

Bản `bench` của sản phẩm, board B hai micro không loa (chuỗi `MM`), dựng bằng `make product NS=ulunas` rồi `NS=gtcrn`. NVS
`afe/ns_engine` đặt qua console (`nvs set afe ns_engine 1 -t u8`) cho bảng này; đổi lúc chạy bằng `SET_CONFIG` ở §10.7.1.
Log serial 45–50 s sau mỗi lần khởi động:

| Bản dựng | Dòng khởi động | Stack `sach_task` còn (của 6 144 B) | Watchdog |
|---|---|---|---|
| `NS=ulunas`, `afe/ns_engine 0` | `afe: ns floor (afe/ns_engine 0, net built)` | 4 400 B | không |
| `NS=ulunas`, `afe/ns_engine 1` | `afe: ns net (afe/ns_engine 1, net built)` | 1 456 B | IDLE1 không chạy, mỗi 5 s |
| `NS=gtcrn`, `afe/ns_engine 1` | `afe: ns net (afe/ns_engine 1, net built)` | 3 904 B | IDLE1 không chạy, 9 lần trong 50 s |
| không `NS` | `afe: ns floor (afe/ns_engine 0, net absent)` | — | không |

- Đường khởi động của công tắc đúng: NVS chọn sàn hay mạng, bản không mang mạng chạy sàn bất kể NVS.
- Cả hai mạng chiếm trọn nhân 1: UL-UNAS 24,2–27,4 ms (§10.6), GTCRN 12,4–13,8 ms (§10.4) cộng phần còn lại của chuỗi hai
  micro đã quá nhịp 16 ms. Watchdog của bản dựng chỉ cảnh báo, không khởi động lại, nên board không kẹt; chuỗi bỏ khung.
- Bản dựng S3 vì thế để `none`, như KẾ HOẠCH §3.9; board B trả về bản ấy. UL-UNAS dùng ~4,7 KB stack của `sach_task`.

#### 10.7.1 Đổi lúc chạy bằng `SET_CONFIG` (25/11)

Board B trên cùng mạng Wi-Fi với máy tính (`make wifi`), `make settings DEVICE=sr-3485188f7a70 KEY=afe/ns_engine VALUE=<v>`.

Từ chối, trên bản không `NS` (mạng vắng): `1` → `CONFIG_REFUSED`, `2` → `CONFIG_REFUSED`, `0` → nhận, không lỗi.

Đổi qua lại trên bản `NS=ulunas`, không khởi động lại giữa chừng; mỗi heartbeat phủ 30 s, tức 1 875 bước 16 ms. Giờ UTC:

| Heartbeat lúc | `ns` trong 30 s ấy | `framesDropped` tăng |
|---|---|---|
| 12:59:23, 12:59:54 | sàn | 0, 0 |
| 13:00:24 | sàn, UL-UNAS từ ~13:00:06 | 523 |
| 13:00:52, 13:01:22 | UL-UNAS | 923, 925 |
| 13:01:52 | UL-UNAS, sàn từ ~13:01:48 | 721 |
| 13:02:22, 13:02:53, 13:03:23 | sàn | 0, 0, 0 |
| 13:03:53 | sàn, UL-UNAS từ ~13:03:28 | 797 |
| 13:04:23 | UL-UNAS, sàn từ ~13:04:13 | 615 |
| 13:04:54 | sàn | 0 |

- Công tắc chạy đúng hai chiều: bật mạng thì bỏ khung ngay trong heartbeat ấy, về sàn thì heartbeat trọn kế tiếp bỏ 0.
- UL-UNAS trong chuỗi bỏ 49% số bước (923/1 875): `sach_task` xong một bước mất khoảng hai nhịp 16 ms, khớp 24,2–27,4 ms
  của mạng (§10.6) cộng phần còn lại của chuỗi.
- `cleanDropped`, `eventsDropped`, `streamDropped`, `dmaOverflows` giữ 0 suốt lượt; `uptimeS` đi liền 6 → 366 s; PSRAM
  trống giữ 3 505 840 B qua bốn lần đổi: đổi không cấp phát gì.
- `SET_CONFIG` ghi NVS: gửi `1` rồi reset board, dòng khởi động là `afe: ns net (afe/ns_engine 1, net built)`; gửi lại `0`.
- "Đổi không mất khung" chưa kiểm được trên S3: mạng tự bỏ khung vì quá nhịp, che mất phần do đổi gây ra. Kiểm ở board có
  mạng vừa nhịp (S31-T9 của fork `esp-sr-s31`).

### 10.8 Sigmoid và tanh bốn làn một lượt, cả hai mạng (25/11, E9-T16, E9-T20)

Bài đo riêng trên board B, 1 024 giá trị trong RAM trong, mỗi biến thể lấy lượt nhanh nhất trong 8 lượt; "khớp" là so từng
bit với bản đang dùng trên 204 800 giá trị trong [−90, 90]:

| Biến thể | Chu kỳ mỗi sigmoid | Khớp |
|---|---|---|
| bản đang dùng, inline, một giá trị một lượt | 141 | — |
| vòng đa thức và vòng Newton duỗi ra | 112 | có |
| thêm `floor.s`, chuyển bit bằng `wfr`/`rfr` thay vì qua stack | 107 | có |
| hai giá trị viết liền nhau | 105 | có |
| bốn làn viết xen theo từng bước, C thuần | 86,5 | có |
| bốn làn xen, thêm `floor.s`, `wfr`, `rfr` | 74,8 | có |
| bốn làn xen, bật `-fschedule-insns -fsched-pressure` | 97,0 | có |
| phép chia IEEE bằng `div0.s` … `divn.s` thay vì Newton | 101 | không: 41–150 lệch, ≤ 1 ulp |

- `madd.s` có độ trễ 4 chu kỳ, thông lượng 1 lệnh mỗi chu kỳ: chuỗi phụ thuộc đo 4,2–4,4 chu kỳ mỗi lệnh, bốn tổng độc lập
  1,01. Một sigmoid là chuỗi ~25 bước nối nhau, nên chỉ xen nhiều giá trị mới lấp được độ trễ.
- GCC không tự xen hai lời gọi inline cạnh nhau, và bộ xếp lệnh của nó làm chậm đi: các làn phải viết xen theo từng bước.

Đưa vào `ns_float.h`: `sigmoid_n`, `tanh_n` chạy bốn làn C thuần, phần lẻ chạy hàm một giá trị; cổng GRU, chú ý và mặt nạ
tính đối số vào mảng rồi gọi chúng; vòng đa thức và Newton duỗi ra. Bước `1 − 2y` của tanh phải nằm trong làn: tách ra
thành vòng riêng sau bốn làn chậm thêm ~100 µs mỗi bước. Không đổi phép tính nào, nên bản soi gương và golden giữ nguyên. Máy
tính: hai bài `ns_host` và `make parity-host` khớp từng bit, đối chứng âm vẫn đỏ. `make ai-unit-gtcrn`, `make ai-unit-ulunas`
trên board B, 200 bước sau 20 bước khởi động, golden 40/40 bước khớp:

| Mạng | Trước, RAM trong | Sau, RAM trong | Trước, PSRAM | Sau, PSRAM |
|---|---|---|---|---|
| GTCRN | 12 391 µs | 10 794 µs | 13 840 µs | 12 283 µs |
| UL-UNAS | 24 180 µs (board C) | 21 326 µs | 27 447 µs (board C) | 24 611 µs |

- Mốc của GTCRN đo lại trên board B: 12 391 / 13 840 µs, khớp board C (§10.4) trong 5 µs; UL-UNAS lấy mốc của board C.
- Phần còn lại: nhân ma trận–vector 2,25 chu kỳ mỗi phép nhân-cộng (5 lệnh nạp, 4 `madd.s` cho 4 hàng); GRU thời gian của
  UL-UNAS chờ PSRAM (§10.6).

### 10.9 Chu kỳ theo khâu, trần bus PSRAM, sigmoid và tanh ngắn hơn (26/11, E9-T16, E9-T20)

App đo riêng chèn bộ đếm chu kỳ quanh từng lời gọi của bản C sau §10.8, trạng thái ở PSRAM như sản phẩm, golden
`case_000` của từng mạng, 50 bước sau 20 bước khởi động. GTCRN 2,95 triệu chu kỳ mỗi bước, UL-UNAS 5,92 triệu:

| Khâu | GTCRN | UL-UNAS |
|---|---|---|
| GRU thời gian (cTFA của UL-UNAS, TRA của GTCRN), phần nhân ma trận–vector | 0,12 M | 1,32 M |
| hai khối GRU hai đường | 1,31 M | 1,30 M |
| trong đó cổng GRU, sigmoid và tanh | 0,69 M | 0,69 M |
| cổng của GRU tần số cTFA | — | 0,33 M |
| conv 1×1 | 0,30 M | 1,00 M |
| conv thời gian và ConvTranspose | 0,51 M | 0,69 M |
| chép hàng đệm và cache trong `temporal` | — | 0,24 M |
| depthwise | 0,27 M | — |

Trần bus PSRAM trên board B, 16 KB đọc sau khi đẩy khỏi cache:

| Cách đọc | Chu kỳ | MB/s |
|---|---|---|
| đọc bằng lần trượt cache | 43 871 | 89,6 |
| ROM preload rồi chờ xong | 43 121 | 91,2 |
| `memcpy` PSRAM → RAM trong | 44 636 | 88,1 |
| `memcpy` RAM trong → PSRAM rồi ghi ngược cache | 85 434 | 46 |

- ROM preload không nhanh hơn lần trượt cache: cả hai chạm cùng trần ~90 MB/s. Preload chỉ giấu được phần tính chồng lên
  phần đọc.
- Thử: GRU của mỗi khối tính theo đoạn 8 KB trọng số, preload đoạn sau trong lúc tính đoạn này. GRU thời gian của UL-UNAS
  1,283 → 1,247 M chu kỳ, GTCRN không đổi: tính một đoạn ~19 µs, đọc nó ~140 µs. Một khối GRU thời gian của UL-UNAS tới
  73,7 KB, lớn hơn dcache 64 KB, nên không preload trọn được từ đầu khối. Bỏ hướng preload cho hai mạng này.
- UL-UNAS đọc ~590 KB trọng số mỗi bước: riêng bus đã ~6,6 ms.

Sigmoid và tanh viết lại, bản soi gương trước (`gtcrn_mirror.sigmoid32`, `tanh32`): u = e^−|v| bằng exp minimax bậc 5
trên |s| ≤ ln2/2 (sai số tương đối 7,5·10⁻⁸, Taylor bậc 6 cũ 1,6·10⁻⁷), n làm tròn bằng cộng trừ 1,5·2²³; mẫu 1 + u
nằm trong (1, 2], nên nghịch đảo không tách số mũ: hạt giống bậc 2 (sai số 1,0·10⁻²) và hai bước Newton. Sigmoid là
1/(1 + u) khi v ≥ 0, u/(1 + u) khi v < 0; tanh là (1 − u)/(1 + u) với u = e^−2|v|, mang dấu của v. Bài đo riêng,
bốn làn, 204 800 giá trị:

| | Chu kỳ, bản §10.8 | Chu kỳ, bản mới | Sai số tuyệt đối lớn nhất, bản §10.8 | bản mới |
|---|---|---|---|---|
| sigmoid trên [−90, 90] | 84,6 | 64,3 | 8,7·10⁻⁸ | 9,5·10⁻⁸ |
| tanh trên [−50, 50] | 88,9 | 70,8 | 1,62·10⁻⁷ | 1,19·10⁻⁷ |
| tanh trên [−0,01, 0,01] | | | 1,17·10⁻⁷ | 7,8·10⁻⁸ |

Bản soi gương vẫn khớp StreamGTCRN và StreamULUNAS của PyTorch trong ngưỡng cũ trên trọng số ngẫu nhiên; golden hai mạng
sinh lại; `ns_host`, `make parity-host` khớp từng bit, đối chứng âm đỏ. Board B, golden 40/40 bước khớp:

| Mạng | §10.8, RAM trong | Mới, RAM trong | §10.8, PSRAM | Mới, PSRAM |
|---|---|---|---|---|
| GTCRN | 10 794 µs | 9 914 µs | 12 283 µs | 11 404 µs |
| UL-UNAS | 21 326 µs | 19 925 µs | 24 611 µs | 23 218 µs |
