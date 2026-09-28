# ai_engine

Tầng L3, mọi mô hình học: `ns`, `wake`, `command`, `synth`, chạy trên `esp-dl` từ slot model đang dùng
(KẾ HOẠCH §1.1, §4.5.4). C++ sau mặt tiền C `ai_engine.h`; `-fno-exceptions -fno-rtti`, không `new`/`delete`
sau boot.

| Mục | Nội dung |
|---|---|
| Phụ thuộc | `common`, `dsp_afe` (kiểu của khe `ns`); riêng: `sys_storage` (mmap slot), `mbedtls` (`esp_sha` trên bộ SHA phần cứng), `espressif/esp-dl` `==3.3.11`, cặp với ESP-PPQ `==1.3.11` |
| Cấu trúc | `src/core/` nạp ảnh, kiểm `grid_hash`, chép trọng số lên PSRAM, không biết tên model nào; mỗi model một thư mục `src/{ns,wake,command,synth}/` với tiền xử lý và hậu xử lý riêng |
| Hiện có | `src/core/model_image` (E11-T9): `ai_engine_load` bỏ ảnh đang giữ, map slot, từ chối `grid_hash` khác lưới của bản dựng (`ESP_ERR_INVALID_VERSION`), chép từng mục lên PSRAM căn 16 B và băm sha256 **bản chép** so với đầu ảnh (`ESP_ERR_INVALID_CRC`); lỗi nào cũng để lại không ảnh nào. `src/core/espdl_net` (E11-T10) dựng một mạng esp-dl từ mục đã nạp, mọi tensor và bộ đệm `StreamingCache` ở PSRAM, chạy một bước khởi động rồi `reset` để không cấp phát sau boot. Bốn nhánh vẫn là bản giả trung tính của E3-T4: `ai_engine_has` luôn sai, `wake`/`command` trả `ESP_ERR_INVALID_STATE`, `synth` trả `ESP_ERR_NOT_SUPPORTED`, `ns_ops` là `NULL` |
| Bộ nhớ | mọi trọng số và vùng làm việc ở PSRAM, chép từ flash lúc `ai_engine_load` (KẾ HOẠCH §6.5) |
| Kiểm | `test_apps/host`: thân C++ dựng bằng g++ trên `sys_storage` giả và `esp_sha` qua OpenSSL; ảnh đúng, sai lưới, lật một bit. `test_apps/unit` (`make ai-unit`): cùng các ca trên slot 1 của board B với một mục 1 MB, đo thời gian nạp và PSRAM giữ; rồi TCN mẫu của `make ai-probe` chạy dòng 200 bước so mô phỏng ESP-PPQ; slot 1 để trống khi xong |
