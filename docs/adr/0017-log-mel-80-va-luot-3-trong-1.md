# ADR-0017 — Log-mel 80 dải cho mọi nhánh, cùng một lượt với mạng `command` lớn hơn và giọng người dùng

- **Trạng thái**: Chấp nhận (chủ repo 04/10: 80 dải, mạng lớn hơn và học giọng chủ repo trong cùng một lượt; số dải đổi
  được bằng một con số cấu hình)
- **Ngày**: 2026-10-04
- **Liên quan**: KẾ HOẠCH §1.3, §3.3, §3.11, §3.12, §4.5.5, §5.4; ADR-0010, ADR-0013; TASKS E11-T20

---

## Bối cảnh

ADR-0010 chọn log-mel 40 cộng ba chiều cao độ và ghi điều kiện xét lại: các cặp lệnh chỉ khác thanh trượt Cửa 3 trong
khi các lệnh khác đạt. Đo ngày 04/10 (`measurements/command.md` §6, §8, §9): "tắt" của chủ repo nghe thành "bật" (ă thành
â, sắc thành nặng) ở 21–22 trên 22 câu của phiên Cửa 3 với cả model đang khoá lẫn mạng v3, trong khi tám lệnh còn lại
đúng 85–100%; trên các phiên chủ repo thu ngày 04/10 mạng v3 đúng khoảng 18 trên 22 câu "tắt", tức ranh giới mỏng, đổi
cách đọc một chút là lật. MultiNet7 cả bản tiếng Anh lẫn tiếng Trung nhận fbank 80 chiều (ADR-0013).

Phân biệt ă/â, ơ/ô/o nằm ở F1 và F2. Trên 20–7 600 Hz, 40 dải mel cách nhau khoảng 77 Hz ở 600 Hz và 131 Hz ở 1,5 kHz;
80 dải cách nhau khoảng 39 Hz và 65 Hz.

## Các phương án

| Phương án | Được | Mất |
|---|---|---|
| A. Giữ 40 dải, chỉ mạng lớn hơn và giọng người dùng | không mô phỏng lại; so được từng thay đổi | không dùng độ phân giải mịn hơn mà chính MultiNet7 dùng |
| **B. 80 dải cho mọi nhánh, vẫn một nguồn `contracts/listen.yaml`** | đỉnh phổ của nguyên âm mịn gấp đôi; khớp MultiNet7; một con số đổi qua lại | mô phỏng lại toàn bộ tập học, khoảng 8–10 giờ; mel trên board khoảng gấp đôi 🔬; tích chập đầu và phép chiếu của `ctc` lớn hơn; model 40 dải không chạy trên firmware 80 dải |
| C. 80 dải cho `command`, 40 cho `wake` | `wake` không đổi | hai bộ lọc mel mỗi bước trên board; hợp đồng có hai số dải |

## Quyết định

- **B.** `contracts/listen.yaml` `features.n_bands` là 80. Đổi về 40 là sửa đúng con số ấy, sinh lại code, dựng lại
  firmware và mô phỏng lại vào một phiên bản `processed/command/<version>` mới; mỗi bản dựng ghi số dải của nó, nên bản
  40 dải `command/v3` vẫn giữ để so.
- `svc_listen` lúc khởi động kiểm model lệnh đã nạp nhận đúng `GEN_LISTEN_N_BANDS` cộng ba chiều cao độ; lệch thì từ
  chối chạy, không bao giờ đưa khung 80 dải cho model 40 dải.
- Cùng lượt này mạng `command` lớn hơn, cỡ chọn bằng probe trên board trong ngân sách §3.3, và học thêm các phiên thu qua
  board mà split giao cho `train` (KẾ HOẠCH §1.3). Chủ repo chọn một lượt gộp cả ba thay vì so từng thay đổi, nên kết
  quả không tách được phần của từng thay đổi.

## Hệ quả

- Split `command/v4`: các danh sách tiếng nói như `command/v3` cộng danh sách phiên board `train`; mô phỏng 80 dải vào
  `processed/command/v4`.
- Bộ vàng của `dsp_spec/mel` và mọi bộ vàng dùng log-mel sinh lại với 80 dải; `make parity-host` và `make parity-board`
  kiểm lại.
- `wake` và `command` `kws` học lại trên 80 dải khi được làm tiếp; mạng hiện có của chúng không nằm trong ảnh sản phẩm.
- Firmware dựng từ HEAD chỉ chạy model 80 dải: board giữ bản dựng 40 dải tới khi model mới được khoá.
- `AI_ENGINE_COMMAND_CTC_FEATURES_MAX` nâng từ 64 lên 96 cho 83 chiều mỗi bước.
