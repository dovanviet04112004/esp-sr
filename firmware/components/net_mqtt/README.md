# net_mqtt

Tầng L3: MQTT tới broker của KẾ HOẠCH §7.3 (bàn thử: `sr-emqx`, §4.7). Topic và payload sinh từ `contracts/`
ở `include/gen_topics.h`, `include/gen_payload.h` — không sửa tay.

| Mục | Nội dung |
|---|---|
| Phụ thuộc | `common`, `espressif/cjson` (công khai, vì `gen_payload.h`); riêng tư `sys_storage`, `espressif/mqtt` |
| Nối | URI từ NVS `device/mqtt_uri`, lùi về `NET_MQTT_URI_FALLBACK`; username `device/mqtt_user`, lùi về `deviceId`; mật khẩu `device/mqtt_pass` |
| `status` | lúc nối gửi `ONLINE` retained QoS 1; LWT là bản `OFFLINE` retained, broker gửi sau ~1,5 × keepalive (`NET_MQTT_KEEPALIVE_S`, 15 s) |
| Gửi | `esp_mqtt_client_enqueue`: task gọi không bao giờ chờ mạng; esp-mqtt tự nối lại |
| cJSON | `cJSON_InitHooks` trỏ vào ba vùng `NET_MQTT_JSON_ARENA_BYTES` ở PSRAM xin lúc boot, cho `gui_task`, esp-mqtt và `nhin_task`; mỗi task gọi cJSON giữ một vùng từ lần cấp đầu tới khi gửi xong, rồi trả cả vùng (`FREERTOS.md` 6.3). `heartbeat` khai điểm cao nhất ở `jsonArenaPeak` |
| Khúc | `net_mqtt_publish_chunks` cắt ảnh, khung xem trực tiếp và lời ghi chú theo khúc của `contracts/vision.yaml`, ghi header và crc32 vào một đệm ở PSRAM (`net_mqtt_chunks_init`, chỉ khi có camera); chỉ `nhin_task` gọi (`FREERTOS.md` 8.20). Khung xem trực tiếp đi QoS 0 ngay, phần còn lại chỉ chờ khi outbox quá 128 KB |
| Thẻ | `net_mqtt_print_entry` in một mục nhật ký thành `.JSN` bằng chính bộ mã của payload, ở vùng JSON của task gọi, rồi đưa chữ cho hàm của nơi gọi |
| Giới hạn | mọi cJSON trong firmware đi qua ba vùng này; task thứ tư dùng cJSON cùng lúc sẽ bị từ chối và tăng `json_alloc_failures`. `prod` (`NET_MQTT_REQUIRE_TLS`) từ chối URI không phải `mqtts://`; cert CA nhúng ở E13 |
| Kiểm | client đăng ký `sr/+/up/status` thấy `ONLINE`, rút điện board thấy `OFFLINE` (E5-T9) |
