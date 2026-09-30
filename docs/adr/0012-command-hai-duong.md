# ADR-0012 — `command` có hai đường cắm cùng một hợp đồng: DS-CNN phân lớp lệnh cố định, và CRNN + CTC của ADR-0010

- **Trạng thái**: Chấp nhận (30/09); mạng của `ctc` theo ADR-0013
- **Ngày**: 2026-09-30
- **Liên quan**: KẾ HOẠCH §3.3, §3.11, §3.12, §4.4, §4.4.1, §4.5.2, §4.5.5, §6.2, §6.3; TASKS E11-T7, E11-T8, E11-T12, E11-T13, E11-T17; ADR-0010

---

## Bối cảnh

Đường CTC của ADR-0010 còn thiếu ba khối: `dsp_spec/pitch` (E11-T8), GRU int8 chạy dòng qua esp-dl trên board
(E11-T12) và chấm có ràng buộc (E11-T13). Mẩu người thật của các lệnh phải trích lại theo luật ngắt hơi (KẾ HOẠCH
§3.11). Bản demo cần nhận lệnh ngay trong tuần, trong khi đường CTC cần thêm khoảng một tuần. Một mạng phân lớp trên
một bộ lệnh cố định học được từ chính các mẩu lệnh, không cần `lang_vi` hay CTC. DS-CNN (Zhang và cộng sự, 2017, "Hello
Edge") là mạng loại ấy, sinh ra cho vi điều khiển.

## Các phương án

| Phương án | Được | Mất | Số đo |
|---|---|---|---|
| Chỉ CTC (ADR-0010) | thêm lệnh bằng một dòng chữ; một mạng cho mọi bộ lệnh | chưa có lệnh nào chạy cho tới khi đủ ba khối trên | chưa đo |
| Chỉ DS-CNN | nhỏ, một lần chạy mỗi câu; dữ liệu chỉ là mẩu lệnh và âm bản | bộ lệnh cố định lúc học, đổi lệnh là học lại; lệnh chưa học ("chụp ảnh") không bao giờ nhận | trên Google Speech Commands, cửa sổ 1 s, 12 lớp (10 từ, `silence`, `unknown`): 94,4 / 94,9 / 95,4% ở ba cỡ 38,6 / 189,2 / 497,6 KB int8, 5,4 / 19,8 / 56,9 triệu phép mỗi lần chạy [1, bảng 5 và 7] |
| **Hai đường sau cùng hợp đồng `ai_engine_command_*`** | lệnh chạy trong tuần bằng DS-CNN, CTC tới sau; đổi đường bằng Kconfig và ảnh model; chung một thước | giữ hai nhánh code và hai bộ dữ liệu | chọn đường mặc định bằng Cửa 3 trên tập thu qua board |

## Quyết định

Hai đường, **không đổi hợp đồng đã đóng băng** (KẾ HOẠCH §4.5.5): `ai_engine_command_begin`, `_step`, `_score` giữ
nguyên. `_step` nhận một khung đặc trưng mà độ dài do model khai, `_score` trả chỉ số lệnh hoặc −1 kèm ba điểm, cùng một
khuôn cho cả hai.

- **`kws` (DS-CNN).** Cửa sổ cố định (cấu hình, khoảng 1,5 s = 94 bước 16 ms) tính ngược từ bước `vad` tắt sau câu;
  mạng chạy một lần mỗi câu. Ba cỡ S, M, L của bài [1, bảng 7] là lựa chọn cấu hình, chọn bằng Cửa 3 sau int8 trong
  ngân sách thời gian của KẾ HOẠCH §3.12 (chủ repo, 30/09). Lớp gồm các lệnh có dữ liệu của `contracts/commands/default_vi.json`, theo đúng
  thứ tự file ấy, cộng `other` và `silence`; "chụp ảnh" là lệnh chưa học của E11-T13 và đứng cuối file, nên các lớp
  lệnh là phần đầu của bộ lệnh. `other` gồm lời nói thường, cụm gần âm ("bật điện", "mở cửa sổ", "đóng góp"…) và mọi
  cụm từ liền nhau ngắn hơn một lệnh nói riêng ("bật", "đèn", "mở", "cửa", "âm lượng"…), để gần âm hay nửa lệnh không
  thành lệnh. Từ chối khi lớp thắng là `other` hoặc `silence`, khi xác suất của nó dưới ngưỡng, hoặc khi nó hơn lớp nhì
  quá ít; ngưỡng ở NVS `kws/` như `δ₁`, `δ₂`. Dương lấy từ mẩu người thật của kho trích `hf_extract`, từ
  `kws_vi_command` và từ TTS (VieNeu và F5 như `wake`, E11-T7); tất cả qua đường mô phỏng board như `wake`, rồi int8
  bằng ESP-PPQ.
- **`ctc` (CRNN + CTC).** Như ADR-0010 và KẾ HOẠCH §3.12.
- **Đặc trưng.** Khai ở cấu hình và ở `meta.json` của model: log-mel 40, hoặc log-mel 40 cộng ba chiều cao độ của
  `dsp_spec/pitch`. `kws` học đủ 43 chiều ngay từ bản đầu (chủ repo, 30/09: không học hai lượt), vì từ chối cụm gần âm
  chỉ khác thanh là việc khó nhất.
- **Bộ lệnh.** `meta.json` của `kws` ghi `backend` và `classes`. Bước đóng gói ảnh model kiểm `classes` là phần đầu của
  bộ lệnh mặc định, nên chỉ số trả về trùng chỉ số trong bảng lệnh. Khi chạy `kws`, lệnh đổi bộ lệnh qua MQTT bị từ chối
  bằng một mã lỗi; host đổi mã thành câu.
- **Code.**
  - `ml/src/srpipe/tasks/command/` giữ phần chung: `eval.py` với thước Cửa 3, `backend.py` với giao diện "cửa sổ đặc
    trưng → lệnh hoặc từ chối, kèm điểm", và `synth.py` sinh tiếng lệnh cho cả hai đường. Hai thư mục con `kws/` và
    `ctc/` theo khuôn nhánh của KẾ HOẠCH §4.4; `data.py` hiện có chuyển vào `ctc/`.
  - Cấu hình `ml/configs/models/command.yaml` chọn `backend` và `features` và giữ bộ TTS, kèm hai file
    `command_kws.yaml` và `command_ctc.yaml`; split của `ctc` chuyển sang file sau.
  - Firmware: `ai_engine/src/command_kws/` và `command_ctc/`. Kconfig `AI_ENGINE_COMMAND_BACKEND` chọn danh sách nguồn
    trong CMake, đúng luật tắt module của CLAUDE.md §4.1. Ảnh model vẫn là `firmware/models/command/`.
- **Chọn đường mặc định** của sản phẩm bằng Cửa 3 trên tập thu qua board, tách theo người nói và phòng. Tới lúc ấy mặc
  định là `kws`, đường duy nhất chạy được trong tuần.

## Hệ quả

- KẾ HOẠCH §3.12 thêm đoạn `kws`; bảng §3.3 tách `command` thành hai dòng; cây §4.4, §4.4.1 và §4.5.2 thêm hai thư mục,
  split `command_kws` và Kconfig chọn đường; §4.5.5 ghi khung đặc trưng do model khai; §6.2 ghi nghĩa của `cmd_reject`,
  `cmd_margin` theo đường; §6.3 ghi `meta.json` của `command` thêm `backend`, `classes`, `features`; hậu xử lý của
  `kws` có bộ vàng ở `contracts/golden/`.
- TASKS: E11-T17 (`kws`) bắt đầu; E11-T7 thêm bộ TTS của lệnh; E11-T12 và E11-T13 giữ nguyên cho `ctc`.
- Xét lại khi `ctc` đạt Cửa 3 trong ngân sách thời gian của §3.3: lúc ấy `kws` chỉ còn là đường dự phòng, hoặc bỏ.

## Nguồn

1. Y. Zhang, N. Suda, L. Lai, V. Chandra, *Hello Edge: Keyword Spotting on Microcontrollers*, arXiv:1711.07128, 2017.
   Số ở bảng 5 và bảng 7, đã đọc lại trên bản PDF ngày 30/09.
