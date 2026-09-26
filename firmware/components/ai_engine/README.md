# ai_engine

Tầng L3, mọi mô hình học: `ns`, `wake`, `command`, `synth`, chạy trên `esp-dl` từ slot model đang dùng
(KẾ HOẠCH §1.1, §4.5.4). C++ sau mặt tiền C `ai_engine.h`; `-fno-exceptions -fno-rtti`, không `new`/`delete`
sau boot.

| Mục | Nội dung |
|---|---|
| Phụ thuộc | `common`, `dsp_afe` (kiểu của khe `ns`); `sys_storage` và `esp-dl` (ghim `==` ở E11-T10) vào cùng bộ nạp thật |
| Cấu trúc | `src/core/` nạp ảnh, kiểm `grid_hash`, chép trọng số lên PSRAM, không biết tên model nào; mỗi model một thư mục `src/{ns,wake,command,synth}/` với tiền xử lý và hậu xử lý riêng |
| Hiện có | năm file là **bản giả trung tính** của E3-T4: `ai_engine_load` báo không có ảnh (`ESP_ERR_NOT_FOUND`), `ai_engine_has` luôn sai, `wake`/`command` trả `ESP_ERR_INVALID_STATE`, `synth` trả `ESP_ERR_NOT_SUPPORTED`, `ns_ops` là `NULL` |
| Bộ nhớ | mọi trọng số và vùng làm việc ở PSRAM, chép từ flash lúc `ai_engine_load` (KẾ HOẠCH §6.5) |
| Kiểm | `test_apps/host`: thân C++ dựng bằng g++, gọi từ một test C qua mặt tiền |
