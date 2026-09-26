# drv_audio

Tầng L2: I2S0 thu hai INMP441 chung một dây dữ liệu (KẾ HOẠCH §2.2), giao từng khung trọn 256 mẫu mỗi kênh,
xen kẽ `ch0 ch1`, mẫu 24 bit dịch về `int16` theo `pcm_shift`.

| Mục | Nội dung |
|---|---|
| Phụ thuộc | `common`; riêng tư `bsp_board` (chân), `esp_driver_i2s` |
| DMA | `dma_desc_num` khối × 256 mẫu; 8 khối = 128 ms biên cho một lần ghi flash (KẾ HOẠCH §5.5) |
| `seq` | đếm khung từ lúc khởi tạo, **kể cả khung mất**: mỗi lần tràn DMA cộng một |
| `pcm_shift` | số bit dịch phải của từ 32 bit: 16 giữ 16 bit cao, nhỏ hơn là thêm gain; chốt ở E2-T5 |
| Giới hạn | chiều phát (TX) trả `ESP_ERR_NOT_SUPPORTED` cho tới E10-T1, khi board có loa |
| Kiểm | `test_apps/unit`: 10 s thu liền, `seq` liên tục, 0 tràn, hai kênh sống và khác nhau |
