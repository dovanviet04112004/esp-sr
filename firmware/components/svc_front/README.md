# svc_front

Tầng L4, dịch vụ ghép: nửa `thu` + `sach` của chuỗi nghe (KẾ HOẠCH §3.2, §5.2). Giữ đúng một thể hiện
`dsp_afe` và bộ nhớ của nó; `svc_front_step` chạy trong `sach_task` ở nhân 1.

| Mục | Nội dung |
|---|---|
| Phụ thuộc | `common`, `dsp_afe`; `ai_engine` vào khi cắm `ns` mạng (sau Cửa 1) |
| Bộ nhớ | `hot` ở RAM nội, `cold` ở PSRAM, cấp một lần trong `svc_front_init` lúc boot; chuỗi mặc định đo trên board B: ~41 KB `hot` + 6,2 KB bảng `dl_fft` (`docs/measurements/ram.md` §3) |
| Hở khung | `seq` nhảy không quá `chain.gap_keep_s` thì `dsp_afe_resume`, xa hơn thì `dsp_afe_reset`, trước khi nạp; khung sau mang cờ `DSP_AFE_FLAG_GAP` |
| Không làm | không đọc NVS (tham số và hiệu chuẩn do `main` trao vào), không tạo task, không tạo hàng đợi |
| Kiểm | trên board qua `main` và `test_apps/soak`; số khung mất ở `heartbeat` |
