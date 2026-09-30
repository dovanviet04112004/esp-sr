# Bộ TTS trên máy tính (E11-T7)

## 1. Pilot: ai đọc được "chào mi na"

`make eval-tts` (`srpipe.tasks.wake.synth pilot`) tại `ddbf97d`. Bộ và bản ghim ở `ml/configs/common/tts.yaml`,
chữ, giọng và seed ở mục `synth` của `ml/configs/models/wake.yaml`.

| Mục | Giá trị |
|---|---|
| Máy | RTX 3050 Laptop 4 GB, cả ba bộ trên GPU |
| VieNeu-TTS v3 Turbo | PyTorch, float32 (độ chính xác của checkpoint), 48 kHz; 25 giọng có sẵn và 5 giọng nhân bản; không gieo seed |
| F5-TTS ViVoice | f5-tts 1.1.22, float16 (mặc định của nó trên GPU), 24 kHz; 5 giọng nhân bản × seed 0, 1, 2; độ dài theo nhịp âm tiết của câu mẫu |
| Giọng nhân bản | VIVOS `train`: VIVOSSPK01, 05, 12, 27, 40; mẩu đầu tiên dài 4–7,5 s của mỗi người, kèm câu của nó |
| Chấm | PhoWhisper-large, float16, nghe lại từng mẩu. **Qua** khi chữ nghe được trùng chữ đưa vào sau khi bỏ hoa/thường, khoảng trắng và dấu câu, **giữ dấu thanh**: "Chào Mina!" trùng "chào mi na", "chào mí na" thì không |
| Thời gian | VieNeu 60 mẩu ~45 s kể cả nạp; F5 ~4,5 s mỗi mẩu; PhoWhisper ~3 phút mỗi lượt, phần lớn là nạp 3 GB từ E: |

| Bộ | Giọng | Chữ đưa vào | Qua PhoWhisper | Dài (s): ngắn nhất / trung vị / dài nhất |
|---|---|---|---|---|
| VieNeu | 25 có sẵn | chào mi na | 17/25 | 0,64 / 0,96 / 1,84 (cả hai câu) |
| VieNeu | 25 có sẵn | Chào Mina! | 14/25 | |
| VieNeu | 5 nhân bản | chào mi na | 3/5 | 0,88 / 0,96 / 1,20 (cả hai câu) |
| VieNeu | 5 nhân bản | Chào Mina! | 4/5 | |
| F5 | 5 nhân bản × 3 seed | chào mi na | 11/15 | 0,62 / 0,69 / 0,89 |

## 2. Đọc bảng

- Hai bộ ngang nhau ở cỡ mẫu này: 62–73% số mẩu qua, trong khi 15 mẩu chỉ cho khoảng tin cậy rộng ±20 điểm. Không bỏ
  bộ nào: bộ dương dùng cả hai; mẩu nào được giữ thì theo §3.
- F5 tự tính độ dài theo số byte UTF-8 của hai câu. Chữ có dấu tốn 2–3 byte nên "chào mi na", ít dấu hơn câu mẫu VIVOS,
  chỉ dài 0,34–0,48 s và qua 0/15 ("chào mấy", "chào mẹ"). Tính theo nhịp âm tiết của câu mẫu thì dài 0,62–0,89 s
  và qua 11/15.
- VieNeu, giọng có sẵn: 8 giọng qua cả hai câu, 15 giọng qua một, Quang Sơn (Trung) và Thanh Bình trượt cả hai. Nghe
  nhầm hay gặp nhất là "chào mỹ na" (4 lần, lệch thanh ở "mi") và "chào min à" (4 lần, nối "min" với "a").
- VieNeu chưa gieo seed nên mỗi lần chạy mỗi khác: cùng cấu hình được 37 (ONNX trên CPU), 34 và 38 trên 60 mẩu.
- PhoWhisper nghe ba âm tiết không có ngữ cảnh. Một mẩu trượt có thể do bộ nghe chứ không do bộ đọc, và bảng này chưa
  tách được hai lỗi đó; muốn tách thì phải nghe thật. Cách lọc vẫn đúng hướng: mẩu không qua thì không vào tập học.
- Mẩu nằm ở `$SRPIPE_DATA_ROOT/interim/wake/synth_pilot/{vieneu,f5}/`; `manifest.yaml` ghi chữ nghe được, độ dài và
  sha256 của từng mẩu.

## 3. Bộ đủ: dương và âm bản gần âm

`make wake-synth` tại `a597aa7`: sàng lọc (`data_screen.md`), sinh `positives` và `negatives`, rồi `select`. Cấu hình ở
mục `synth` của `ml/configs/models/wake.yaml`.

| Mục | Giá trị |
|---|---|
| Giọng nhân bản | 356 giọng, chỉ lấy từ vật liệu học (KẾ HOẠCH §1.3) và chỉ từ mẩu đã qua sàng lọc: 46 người nói VIVOS `train`, 80 mẩu FPT, 80 VLSP, 150 Bud500. Mẩu parquet rút theo seed 20260928, mỗi lượt vào một tệp lấy một mẩu ở một nhóm dòng ngẫu nhiên |
| Dương | VieNeu: 25 giọng có sẵn × 2 chữ × seed 0–2, và 356 giọng nhân bản × 2 chữ × seed 0–1. F5: 356 giọng nhân bản × 2 chữ × tốc độ 0,85 / 1,0 / 1,15. Tổng 3 710 mẩu |
| Âm bản gần âm | 69 cụm: 34 cụm của kho cách "chào mi na" ≤ 3 thành phần âm tiết, 30 cụm "chào X Y" hay gặp nhất, 5 cụm viết tay. Mỗi cụm do 6 giọng VieNeu và 4 giọng F5 đọc, rút theo seed. Tổng 690 mẩu |
| Chấm | PhoWhisper-large nghe lại từng mẩu, rồi chấm log-xác suất của chữ phải nói và của "chào mi na" khi biết mẩu. **Độ chênh** = log-xác suất của chữ nghe được trừ log-xác suất của chữ đích |
| Ngưỡng | phân vị 1% của độ chênh tới "chào mi na" trên các âm bản nghe đúng chữ của chính nó: **15,40 nat**. Nghĩa là chỉ 1% âm bản gần âm lọt thành dương |
| Giữ | dương: nghe đúng chữ, hoặc độ chênh ≤ ngưỡng. Âm bản: nghe đúng chữ của nó, và độ chênh tới "chào mi na" > ngưỡng. Mẩu trùng byte với một mẩu đã giữ thì bỏ (lần này không có) |

| Bộ | Nguồn giọng | Dương | Nghe đúng chữ | Giữ | Âm bản | Giữ |
|---|---|---|---|---|---|---|
| F5 | VIVOS | 276 | 212 (77%) | 275 | 38 | 30 |
| F5 | Bud500 | 900 | 667 (74%) | 864 | 119 | 78 |
| F5 | FPT | 480 | 290 (60%) | 465 | 63 | 31 |
| F5 | VLSP | 480 | 240 (50%) | 456 | 56 | 24 |
| VieNeu | VIVOS | 184 | 134 (73%) | 182 | 58 | 37 |
| VieNeu | Bud500 | 600 | 369 (62%) | 590 | 167 | 92 |
| VieNeu | có sẵn | 150 | 75 (50%) | 142 | 19 | 8 |
| VieNeu | FPT | 320 | 136 (43%) | 303 | 88 | 46 |
| VieNeu | VLSP | 320 | 129 (40%) | 307 | 82 | 38 |
| **Tổng** | | **3 710** | **2 252 (61%)** | **3 584** | **690** | **384** |

- **F5 rõ hơn VieNeu**: nghe đúng chữ 66% so với 54%. Nghe thật cũng cho cùng nhận xét, nên bộ dương dựa nhiều vào F5.
  F5 đọc chậm thì rõ hơn: 71% ở tốc độ 0,85, 66% ở 1,0, 61% ở 1,15. Chữ "chào mi na" rõ hơn "chào mi na!": F5 76% so
  với 56%, VieNeu 58% so với 49%.
- **Giọng mẫu sạch thì bản nhân bản rõ**: VIVOS và Bud500 cho 62–77% mẩu nghe đúng chữ, FPT và VLSP chỉ 40–60%. Mẩu mẫu
  của hai kho này ồn hơn, và bộ TTS chép cả tiếng ồn.
- **1 332 mẩu dương được giữ dù PhoWhisper nghe không đúng hẳn**. Độ chênh của chúng: trung vị 2,35 nat, phân vị 90%
  là 10,45 nat, trong khi âm bản gần âm có trung vị 34,5 nat. Chữ nghe được phần lớn là cách viết khác của cùng một
  tiếng: "chào mỹ na" 154 mẩu, "chào minah" 86, "chào mỹ nam" 76, "chào minào" 50, "chào minh na" 43. Bị bỏ là những
  mẩu nghe ra câu khác hẳn: "với", "chào mẹ", "cabina".
- **Giọng mẫu câm**: ba mẩu FPT Set002 toàn số 0 từng làm giọng mẫu, và F5 nhân bản chúng thành tiếng rác bão hoà,
  giống hệt nhau từng byte với mọi câu. Từ khi có sàng lọc, giọng mẫu chỉ rút từ mẩu không bị loại. Chỉ các giọng
  thay thế phải sinh lại: 40 mẩu dương và 12 mẩu âm, nhờ dấu vân tay của từng mẩu (`work/made.jsonl`).
- Tốc độ trên RTX 3050 4 GB: F5 khoảng 2,2 s mỗi mẩu, PhoWhisper khoảng 0,5 s mỗi mẩu. Kết quả nghe được lưu theo
  sha256 của mẩu ở `cache/tts/heard.jsonl`, nên chạy lại chỉ nghe mẩu mới.
- Mẩu nằm ở `$SRPIPE_DATA_ROOT/interim/wake/synth_{pos,neg}/{vieneu,f5}/`; `manifest.yaml` ghi mỗi mẩu: chữ, giọng,
  seed, tốc độ, chữ nghe được, độ chênh, `kept`, sha256. Không mẩu nào vào tập thử (KẾ HOẠCH §1.3).

## 4. Bộ nghe kiểm nhanh cho việc cắt mẩu (29/09)

Bước `cut` của `srpipe.core.extract` cho PhoWhisper-large nghe lại từng mẩu cắt, mỗi mẩu được đệm thành cửa sổ 30 s.
Trên RTX 3050 Laptop 4 GB, bản float16 qua transformers nghe khoảng **1 mẩu/giây**, và với hàng chục nghìn cụm thì đây
là nút thắt của cả chuỗi trích. Cùng trọng số (`vinai/PhoWhisper-large@b9136a44`) được chuyển sang CTranslate2
int8_float16 (1,55 GB, bằng nửa bản float16), chạy tham lam, chỉ lấy chữ (`ml/tts/asr_ct2`).

400 mẩu cắt mà bản float16 đã nghe ra đúng cụm, rút ngẫu nhiên (seed 0), vuốt 10 ms và đệm 0,3 s lặng như bước `cut`:

| Lô | Thời gian cho 400 mẩu, tính cả nạp mô hình | Mẩu/giây | Cùng chữ với bản float16 | Vẫn nghe ra đúng cụm |
|---|---|---|---|---|
| 1 | 224 s | 1,8 | 392 | 393 |
| 2 | 206 s | 1,9 | 394 | 394 |
| **4** | **197 s** | **2,0** | **394** | **394** |
| 8 | hết bộ nhớ GPU | — | — | — |

Sáu mẩu lệch đều là bản int8 nghe sai thứ bản float16 nghe đúng ("dừng lại" thành "đứng lại" hai lần, "đóng cửa" thành
"đống cửa", một lần nghe ra cả câu không có trong mẩu), tức nó chỉ bỏ oan chừng 1,5% mẩu tốt. Phép đo chưa có mẩu mà
bản float16 từ chối, nên chưa biết bản int8 có nhận nhầm mẩu nào bản float16 bỏ hay không. Bước `cut` dùng bản int8,
lô 4 (`asr_fast` của `ml/configs/common/tts.yaml`); bước chọn mẩu TTS vẫn dùng bản float16, vì cần log-xác suất.

## 5. Pilot lệnh (30/09): VieNeu sạch, F5 cụt đuôi và chạm trần

`make command-synth-pilot` tại `f06b90d` (`srpipe.tasks.command.synth pilot`, mục `synth.pilot` của
`configs/models/command.yaml`): ba lệnh "bật đèn", "tăng âm lượng", "dừng lại" ở hai dạng chữ, 5 giọng có sẵn và 8 giọng
nhân bản, 4 âm bản mỗi loại (gần âm, mở đầu, nửa lệnh, danh sách tay); 254 mẩu trên RTX 3050 4 GB. Mép đo bằng
`clips.edges`: khung 10 ms, im lặng là dưới đỉnh 40 dB.

| Bộ | Mẩu | Sinh (s, kể cả nạp) | Nghe lại (s, kể cả nạp) | Chạm trần | 30 ms cuối so với đỉnh, trung vị | Lặng đầu / cuối, trung vị (s) |
|---|---|---|---|---|---|---|
| VieNeu | 126 | 57,9 | 103,8 | 0 | −90 dB | 0,03–0,06 / 0,12–0,27 |
| F5 | 128 | 331,5 | 130,2 | 18 mẩu | **−22 dB** | 0,06–0,15 / **0,00** |

| Lệnh | VieNeu giọng có sẵn | VieNeu nhân bản | F5 nhân bản |
|---|---|---|---|
| bật đèn | 2/10 ("bật đền", "bớt đen") | 9/16 | 18/32 |
| tăng âm lượng | 10/10 | 12/16 | 26/32 |
| dừng lại | 10/10 | 16/16 | 10/32 ("đừng lại", câu dài không liên quan) |

F5 đặt độ dài theo tốc độ đọc của câu mẫu, nên một lệnh hai âm tiết chỉ được khoảng 0,35 s: mẩu dừng khi âm cuối còn
kêu (30 ms cuối chỉ dưới đỉnh 22 dB), và có mẩu bị đọc lan ra câu khác. Ghi PCM 16 bit không chặn đỉnh nên 18 mẩu chạm
trần. Sửa ở `ml/tts/f5/run.py` với `engines.f5.timing` của `common/tts.yaml`: mỗi âm tiết ít nhất 0,3 s (VieNeu đọc lệnh
khoảng 0,26 s một âm tiết, trừ lặng hai đầu), thêm 0,25 s đuôi, đỉnh chặn ở −1 dBFS. Pilot F5 phải chạy lại để nghe
trước khi chạy bộ đủ; đổi `run.py` và cấu hình cũng đổi dấu vân tay của mẩu F5 của `wake`, nên chạy lại `wake-synth` sẽ
sinh lại chúng.

Pilot F5 chạy lại sau bản sửa (`f7f07c4`), cùng giọng, cùng chữ; mẩu VieNeu giữ nguyên dấu vân tay nên không sinh lại:

| F5 | Trước sửa | Sau sửa |
|---|---|---|
| "bật đèn" nghe đúng | 18/32 | **31/32** |
| "tăng âm lượng" nghe đúng | 26/32 | **32/32** |
| "dừng lại" nghe đúng | 10/32 | 16/32, 13 mẩu nghe thành "đừng lại" |
| âm bản nghe đúng | 15/32 | 26/32 |
| chạm trần | 18 mẩu | 0, đỉnh −1 dBFS |
| 30 ms cuối so với đỉnh, trung vị / tệ nhất | −22 dB / −2,6 dB | −48 dB / −17 dB |
| độ dài trung vị | 0,54 s | 0,91 s, lặng đầu 0,26–0,38 s |
| sinh (kể cả nạp) | 331,5 s | 378,6 s cho 128 mẩu, khoảng 2,9 s một mẩu |

Không mẩu nào còn đọc lan ra câu khác. "đừng lại" nằm trong âm bản gần âm của "dừng lại", nên ngưỡng độ chênh của `select`
quyết mẩu nào đủ gần "dừng lại" để giữ. Bộ đủ theo cấu hình hiện tại: dương 7 668 mẩu VieNeu và 6 318 mẩu F5 cho 9 lệnh,
351 giọng nhân bản (41 người VIVOS mà split `command` giữ ở `train`, cộng 310 mẩu parquet) và 25 giọng có sẵn; âm bản 84 cụm
(26 gần âm, 35 mở đầu, 16 nửa lệnh, 7 danh sách tay) × 10 giọng = 840 mẩu. Theo tốc độ pilot (VieNeu khoảng 0,9 s, F5
khoảng 3,5 s một mẩu kể cả nghe lại) là khoảng 8,5 giờ trên RTX 3050 🔬.
