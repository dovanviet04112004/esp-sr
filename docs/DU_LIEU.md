# DU_LIEU.md

Dữ liệu đã tải và đã xử lí — số đo trên đĩa, không phải kế hoạch. Ứng viên và luật chia ở KẾ HOẠCH §1.2,
§1.3; giấy phép và dữ liệu cá nhân ở §1.4. File này chỉ ghi thứ đã thật sự nằm trên đĩa.

Đường dẫn gốc khai ở `ml/configs/common/paths.yaml`. `raw/` chỉ đọc; `interim/` và `processed/` sinh lại
được bằng một lệnh.

## 1. Nguồn

| Nguồn | Dùng cho | Giấy phép | Giờ | Người nói | Tải ngày | sha256 bản tải | Trạng thái |
|---|---|---|---|---|---|---|---|
| Common Voice tiếng Việt | `command`, âm bản `wake` | CC0 | | | | | chưa tải |
| VIVOS | `command`; tập thử của `vad` | CC BY-NC-SA 4.0 | 15,67 (thử 0,75) | 65 (thử 19) | 07/09, vào `raw/` 27/09 | cây `6325ac21…` (`manifests/speech/vivos.yaml`) | ở `raw/speech/vivos` |
| FPT Open Speech Data | `command` | 🔬 chưa kiểm | | | | | chưa tải |
| MUSAN | `ns`, tăng cường | CC BY 4.0 phần lớn 🔬 | | — | | | chưa tải |
| DEMAND | `ns` | CC BY-SA 3.0 🔬 | | — | | | chưa tải |
| OpenSLR 28 | tăng cường vang | Apache 2.0 | | — | | | chưa tải |
| Speech Commands v0.02, chỉ `_background_noise_` | nhiễu của cảnh `vad` | CC BY 4.0 | 0,11 | — | 07/09, vào `raw/` 27/09 | cây `ded5bbb6…` (`manifests/noise/speech_commands.yaml`) | ở `raw/noise/speech_commands` |
| Thu qua board | `wake`, lệnh, tập thử | của dự án, có phiếu đồng ý | | | | | chưa thu |

Trên máy còn ba kho tải từ trước cho dự án khác, **chưa nhập** vào `raw/`: VLSP 2020 (tiếng Việt, 35 tệp parquet, 11 GB, giấy phép 🔬 chưa kiểm), LibriSpeech `dev-clean`, `test-clean`, `train-clean-100` (tiếng Anh, CC BY 4.0, 7 GB) và phần lời của Speech Commands (tiếng Anh, CC BY 4.0). Nhập kho nào thì theo đúng cách của VIVOS: chép nguyên vào `raw/`, manifest có sha256 cây, một dòng ở bảng trên.

## 2. Split

| Nhánh | Split | Luật | Seed | sha256 `split.lock` | `SPLIT.md` |
|---|---|---|---|---|---|

## 3. Bản thu qua board

Mỗi phiên một dòng. Mã người nói `spk_NNN`; tên thật chỉ nằm trong bảng ngoài repo (KẾ HOẠCH §1.4).

| Phiên | Ngày | Phòng | Người nói | Mã phiếu | Nội dung | Phút | Hở `seq` |
|---|---|---|---|---|---|---|---|
