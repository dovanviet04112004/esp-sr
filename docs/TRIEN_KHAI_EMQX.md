# Triển khai broker EMQX cho thiết bị esp-sr

Tài liệu cho đội server. Đọc xong là dựng được broker mà không cần đọc mã nguồn của dự án, và biết phải gửi lại
cho bên thiết bị những gì.

## 1. Bối cảnh

Thiết bị là một board ESP32-S3 nghe lệnh tiếng Việt, có camera. Nó nối Wi-Fi 2,4 GHz và nói chuyện với phần mềm
trên máy tính qua **một broker MQTT**. Broker chỉ là trạm chuyển tin: nó không chạy logic nào của dự án.

```
  board (sr-<12 hex>) ──MQTT──►  EMQX  ◄──MQTT── máy tính (tài khoản srhost)
   gửi: trạng thái, sự kiện,     (phần việc          đọc mọi tin "up/", gửi lệnh
   ảnh, kết quả nhận diện         của đội server)     xuống "down/"
   nhận: lệnh, bộ lệnh, danh sách người nói
```

Điểm cần nhớ:

- Mỗi board là một client riêng, **username và clientid đều là `deviceId`** của nó, dạng `sr-` cộng 12 ký tự hex
  chữ thường (ví dụ `sr-30eda01e4630`). Tối đa 32 ký tự.
- Phía máy tính là **một** client dùng tài khoản `srhost`.
- Giao thức: **MQTT 3.1.1**, client là esp-mqtt của ESP-IDF. Không dùng MQTT 5, không dùng WebSocket.
- Firmware hiện nối bằng `mqtt://` (không mã hoá). Mục 9 nói rõ vì sao và rủi ro đi kèm.

## 2. Việc cần làm, tóm tắt

1. Dựng EMQX (bản đã chạy thử: **6.3.1**; bản 5.x trở lên cùng cách cấu hình, chưa thử).
2. Mở listener TCP **1883** cho mạng của thiết bị.
3. Bật xác thực bằng tên và mật khẩu (Built-in Database), cấp một tài khoản cho mỗi board và một tài khoản `srhost`.
4. Bật phân quyền theo ACL ở mục 5. Đây là phần dễ sót nhất và là lớp bảo vệ duy nhất giữa các board.
5. Lưu retained xuống đĩa, cho phép tin lớn (mục 6).
6. Chạy bộ kiểm ở mục 8 và gửi kết quả cùng thông tin ở mục 10.

## 3. Cấu hình mẫu (Docker), đã chạy thật ở bàn thử

Đây là cấu hình đội thiết bị đang dùng trên máy phát triển. Đội server có thể dùng nguyên hoặc chuyển ý sang hệ
thống của mình.

`docker-compose.yml`:

```yaml
name: esp-sr
services:
  emqx:
    image: emqx/emqx:6.3.1
    container_name: sr-emqx
    restart: unless-stopped
    ports:
      - "1883:1883"
      - "127.0.0.1:18084:18083"        # dashboard chỉ ở loopback, đừng mở ra ngoài
    environment:
      EMQX_NODE__NAME: emqx@127.0.0.1
      EMQX_NODE__COOKIE: ${EMQX_NODE_COOKIE:?đặt trong .env}
      EMQX_DASHBOARD__DEFAULT_PASSWORD: ${EMQX_DASHBOARD_PASSWORD:?đặt trong .env}
      EMQX_RETAINER__BACKEND__STORAGE_TYPE: disc
    volumes:
      - ./emqx/emqx.conf:/opt/emqx/etc/emqx.conf:ro
      - ./emqx/acl.conf:/opt/emqx/etc/sr-acl.conf:ro
      - ./emqx/users.csv:/opt/emqx/etc/sr-users.csv:ro
      - sr-emqx-data:/opt/emqx/data
      - sr-emqx-log:/opt/emqx/log
    healthcheck:
      test: ["CMD", "/opt/emqx/bin/emqx", "ctl", "status"]
      interval: 10s
      timeout: 5s
      retries: 12
volumes:
  sr-emqx-data:
  sr-emqx-log:
```

`emqx/emqx.conf`:

```hocon
node { data_dir = "data" }
cluster { name = srcl, discovery_strategy = manual }
listeners.tcp.default { bind = "0.0.0.0:1883" }
listeners.ssl.default { enable = false }
listeners.ws.default  { enable = false }
listeners.wss.default { enable = false }

authentication = [
  {
    mechanism = password_based
    backend = built_in_database
    user_id_type = username
    password_hash_algorithm { name = sha256, salt_position = suffix }
    bootstrap_file = "/opt/emqx/etc/sr-users.csv"
    bootstrap_type = plain
  }
]

authorization {
  no_match = deny
  deny_action = ignore
  sources = [
    { type = file, enable = true, path = "/opt/emqx/etc/sr-acl.conf" }
  ]
}
```

`emqx/users.csv` (một dòng một tài khoản, **mật khẩu thật do đội server sinh**, không đặt tay):

```csv
user_id,password,is_superuser
srhost,<MẬT KHẨU srhost>,false
sr-30eda01e4630,<MẬT KHẨU board này>,false
```

Mỗi board mới thêm một dòng. EMQX đọc file này lúc khởi động. Nếu dùng Dashboard thay file (Access Control, Authentication,
Built-in Database, Users) thì cũng được, miễn username đúng như trên.

## 4. Tài khoản

| Tài khoản | Số lượng | Username | Mật khẩu |
|---|---|---|---|
| Board | một cho mỗi board | đúng `deviceId`, ví dụ `sr-30eda01e4630` | riêng từng board, đội server sinh, 20 ký tự trở lên, ngẫu nhiên |
| Phía máy tính | một | `srhost` | một mật khẩu riêng |

Không tài khoản nào là superuser. Không dùng chung mật khẩu giữa các board: mật khẩu nằm trong bộ nhớ flash của board,
mất board là phải thu hồi đúng một tài khoản.

## 5. Phân quyền (ACL)

Quy tắc đọc từ trên xuống, khớp quy tắc nào dừng ở đó. File `emqx/acl.conf`:

```erlang
%% Tin "up" là báo cáo của chính board: srhost được đọc, không bao giờ được giả mạo.
{deny,  {username, "srhost"}, publish,   ["sr/+/up/#"]}.
{allow, {username, "srhost"}, subscribe, ["sr/+/up/#"]}.
{allow, {username, "srhost"}, publish,   ["sr/+/down/#"]}.

%% Board đăng nhập bằng deviceId, nên ${username} nhốt nó vào nhánh của chính nó.
{allow, all, publish,   ["sr/${username}/up/#"]}.
{allow, all, subscribe, ["sr/${username}/down/#"]}.

{deny, all}.
```

Bảng quyền cho dễ nhìn:

| Ai | Được publish | Được subscribe | Bị cấm |
|---|---|---|---|
| Board `sr-X` | `sr/sr-X/up/#` | `sr/sr-X/down/#` | mọi nhánh của board khác, mọi topic ngoài `sr/` |
| `srhost` | `sr/+/down/#` | `sr/+/up/#` | publish vào `up/` của bất kỳ board nào |

Lưu ý: cấu hình trên đặt `deny_action = ignore`, nghĩa là tin bị cấm **bị bỏ lặng lẽ**, client không nhận lỗi. Muốn biết
quy tắc có chạy không thì phải kiểm bằng cách có người subscribe, như mục 8.

## 6. Giới hạn kích thước và lưu trữ

| Hạng mục | Cần | Vì sao |
|---|---|---|
| Kích thước tin tối đa | **≥ 64 KB** | ảnh gửi từng mảnh 16 384 byte cộng 28 byte đầu, tức 16 412 byte một tin; bộ lệnh tối đa 64 lệnh, JSON dưới 32 KB 🔬 |
| Retained | **bật, lưu xuống đĩa**, kích thước tin retained ≥ 64 KB | `status`, bộ lệnh và danh sách người nói là tin retained; để trong RAM thì broker khởi động lại là mất, và trạng thái `offline` của board đang tắt cũng mất |
| Keepalive | chấp nhận **15 giây**; đừng đặt mức tối thiểu cao hơn | board dùng 15 giây; broker coi board chết sau khoảng 1,5 lần keepalive rồi phát tin "offline" thay nó |
| Clean session | board nối với clean session | không cần giữ phiên lúc ngắt |
| Clientid trùng | cho phép broker đá phiên cũ | board nối lại sau mất Wi-Fi có thể trùng clientid với phiên chết chưa kịp dọn |
| Số client | vài chục | mỗi board một kết nối, tốc độ vài tin mỗi giây |
| QoS | chấp nhận QoS 0 và 1 | QoS 2 không dùng |

Nếu có proxy, tường lửa ứng dụng hay bộ cân bằng tải phía trước broker thì chúng cũng không được cắt tin dưới 64 KB và
phải giữ kết nối TCP nhàn rỗi ít nhất 60 giây.

## 7. Topic

`{deviceId}` là deviceId của board. "Up" là board gửi lên, "down" là máy tính gửi xuống.

| Topic | Hướng | QoS | Retained | Việc |
|---|---|---|---|---|
| `sr/{deviceId}/up/status` | up | 1 | có | trạng thái online/offline; **cũng là last will**: broker phát `offline` khi board mất kết nối |
| `sr/{deviceId}/up/heartbeat` | up | 0 | không | nhịp tim 30 giây một lần |
| `sr/{deviceId}/up/telemetry` | up | 0 | không | số đo mỗi giây, gói 10 mẫu |
| `sr/{deviceId}/up/event` | up | 1 | không | lệnh nhận được, từ chối, tiếng đánh thức |
| `sr/{deviceId}/up/detections` | up | 1 | không | kết quả nhận diện vật thể từ ảnh |
| `sr/{deviceId}/up/image` | up | 1 | không | ảnh nhị phân, từng mảnh 16 412 byte |
| `sr/{deviceId}/up/sensors` | up | 0 | không | cảm biến (đang tắt) |
| `sr/{deviceId}/up/speakers` | up | 1 | có | danh sách người nói board đang giữ |
| `sr/{deviceId}/down/cmd` | down | 1 | không | lệnh điều khiển board |
| `sr/{deviceId}/down/commands` | down | 1 | có | bộ lệnh thoại |
| `sr/{deviceId}/down/speakers` | down | 1 | có | danh sách người nói |
| `sr/{deviceId}/down/ota` | down | 1 | không | cập nhật firmware (chưa dùng) |

Khi nối, board gửi `up/status` online (retained) và subscribe các topic `down/`. Hai trong số đó (`down/commands`,
`down/speakers`) là retained, nên broker phải giao ngay tin retained cũ cho nó.

Dữ liệu `up/image` là nhị phân: ảnh chụp trong phòng, có thể có người. Phía server không cần mở nội dung nó, nhưng hãy
coi đường truyền và đĩa của broker là nơi đi qua dữ liệu riêng tư.

## 8. Bộ kiểm sau khi dựng

Cần công cụ `mosquitto_pub` và `mosquitto_sub` (gói `mosquitto-clients`). Thay `<HOST>`, `<MK_...>` bằng giá trị thật. Dùng
clientid khác với deviceId thật (`-i`), kẻo đá board đang chạy khỏi broker.

Mở một cửa sổ nghe bằng `srhost`:

```bash
mosquitto_sub -h <HOST> -p 1883 -u srhost -P '<MK_SRHOST>' -i kiem-srhost -v -t 'sr/+/up/#'
```

Rồi ở cửa sổ khác, lần lượt:

| # | Lệnh | Kết quả đúng |
|---|---|---|
| 1 | board `sr-30eda01e4630` publish vào nhánh của nó: `mosquitto_pub -h <HOST> -u sr-30eda01e4630 -P '<MK_BOARD>' -i kiem-1 -t sr/sr-30eda01e4630/up/event -m '{}'` | cửa sổ nghe **có** tin |
| 2 | cùng tài khoản đó publish vào nhánh board khác: `... -t sr/sr-khac/up/event -m '{}'` | cửa sổ nghe **không** thấy tin |
| 3 | `srhost` publish vào `up/`: `mosquitto_pub -u srhost -P '<MK_SRHOST>' -i kiem-2 -t sr/sr-30eda01e4630/up/event -m '{}'` | **không** thấy tin |
| 4 | board subscribe nhánh của board khác: `mosquitto_sub -u sr-30eda01e4630 -P '<MK_BOARD>' -i kiem-3 -t 'sr/sr-khac/down/#'` rồi `srhost` publish vào đó | board **không** nhận |
| 5 | sai mật khẩu | bị từ chối lúc nối (`not authorized`) |
| 6 | một tin 20 KB: `head -c 20000 /dev/zero \| tr '\0' x \| mosquitto_pub -u sr-30eda01e4630 ... -s -t sr/sr-30eda01e4630/up/image` | cửa sổ nghe nhận đủ |
| 7 | publish retained `sr/sr-30eda01e4630/down/commands`, **khởi động lại broker**, rồi subscribe lại | tin retained còn |

Ca 2, 3, 4 là ca bảo vệ giữa các board; ca 7 là ca retained xuống đĩa. Nếu ca nào sai thì đừng gắn board thật vào.

## 9. Bảo mật: hiện trạng cần biết

- **Chưa có mã hoá.** Firmware hiện chỉ nối `mqtt://`. Dự án có chế độ `prod` chỉ nhận `mqtts://` với chứng chỉ CA nhúng
  trong firmware, nhưng phần đó **chưa làm xong**. Hệ quả: tên và mật khẩu của board đi trên mạng dưới dạng rõ, và ảnh
  cũng vậy.
- Vì vậy **cổng 1883 chỉ nên mở trong mạng tin cậy** (LAN, VPN hoặc dải IP cố định của nơi đặt board), không mở ra Internet.
- Đội server vẫn nên chuẩn bị sẵn listener TLS 8883 và chứng chỉ để khi firmware xong thì chuyển sang ngay: chứng chỉ
  broker có tên miền hoặc IP khớp địa chỉ board dùng. Chưa cần gửi chứng chỉ vào lúc này.
- Dashboard EMQX chỉ ở loopback hoặc sau VPN. Đổi mật khẩu mặc định ngay.
- Mật khẩu board và `srhost` chỉ gửi qua kênh an toàn (trình quản lý mật khẩu, tin nhắn có hạn xem), không email thường,
  không nhóm chat chung, không commit vào git.

## 10. Đội server gửi lại cho bên thiết bị

Chia làm hai nhóm: cần ngay để nối được, và cần để vận hành.

### 10.1 Cần ngay

| # | Thông tin | Ghi chú |
|---|---|---|
| 1 | **Địa chỉ broker**: IP hoặc tên miền, cổng TCP (mặc định 1883) | địa chỉ board ở mạng của nó nhìn thấy, không phải địa chỉ nội bộ của server nếu có NAT |
| 2 | **Mạng nào được phép nối**: dải IP hoặc VPN, và có cần mở tường lửa cho nơi đặt board không | board dùng Wi-Fi 2,4 GHz, địa chỉ nguồn của nó là IP của router tại chỗ |
| 3 | **Mật khẩu `srhost`** | qua kênh an toàn (mục 9) |
| 4 | **Mật khẩu từng board** | bên thiết bị gửi danh sách deviceId; hiện có `sr-30eda01e4630` (board C) và `sr-3485188f7a70` (board B). Qua kênh an toàn |
| 5 | **Phiên bản EMQX** đang chạy và ai quản trị | |
| 6 | **Kết quả bộ kiểm mục 8**: bảng bảy ca, đạt hay không, kèm dòng lệnh đã chạy | không kèm mật khẩu |

### 10.2 Cần để vận hành

| # | Thông tin | Vì sao |
|---|---|---|
| 7 | Giới hạn thực tế của hệ thống: kích thước tin tối đa, kích thước retained tối đa, số client tối đa, giới hạn tốc độ nếu có | để biết ảnh và bộ lệnh có lọt |
| 8 | Có proxy hay bộ cân bằng tải phía trước không, và thời gian chờ kết nối nhàn rỗi | mục 6 |
| 9 | **Quy trình cấp tài khoản cho board mới**: ai làm, mất bao lâu, bên thiết bị gửi gì (deviceId) | mỗi board bán ra cần một tài khoản |
| 10 | **Quy trình thu hồi** một board mất hay hỏng: xoá tài khoản ở đâu | |
| 11 | Kế hoạch **TLS**: có mở 8883 không, khi nào, tên miền nào, ai giữ khoá CA | khi nào firmware nhúng CA thì cần ngay |
| 12 | Cách **xem log kết nối** (client nào nối, bị từ chối vì sao) và có sao lưu retained không | để tìm lỗi khi board không lên |
| 13 | Người liên hệ khi broker có sự cố | |

**Không gửi:** khoá riêng (`*.key`) của CA hay của broker, mật khẩu Dashboard, cookie node của EMQX. Bên thiết bị không
cần những thứ đó.

## 11. Sau khi nhận, bên thiết bị sẽ làm

1. Ghi vào board: địa chỉ broker và mật khẩu của chính board đó.
2. Điền cấu hình phía máy tính: địa chỉ, cổng, `srhost` và mật khẩu.
3. Mở màn hình theo dõi, thấy board **ONLINE**, nhận `up/status` và nhịp tim.
4. Chạy một lệnh thoại và một lần nhận diện ảnh để thử hai chiều: sự kiện lên, ảnh lên từng mảnh, lệnh và bộ lệnh xuống.
5. Báo lại đạt hay không, kèm log.

## 12. Những gì chưa kiểm chứng

- Mọi số liệu ở mục 6 về kích thước tin và keepalive đến từ cấu hình của firmware và hợp đồng topic. Con số "dưới 32 KB" của bộ lệnh là tính từ giới hạn 64 lệnh, mỗi lệnh 64 ký tự, chưa đo một bộ đầy. Chưa thử với một
  broker ngoài mạng LAN của bàn thử, hay sau bộ cân bằng tải.
- Cấu hình mẫu ở mục 3 đã chạy ở bàn thử với EMQX 6.3.1; chưa chạy với EMQX 5.x.
- Chưa thử nhiều board cùng nối một lúc ở quy mô vài chục.
