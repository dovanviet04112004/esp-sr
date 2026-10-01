# Pilot kws và wake trên Speech Commands, so với pilot lệnh github

Đo ngày 01/10/2026 trên board B, một người nói (`spk_001`), phòng `home`. Số ở đây là số đo; số chưa đo đánh 🔬.

## 1. Câu hỏi

DS-CNN học 4 lệnh của kho github `kws_vi_command` (`p2`) nhận đúng 0 câu ở ba lệnh trên phiên thu qua board, dù trên
mẩu thử của chính tác giả kho nhận đúng 64–86%. Lỗi do đường xử lý và mô hình, hay do dữ liệu? Phép thử: giữ nguyên
đường, mô hình và thước, chỉ đổi dữ liệu sang Speech Commands v0.02 (tiếng Anh, 2 618 người nói, `DU_LIEU.md`).

## 2. Cách làm chung

- Đường: mô phỏng board của `configs/scenes/device.yaml` (phòng, vang, nhiễu SNR 0–30 dB, micro INMP441, `pcm_shift`
  13), chuỗi `dsp_afe` của sản phẩm, rồi 40 log-mel cộng 3 chiều cao độ; cửa sổ 94 bước kết thúc ở bước `vad` tắt.
- Mô hình DS-CNN cỡ S, 10 000 bước lô 128, SpecAugment; ngưỡng từ chối và khoảng cách chọn trên `val` để từ chối ≥ 95%
  `other` và `silence` (KẾ HOẠCH §3.12).
- Thước board: `python -m srpipe.tasks.command.eval kws <run>`; mỗi câu `vad` tìm được chấm một lần ở bước `vad` tắt.
  Phiên tính: lệnh tiếng Việt 28/09 (10 lệnh, 1 m và 3 m, 5 lần mỗi phiên), câu gần âm, nói tự do, nhiễu; và 10 phiên
  tiếng Anh 01/10 (`host/plans/keywords_en.tsv`: yes, no, up, down, left, right, on, off, stop, go; 1 m, 5 lần, 15 s;
  phiên "go" chỉ thu được đoạn cuối).
- Mọi pilot không dùng TTS.

## 3. Ba pilot DS-CNN

| Pilot | Run (`ml/artifacts/command_kws/runs/`) | Lớp lệnh | Mẩu train mỗi lệnh | Người nói train mỗi lệnh | Lượt mô phỏng |
|---|---|---|---|---|---|
| `p2` | `20261001_01cbacd-dirty_5c7d79` | bật đèn, tắt đèn, bật quạt, tắt quạt | 200 | một hoặc vài, không rõ | 2 |
| `p4` | `20261001_03ea25b-dirty_520921` | 10 từ của bộ | 200 | ~181 | 1 |
| `p5` | `20261001_03ea25b-dirty_dff624` | yes, no | 3 228 và 3 130 | 1 305 và 1 266 | 1 |

`other` của `p2` là "bật hết", "tắt hết" của kho github và 1 giờ lời nói thường; của `p4`, `p5` là các từ còn lại của bộ
(1 200 và 2 500 mẩu). `silence` của cả ba là nhiễu MUSAN, DEMAND; `p2` thêm nhiễu phòng của kho github.

### 3.1 Val

`val` của `p2` là mẩu thử của chính tác giả kho github; của `p4`, `p5` là `testing_list.txt` của bộ, người nói không có
trong `train`.

| Pilot | Ngưỡng (‰) | Đúng (mọi lớp) | Nhận đúng mỗi lệnh | Nhầm lệnh khác |
|---|---|---|---|---|
| `p2` | 300, 10 | 84,2% | bật đèn 82,6%; tắt đèn 63,6%; bật quạt 86,4%; tắt quạt 74,0% | 14,7% |
| `p4` | 620, 450 | 69,9% | trung bình 51,9%: yes 71, no 51, up 54, down 56, left 48, right 69, on 51, off 36, stop 62, go 21 | 5,8% |
| `p5` | 780, 0 | 91,2% | yes 92,6%; no 83,0% | 0,4% |

### 3.2 Board

| Pilot | Lệnh nhận đúng | Từ chối đúng |
|---|---|---|
| `p2` | bật đèn 0/12, tắt đèn 0/11, bật quạt 10/11, tắt quạt 0/11 | 88/93: lệnh khác 64/67, gần âm 22/24, nhiễu 2/2 |
| `p4` | yes 4/6, no 2/7, up 6/6, down 0/7, left 0/7, right 1/7, on 5/5, off 5/7, stop 2/7, go 0/1 | 109/138: phiên lệnh 85/112, gần âm 22/24, nhiễu 2/2 |
| `p5` | yes 5/6, no 7/7 | 173/185: phiên lệnh 149/159, gần âm 22/24, nhiễu 2/2 |

`p2` chấm trước khi có phiên tiếng Anh. Câu `vad` tìm được có thể nhiều hơn số lần nói: một câu bị tách đôi được tính
hai lần.

- `p5`: 5 lần nói "yes" đều ra `yes` ở 851–1000‰, câu thứ sáu là mảnh `vad` cắt thừa, bị từ chối; 7/7 câu "no" ở
  998–1000‰. Lọt: "down" thành `no` 5/7, "go" thành `no` 1/1, "chụp ảnh" 3 m thành `no` 3/5, hai câu gần âm.
- `p2` trượt cả mẩu người thật của người khác: 58 mẩu HF chưa từng học, qua cùng đường mô phỏng, bật đèn 7/15, tắt đèn
  22/40, bật quạt 2/2, tắt quạt 1/1; cùng lúc mẩu thử của tác giả bật đèn 38/46, tắt đèn 28/44, bật quạt 38/44, tắt
  quạt 37/50.

### 3.3 Cửa sổ board so với cửa sổ mô phỏng (4 lệnh của `p2`)

Trung bình trên các bước `vad` bật của cửa sổ; mức log-mel quy ra dB.

| | Train tác giả | Thử tác giả | Mẩu HF | Board |
|---|---|---|---|---|
| Cửa sổ | 1 600 | 184 | 58 | 45 |
| `vad` bật trong cửa sổ | 971 ms | 904 ms | 761 ms | 742 ms |
| Mức tiếng nói | −49,6 dB | −50,9 dB | −51,4 dB | −49,9 dB |
| Nền | −58,9 dB | −58,9 dB | −58,7 dB | −58,4 dB |
| Nghiêng phổ (dải 30–39 trừ 0–9) | −17,1 dB | −16,9 dB | −15,5 dB | −20,6 dB |
| POV, độ lệch log F0 | −0,28; 0,69 | −0,38; 0,59 | −0,39; 0,67 | −0,37; 0,56 |

Board trừ train tác giả, theo nhóm 5 dải từ thấp lên: +6,4 dB ở dải 5–9 (tâm ~330–610 Hz), −3,5 và −4,2 dB ở dải 15–24
(tâm ~1,2–2,5 kHz), các nhóm khác trong ±1,2 dB. Trừ 240 ms `vad` giữ thêm sau câu, lệnh của tác giả kho github dài
~730 ms tiếng, của người nói board và HF ~500–520 ms: tác giả nói chậm hơn khoảng 30%.

## 4. Pilot wake "yes"

TCN của `wake` (KẾ HOẠCH §3.11), cùng công thức `v4`, chỉ đổi dữ liệu: dương là mọi mẩu "yes" của bộ (3 228 train, 419
`val`), âm bản khó là các từ còn lại (3 000 train, 600 `val`), âm bản thường là của `wake/v4` (100 giờ). Split
`wake/yes1`. Kết quả 🔬, chờ run xong.

## 5. Điều số đo cho thấy

- Cùng đường mô phỏng, cùng chuỗi trên board, cùng thước: DS-CNN học từ hàng nghìn người nói nhận giọng board của người
  nói `spk_001` ở 851–1000‰ (`p5`). Đường xử lý và mô hình không làm hỏng nhận dạng.
- Số mẩu mỗi lệnh quyết định: cùng từ "no", cùng phiên board, 200 mẩu (`p4`) nhận 2/7, 3 130 mẩu (`p5`) nhận 7/7.
- 200 mẩu một người nói (`p2`) không ra được giọng khác: trượt người nói HF và board.

## 6. Chạy lại

Mỗi pilot: `data split`, `data simulate`, rồi `train` của `srpipe.tasks.command.kws` với cùng các `--set`, rồi thước board.

```bash
cd ml
# p2
S="--set split.version=p2 --set 'commands=[bat_den, tat_den, bat_quat, tat_quat]' --set split.tts=false \
   --set split.hf_extract=null --set 'split.other_hours={train: 1.0, val: 0.2}' \
   --set 'split.silence.count={train: 600, val: 100}' \
   --set 'split.kws_vi_command.val_folders=[speech/kws_vi_command/dataset/data_test/data_1s]' \
   --set 'simulate.repeats={real: 2}'"
# p5; p4 đổi keywords thành 10 từ, clips {train: 200, val: 100}, other_clips {train: 1200, val: 300}
S="--set split.version=p5 --set \"speech_commands={dir: speech/speech_commands, keywords: ['yes', 'no'], \
   clips: {train: 5000, val: 1000}, other_clips: {train: 2500, val: 300}}\" \
   --set 'split.silence.count={train: 600, val: 100}' --set 'simulate.repeats={real: 1}'"
eval uv run python -m srpipe.tasks.command.kws.data split $S
eval uv run python -m srpipe.tasks.command.kws.data simulate $S
eval uv run --extra train python -m srpipe.tasks.command.kws.train $S --set train.steps=10000 --set train.eval_every=2000
uv run --extra train python -m srpipe.tasks.command.eval kws artifacts/command_kws/runs/<run>
```

YAML đọc `yes`, `no`, `on`, `off` không có nháy là boolean: từ khoá phải có nháy.
