# RAM

## 1. Vùng làm việc theo module (E3-T3, rồi đo lại ở bước 4 của mỗi module)

| Component | Module | `sizeof` trạng thái B | Vùng `hot` B | Vùng `cold` B | Ước hay đo |
|---|---|---|---|---|---|

## 2. Ngăn xếp task (E14-T3)

| Task | Cấp B | Watermark còn trống B | Biên B | Profile | Ngày |
|---|---|---|---|---|---|

## 3. Heap sau boot (E5-T11)

| Mốc | Heap nội còn B | Mảnh liền lớn nhất B | PSRAM còn B | Profile | Ngày |
|---|---|---|---|---|---|
| `heap_init` lúc khởi động, app chưa có gì, chưa Wi-Fi | 346292 (323984 + 22308) | 323984 | 8 388 608 (pool 8192 KiB) | `dev` @ `32e9b55` | 26/09 |

Đọc từ log `heap_init` của board B qua CH340. Đây là **trần** cho mọi thứ ở KẾ HOẠCH §6.5 cộng lại, trước khi Wi-Fi và lwIP lấy phần của chúng; nó khớp khoảng ước 300–340 KB của §6.5.
