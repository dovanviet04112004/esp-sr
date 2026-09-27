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
| Bud500 (VietAI) | `command`: người nói ba miền | CC BY-NC-SA 4.0, chỉ nghiên cứu | ~500 🔬 | không rõ | 27/09 | sha256 LFS từng tệp (`manifests/speech/bud500.yaml`) | đang tải |
| MUSAN | `ns`, tăng cường | CC BY 4.0 phần lớn; phần nhạc mỗi tệp một giấy phép | | — | 27/09 | `86d1061c…` (`manifests/noise/musan.yaml`) | đang giải nén |
| DEMAND, 16 kHz | `ns` | CC BY 4.0 (bản ghi Zenodo) | | — | 27/09 | md5 từng môi trường (`manifests/noise/demand.yaml`) | đang tải |
| OpenSLR 28 (RIRS_NOISES) | tăng cường vang, đường mô phỏng board | Apache 2.0 | | — | 27/09 | `3b50cfde…` (`manifests/rir/openslr28.yaml`) | đang giải nén |
| Speech Commands v0.02, chỉ `_background_noise_` | nhiễu của cảnh `vad` | CC BY 4.0 | 0,11 | — | 07/09, vào `raw/` 27/09 | cây `ded5bbb6…` (`manifests/noise/speech_commands.yaml`) | ở `raw/noise/speech_commands` |
| Thu qua board | `wake`, lệnh, tập thử | của dự án, có phiếu đồng ý | | | | | chưa thu |

Trên máy còn hai kho tải từ trước cho dự án khác, **chưa nhập** vào `raw/`: LibriSpeech `dev-clean`, `test-clean`, `train-clean-100` (tiếng Anh, CC BY 4.0, 7 GB) và phần lời của Speech Commands (tiếng Anh, CC BY 4.0). Nhập kho nào thì theo đúng cách của VIVOS: chép nguyên vào `raw/`, manifest có sha256 cây, một dòng ở bảng trên.

## 2. Split

| Nhánh | Split | Luật | Seed | sha256 `split.lock` | `SPLIT.md` |
|---|---|---|---|---|---|

## 3. Bản thu qua board

Mỗi phiên một dòng. Mã người nói `spk_NNN`; tên thật chỉ nằm trong bảng ngoài repo (KẾ HOẠCH §1.4).

| Phiên | Ngày | Phòng | Người nói | Mã phiếu | Nội dung | Phút | Hở `seq` |
|---|---|---|---|---|---|---|---|
