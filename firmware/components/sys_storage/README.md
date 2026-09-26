# sys_storage

Tầng L2: cửa duy nhất vào NVS, LittleFS và phân vùng thô (CLAUDE.md §4.1). Mọi khuôn dữ liệu trên flash
khai ở `include/storage_format.h`, kèm `static_assert` chốt `sizeof` (KẾ HOẠCH §6.2, §6.3).

| Mục | Nội dung |
|---|---|
| Phụ thuộc | `common`; riêng tư `nvs_flash`, `esp_partition`, `esp_hw_support` (MAC), `joltwallet/littlefs` `^1.22` |
| Khoá | `m_storage`, khoá lá, chờ tối đa 200 ms (KẾ HOẠCH §5.3); không gọi từ nhân 1 |
| NVS | mở theo từng lần gọi: đọc `READONLY` nên không tạo namespace, ghi `READWRITE` rồi commit |
| Kiểu | `u8` `i8` `u16` `u32` `str` `blob` đúng như bảng §6.2; đọc bằng kiểu khác trả `ESP_ERR_NOT_FOUND` |
| Chuỗi rỗng | đọc ra là vắng (`ESP_ERR_NOT_FOUND`), ghi thì bị từ chối |
| Bộ gieo | `sys_storage_seed(seed_ver, …)`: luôn ghi khoá vắng; ghi đè khoá khác giá trị chỉ khi `sys/seed_ver` nhỏ hơn. Bảng gieo và `APP_SEED_VER` nằm ở `main` |
| `deviceId` | `device/serial` nếu có, không thì `sr-` + MAC eFuse 12 hex thường |
| File | `write_file` ghi `path.tmp` rồi một lần `rename`; `lfs_rename` thay đích trong một commit nên mất điện chỉ để lại file cũ hoặc file mới |
| Ảnh model | `map_models(slot)` mmap chỉ đọc, kiểm magic, `format_ver`, biên từng entry; `grid_hash` do `ai_engine` kiểm |
| Kiểm | `test_apps/unit`: tự cắt điện 20 lần giữa lúc ghi `set.json` bằng reset từ ISR ở IRAM, rồi 11 case NVS, file, ảnh model |
| Số đo | thời gian thay một file 12 KB: `docs/measurements/latency.md` §2 |
