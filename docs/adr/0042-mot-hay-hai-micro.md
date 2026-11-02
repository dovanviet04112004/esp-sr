# ADR-0042 — Một hay hai micro là cấu hình build

- **Trạng thái**: Chấp nhận (chủ repo 02/11: "sản phẩm sắp tới cần 1 mic 1 cam ý nên setup trước", sau bảng đo một micro
  so với hai)
- **Ngày**: 2026-11-02
- **Liên quan**: KẾ HOẠCH §2.1, §2.2, §2.3, §3.2, §4.5.5, §4.5.8, §7.4; `docs/measurements/mic_array.md` §5; TASKS E2-T11

---

## Bối cảnh

Board B có hai INMP441 chung một dây dữ liệu. Sản phẩm kế tiếp có một micro và một camera. Chuỗi hiện nay chỉ nhận đúng
hai micro: `dsp_afe` chỉ có định dạng `"MM"` và `"MMR"`, `drv_audio` luôn đọc hai khe, và luồng tiếng chỉ có mode mang
`ch0 ch1`.

Đo trên các phiên của board B, chép một kênh sang cả hai đầu vào và tắt `balance` (`mic_array.md` §5): nếu chỉ dùng micro
tốt (`ch1`), `wake` không đổi, còn lệnh đúng 73% so với 74% khi có hai micro. Nếu chỉ dùng micro kém (`ch0`, nhỏ hơn chừng
10 dB), lệnh đúng còn 65%. Một micro mất hướng người nói, và mất phần lợi chừng 3 dB của phép trung bình hai kênh với nhiễu
không tương quan.

## Các phương án

| Phương án | Được | Mất |
|---|---|---|
| Chỉ tắt `balance` và `doa`, vẫn đọc hai khe | không sửa `dsp_afe` | khe trống làm trung bình nhỏ đi một nửa; `doa` đọc rác |
| Chép một kênh sang hai đầu vào trong firmware | không sửa `dsp_afe` | tốn hai STFT và `doa` cho một tín hiệu; `doa` ra 90° như thật |
| **Định dạng `"M"` của `dsp_afe`, số micro chọn bằng Kconfig** | chuỗi chỉ làm những gì một micro làm được; hợp đồng chỉ thêm giá trị | phải dựng bản Python, golden và bản C |
| Số micro đọc từ NVS lúc chạy | một ảnh firmware cho hai board | số micro là phần cứng, không đổi khi máy chạy; vùng làm việc phải tính cho trường hợp lớn nhất |

## Quyết định

1. **Kconfig `APP_MIC_COUNT`** (1 hay 2, mặc định 2) và **`APP_MIC_SLOT`** (trái hay phải, chỉ khi có một micro) nằm
   trong `firmware/main/Kconfig.projbuild`, cạnh `APP_SPEAKER_ENABLE`. `firmware/sdkconfig.mic1` chọn một micro ở khe phải.
   Đây là `ch1` của board B, micro tốt. `make product MICS=1` dựng và nạp bản ấy trong thư mục build riêng.
2. **`drv_audio`** thêm trường `mics` vào cấu hình: cả hai khe (giá trị 0, như hiện nay), chỉ khe trái hoặc chỉ khe phải.
   Một khe thì I2S chạy mono theo `slot_mask`, BCLK vẫn 64 mỗi WS như INMP441 cần, và đệm DMA chỉ bằng một nửa.
   `drv_audio_channels()` trả 1 hay 2, cộng 1 khi có tham chiếu.
3. **`dsp_afe` nhận thêm `"M"` và `"MR"`.** Số chữ `M` là số micro (1 hay 2), `R` là tham chiếu. Khi có một micro:
   - có một STFT phân tích, một kênh `hpf`, và `aec` cho một micro;
   - không có `balance`, vì không có cặp để cân; `calib` được nhận nhưng hệ số `balance` bị bỏ qua;
   - không có `doa`: khung ra `doa_deg` −1, `doa_conf` 0;
   - không có trộn trần: phổ của micro đi thẳng vào khe `ns`;
   - `gsc` và `bss` bị từ chối với `ESP_ERR_NOT_SUPPORTED`.

   Khi hai kênh giống nhau và không có `balance`, chuỗi `"M"` cho `pcm`, `vad`, `level_dbfs`, `gain_db` và `flags` trùng
   từng bit với chuỗi `"MM"`. Khung ra chỉ khác ở `doa`. Chữ ký hàm không đổi, chỉ thêm giá trị được nhận, nên đây không
   phải thay đổi phá hợp đồng của `contract-v1`.
4. **`svc_front`** nhận `n_mics` trong cấu hình và dựng định dạng từ `n_mics` cộng tham chiếu. Chỉ đếm kênh thì không
   phân biệt được một micro có tham chiếu với hai micro không có tham chiếu.
5. **Luồng tiếng** thêm hai mode vào `contracts/stream/frame.yaml`: 9 `mic` (`[mic]`, 32 KB/s) và 10 `mic_clean`
   (`[mic, clean]`, 64 KB/s). Kênh `mic` là micro duy nhất, nằm ở khe mà `APP_MIC_SLOT` chọn. Mode có `ch0`, `ch1` chỉ mở
   khi có hai micro, mode có `mic` chỉ mở khi có một micro; `svc_report` nhận `n_mics` để kiểm.
6. **`host` và `ml`.** Một phiên có `mic.wav`, không có `ch0.wav`/`ch1.wav`, chạy chuỗi `"M"`. Các phép chỉ có nghĩa với
   một cặp micro (`pair_stats`, `calib`, hướng) báo phiên ấy không dùng được, thay vì đọc nhầm.
7. **Model không đổi.** `wake`, `command` và người nói học trên khung sạch một kênh. Micro của sản phẩm mới phải đo lại
   trên board thật 🔬.
8. **Kiểm**:
   - golden `chain` và `chain_modules` có thêm ca một micro, nhận ra bằng bề rộng đầu vào. `chain_modules` có thêm một đối
     chứng âm: khung tính như trung bình của micro với một khe im, là lỗi của một bản một micro vẫn đọc hai khe;
   - test máy tính của `dsp_afe` thêm `"M"` và `"MR"`;
   - board B chạy bản `MICS=1` (khe phải) và vẫn thức, nhận lệnh. Bản hai micro giữ nguyên từng bit: golden cũ không đổi
     byte nào.

## Hệ quả

- Bản một micro không có hướng người nói, nên `bss`, `gsc` và sự kiện có `doa` không dùng được trên board ấy.
- Lỗ âm của micro duy nhất bị che là mất tiếng; hai micro còn có micro dự phòng.
- Bản một micro nhẹ hơn: bớt một STFT phân tích, vùng làm việc của `doa` và một nửa đệm DMA. Số đo trên board ghi ở
  `budget.md` 🔬.
- Khi lắp loa (E10), RX một khe và TX hai khe dùng chung BCLK và WS. Phải kiểm lại cách RX mono đi cùng TX stereo trên
  cùng một bộ I2S 🔬.
