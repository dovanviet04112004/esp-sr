# ml/data/

Thư mục dữ liệu, gitignore toàn bộ trừ file này, `splits/` và mọi `manifest.yaml` (KẾ HOẠCH §4.4).

| Tầng | Nội dung | Luật |
|---|---|---|
| `raw/` | đúng như lúc tải về | **chỉ đọc**; script ghi vào đây là bug |
| `interim/` | đã giải nén, đổi định dạng, lấy mẫu lại 16 kHz | sinh lại được từ `raw/` bằng một lệnh |
| `processed/` | sẵn sàng nạp cho huấn luyện | sinh lại được từ `interim/` bằng một lệnh |
| `splits/` | danh sách file theo tập, `SPLIT.md` | ✅ commit |

Ổ C: của máy phát triển gần đầy, nên đặt dữ liệu ở ổ khác và trỏ tới bằng biến môi trường:

```bash
mkdir -p /mnt/e/esp-sr-data
echo "SRPIPE_DATA_ROOT=/mnt/e/esp-sr-data" >> ml/.env
```

Thứ đã thật sự nằm trên đĩa — nguồn, số giờ, giấy phép, sha256 — ghi ở `docs/DU_LIEU.md`.
