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
  bộ nào: bộ dương dùng cả hai, và chỉ giữ mẩu qua.
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
