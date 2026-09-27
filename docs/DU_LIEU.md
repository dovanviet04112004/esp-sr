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
| MUSAN | `ns`, tăng cường, nhiễu của đường mô phỏng board | CC BY 4.0 phần lớn; phần nhạc mỗi tệp một giấy phép | 109,29: nhạc 42,61 (660 tệp), nhiễu 6,23 (930), lời nói 60,45 (426) | — | 27/09 | `86d1061c…` (`manifests/noise/musan.yaml`) | ở `raw/noise/musan` |
| DEMAND, 16 kHz | `ns`, nhiễu của đường mô phỏng board | CC BY 4.0 (bản ghi Zenodo) | 1,42 mỗi kênh: 17 môi trường × 5 phút, 16 kênh | — | 27/09 | md5 từng môi trường (`manifests/noise/demand.yaml`) | ở `raw/noise/demand` |
| OpenSLR 28 (RIRS_NOISES) | tăng cường vang, đường mô phỏng board | Apache 2.0 | 325 RIR thật, 60 000 RIR mô phỏng; nhiễu nguồn điểm 5,91 (843 tệp), 92 nhiễu đẳng hướng | — | 27/09 | `3b50cfde…` (`manifests/rir/openslr28.yaml`) | ở `raw/rir/openslr28` |
| Speech Commands v0.02, chỉ `_background_noise_` | nhiễu của cảnh `vad` | CC BY 4.0 | 0,11 | — | 07/09, vào `raw/` 27/09 | cây `ded5bbb6…` (`manifests/noise/speech_commands.yaml`) | ở `raw/noise/speech_commands` |
| Thu qua board | `wake`, lệnh, tập thử | của dự án, có phiếu đồng ý | | | | | chưa thu |

Trên máy còn hai kho tải từ trước cho dự án khác, **chưa nhập** vào `raw/`: LibriSpeech `dev-clean`, `test-clean`, `train-clean-100` (tiếng Anh, CC BY 4.0, 7 GB) và phần lời của Speech Commands (tiếng Anh, CC BY 4.0). Nhập kho nào thì theo đúng cách của VIVOS: chép nguyên vào `raw/`, manifest có sha256 cây, một dòng ở bảng trên.

## 2. Split

| Nhánh | Split | Luật | Seed | sha256 `split.lock` | `SPLIT.md` |
|---|---|---|---|---|---|

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
