# ADR-0023 — `speaker` trên chip: chấm theo AS-norm, chuẩn hoá cả mẫu giọng lẫn lệnh

- **Trạng thái**: Chấp nhận (chủ repo, 2026-10-11). Thay ADR-0022
- **Ngày**: 2026-10-11
- **Liên quan**: KẾ HOẠCH §3.17, §4.5.5, §6.3; TASKS E11-T27, E11-T31; ADR-0022; `docs/measurements/speaker.md` §6.5,
  §6.6

---

## Bối cảnh

ADR-0022 chọn Z-norm theo một bảng chỉ đo lệnh của chủ repo. `speaker.md` §6.6 đo thêm, trên cùng embedding đường
chip và cùng 20 lần rút của `speaker-deploy`, hai điều mà bảng ấy bỏ qua:

- lệnh của những người đăng ký thêm được giữ bao nhiêu;
- mẫu cho điểm cao nhất có đúng là mẫu của người nói không, tức `speakerId` của sự kiện.

Người đăng ký thêm là người lạ qua đường mô phỏng, cùng kênh với người lạ thử, như người trong nhà nói qua cùng một
board. Với họ, Z-norm kém cả cosine thô. Ca chuỗi trên board cho thấy điều ấy: mẫu cao nhất đúng người cho thành viên
2/7 với Z-norm, 5/7 với cosine (`latency.md` §27).

## Các phương án

Cùng vật liệu, mỗi cách đặt ngưỡng riêng ở 5% người lạ lọt. Số ở 8 người có mẫu giọng:

| Phương án | Chủ repo giữ 1 m / 3 m | Người đăng ký thêm giữ | Mẫu cao nhất đúng người, người đăng ký thêm | Được | Mất |
|---|---|---|---|---|---|
| cosine thô | 52,7 / 30,6% | 32,2% | 78,7% | không cần gì thêm | ngưỡng leo theo số người, chủ repo bị chặn |
| Z-norm, ADR-0022 | 96,8 / 91,6% | 23,0% | 70,4% | giữ chủ repo | người đăng ký thêm và việc gắn tên kém nhất |
| S-norm: trung bình z của mẫu và z của lệnh, trên cả nhóm chuẩn | 97,7 / 97,4% | 32,2% | 77,1% | không phải chọn hàng | giữ người đăng ký thêm kém AS-norm |
| **AS-norm**: như S-norm, μ và σ mỗi phía trên K = 100 hàng cao nhất | 98,0 / 97,3% | 36,3% | 77,7% | tốt nhất cho chủ repo và người đăng ký thêm | mỗi lệnh thêm 380 tích vô hướng và chọn 100 hàng |
| học lại b0 trên tiếng Việt và kênh board | chưa đo | | | sửa gốc: b0 tách người tốt hơn | GPU, chủ repo chưa cho (11/10) |

- **Theo số người:**
  - Từ 2 tới 8 người, AS-norm giữ chủ repo ≥ 97,3% ở cả 1 m và 3 m, nên luật "giữ ≥ 95% ở cả hai khoảng cách" đạt đủ 8
    người. Z-norm hụt ở 3 m từ 4 người.
  - AS-norm giữ người đăng ký thêm nhiều nhất từ 3 người trở lên.
  - K từ 50 tới 200 cho số gần nhau.
- **Gắn đúng tên:** cosine thô vẫn nhỉnh hơn, 78,7% so với 77,7% ở 8 người.

## Quyết định

Đường chip chấm bằng AS-norm, K = 100. Chi tiết ở KẾ HOẠCH §3.17.

- **μ, σ của một vector trên nhóm chuẩn**, dùng cho cả mẫu giọng lẫn embedding của lệnh:
  - tích vô hướng với từng hàng;
  - K hàng cao nhất, bằng nhau thì hàng đứng trước;
  - μ và σ trên K hàng ấy, cộng float32 theo thứ tự hàng.
- **Khi nào tính:** μ, σ của mẫu giọng tính lúc khởi động và mỗi khi đăng ký xong. Của lệnh thì tính trong mỗi lần xác
  minh.
- **Một lần chấm:**
  - điểm của mỗi mẫu là (z_e + z_t)/2, với z_e = (cos − μ_e)/σ_e và z_t = (cos − μ_t)/σ_t, đổi ra phần nghìn;
  - chọn mẫu có điểm cao nhất, so với ngưỡng cho số người có mẫu giọng, cùng đơn vị ấy.
- **K** là `deploy.cohort_top` của cấu hình `speaker`. `speaker-deploy` ghi nó vào khoá và header ảnh, cạnh nhóm chuẩn
  và bảng ngưỡng.

## Hệ quả

- **Ảnh model đổi sang khuôn 8:** header thêm K, và bảng ngưỡng nay là phần nghìn của điểm AS-norm (KẾ HOẠCH §6.3).
- **Hợp đồng gọi của `ai_engine` giữ chữ ký nhưng đổi nghĩa** (§4.5.5):
  - `ai_engine_speaker_norm` lấy K hàng cao nhất;
  - `ai_engine_speaker_best` tính μ, σ của embedding lệnh rồi mới chấm.
- **`speaker-deploy` ghi thêm, cạnh mỗi mức của bảng:** phần lệnh của người đăng ký thêm được giữ, và phần mẫu cao nhất
  đúng người. Từ nay một phương án chấm phải được xét trên cả hai cột ấy.
- **Giới hạn vẫn còn:**
  - Dù chấm cách nào, người đăng ký thêm cũng chỉ được giữ 36–59%, vì b0 tách kém những người cùng kênh (EER 19,3%).
  - Nhóm chuẩn và người lạ thử vẫn là mô phỏng. Bản thu thật qua board (E11-T27) quyết con số thật. Nếu vẫn chưa đủ,
    bước sau là học lại b0.
- **Về ADR-0022:** dòng AS-norm trong bảng của nó không tái tạo được, vì script cho ra dòng ấy không còn
  (`speaker.md` §6.6).
