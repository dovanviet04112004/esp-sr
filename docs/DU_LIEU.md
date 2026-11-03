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
| Bud500 (VietAI) | `command`, chỉ tập học: người nói ba miền, không có mã người nói | CC BY-NC-SA 4.0, chỉ nghiên cứu | 462,08 (học 451,41; kiểm 5,34; thử 5,33) | không rõ | 27/09 | sha256 LFS từng tệp (`manifests/speech/bud500.yaml`) | **xoá khỏi `raw/` ngày 06/10** theo chủ dự án: 437 giờ thêm vào không đổi Cửa 3 (`measurements/command.md` §12.13); tải lại bằng `scripts/10_prepare.sh speech/bud500`. `ns/v1`, giọng mẫu TTS của `wake` và `command`, nguồn local của `hf_extract` còn ghi tên nó: dựng lại chúng phải tải lại trước |
| LSVSC (bản parquet trên Hugging Face, `doof-ferb/LSVSC@269de060`) | `command`, chỉ tập học: không có mã người nói | CC BY 4.0 theo bản sao | 100,66 | không rõ; nhãn giới (nam 48% trên phần Hugging Face thống kê), phương ngữ (Bắc 86%), tuổi | 06/10 | sha256 LFS từng tệp (`manifests/speech/lsvsc.yaml`) | ở `raw/speech/lsvsc`; 56 823 câu, WAV 16 kHz |
| Speech-MASSIVE, phần tiếng Việt (`doof-ferb/Speech-MASSIVE_vie@3a47437e`) | `command`, chỉ tập học trong `command/v7` để `val`, `test` giữ như v6 | CC BY 4.0 theo bản sao | 5,44 | 44, cột `speaker_id` | 06/10 | sha256 LFS từng tệp (`manifests/speech/speech_massive_vi.yaml`) | ở `raw/speech/speech_massive_vi`; 5 122 câu ra lệnh cho trợ lý ảo, 16 kHz |
| ViMD (`nguyendv02/ViMD_Dataset@3a5b3015`, thời sự của đài 63 tỉnh) | `command`, chỉ tập học | CC BY-NC-ND 4.0, không phái sinh | 97,16 trong 55 203 đoạn | mã người nói, giới, 63 tỉnh | 06/10 | sha256 LFS từng tệp (`manifests/speech/vimd.yaml`) | ở `raw/speech/vimd`, 59,8 GB, 18 949 câu ~19 s; cắt theo mốc từ thành đoạn tới 8 s (`make spans NAME=vimd`, KẾ HOẠCH §1.2), 32 lô 600 câu, khoảng 4,7 phút mỗi lô cạnh một lượt dựng; danh sách ở `interim/spans/vimd.tsv` |
| Lệnh điều khiển nhà (repo `edge-ai-voice-control-esp32`, commit `ae3957e`) | `command`, chỉ tập học: không có mã người nói; 4 lệnh trùng bộ lệnh (bật/tắt đèn, bật/tắt quạt), thêm "bật hết", "tắt hết" | MIT, giữ thông báo bản quyền ở `LICENSE` | 0,37 lệnh (1 343 mẩu 1 s: 6 lệnh × 200 học + 143 thử) + 260 mẩu nhiễu phòng | không rõ, tác giả tự thu | 29/09 | cây `e25d54a3…` (`manifests/speech/kws_vi_command.yaml`) | ở `raw/speech/kws_vi_command`; chỉ đọc `dataset_1s` và `data_test/data_1s`, bản `data_cutted` trùng lời |
| Mẩu trích theo lời `hf_extract` (`ml/configs/common/extract.yaml`): câu của 25 kho tiếng Việt trên Hugging Face và của FPT, VLSP, Bud500 có lời đọc ra một cụm, cắt lấy đúng cụm ấy (KẾ HOẠCH §1.2) | dương `wake`, học `command`; chỉ tập học: không có mã người nói | theo từng kho, ghim `revision` ở manifest | 4,49 giờ, 35 643 mẩu: dừng lại 13 801, mở cửa 11 631, đóng cửa 6 929, **trợ lý 2 032**, bật đèn 583, tắt đèn 544, giảm âm lượng 45, tăng âm lượng 42, bật quạt 31, tắt quạt 5; nhiều nhất GigaSpeech2 14 098, PhoAudiobook 4 284 | không rõ | 29–30/09 | `clips.tsv` `f61a856f…` (`manifests/speech/hf_extract.yaml`) | ở `raw/speech/hf_extract`, 574 MB; câu gốc đã xoá sau khi cắt; YODAS2 đã quét, chưa kéo (`hold`); WorldSpeech không có âm thanh, bỏ; dolly-audio (2 571 mẩu, 139 "trợ lý") tự gắn thẻ `synthetic` trên Hugging Face: mẩu mang nhãn `synth`, `wake` không lấy làm người thật (còn 1 893 "trợ lý") |
| MUSAN | `ns`, tăng cường, nhiễu của đường mô phỏng board | CC BY 4.0 phần lớn; phần nhạc mỗi tệp một giấy phép | 109,29: nhạc 42,61 (660 tệp), nhiễu 6,23 (930), lời nói 60,45 (426) | — | 27/09 | `86d1061c…` (`manifests/noise/musan.yaml`) | ở `raw/noise/musan` |
| DEMAND, 16 kHz | `ns`, nhiễu của đường mô phỏng board | CC BY 4.0 (bản ghi Zenodo) | 1,42 mỗi kênh: 17 môi trường × 5 phút, 16 kênh | — | 27/09 | md5 từng môi trường (`manifests/noise/demand.yaml`) | ở `raw/noise/demand` |
| DNS Challenge 2/3 (Microsoft), nhiễu băng rộng 16 kHz | `ns`, nhiễu của đường mô phỏng board | CC BY 4.0 cả bộ; đoạn AudioSet CC BY 4.0, đoạn Freesound chỉ CC0 | 180,45: AudioSet 151,00 (54 698 đoạn, tên là mã YouTube), Freesound 29,46 (10 604 đoạn: cửa, quạt, gõ phím, cót két, thở, nhai, máy photo, kéo đồ); 10 s mỗi đoạn; 188 tệp ở 44,1–96 kHz, đọc qua `to_grid_rate` | — | 28/09 | `801773f8…` (`manifests/noise/dns.yaml`) | ở `raw/noise/dns`; không chứa DEMAND, nên không trùng `raw/noise/demand` |
| OpenSLR 28 (RIRS_NOISES) | tăng cường vang, đường mô phỏng board | Apache 2.0 | 325 RIR thật, 60 000 RIR mô phỏng; nhiễu nguồn điểm 5,91 (843 tệp), 92 nhiễu đẳng hướng | — | 27/09 | `3b50cfde…` (`manifests/rir/openslr28.yaml`) | ở `raw/rir/openslr28` |
| Speech Commands v0.02, chỉ `_background_noise_` | nhiễu của cảnh `vad` | CC BY 4.0 | 0,11 | — | 07/09, vào `raw/` 27/09 | cây `ded5bbb6…` (`manifests/noise/speech_commands.yaml`) | ở `raw/noise/speech_commands` |
| Speech Commands v0.02, phần lời (35 từ tiếng Anh) | `wake` của sản phẩm, từ tạm "happy" (ADR-0033, split `wake/happy1`): dương "happy", âm bản khó là các từ khác; các pilot kws của E11-T17 | CC BY 4.0, ghi nguồn: Warden, 2018, "Speech Commands: A Dataset for Limited-Vocabulary Speech Recognition" | 28,84 (105 829 mẩu ≤ 1 s) | 2 618, mã trong tên file | 07/09, vào `raw/` 01/10, tải lại 29/10 | cây `37ff2c21…` (`manifests/speech/speech_commands.yaml`) | ở `raw/speech/speech_commands`; `val` lấy từ `testing_list.txt` |
| Tiếng tổng hợp VieNeu-TTS v3 Turbo, codec MOSS-Audio-Tokenizer-Nano | dương `wake` (E11-T7), tiếng nguồn `synth` (KẾ HOẠCH §3.13); không bao giờ vào tập thử | Apache 2.0 cả hai | sinh theo lệnh | 25 giọng có sẵn, giọng nhân bản | 28/09 | ghim `repo@revision` ở `ml/configs/common/tts.yaml` | bộ `wake` 28/09: 1 574 dương, 414 âm bản gần âm; giữ 1 524 và 221 (`measurements/tts_engines.md` §3) |
| Tiếng tổng hợp F5-TTS ViVoice, vocoder vocos-mel-24khz | như trên | CC BY-NC-SA 4.0, **phi thương mại**; vocos MIT | sinh theo lệnh | giọng nhân bản | 28/09 | như trên | bộ `wake` 28/09: 2 136 dương, 276 âm bản gần âm; giữ 2 060 và 163 |
| PhoWhisper-large (VinAI) | nghe lại mọi mẩu tổng hợp, mẩu không qua thì bỏ | BSD-3-Clause | — | — | 28/09 | như trên | đang dùng |
| Montreal Forced Aligner 3.4 (image Docker `v3.4.2`), mô hình âm học và từ điển `vietnamese_mfa` 3.0.0 | căn mốc từng từ của câu kho nói từ đánh thức, để cắt mẫu dương thật của `wake` | MIT (bộ căn), CC BY 4.0 (mô hình; học trên Common Voice 17, VIVOS, GlobalPhone) | — | — | 29/09 | ghim ở `ml/configs/common/tts.yaml` | mô hình ở `cache/mfa/3.0.0/` (`measurements/wake.md` §6) |
| DNSMOS P.835 (Microsoft DNS Challenge), `sig_bak_ovr.onnx` của `microsoft/DNS-Challenge@591184a9` | thước nghe không cần tiếng sạch của bàn so `afe_compare` (KẾ HOẠCH §3.16), chạy trên CPU qua `ml/afe_ref/dnsmos` | code MIT, phần còn lại của repo CC BY 4.0 (file `LICENSE`) | — | — | 30/09 | sha256 `269fbebd…6edd`, blob git `814b8dc6…`, ghim ở mục `refs` của `configs/afe/compare.yaml` | ở `cache/afe_ref/dnsmos` |
| NSNet2 (Microsoft DNS Challenge), `nsnet2-20ms-baseline.onnx` của `microsoft/DNS-Challenge@a2c7487e` | bộ dìm nhiễu tham chiếu của bàn so `afe_compare` (KẾ HOẠCH §3.16), chạy trên CPU qua `ml/afe_ref/nsnet2` | code MIT, phần còn lại của repo CC BY 4.0 (file `LICENSE`) | — | — | 30/09 | sha256 `88429b62…4ca9`, blob git `6b8e3362…`, ghim ở mục `refs` của `configs/afe/compare.yaml` | ở `cache/afe_ref/nsnet2` |
| RNNoise qua `pyrnnoise` 0.4.5 (PyPI), gói sẵn `xiph/rnnoise@70f1d25` cùng trọng số của nó | bộ dìm nhiễu tham chiếu của bàn so `afe_compare` (KẾ HOẠCH §3.16), chạy trên CPU qua `ml/afe_ref/rnnoise` | `pyrnnoise` Apache-2.0, rnnoise BSD-3-Clause | — | — | 30/09 | hash của bánh xe ghim trong `ml/afe_ref/rnnoise/uv.lock` | trong môi trường uv của `ml/afe_ref/rnnoise` |
| ECAPA-TDNN của SpeechBrain, `speechbrain/spkrec-ecapa-voxceleb@0f99f2d0` (Hugging Face), học trên VoxCeleb 1 và 2 | bộ trích embedding người nói của khảo sát KẾ HOẠCH §3.17, chạy trên CPU qua `ml/spk_ref/ecapa` (`speechbrain` 1.1.1) | Apache-2.0 (thẻ của model) | — | — | 10/10 | `embedding_model.ckpt` sha256 `0575cb64…26a2` cùng bốn file đi kèm, ghim ở `configs/scenes/speaker.yaml` | ở `cache/spk_ref/ecapa` |
| CAM++ của 3D-Speaker, `iic/speech_campplus_sv_zh-cn_16k-common` v1.0.0 (ModelScope), học trên 200 000 người nói tiếng Trung; định nghĩa mạng `speakerlab/models/campplus/{DTDNN,layers}.py` của `modelscope/3D-Speaker@065629c3` | bộ trích embedding người nói của khảo sát KẾ HOẠCH §3.17, chạy trên CPU qua `ml/spk_ref/speakerlab` | Apache License 2.0 (thẻ của model và `LICENSE` của repo) | — | — | 10/10 | `campplus_cn_common.bin` sha256 `3388cf5f…ada8`, hai file mã ghim theo commit, ở `configs/scenes/speaker.yaml` | ở `cache/spk_ref/campplus` |
| CAM++ của 3D-Speaker, `iic/speech_campplus_sv_zh_en_16k-common_advanced` v1.0.0 (ModelScope), học trên kho lớn tiếng Trung và tiếng Anh | như dòng trên, qua `ml/spk_ref/speakerlab` | Apache License 2.0 (thẻ của model) | — | — | 10/10 | `campplus_cn_en_common.pt` sha256 `92f29b94…0199`, ghim ở `configs/scenes/speaker.yaml` | ở `cache/spk_ref/campplus_zh_en` |
| ERes2NetV2 của 3D-Speaker, `iic/speech_eres2netv2_sv_zh-cn_16k-common` v1.0.1 (ModelScope), 200 000 người nói tiếng Trung; mã `speakerlab/models/eres2net/{ERes2NetV2,pooling_layers,fusion}.py` của `modelscope/3D-Speaker@065629c3` | như dòng trên, qua `ml/spk_ref/speakerlab` | Apache License 2.0 (thẻ của model) | — | — | 10/10 | `pretrained_eres2netv2.ckpt` sha256 `0eb40571…963c`, ghim ở `configs/scenes/speaker.yaml` | ở `cache/spk_ref/eres2netv2` |
| ReDimNet của IDRnD, `M-vb2+vox2+cnc-ft_mix.pt` và `b6-vox2-ft_lm.pt` (bản phát hành `latest` của `IDRnD/ReDimNet`), mã `redimnet/` của `IDRnD/ReDimNet@ce039a62`; học trên VoxBlink2, VoxCeleb2, CN-Celeb (M) và VoxCeleb2 (b6) | như dòng trên, qua `ml/spk_ref/redimnet`; **chỉ khảo sát** | mã MIT; trọng số không ghi giấy phép, kho học VoxBlink2 và CN-Celeb chỉ cho nghiên cứu 🔬 | — | — | 10/10 | sha256 `1e0716c2…94e6a` (M), `e0f30343…bedea` (b6), ghim ở `configs/scenes/speaker.yaml`; bản phát hành không công bố sha256 | ở `cache/spk_ref/redimnet_{m,b6}` |
| ReDimNet2 của PalabraAI, `b6-vb2+vox2+cnc2_v0-lm.pt` (bản phát hành `v1.0.0` của `PalabraAI/redimnet2`), mã `redimnet2/` của `PalabraAI/redimnet2@c5bbe0b7`; học trên VoxBlink2, VoxCeleb2, CN-Celeb2 | như dòng trên, qua `ml/spk_ref/redimnet`; **chỉ khảo sát** | mã MIT; trọng số không ghi giấy phép, kho học VoxBlink2 và CN-Celeb chỉ cho nghiên cứu 🔬 | — | — | 10/10 | sha256 `287365f6…9868`, trùng sha256 bản phát hành công bố, ghim ở `configs/scenes/speaker.yaml` | ở `cache/spk_ref/redimnet2_b6` |
| Thu qua board | `wake`, lệnh, tập thử | của dự án, có phiếu đồng ý | | | | | 115 phiên ở `ml/data/manifests/device/board_b.csv`; tiếng ở data root, không vào git (KẾ HOẠCH §1.4) |
| Bản thu hai kênh của repo `tinyai-signal` (dự án trước, cùng board B, micro cách 45 mm), commit `642e6e4` | chỉ bàn so làm sạch `afe_compare` (KẾ HOẠCH §3.16), không vào split nào | của chủ dự án; tiếng người, không vào git | 0,025 (6 tệp, 89,1 s): luân phiên 30 s, đè nhau 10 s, từng người 2 × 15 s, giọng cùng loa nhạc 9,6 s và đầu ra chuỗi lọc của `tinyai-signal` trên nó 9,5 s | hai người A, B, chưa mã hoá `spk_NNN`, không có phiếu trong repo này | 30/09, chép tay | sha256 từng tệp (`manifests/device/tinyai.yaml`) | ở `raw/device/tinyai`; 16 kHz `int16`, kênh đúng quy ước của repo; `pcm_shift` lúc thu không ghi |

VietMed (`doof-ferb/VietMed_labeled`) tải ngày 06/10 rồi xoá: tiếng điện thoại 8 kHz, board thu 16 kHz.

Trên máy còn một kho tải từ trước cho dự án khác, **chưa nhập** vào `raw/`: LibriSpeech `dev-clean`, `test-clean`, `train-clean-100` (tiếng Anh, CC BY 4.0, 7 GB). Nhập thì theo đúng cách của VIVOS: chép nguyên vào `raw/`, manifest có sha256 cây, một dòng ở bảng trên.

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
| LSVSC (06/10) | 5 mẩu, 0,01 h | nhịp lời ngoài khoảng 5 | 100,65 |
| Speech-MASSIVE (06/10) | 111 mẩu, 0,15 h | nhịp lời ngoài khoảng 108, câm 3 | 5,29 |
| ViMD, đoạn (06/10) | 64 đoạn, 0,10 h | trùng 45, nhịp lời ngoài khoảng 19 | 97,06 |
| DNS Challenge | 132 mẩu, 0,36 h | câm 109, trùng 23 | 180,09 |
| MUSAN | 1 mẩu | câm | 109,29 |
| OpenSLR 28 | 843 mẩu, 5,91 h | `pointsource_noises` là bản chép nhiễu MUSAN: 842 trùng | 20,34 |
| DEMAND, Speech Commands | 0 | | 22,67; 0,11 |

## 2. Split

Mỗi split là một thư mục `ml/data/splits/<nhánh>/<split>/`: các file danh sách và `SPLIT.md` ghi luật, seed, số mẩu và
sha256 từng file. Cột sha256 là tám ký tự đầu của sha256 tệp `split.lock` mà một lượt học trên đủ các file `.txt` của
split ghi (`srpipe.core.run_dir.split_lock`). Phiên board không nằm trong file danh sách mà ở `eval.board.train` của cấu
hình nhánh, nên split chỉ thêm phiên board thì trùng băm với bản trước. Mỗi dòng sau dòng đầu chỉ ghi phần khác bản
trước; "của vN" nghĩa là file trùng từng byte.

**`wake`**, seed 20260928:

| Split | Ngày | File | Luật, phần khác bản trước | sha256 `split.lock` |
|---|---|---|---|---|
| v1 | 28/09 | `train_pos` 3 466 (1,0 h TTS), `train_neg` 122 483 (100,1 h), `val_pos` 118, `val_neg` 2 497 (3,3 h), `test_neg` 20 717 (22,9 h) | theo người nói; giọng nhân bản theo vai của người được nhân bản; `test_neg` là Common Voice và VIVOS test, không kho nào làm giọng mẫu; không âm bản nào nói từ đánh thức | `6f81be63…` |
| v2 | 29/09 | năm file của v1; thêm `train_hard` 2 590 (4,19 h), `val_hard` 8 | âm bản khó: TTS `synth_hard`, 370 cụm gần âm, 1 979 câu kho học khớp `split.hard`; không câu nào chứa cụm của phiên board 039, giữ lại để đo tổng quát hoá | `41c6fe2e…` |
| v3 | 29/09 | `val_neg` 8 545 (9,45 h), `test_neg` 14 669 (16,82 h); còn lại của v2 | 40% người nói Common Voice chuyển trọn từ `test_neg` sang `val_neg`, để mục tiêu báo nhầm của `val` dựa trên nhiều lần vượt | `60544ef5…` |
| v4 | 01/10 | `train_pos` 2 243 (0,42 h), `train_neg` 122 229, `val_pos` 74, `val_neg` 8 532; `test_neg` của v3; không có `*_hard` | từ "trợ lý" (ADR-0011): 2 219 mẩu TTS và 24 mẩu thật của `hf_extract`; âm bản bỏ câu đọc như "trợ lý" giọng Bắc | `92d35d27…` |
| yes1 | 01/10 | `train_pos` 3 228, `val_pos` 419, `train_hard` 3 000, `val_hard` 600; `train_neg`, `val_neg`, `test_neg` của v4 | thử "yes" của Speech Commands, các từ khác làm âm bản khó | `dc1d6635…` |
| on1 | 29/10 | `train_pos` 3 086, `val_pos` 396, `train_hard` 3 000, `val_hard` 600, `train_neg` 59 403 (100 h), `val_neg` 8 531; `test_neg` của v3 | từ tạm "on" (ADR-0029); `train_neg` rút mới, không người nói nào của `val`, `test`; `val_neg` là của v4 trừ một mẩu TTS gần âm "trợ lý" | `be597fd6…` |
| on2 | 29/10 | `train_hard` 30 000 (8,17 h); còn lại của on1 | thêm 27 000 âm bản khó rút sau `val` | `03c25f9f…` |
| happy1 | 31/10 | `train_pos` 1 632, `val_pos` 203, `train_hard` 30 000, `val_hard` 600, `train_neg` 59 560; `val_neg` của on1, `test_neg` của v3 | từ tạm "happy" (ADR-0033, E11-T39), split của lock sản phẩm; âm bản khó là các từ khác, cả "on", rút như on2; `train_neg` rút mới | `f73e4987…` |
| vung1 | 01/11 | `train_pos` 1 676 (0,60 h), `train_neg` 59 939, `val_pos` 68, `val_neg` 18 494 (27,6 h), `train_hard` 826, `val_hard` 11; `test_neg` của v3 | thử "vừng ơi" (ADR-0040): mẫu dương và gần âm OmniVoice, 20% giọng nhân bản và giọng thiết kế vào `val`; `train_neg` rút mới; `val_neg` là ViMD 11 029 mẩu, Common Voice 6 048, VIVOS 1 411 và 6 mẩu TTS gần âm; lượt học còn lấy 32 phiên board | `7cdd7cfd…` |

**`command`**, seed 20260928; `val` 1 583 (1,9 h) và `test` 2 000 (2,0 h, 59 người nói) giữ nguyên từ v1 tới v9:

| Split | Ngày | File | Luật, phần khác bản trước | sha256 `split.lock` |
|---|---|---|---|---|
| v1 | 28/09 | `train_*` một file mỗi kho, 758 897 mẩu (626,2 h) | theo người nói; FPT, VLSP, Bud500 chỉ vào học; 196 câu chứa "chụp ảnh" rời tập học (lệnh chưa học, E11-T13) | `4bfcd902…` |
| v2 | 01/10 | `train_*` 97 443 mẩu (103,4 h) | trần giờ rút theo seed: VLSP 15 h, Bud500 25 h | `dc992c77…` |
| v3 | 02/10 | của v1 | bỏ trần giờ | `4bfcd902…` |
| v4 | 04/10 | của v3 | thêm 10 phiên board 20261004_home_001…010 vào học, câu 0,8–2,4 s, shard lặp 75 lần | `4bfcd902…` |
| v5 | 05/10 | thêm `val_commands` 216 | tới 60 mẩu người thật mỗi lệnh đã học từ `hf_extract`, không học, chỉ để chọn δ₁, δ₂ | `c0951d0d…` |
| v6 | 06/10 | bỏ `train_bud500`: `train_*` 109 887 mẩu (164,2 h) | Bud500 đã xoá khỏi máy; "chụp ảnh" rời học 63 câu | `e4cdfaca…` |
| v7 | 06/10 | thêm `train_lsvsc` 56 808 (100,6 h), `train_speech_massive_vi` 5 001 (5,3 h), `train_vimd` 55 117 đoạn (97,0 h): `train_*` 226 810 mẩu (367,2 h) | câu nguồn của `val_commands` rời học (17 mẩu); "chụp ảnh" 91 câu | `0bcd2604…` |
| v8 | 07/10 | của v7 | thử cao độ SwiftF0 (E11-T12): đặc trưng dựng riêng, log-mel nối từ v7; split của lock sản phẩm | `0bcd2604…` |
| v9 | 29/10 | của v8 | front của hợp đồng `listen.yaml` (E11-T37), không nối log-mel của v7 | `0bcd2604…` |

**`command_kws`**, seed 20260930, các split thử KWS của E11-T17 (`measurements/speech_commands.md`):

| Split | Ngày | File | Luật | sha256 `split.lock` |
|---|---|---|---|---|
| p2 | 01/10 | `train` 3 308, `val` 414 mẩu | bốn lệnh của `kws_vi_command`, mẩu thử của chính kho làm `val` | `99e0993d…` |
| p4 | 01/10 | `train` 3 800, `val` 1 400 | mười từ của Speech Commands, 200 mẩu học mỗi từ | `d93afc11…` |
| p5 | 01/10 | `train` 9 458, `val` 1 224 | "yes" và "no" với mọi mẩu của kho | `d7ee166b…` |

**`ns`**, seed 20260930:

| Split | Ngày | File | Luật | sha256 `split.lock` |
|---|---|---|---|---|
| v1 | 01/10 | `train` tiếng 150 614 mẩu (135,1 h: VIVOS train, FPT, VLSP, Bud500), nhiễu 59 845 tệp (201,4 h), nền phòng 52 (0,4 h); `val` 1 520 mẩu (1,8 h, 25 người nói), nhiễu 14,6 h; `test` 1 780 (1,7 h, 57 người nói), nhiễu 13,5 h | tiếng học qua luật sạch từng kho và dải tần ≥ 7 kHz đo từng mẩu, không trần giờ, không Common Voice; `val`, `test` là dòng của `command/v1` qua khoảng động ≥ 30 dB và cùng luật dải tần, người nói của chúng không vào học; nhiễu chia vai theo nhóm nguồn (Freesound, nghệ sĩ, môi trường DEMAND, phòng, tệp), không nhóm nào ở hai vai; giấy phép theo nguồn ở §1, VIVOS và Bud500 phi thương mại | `2d6a8556…` |

## 3. Đặc trưng qua đường mô phỏng board

Mỗi tập một dòng: `python -m srpipe.scenes.device build <file split> <tập>` ghi `processed/<tập>/` (KẾ HOẠCH §1.2), cấu
hình và sha256 ở `manifest.yaml` của tập. Kho phòng `interim/scenes/device/rooms/` (512 phòng, RT60 đo 0,18 / 0,46 / 0,74 s
ở p5 / p50 / p95) dựng một lần trong ~1,5 phút với 8 tiến trình.

| Tập | File split | Mẩu | Giờ đặc trưng | Dựng | Cỡ trên đĩa | Ngày |
|---|---|---|---|---|---|---|
| `command/vivos_test` | VIVOS test, 19 người nói, lập tay để đo chi phí | 760 | 0,876 | 2,9 phút trên 3 tiến trình, ~8 lần thời gian thực mỗi nhân | 36 MB/giờ đặc trưng, 115 MB/giờ PCM sạch | 27/09 |

**Tốc độ dựng** (04/10, i7-12700H, WSL 10 GB, đo khi 14 tiến trình của bản dựng `command/v3` cùng chạy). Với chuỗi đủ module,
`pitch` và tăng giảm tốc độ, mỗi nhân chỉ dựng chừng 1,6 lần thời gian thực; profile hai phiên BUD500 (16 mẩu) cho thấy
thời gian nằm ở các vòng lặp Python của bản soi gương: bộ giới hạn của `agc` chạy từng mẫu qua ba hàng đợi 24%, `pitch`
20% (một nửa là biến đổi khoảng cách), `vad` 17% (bộ lọc tách băng từng mẫu), `ns_omlsa` 14%, đọc và giải mã tiếng 11%.
Các vòng nóng của bản soi gương biên dịch bằng numba (KẾ HOẠCH §4.5.1), cùng thứ tự phép float32: bộ giới hạn và tổng
năng lượng của `agc`, biquad của `hpf`, cả `vad` (bộ lọc tách băng, DF-I, GMM, bộ dò mức nền), cả một bước `ns_omlsa`
cùng các hàm dùng chung mà `gsc`, `doa`, `bss` gọi, và của `pitch` biến đổi khoảng cách, NCCF ra POV, đặc trưng đường.
Số đo lúc chuyển bốn vòng đầu:

| Đo | numpy | numba | Bit |
|---|---|---|---|
| Biến đổi khoảng cách, 400 trạng thái | 0,45 ms/lần | 5,7 µs/lần | 300/300 ca ngẫu nhiên trùng |
| `agc`, 60 s tiếng có bùng nổ | 1,62 s | ~0,03 s, chưa tính 0,35 s biên dịch lần đầu | trùng |
| Một shard 32 mẩu BUD500, `agc` và `pitch` | 51,3 s | 32,3 s (1,59×) | 4/4 tệp trùng sha256 |
| Dựng lại shard 3 của `command/v3/train_vivos`, đủ bốn vòng | 371 s (chỉ `agc`, `pitch`) | 151 s | 4/4 tệp trùng bản đã dựng |

Mọi bộ vàng của `srpipe.dsp` sinh lại không đổi tệp nào. Còn Python và numpy: vòng từng khung của `pitch` (lọc hạ mẫu,
NCCF) và phép dò của `doa` ngoài `_steer`. Chỗ chậm còn lại của một lượt dựng chưa profile 🔬.

## 4. Bản thu qua board

Mỗi phiên một dòng. Mã người nói `spk_NNN`; tên thật chỉ nằm trong bảng ngoài repo (KẾ HOẠCH §1.4).

| Phiên | Ngày | Phòng | Người nói | Mã phiếu | Nội dung | Phút | Hở `seq` |
|---|---|---|---|---|---|---|---|
