# contracts/

Hợp đồng dùng chung cho `ml/`, `firmware/` và `host/` — nguồn sự thật duy nhất (KẾ HOẠCH §4.2).
Không bên nào định nghĩa lại những gì nằm ở đây; mỗi bên sinh code bằng một lệnh:

```bash
make gen          # python3 tools/gen_contracts.py
make check        # sinh lại rồi git diff --exit-code
```

| File | Là gì | Sinh ra |
|---|---|---|
| `grid.yaml` | lưới thời gian §3.1: tần số lấy mẫu, bước khung, cỡ FFT | `gen_grid.h`, `srpipe/generated/grid.py` |
| `array.yaml` | dàn micro §2.3: khoảng cách, thứ tự kênh, quy ước dấu | `gen_array.h`, `srpipe/generated/array.py` |
| `stream/frame.yaml` | khuôn nhị phân một khung luồng tiếng §7.4 | `gen_stream.h`, `srhost/generated/stream.py` |
| `schema/*.schema.json` | payload MQTT §7.3, và khuôn `responses/` | `gen_payload.h`, `srhost/generated/payload.py` |
| `mqtt_topics.yaml` | topic, QoS, retained, chiều, schema, nhịp | `gen_topics.h`, `srhost/generated/topics.py` |
| `commands/default_vi.json` | bộ lệnh mặc định, hợp lệ theo `command_set` | nướng vào LittleFS |
| `responses/vi.json` | câu trả lời theo id, hợp lệ theo `responses` | nướng vào LittleFS |
| `golden/<khối>/` | vector vàng `.gold` + `tolerance.yaml` | Python ghi, C đọc |
| `models.lock.json` | model đang deploy: file, sha256, `grid_hash`, nguồn dữ liệu | đọc thẳng |

## Luật viết schema

Generator dựng struct C cỡ cố định từ schema, nên mỗi schema phải nằm trong tập con này:

- Gốc là `object`, `additionalProperties: false`.
- Mọi `string` có `maxLength`; mọi `array` có `maxItems`.
- `maxLength` đếm **ký tự**, C cần **byte**. Chuỗi có `pattern` ASCII được cấp đúng `maxLength` byte;
  chuỗi không có `pattern` là UTF-8 và được cấp `4 × maxLength` byte — một chữ Việt như "ệ" đã là 3 byte.
  Chuỗi kỹ thuật (id, phiên bản, host, url) vì thế luôn mang `pattern`.
- Chuỗi dài hơn bộ đệm bị **từ chối** lúc phân tích, không bị cắt.
- Mọi `integer` có `minimum` và `maximum` — generator chọn kiểu C nhỏ nhất chứa được.
- Giá trị liệt kê dùng `enum` các chuỗi IN HOA tiếng Anh.
- Ràng buộc theo trường hợp viết bằng `allOf` + `if`/`then`; generator bỏ qua chúng, bộ kiểm schema thì không.

Chỉ giá trị gốc nằm ở YAML. Giá trị suy ra — số vạch, số khung mỗi giây, băm của lưới — do generator
tính, để không có bản sao thứ hai.
