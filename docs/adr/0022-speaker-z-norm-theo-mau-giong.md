# ADR-0022 — `speaker` trên chip: chuẩn hoá điểm theo từng mẫu giọng (Z-norm)

- **Trạng thái**: Thay bằng ADR-0023 (chủ repo, 2026-10-11)
- **Ngày**: 2026-10-11
- **Liên quan**: KẾ HOẠCH §3.17, §4.5.5, §6.3; TASKS E11-T27, E11-T31; ADR-0021; `docs/measurements/speaker.md` §6.4

---

## Bối cảnh

Đường chip của `speaker` chấm mỗi lệnh với mọi mẫu giọng của danh sách, lấy điểm lớn nhất, rồi so với ngưỡng cho số
người đang có mẫu giọng (KẾ HOẠCH §3.17, E11-T31). `make speaker-deploy` đặt bảng ngưỡng ấy theo luật 1%, nới tới 5%
người lạ lọt (`speaker.md` §6.4). Bảng cho thấy:

- Với 1 người, ngưỡng 744 ‰. Lệnh của chủ repo được giữ 98,7% ở 1 m, 97,5% ở 3 m.
- Từ 2 người, ngưỡng phải leo, và lệnh của chủ repo chỉ còn 75,7% / 54,8%. Với 8 người còn 45,1% / 23,4%.

Nguyên nhân, đo trên chính các embedding đường chip ấy:

- giữa những người lạ cùng kênh mô phỏng, b0 có EER 19,3%;
- chủ repo, thu thật qua board B, chỉ có EER 2,4% với người lạ: b0 tách chủ yếu theo kênh thu.

Mỗi người đăng ký thêm là một mẫu giọng mà người lạ cùng kênh dễ khớp, và điểm của người lạ với từng mẫu nằm ở những
mức khác nhau. Lấy điểm lớn nhất của các điểm thô ấy làm ngưỡng phải leo theo mẫu "dễ dãi" nhất.

## Các phương án

Cùng 986 embedding đường chip của `speaker-deploy` (209 cửa sổ chủ repo, 777 cửa sổ người lạ), cùng 20 lần rút 10 cửa
sổ đăng ký. Một nửa số người lạ, rút theo seed, làm nhóm chuẩn của hai phương án chuẩn hoá. Nửa còn lại làm người
đăng ký thêm và người lạ thử, cho cả ba phương án. Cột: lệnh của chủ repo giữ ở 5% người lạ lọt, 1 m / 3 m.

| Phương án | 1 người | 2 người | 4 người | 8 người | Được | Mất |
|---|---|---|---|---|---|---|
| điểm thô, ngưỡng theo số người | 98,8 / 97,5 | 80,3 / 60,4 | 66,8 / 44,1 | 48,9 / 27,3 | không cần gì thêm | sụt mạnh từ 2 người |
| **Z-norm**: (cos − μ)/σ, μ và σ của mẫu giọng trên cả nhóm chuẩn | 98,8 / 97,2 | 98,6 / 96,3 | 98,2 / 94,7 | 97,6 / 93,0 | gần như hết sụt; μ, σ tính một lần cho mỗi mẫu giọng | nhóm chuẩn ~0,3 MB flash; tính lại μ, σ khi mẫu giọng đổi |
| AS-norm, 50 điểm cao nhất của nhóm chuẩn | 98,8 / 97,2 | 98,5 / 96,1 | 98,0 / 94,1 | 95,8 / 90,0 | như Z-norm | kém Z-norm từ 4 người, cần sắp xếp |
| học lại b0 trên tiếng Việt và kênh board | chưa đo | | | | sửa gốc: b0 tách người tốt hơn | GPU, chủ repo chưa cho (11/10) |

## Quyết định

Đường chip chấm bằng Z-norm theo từng mẫu giọng. Chi tiết ở KẾ HOẠCH §3.17:

- **Nhóm chuẩn:** các embedding đơn vị, tính trên đường chip, của một nửa số người lạ của khảo sát, rút theo seed.
  Chúng nằm trong ảnh model, mục `speaker_cohort`.
- **Thống kê của mẫu giọng:** μ và σ của tích vô hướng giữa mẫu giọng và từng hàng của nhóm chuẩn. Tính lúc khởi động
  và mỗi khi đăng ký xong.
- **Một lần chấm:** điểm của mỗi mẫu giọng là (cos − μ)/σ, đổi ra phần nghìn. Chọn mẫu có điểm lớn nhất, so với
  ngưỡng cho số người có mẫu giọng, cùng đơn vị ấy.
- **Bảng ngưỡng:** `speaker-deploy` đặt trên nửa người lạ còn lại, theo luật 1%, nới tới 5% như cũ.

## Hệ quả

- Ảnh model đổi khuôn: header mang bảng ngưỡng theo phần nghìn của z, và có thêm loại mục `COHORT` (khuôn 7, KẾ HOẠCH
  §6.3). Hợp đồng gọi của `ai_engine` đổi theo (§4.5.5).
- Sự kiện vẫn mang `speakerPermille` là cosine của người khớp nhất, để người đọc so được với các số cũ. Quyết định
  dựa trên z.
- Nhóm chuẩn hiện là người lạ qua đường mô phỏng, cùng kênh với người lạ dùng để đặt ngưỡng. Ở nhà thật, người lạ nói
  qua board B, nên nhóm chuẩn phải thu qua board như người dùng. Khi có bản thu của E11-T27, thay nhóm chuẩn và đặt lại
  bảng trên họ.
- Z-norm sửa phần ngưỡng leo theo số người, không làm b0 tách người tốt hơn. Nếu bản thu thật cho thấy vẫn chưa đủ,
  bước sau là học lại b0 (dòng cuối của bảng trên).
