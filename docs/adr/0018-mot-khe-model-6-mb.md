# ADR-0018 — Một khe model 6 MB thay hai khe 3 MB

- **Trạng thái**: Chấp nhận (chủ repo 04/10: mạng `command` lớn hơn, chia lại flash cho nó)
- **Ngày**: 2026-10-04
- **Liên quan**: KẾ HOẠCH §3.12, §6.1, §6.2, §6.3, §7.6; ADR-0014, ADR-0016, ADR-0017; TASKS E11-T20

---

## Bối cảnh

Bảng phân vùng của KẾ HOẠCH §6.1 có hai khe model 3 MB, `models_0` và `models_1`, để cập nhật model hỏng thì quay về
khe kia. Sau `ns` 337 KB (NSNet-16k L, ADR-0014) và `wake` khoảng 100 KB 🔬, một khe còn khoảng 2,5 MB cho `command`.

ADR-0017 cho `command` một mạng lớn hơn. Bản chủ repo duyệt có bề rộng 160 và feedforward 320 (MultiNet7 là 128 và 256):
encoder và đầu CTC 3 138 429 tham số, bộ dự đoán và bộ nối RNN-T của ADR-0016 thêm 245 421, tức khoảng 3,5–3,7 MB int8
🔬 cho riêng `command`. Cộng `ns` và `wake` là khoảng 4 MB 🔬, vượt một khe 3 MB. KẾ HOẠCH §6.1 đã ghi sẵn đường lùi: bỏ
`models_1`, cho `models_0` 6 MB, quyết bằng ADR.

## Các phương án

| Phương án | Được | Mất |
|---|---|---|
| A. Giữ hai khe 3 MB, giữ mạng 128 | quay về model cũ khi cập nhật hỏng | không có chỗ cho mạng lớn hơn của ADR-0017 |
| B. Thu hai khe app từ 3 xuống 2 MB để có hai khe model 4 MB | giữ hai khe model | app đã cỡ 2 MB 🔬 với esp-dl, Wi-Fi, MQTT, TLS nên hết biên; 4 MB vẫn sát cho `command`, `ns`, `wake` |
| **C. Bỏ `models_1`, `models_0` 6 MB ở đúng địa chỉ cũ** | đủ chỗ cho mạng mới cùng `ns`, `wake`, còn khoảng 2 MB 🔬; `nvs`, `voice`, `storage`, `coredump` không dời, nên khoá NVS, hiệu chuẩn và bộ lệnh trên board còn nguyên | cập nhật model hỏng giữa chừng thì máy chạy thiếu model ấy tới lần cập nhật sau |

## Quyết định

- **C.** `firmware/partitions.csv`: `models_0` ở 0x620000, 6 MB; không còn `models_1`.
- NVS bỏ `model/active_slot`; `app_boot` luôn nạp `models_0`. `ai_engine_load(slot)` giữ chữ ký của hợp đồng đóng băng
  (KẾ HOẠCH §4.5.5); khe 1 không có phân vùng thì trả lỗi không thấy.
- Định dạng ảnh model không đổi nên `format_ver` giữ 1: ảnh đang nằm ở `models_0` vẫn đọc được với bảng mới.
- Cập nhật model ghi đè `models_0`. `ai_engine_load` kiểm sha256 từng mục, nên ảnh ghi dở không bao giờ được nạp
  (KẾ HOẠCH §7.6).
- Unit test trên board (`ai_engine`, `svc_listen`, `sys_storage`) dựng với `firmware/test_apps/partitions_unit.csv`:
  bảng sản phẩm, riêng vùng 3 MB của `ota_1`, nơi test không bao giờ cập nhật OTA, là khe nháp `models_1`. Probe, bản
  ghi từng vòng của `make listen-unit` và các ca tự dựng ảnh model giữ nguyên chỗ, còn `models_0` mang model như sản
  phẩm.

## Hệ quả

- Đổi bảng phân vùng là nạp lại qua cổng CH340 (KẾ HOẠCH §6.1). Board B nhận bảng mới cùng firmware 80 dải khi model 80
  dải được khoá (ADR-0017); tới lúc ấy board giữ bản dựng cũ, đọc đúng ảnh ở `models_0` vì địa chỉ không đổi.
- `make models-flash` ghi một phân vùng; `pack_models` lấy cỡ khe từ bảng, nên trần ảnh tự lên 6 MB.
- `sys_storage_map_models` ánh xạ cả phân vùng lúc nạp: 6 MB thay 3 MB, 96 trang MMU 64 KB thay 48 🔬; probe của mạng
  mới trên board B là lần nạp đầu với bảng này.
- Sau unit test, vùng `ota_1` của board mang rác của test. Firmware sản phẩm nạp lại đặt otadata về `ota_0`, nên vùng
  ấy chỉ bị ghi đè ở lần OTA sau.
