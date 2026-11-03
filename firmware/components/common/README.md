# common

Tầng L0: header dùng chung, không có mã chạy. Mọi component đọc được, không component nào phụ thuộc ngược lên nó
(KẾ HOẠCH §4.5.4).

| Mục | Nội dung |
|---|---|
| Phụ thuộc | không |
| Viết tay | `app_err.h` (mã lỗi riêng của ứng dụng), `app_events.h` (sự kiện, mã từ chối và yêu cầu chạy giữa các task) |
| Sinh từ `contracts/` | `gen_array.h`, `gen_grid.h`, `gen_listen.h`, `gen_speaker.h`, `gen_stream.h`, `gen_units.h`; không sửa tay, sinh lại bằng `make gen` (KẾ HOẠCH §4.2) |
| Giới hạn | chỉ khai báo, macro và hàm `static inline`; thêm mã chạy thì chuyển nó sang component có tầng của nó |
