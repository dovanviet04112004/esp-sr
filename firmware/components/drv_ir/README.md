# `drv_ir` — L2

Cảm biến hồng ngoại của board C ở GPIO 19, bình thường mức cao, thấp khi có vật (KẾ HOẠCH §3.19).

- `filter.h` là phần thuần: gộp chuỗi cạnh đổi mức dồn dập thành một lần chạm. Test chạy trên máy tính ở `test_apps/host`, có đối
  chứng âm.
- `drv_ir.h` đếm cạnh trong ngắt, không tạo task; `do_task` đọc nó.
- Phụ thuộc: `common`, `bsp_board`, driver GPIO và `esp_timer` của IDF.
