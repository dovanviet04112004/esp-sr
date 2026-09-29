# `wake` (E11)

Mục 1–5 đo từ đánh thức "chào mi na" (ADR-0007), đã thay bằng "trợ lý" (ADR-0011); các bài học về mốc, ngưỡng,
âm bản khó và việc phụ CTC dùng tiếp cho từ mới.

## 1. `wake/v1`: lượt học đầu

Run `ml/artifacts/wake/runs/20260929_08b3d06-dirty_b33b0b` (`make wake-train` tại `08b3d06`; phần chưa commit của cây là
`bss`, không đụng `wake`). Split `wake/v1` (`ml/data/splits/wake/v1/SPLIT.md`): 3 466 mẩu dương TTS mô phỏng 8 lượt,
100 giờ lời nói của kho học cộng 370 mẩu gần âm TTS làm âm bản. Mục `train` của `ml/configs/models/wake.yaml`: 20 000
bước, lô 128, 25% cửa sổ dương, cửa sổ 256 bước, seed 20260928. Học hết khoảng 10 phút trên RTX 3050 4 GB.

| Tập | Ngưỡng | Bắt | Báo nhầm |
|---|---|---|---|
| `val`: 118 mẩu dương TTS, 3,76 giờ âm bản đã mô phỏng | 0,975 | **77,1%** (91/118) | 0,80/giờ (3 lần) |
| `test_neg`: Common Voice + VIVOS `test`, 26,49 giờ đã mô phỏng | 0,975 | chưa có `test_pos` | **0,45/giờ** (12 lần) |

Ngưỡng là điểm làm việc chọn trên `val`: bắt nhiều nhất mà báo nhầm ≤ 1/giờ. Giữa các mốc 1 000 bước, bắt trên `val` nhảy
từ 41% tới 77% trong khi loss giảm đều, vì chỉ vài âm bản điểm cao đã đẩy ngưỡng lên 0,915–0,985. Mạng giữ là mốc
18 000. Các âm bản ấy phần lớn là câu đọc số:

| Âm bản `val_neg` | Điểm | Câu |
|---|---|---|
| VIVOSSPK13_229 | 0,994 | sáu mươi bốn sáu mươi lăm |
| VIVOSSPK15_228 | 0,986 | sáu mươi hai sáu mươi ba |
| VIVOSSPK13_256 | 0,983 | đây là vòng đua cuối cùng |
| VIVOSSPK15_223, VIVOSSPK15_238, VIVOSSPK13_239 | 0,93–0,96 | năm/tám mươi hai … ba, tám mươi bốn … lăm |

## 2. `wake/v1` trên bản thu qua board B

`cd ml && uv run --extra train python -m srpipe.tasks.wake.eval board artifacts/wake/runs/20260929_08b3d06-dirty_b33b0b`,
cấu hình mục `eval.board` của `ml/configs/models/wake.yaml`. Phiên của `ml/data/manifests/device/board_b.csv` ở
`pcm_shift` 13: thu 28/09 ở nhà, một người nói (phiếu C001), firmware `capture@contract-v1-368-gc786e8f`. Mỗi phiên đi
qua chuỗi của sản phẩm (`device.listen`: `dsp_afe`, hệ số cân bằng của board, log-mel). Một đoạn là một chuỗi `vad`
liền, nối qua chỗ ngắt ≤ 0,4 s, dài ≥ 0,25 s; điểm của đoạn là đỉnh điểm làm trơn từ đầu đoạn tới 0,3 s sau cuối.

| Phiên | Nói | Khoảng cách, hướng | Đoạn | Trung vị | Đỉnh | ≥ 0,975 | ≥ 0,9 | ≥ 0,85 |
|---|---|---|---|---|---|---|---|---|
| 034 | chào mi na | 1 m, 90° chính diện | 11 | 0,87 | 0,94 | 0 | 5 | 7 |
| 035 | chào mi na, giọng nhỏ | 1 m, 90° | 12 | 0,68 | 0,88 | 0 | 0 | 2 |
| 036 | chào mi na | 1 m, 0° dọc trục mic | 14 | 0,74 | 0,88 | 0 | 0 | 5 |
| 037 | chào mi na | 3 m, 90° | 12 | 0,84 | 0,95 | 0 | 3 | 6 |
| 038 | chào mi na | 3 m, 45° | 11 | 0,74 | 0,94 | 0 | 2 | 2 |
| **Năm phiên từ đánh thức** | | | **60** | **0,77** | | **0** | **10** | **22** |
| 039 | mười cụm gần âm, mỗi cụm hai lần (§3.3) | 1 m, 90° | 20 | | 0,92 | 0 | 1 | 5 |
| 032 | lệnh và cụm gần lệnh ("bật điện", "đóng góp"…) | 1 m, 90° | 11 | | 0,21 | 0 | 0 | 0 |
| 033 | nói tự do | 1 m, 90° | 13 | | 0,28 | 0 | 0 | 0 |

Báo nhầm trên 39 phiên không có từ đánh thức (0,39 giờ: 20 phiên lệnh, 3 phiên âm bản gồm 039, 3 phiên ồn, 13 phiên phát
loa): **0** ở ngưỡng 0,975.

- **Không bắt được lần nào.** Đỉnh mỗi phiên nói đúng từ chỉ 0,88–0,95, dưới ngưỡng 0,975 chọn trên `val`.
- **Hạ ngưỡng không cứu được.** Ở 0,9, bắt 10 đoạn và một cụm gần âm qua. Ở 0,85, bắt 22 đoạn nhưng 5/20 lần đọc cụm gần
  âm cũng qua.
- Phiên nói nhỏ (035) và phiên nói dọc trục mic (036) thấp nhất.
- Mỗi phiên có 11–14 đoạn `vad`, nhiều hơn số lần nói. Các đoạn điểm dưới 0,2 có lẽ là tiếng thở hay tiếng động; chưa
  nghe lại để chắc.

## 3. Nguyên nhân, không dùng bản tự thu để học

Mọi phép dò chấm bằng mạng của §1, bản thu board đi qua cùng chuỗi như ở §2. §3.1, §3.2 và §3.4 chạy bằng script
một lần ngoài repo; chỉ lệnh ở §2 dựng lại được từ repo.

### 3.1 Mức: không phải; phổ lệch cỡ hai nhóm mô phỏng với nhau

Thống kê trên các bước có tiếng (`vad` = 1). Log-mel là log tự nhiên của công suất, một đơn vị ≈ 4,3 dB. Cột dải là
trung bình log-mel của 8 trong 40 dải, trừ đi của mẩu dương mô phỏng.

| Nhóm | Bước có tiếng | Mức | Gain `agc` | Log-mel TB | Chênh 8 dải so với dương mô phỏng |
|---|---|---|---|---|---|
| Thật, "chào mi na" 1 m (034, 036) | 37% | −54 dBFS | +21 dB | −9,78 | +2,1 +1,0 +1,6 −0,7 −0,8 −0,4 +1,1 +0,0 |
| Thật, "chào mi na" 3 m (037, 038) | 32% | −54 dBFS | +19 dB | −9,83 | +2,0 +1,8 +1,6 −0,7 −1,1 −0,6 +0,8 +0,0 |
| Loa phát VIVOS 1 m (040) | 57% | −55 dBFS | +19 dB | −9,26 | −0,4 +0,5 +3,1 −0,5 −0,7 +1,3 +2,6 +1,5 |
| Dương TTS mô phỏng (`train_pos`) | 67% | −57 dBFS | +15 dB | −10,10 | 0 |
| Âm bản kho mô phỏng (`train_neg`) | 78% | −56 dBFS | +22 dB | −8,78 | +1,4 +2,2 +1,8 +1,6 +0,9 +1,2 +1,3 +0,4 |

Giọng thật lệch dương mô phỏng tới 2,1 đơn vị ở các dải thấp, cỡ độ lệch giữa hai nhóm mô phỏng với nhau (tới 2,2);
chưa thử bù riêng từng dải. Dời cả log-mel của bản thu đi một mức cố định không đưa lần nói nào qua ngưỡng:

| Dời | 034: trung vị điểm, số lần ≥ 0,975 | 037: trung vị, số lần ≥ 0,975 | 039: đỉnh |
|---|---|---|---|
| −9 dB | 0,738, 0/11 | 0,758, 0/12 | 0,895 |
| −6 dB | 0,838, 0/11 | 0,817, 0/12 | 0,909 |
| −3 dB | 0,864, 0/11 | 0,825, 0/12 | 0,925 |
| 0 | 0,865, 0/11 | 0,835, 0/12 | 0,922 |
| +3 dB | 0,720, 0/11 | 0,837, 0/12 | 0,919 |

### 3.2 Giọng TTS và giọng người: không phải

Cặp đối chứng: 38 câu VIVOS `test` (5–12 từ, hai câu mỗi người nói), người đọc và TTS đọc lại bằng giọng của mẫu dương
(19 VieNeu, 19 F5), cùng qua đường mô phỏng board hai lượt. Không câu nào có từ đánh thức, nên điểm cao chỉ có thể do
mạng nghe ra "chất TTS".

| Người đọc | Số lượt | Trung vị | Trung bình | Phân vị 90 | > 0,5 | > 0,9 |
|---|---|---|---|---|---|---|
| TTS | 76 | 0,001 | 0,061 | 0,200 | 5% | 0% |
| Người | 76 | 0,001 | 0,049 | 0,151 | 3% | 0% |

### 3.3 Cụm gần âm: nguyên nhân

Phiên 039 đọc mười cụm, mỗi cụm hai lần liền nhau; 20 đoạn `vad` ghép với cụm theo thứ tự và độ dài (cụm hai âm tiết
0,74–0,93 s, ba âm tiết 1,17–1,71 s). Điểm là của lệnh ở §2. Cột "Trong `v1`" là cụm có trong 69 chữ của `synth_neg`.

| Cụm | Trong `v1` | Điểm hai lần |
|---|---|---|
| chào mi | có | 0,65 0,48 |
| mi na | có | 0,84 0,21 |
| mi na ơi | có | 0,03 0,08 |
| chào chị na | có | 0,44 0,05 |
| chào mi nhé | có | 0,06 0,89 |
| **chào minh** | không | **0,86 0,88** |
| **chào mẹ** | không | **0,92 0,89** |
| bang mi na | có | 0,03 0,00 |
| và vi na | có | 0,05 0,02 |
| chào bạn | không | 0,01 0,00 |

- Cụm đã học thì mạng đẩy xuống gần 0 ("mi na ơi", "bang mi na", "và vi na"). "chào" + một âm tiết mở đầu bằng "m" chưa
  từng có trong tập học, và lên 0,86–0,92, ngang đỉnh của giọng thật nói đúng từ (0,88–0,95). "chào bạn" không lên: lỗi nằm ở
  âm đầu "m", không ở mọi cụm "chào X".
- 69 chữ của `synth_neg` chọn theo khoảng cách thành phần âm tiết (`candidates`) và "chào X Y" hay gặp. Cả hai cách đều
  không sinh ra "chào" + âm tiết "m", hay số đọc "…mươi lăm / ba".
- Mạng hầu như không gặp cụm gần âm: 370 mẩu ngắn giữa 122 483 âm bản dài 100 giờ, cửa sổ rút theo độ dài, nên chỉ
  khoảng 0,1% cửa sổ âm là cụm gần âm. Loss vẫn thấp dù mạng không phân biệt chúng.

### 3.4 Tốc độ đọc: không phải, giữ mẩu đọc nhanh

Độ dài phần có tiếng: mẩu dương TTS trung vị 1,06 s (phân vị 5 / 25 / 75 / 95: 0,63 / 0,85 / 1,22 / 1,59 s; F5 ở tốc độ
0,85 / 1,0 / 1,15 là 1,18 / 1,01 / 0,88 s; VieNeu 1,06 s), giọng thật trên board trung vị 1,19 s (48 đoạn `vad`, có thể lẫn
đoạn không phải lần nói; 1,12–1,27 s phân vị 25–75). Dưới 0,5 s chỉ có ≤ 1% mẩu mỗi nhóm.

Học lại `wake/v1` cùng seed và số bước, bỏ 7 164 trên 27 728 mẩu dương đã mô phỏng (26%) có tiếng ngắn hơn 0,85 s, rồi
chấm trên board bằng cùng hàm của §2:

| | Bắt trên `val` | Ngưỡng | Trung vị 60 đoạn từ đánh thức | Đỉnh của chúng | Đỉnh 039 |
|---|---|---|---|---|---|
| `wake/v1` | 77,1% | 0,975 | 0,77 | 0,95 | 0,92 |
| Bỏ mẩu đọc nhanh | 60,2% | 0,925 | 0,54 | 0,82 | 0,85 |

Bỏ mẩu đọc nhanh hạ điểm của cả giọng thật lẫn cụm gần âm mà không tách chúng ra, nên không phải nguyên nhân và mẩu
đọc nhanh được giữ.

Cách sửa là âm bản khó của `wake/v2`, ở KẾ HOẠCH §3.11.

## 4. `wake/v2`: âm bản khó

`make wake-synth` phần `hard`, rồi split `wake/v2` (`ml/data/splits/wake/v2/SPLIT.md`); năm file của `wake/v1` giữ
nguyên sha256 nên bản mô phỏng của chúng là liên kết cứng. Bộ TTS `synth_hard` đọc 101 cụm, mỗi cụm 4 giọng VieNeu và
3 giọng F5; giữ theo luật của âm bản gần âm (`tts_engines.md` §3: nghe đúng chữ, cách "chào mi na" hơn 15,40 nat):

| Họ | Cụm | Mẩu | Nghe đúng chữ | Giữ |
|---|---|---|---|---|
| chào {x} | 28 | 196 | 101 | 101 |
| chào mi {x} | 30 | 210 | 50 | 32 |
| chào {x} na | 28 | 196 | 73 | 66 |
| {x} mi na | 15 | 105 | 53 | 50 |
| **Tổng** | **101** | **707** | **277** | **249** |

Mẩu nghe sai chữ phần lớn vẫn là âm bản dùng được ("chào và" nghe thành "chào bà", "chào mà" thành "chào má") và
cách "chào mi na" 20–50 nat, nhưng luật hiện tại bỏ chúng. `train_hard` có 2 590 mẩu, 4,19 giờ: 241 mẩu `synth_hard`,
370 mẩu `synth_neg`, 1 979 câu kho học khớp một mẫu của `split.hard`; mô phỏng 4 lượt. `val_hard` chỉ giữ 8 mẩu
`synth_hard` mà `val_neg` chưa có.

Hai run cùng split, seed và số bước, chỉ khác số kênh: `20260929_4f8d92d-dirty_7d719b` (32) và
`20260929_8aa9737-dirty_f578c6` (`--set model.channels=64`). Board chấm bằng lệnh của §2.

| | `wake/v1`, 32 | `wake/v2`, 32 | `wake/v2`, 64 |
|---|---|---|---|
| Loss cuối | 0,049 | 0,054 | 0,025 |
| Mốc giữ | 18 000 | 3 000 | 5 000 |
| Ngưỡng chọn trên `val` | 0,975 | 0,785 | 0,830 |
| Bắt trên `val` (TTS) | 77,1% | 93,2% | 94,1% |
| Báo nhầm `test_neg` ở ngưỡng ấy | 0,45/giờ | **2,34/giờ** (62 lần) | **1,89/giờ** (50 lần) |
| Board, 60 đoạn "chào mi na": trung vị / phân vị 75 / đỉnh | 0,77 / 0,88 / 0,95 | **0,21 / 0,38 / 0,67** | **0,14 / 0,27 / 0,71** |
| Board, "chào minh" hai lần | 0,86 0,88 | 0,27 0,18 | 0,16 0,28 |
| Board, "chào mẹ" hai lần | 0,92 0,89 | 0,22 0,15 | 0,05 0,01 |
| Board, "chào mi" / "mi na" | 0,65 0,48 / 0,84 0,21 | 0,49 0,13 / 0,57 0,22 | 0,16 0,01 / 0,03 0,01 |
| Board, lệnh và nói tự do, đỉnh | 0,28 | 0,15 | 0,16 |
| Board, lần nói qua ngưỡng / báo nhầm trên 39 phiên | 0 / 0 | 0 / 0 | 0 / 0 |

- **Âm bản khó tổng quát được**: "chào mẹ", "chào minh" không có trong tập học mà vẫn tụt từ ~0,9 xuống ≤ 0,28.
- **Nhưng giọng thật nói đúng từ tụt theo**, từ trung vị 0,77 xuống 0,21 và 0,14, và lại chồng lên cụm gần âm ở mức
  thấp hơn. Mạng bị ép phân biệt tinh hơn thì bám vào "chào mi na" kiểu TTS; giọng thật nằm ngoài vùng ấy. Mẫu dương
  toàn TTS là giới hạn gốc (KẾ HOẠCH §3.11).
- **64 kênh không giúp**: khớp tập học chặt hơn (loss 0,025 so với 0,054), bắt trên `val` TTS ngang, nhưng điểm giọng
  thật còn thấp hơn. Thiếu là thiếu dữ liệu giọng thật, không phải sức chứa.
- **Cách chọn mốc và ngưỡng không tin được**: `val` có 3,76 giờ âm bản, "≤ 1 lần/giờ" là ≤ 3 lần, nên ngưỡng do câu
  âm bản thứ 4 quyết định và nhảy 0,785–0,95 giữa các mốc. Giữ mốc có tỉ lệ bắt cao nhất trong 20 lần đo nhiễu là giữ
  lần may: mốc 3 000 và 5 000, còn ở đầu lịch học, và ngưỡng của chúng cho 1,9–2,3 lần/giờ trên `test_neg`.

## 5. `wake/v3`: mốc cuối, `val` lớn hơn, việc phụ CTC

Split `wake/v3` (`ml/data/splits/wake/v3/SPLIT.md`): dữ liệu học y hệt `wake/v2`; 40% người nói Common Voice rời
`test_neg` sang `val_neg`, nên `val_neg` 10,92 giờ và `test_neg` 19,33 giờ đã mô phỏng. Mỗi run giữ trọng số của bước
cuối và lưu mọi mốc chấm ở `checkpoints/`. Việc phụ CTC (KẾ HOẠCH §3.11): 32 câu người thật mỗi bước, trọng số 0,1,
117 981 câu của `train_neg`. Ba run cùng seed và số bước; board chấm bằng lệnh của §2.

| | v3, 32, không CTC | v3, 32, CTC | v3, 64, CTC |
|---|---|---|---|
| Run | `20260929_5ad9f0c-dirty_8e233b` | `…_a74c64` | `…_0801aa` |
| Mất mát CTC cuối, mỗi đơn vị | | 2,12 | 1,82 |
| Ngưỡng chọn trên `val` | 0,940 | 0,895 | 0,925 |
| Bắt trên `val` (TTS) / báo nhầm `val` | 83,1% / 0,92 lần/giờ | 86,4% / 0,55 | 94,1% / 0,92 |
| Báo nhầm `test_neg` ở ngưỡng ấy | 1,09/giờ (21 lần) | 1,50/giờ (29 lần) | 1,50/giờ (29 lần) |
| Board, 60 đoạn "chào mi na": trung vị / phân vị 75 / đỉnh | 0,48 / 0,68 / 0,98 | 0,38 / 0,57 / 0,84 | **0,71 / 0,86 / 0,95** |
| Board, qua ngưỡng đã chọn | 2 | 0 | 5 |
| Board, "chào minh" / "chào mẹ" | 0,05 0,09 / 0,02 0,03 | 0,05 0,06 / 0,10 0,04 | 0,43 0,24 / 0,02 0,00 |
| Board, "chào mi nhé" hai lần | 0,06 0,77 | 0,05 0,74 | 0,03 **0,93** |
| Board, lệnh và nói tự do, đỉnh | 0,47 | 0,05 | 0,02 |

Đoạn qua ngưỡng / số lần đọc cụm gần âm của 039 qua ngưỡng, quét ngưỡng trên cùng các đoạn:

| Ngưỡng | v1 | v3, 32, không CTC | v3, 32, CTC | v3, 64, CTC |
|---|---|---|---|---|
| 0,5 | 45 / 7 | 29 / 1 | 22 / 1 | 38 / 1 |
| 0,6 | 41 / 7 | 21 / 1 | 12 / 1 | 34 / 1 |
| 0,7 | 38 / 6 | 15 / 1 | 7 / 1 | 30 / 1 |
| 0,8 | 25 / 6 | 8 / 0 | 2 / 0 | 22 / 1 |
| 0,9 | 10 / 1 | 2 / 0 | 0 / 0 | 7 / 1 |

- **Mốc cuối thay mốc may** là thay đổi lớn nhất giữa v2 và v3 32 không CTC (cùng dữ liệu): giọng thật trung vị 0,21 lên
  0,48; ngưỡng chọn trên `val` 10,92 giờ đoán đúng `test_neg` (0,92 so với 1,09 lần/giờ) thay vì lệch gấp ba.
- **CTC ở 32 kênh không giúp**: mạng dè dặt với mọi thứ, giọng thật cũng tụt; mất mát CTC 2,12 cho thấy thân mạng không
  đủ sức gánh hai việc.
- **CTC ở 64 kênh là cấu hình tốt nhất đến giờ**: giọng thật gần mức của v1 (≥ 0,7 ở 30/60 đoạn so với 38) trong khi cụm
  gần âm chỉ còn một lần qua (v1: 6–7), lời nói thường ≤ 0,02. Mỗi cấu hình mới chạy một seed trên một người nói, nên
  chênh giữa các cột chưa tách được khỏi dao động giữa các lần học.
- **Lần qua còn lại là "chào mi nhé"**, lần đọc thứ hai. PhoWhisper nghe lần ấy là "chào my nhé" (cách "chào mi na"
  15,9 nat) còn lần thứ nhất là "chào đi nhé", nên đây là cụm gần âm thật, chỉ khác âm tiết cuối. Tập học gần như không
  có họ này: trong 80 mẩu TTS "chào mi n…" của `synth_neg` và `synth_hard` chỉ 6 mẩu được giữ, vì luật chọn bỏ mẩu nghe
  sai chữ và mẩu cách "chào mi na" dưới 15,40 nat, đúng vùng của cụm ấy.

## 6. Cuối từ của mẫu dương: căn cưỡng bức và khoảng lặng TTS (29/09)

Nhãn của một mẫu dương đặt ở cuối mẩu (KẾ HOẠCH §3.11), nên mẩu phải dừng ngay sau âm cuối của từ đánh thức.
Mục này đo hai chỗ lệch: khoảng lặng TTS để lại sau từ, và độ đúng của mốc cuối từ khi cắt câu thật trong kho.

**Khoảng lặng TTS.** Mẩu dương "chào mi na" của `synth_pos` (một mẩu trong mười), bước 16 ms, âm tính là còn trong
40 dB so với bước to nhất:

| Bộ | Mẩu | Dài p5 / p50 / p95 (s) | Lặng đầu (s) | Lặng cuối (s) |
|---|---|---|---|---|
| VieNeu | 158 | 0,80 / 1,04 / 1,36 | 0,00 / 0,05 / 0,08 | 0,00 / 0,16 / 0,39 |
| F5 | 214 | 0,59 / 0,97 / 1,64 | 0,08 / 0,21 / 0,69 | 0,00 / 0,00 / 0,18 |

Nhãn đặt ở cuối mẩu nghĩa là với VieNeu, nhãn trễ trung vị 0,16 s sau từ, có mẩu tới 0,4 s.

**Mẩu ghép có đáp án.** 120 mẩu TTS ấy, cắt ở cuối âm, đặt sau 0,3–1,0 s lặng, rồi hoặc để lặng 0,5 s ("đứng một
mình"), hoặc nối ngay một câu VIVOS train đã bỏ lặng đầu ("có từ nói nối sau"), hai phần cùng mức RMS. Đáp án là
cuối âm của mẩu TTS. Bộ căn: Montreal Forced Aligner 3.4 (image Docker `v3.4.2`), mô hình âm học và từ điển
`vietnamese_mfa` 3.0.0, lời đã chuẩn hoá. 464 mẩu (240 ghép và 224 câu thật) căn trong 37 s với 8 tiến trình; mọi
mẩu đều căn được.

Mốc cuối "na" của bộ căn trừ đáp án, tính theo ngưỡng của đáp án (âm tụt bao nhiêu dB dưới đỉnh):

| Mẩu | Đáp án | p5 | p50 | p95 | Sớm > 0,1 s | Muộn > 0,1 s |
|---|---|---|---|---|---|---|
| đứng một mình | −15 dB | −0,070 | +0,031 | +0,189 | 3% | 23% |
| đứng một mình | −25 dB | −0,111 | +0,002 | +0,140 | 6% | 11% |
| đứng một mình | −30 dB | −0,124 | −0,005 | +0,098 | 7% | 5% |
| đứng một mình | −40 dB | −0,174 | −0,022 | −0,004 | 10% | 0% |
| có từ nói nối sau | −30 dB | −0,160 | −0,016 | +0,028 | | |
| có từ nói nối sau | −40 dB | −0,202 | −0,038 | −0,002 | | |

Bộ căn đặt cuối từ ở lúc âm tụt khoảng 30 dB (lệch trung vị 5–16 ms). Đáp án −40 dB tính thêm đuôi rất nhỏ nên muộn
hơn bộ căn vài chục ms. Khi có từ nói nối sau, sai số lệch về phía sớm, 5% sớm hơn 0,16 s.

**Quãng chừa sau mốc** (mẩu có từ nói nối sau; "lẫn từ sau" tính từ chỗ nối, tức đáp án −40 dB):

| Chừa | Cắt trước cuối từ (−30 dB) | Lẫn hơn 0,1 s của từ sau |
|---|---|---|
| 0,05 s | 22% | 2% |
| **0,1 s** | **12%** | **5%** |
| 0,15 s | 7% | 59% |

Chọn **0,1 s** (`split.positive_tail_s`). Mẩu TTS dừng ở bước cuối còn trong **30 dB** (`split.tts_pos`), đúng chỗ bộ
căn đặt cuối từ, cộng cùng 0,1 s. Hai loại mẫu dương nhờ vậy dừng cùng một kiểu.

**Câu thật.** Cả 224 câu kho học nói "trợ lý" đều căn được, tìm thấy "trợ lý" ở cả 224. Độ dài hai âm tiết p5 / p50 /
p95 là 0,25 / 0,38 / 0,58 s. `python -m srpipe.tasks.wake.data corpus` cắt cả 224 (dài 0,62 / 1,70 / 2,52 s) và giữ
**218**: Bud500 190, VLSP 27, FPT 1. Sáu mẩu bỏ vì PhoWhisper nghe lại không ra "trợ lý", phần lớn khi từ ấy ở cuối
mẩu và câu gốc nói tiếp tên người ("trợ lý long" nghe thành "chợ ly", "quà trợ lý" thành "quà thợ lý").

**Mốc từ của PhoWhisper không dùng.** Chế độ mốc từng từ (chú ý chéo, kernel eager) trên card 4 GB đầy bộ nhớ (3,9 GB)
và tràn sang RAM: 240 mẩu ghép chưa xong sau 25 phút, tức hơn 6 s mỗi mẩu, nên không có số. Theo WhisperX (Bain và
cộng sự, 2023), mốc từ của Whisper kém căn cưỡng bức, và họ cũng căn lại bằng mô hình âm vị.
