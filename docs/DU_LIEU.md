# DU_LIEU.md

Dữ liệu đã tải và đã xử lí — số đo trên đĩa, không phải kế hoạch. Ứng viên và luật chia ở KẾ HOẠCH §1.2,
§1.3; giấy phép và dữ liệu cá nhân ở §1.4. File này chỉ ghi thứ đã thật sự nằm trên đĩa.

Đường dẫn gốc khai ở `ml/configs/common/paths.yaml`. `raw/` chỉ đọc; `interim/` và `processed/` sinh lại
được bằng một lệnh.

## 1. Nguồn

| Nguồn | Dùng cho | Giấy phép | Giờ | Người nói | Tải ngày | sha256 bản tải | Trạng thái |
|---|---|---|---|---|---|---|---|
| Common Voice tiếng Việt, bản 27.0 (Mozilla Data Collective) | `command`, âm bản `wake` | CC0; điều khoản cấm tìm danh tính người nói | 7,95 đã kiểm (22,97 cả kho) | 229 đã kiểm (354 ở `other`) | 27/09 | `dec3d030…` (`manifests/speech/common_voice_vi.yaml`) | ở `raw/speech/common_voice_vi` |
| VIVOS | `command`; tập thử của `vad` | CC BY-NC-SA 4.0 | 15,67 (thử 0,75) | 65 (thử 19) | 07/09, vào `raw/` 27/09 | cây `6325ac21…` (`manifests/speech/vivos.yaml`) | ở `raw/speech/vivos` |
| FPT Open Speech Data (FOSD, bản parquet trên Hugging Face) | `command`, chỉ tập học: không có mã người nói | CC BY 4.0 (bản gốc trên Mendeley) | 30,21 | không rõ | 27/09 | sha256 LFS từng tệp (`manifests/speech/fpt_open.yaml`) | ở `raw/speech/fpt_open` |
| VinBigData-VLSP2020-100h (bản parquet trên Hugging Face) | `command`, chỉ tập học: không có mã người nói | CC BY 4.0 theo bản sao; openscience.vn ghi CC0, trang công bố không nêu 🔬 | 101,38 (~20 đọc, ~80 nói tự nhiên) | không rõ | 27/09 | sha256 LFS từng tệp (`manifests/speech/vlsp.yaml`) | ở `raw/speech/vlsp` |
| Bud500 (VietAI) | `command`, chỉ tập học: người nói ba miền, không có mã người nói | CC BY-NC-SA 4.0, chỉ nghiên cứu | 462,08 (học 451,41; kiểm 5,34; thử 5,33) | không rõ | 27/09 | sha256 LFS từng tệp (`manifests/speech/bud500.yaml`) | ở `raw/speech/bud500` |
| Lệnh điều khiển nhà (repo `edge-ai-voice-control-esp32`, commit `ae3957e`) | `command`, chỉ tập học: không có mã người nói; 4 lệnh trùng bộ lệnh (bật/tắt đèn, bật/tắt quạt), thêm "bật hết", "tắt hết" | MIT, giữ thông báo bản quyền ở `LICENSE` | 0,37 lệnh (1 343 mẩu 1 s: 6 lệnh × 200 học + 143 thử) + 260 mẩu nhiễu phòng | không rõ, tác giả tự thu | 29/09 | cây `e25d54a3…` (`manifests/speech/kws_vi_command.yaml`) | ở `raw/speech/kws_vi_command`; chỉ đọc `dataset_1s` và `data_test/data_1s`, bản `data_cutted` trùng lời |
| Mẩu trích theo lời `hf_extract` (`ml/configs/common/extract.yaml`): câu của 25 kho tiếng Việt trên Hugging Face và của FPT, VLSP, Bud500 có lời đọc ra một cụm, cắt lấy đúng cụm ấy (KẾ HOẠCH §1.2) | dương `wake`, học `command`; chỉ tập học: không có mã người nói | theo từng kho, ghim `revision` ở manifest | 4,49 giờ, 35 643 mẩu: dừng lại 13 801, mở cửa 11 631, đóng cửa 6 929, **trợ lý 2 032**, bật đèn 583, tắt đèn 544, giảm âm lượng 45, tăng âm lượng 42, bật quạt 31, tắt quạt 5; nhiều nhất GigaSpeech2 14 098, PhoAudiobook 4 284 | không rõ | 29–30/09 | `clips.tsv` `f61a856f…` (`manifests/speech/hf_extract.yaml`) | ở `raw/speech/hf_extract`, 574 MB; câu gốc đã xoá sau khi cắt; YODAS2 đã quét, chưa kéo (`hold`); WorldSpeech không có âm thanh, bỏ; dolly-audio (2 571 mẩu, 139 "trợ lý") tự gắn thẻ `synthetic` trên Hugging Face: mẩu mang nhãn `synth`, `wake` không lấy làm người thật (còn 1 893 "trợ lý") |
| MUSAN | `ns`, tăng cường, nhiễu của đường mô phỏng board | CC BY 4.0 phần lớn; phần nhạc mỗi tệp một giấy phép | 109,29: nhạc 42,61 (660 tệp), nhiễu 6,23 (930), lời nói 60,45 (426) | — | 27/09 | `86d1061c…` (`manifests/noise/musan.yaml`) | ở `raw/noise/musan` |
| DEMAND, 16 kHz | `ns`, nhiễu của đường mô phỏng board | CC BY 4.0 (bản ghi Zenodo) | 1,42 mỗi kênh: 17 môi trường × 5 phút, 16 kênh | — | 27/09 | md5 từng môi trường (`manifests/noise/demand.yaml`) | ở `raw/noise/demand` |
| DNS Challenge 2/3 (Microsoft), nhiễu băng rộng 16 kHz | `ns`, nhiễu của đường mô phỏng board | CC BY 4.0 cả bộ; đoạn AudioSet CC BY 4.0, đoạn Freesound chỉ CC0 | 180,45: AudioSet 151,00 (54 698 đoạn, tên là mã YouTube), Freesound 29,46 (10 604 đoạn: cửa, quạt, gõ phím, cót két, thở, nhai, máy photo, kéo đồ); 10 s mỗi đoạn; 188 tệp ở 44,1–96 kHz, đọc qua `to_grid_rate` | — | 28/09 | `801773f8…` (`manifests/noise/dns.yaml`) | ở `raw/noise/dns`; không chứa DEMAND, nên không trùng `raw/noise/demand` |
| OpenSLR 28 (RIRS_NOISES) | tăng cường vang, đường mô phỏng board | Apache 2.0 | 325 RIR thật, 60 000 RIR mô phỏng; nhiễu nguồn điểm 5,91 (843 tệp), 92 nhiễu đẳng hướng | — | 27/09 | `3b50cfde…` (`manifests/rir/openslr28.yaml`) | ở `raw/rir/openslr28` |
| Speech Commands v0.02, chỉ `_background_noise_` | nhiễu của cảnh `vad` | CC BY 4.0 | 0,11 | — | 07/09, vào `raw/` 27/09 | cây `ded5bbb6…` (`manifests/noise/speech_commands.yaml`) | ở `raw/noise/speech_commands` |
| Tiếng tổng hợp VieNeu-TTS v3 Turbo, codec MOSS-Audio-Tokenizer-Nano | dương `wake` (E11-T7), tiếng nguồn `synth` (KẾ HOẠCH §3.13); không bao giờ vào tập thử | Apache 2.0 cả hai | sinh theo lệnh | 25 giọng có sẵn, giọng nhân bản | 28/09 | ghim `repo@revision` ở `ml/configs/common/tts.yaml` | bộ `wake` 28/09: 1 574 dương, 414 âm bản gần âm; giữ 1 524 và 221 (`measurements/tts_engines.md` §3) |
| Tiếng tổng hợp F5-TTS ViVoice, vocoder vocos-mel-24khz | như trên | CC BY-NC-SA 4.0, **phi thương mại**; vocos MIT | sinh theo lệnh | giọng nhân bản | 28/09 | như trên | bộ `wake` 28/09: 2 136 dương, 276 âm bản gần âm; giữ 2 060 và 163 |
| PhoWhisper-large (VinAI) | nghe lại mọi mẩu tổng hợp, mẩu không qua thì bỏ | BSD-3-Clause | — | — | 28/09 | như trên | đang dùng |
| Montreal Forced Aligner 3.4 (image Docker `v3.4.2`), mô hình âm học và từ điển `vietnamese_mfa` 3.0.0 | căn mốc từng từ của câu kho nói từ đánh thức, để cắt mẫu dương thật của `wake` | MIT (bộ căn), CC BY 4.0 (mô hình; học trên Common Voice 17, VIVOS, GlobalPhone) | — | — | 29/09 | ghim ở `ml/configs/common/tts.yaml` | mô hình ở `cache/mfa/3.0.0/` (`measurements/wake.md` §6) |
| Thu qua board | `wake`, lệnh, tập thử | của dự án, có phiếu đồng ý | | | | | chưa thu |

Trên máy còn hai kho tải từ trước cho dự án khác, **chưa nhập** vào `raw/`: LibriSpeech `dev-clean`, `test-clean`, `train-clean-100` (tiếng Anh, CC BY 4.0, 7 GB) và phần lời của Speech Commands (tiếng Anh, CC BY 4.0). Nhập kho nào thì theo đúng cách của VIVOS: chép nguyên vào `raw/`, manifest có sha256 cây, một dòng ở bảng trên.

### 1.1 Sàng lọc (E11-T16)

`make screen` ngày 28/09 (KẾ HOẠCH §1.2). Mọi nơi đọc kho bỏ qua các mẩu ở `interim/screen/rejects.tsv`. Ngưỡng và
bảng quét ở `measurements/data_screen.md`.

| Kho | Loại | Lý do chính | Còn dùng được (giờ) |
|---|---|---|---|
| Common Voice 27.0 | 143 mẩu, 0,15 h | câm 62, lời lệch tiếng 81 | 22,20 |
| VIVOS | 17 mẩu, 0,02 h | lời lệch tiếng 13 | 15,65 |
| FPT | 470 mẩu, 0,73 h | câm 442, trong đó 437 mẩu Set002 toàn số 0 | 29,45 |
| VLSP2020-100h | 701 mẩu, 0,44 h | lời lệch tiếng 668 | 100,94 |
| Bud500 | 15 mẩu, 0,02 h | trùng 10 | 462,06 |
| DNS Challenge | 132 mẩu, 0,36 h | câm 109, trùng 23 | 180,09 |
| MUSAN | 1 mẩu | câm | 109,29 |
| OpenSLR 28 | 843 mẩu, 5,91 h | `pointsource_noises` là bản chép nhiễu MUSAN: 842 trùng | 20,34 |
| DEMAND, Speech Commands | 0 | | 22,67; 0,11 |

## 2. Split

| Nhánh | Split | Luật | Seed | sha256 `split.lock` | `SPLIT.md` |
|---|---|---|---|---|---|
| `wake` | v1: `train_pos` 3 466 (1,0 h TTS), `train_neg` 122 483 (100,1 h), `val_pos` 118, `val_neg` 2 497 (3,3 h), `test_neg` 20 717 (22,9 h) | theo người nói; giọng nhân bản theo vai của người được nhân bản; `test_neg` là Common Voice và VIVOS test, không kho nào làm giọng mẫu; không âm bản nào nói từ đánh thức | 20260928 | `6f81be63…` | `ml/data/splits/wake/v1/SPLIT.md` |
| `command` | v1: `train_*` một file mỗi kho, 758 897 mẩu (626,2 h), `val` 1 583 (1,9 h), `test` 2 000 (2,0 h, 59 người nói) | theo người nói; FPT, VLSP, Bud500 chỉ vào học; 196 câu chứa "chụp ảnh" rời tập học (lệnh chưa học, E11-T13) | 20260928 | `4bfcd902…` | `ml/data/splits/command/v1/SPLIT.md` |

## 3. Đặc trưng qua đường mô phỏng board

Mỗi tập một dòng: `python -m srpipe.scenes.device build <file split> <tập>` ghi `processed/<tập>/` (KẾ HOẠCH §1.2), cấu
hình và sha256 ở `manifest.yaml` của tập. Kho phòng `interim/scenes/device/rooms/` (512 phòng, RT60 đo 0,18 / 0,46 / 0,74 s
ở p5 / p50 / p95) dựng một lần trong ~1,5 phút với 8 tiến trình.

| Tập | File split | Mẩu | Giờ đặc trưng | Dựng | Cỡ trên đĩa | Ngày |
|---|---|---|---|---|---|---|
| `command/vivos_test` | VIVOS test, 19 người nói, lập tay để đo chi phí | 760 | 0,876 | 2,9 phút trên 3 tiến trình, ~8 lần thời gian thực mỗi nhân | 36 MB/giờ đặc trưng, 115 MB/giờ PCM sạch | 27/09 |

## 4. Bản thu qua board

Mỗi phiên một dòng. Mã người nói `spk_NNN`; tên thật chỉ nằm trong bảng ngoài repo (KẾ HOẠCH §1.4).

| Phiên | Ngày | Phòng | Người nói | Mã phiếu | Nội dung | Phút | Hở `seq` |
|---|---|---|---|---|---|---|---|
