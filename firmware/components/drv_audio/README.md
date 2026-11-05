# drv_audio

Tầng L2: I2S0 thu một hay hai INMP441 chung một dây dữ liệu (KẾ HOẠCH §2.2, §2.3), giao từng khung trọn 256 mẫu mỗi
kênh: hai micro xen kẽ `ch0 ch1`; một micro đọc mono ở khe `APP_MIC_SLOT` (ADR-0042). Mẫu 24 bit dịch về `int16` theo
`pcm_shift`.

| Mục | Nội dung |
|---|---|
| Phụ thuộc | `common`; riêng tư `bsp_board` (chân), `esp_driver_i2s` |
| DMA | `dma_desc_num` khối × 256 mẫu; 8 khối = 128 ms biên cho một lần ghi flash (KẾ HOẠCH §5.5) |
| `seq` | đếm khung từ lúc khởi tạo, **kể cả khung mất**: mỗi lần tràn DMA cộng một |
| `pcm_shift` | số bit dịch phải của từ 32 bit: 16 giữ 16 bit cao, nhỏ hơn là thêm gain; chốt ở E2-T5 |
| Phát | board có loa: một khối TX sau mỗi khối RX, khối ấy đi vào kênh tham chiếu; khối RX đọc dồn sau một lần `thu_task` đứng thì không ghi TX, để trễ giữ nguyên (KẾ HOẠCH §2.4). Board không loa: `ESP_ERR_NOT_SUPPORTED` |
| Kiểm | `test_apps/unit`: 10 s thu liền, `seq` liên tục, 0 tràn, hai kênh sống và khác nhau |
