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
