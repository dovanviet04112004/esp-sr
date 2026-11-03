# dsp_afe

Tầng L2, thuật toán thuần: tiếng xen kẽ một hay hai micro (cộng kênh tham chiếu nếu có) vào, một kênh sạch và số
liệu của nó ra (KẾ HOẠCH §3.2, §4.5.5). Bản soi gương Python là `ml/src/srpipe/dsp/afe/`.

| Mục | Nội dung |
|---|---|
| Phụ thuộc | `common`, `dsp_spec` |
| Mặt tiền | `dsp_afe_workspace_bytes` → `dsp_afe_init` → `dsp_afe_feed` / `dsp_afe_fetch` cùng một task; hàng đệm bên trong `DSP_AFE_FIFO_FRAMES` bước |
| Chuỗi | `hpf` → `aec` → `stft` → `balance` → `doa` → trộn trần / `gsc` / `bss` → khe `ns` → `istft` → `vad` → `agc`; mỗi module bật bằng `CONFIG_DSP_AFE_<MODULE>_ENABLE`, tắt là file nguồn không được dịch |
| Mặc định | mọi module tắt: STFT từng micro → trung bình hai kênh → iSTFT, ra trễ vào đúng một bước; đây là app khung rỗng của E5-T11 và khớp `srpipe.dsp.afe.chain` |
| Module hiện có | `hpf` (E7-T1), `balance` (E7-T2), `vad` (E7-T3), `agc` (E7-T4), `doa` (E8-T1), `gsc` (E8-T2), `bss` (E8-T3) và `ns_omlsa` (E9-T1) là thuật toán thật, khớp bản Python từng bit trên máy tính và board B; `aec` còn là **bản giả trung tính** của E3-T4: đúng hợp đồng, kiểm tham số, cho tín hiệu đi qua nguyên vẹn, tới E10-T4 |
| Bộ nhớ | người gọi cấp `hot` (RAM nội) và `cold` (PSRAM); mọi phần hiện có bị chạm mỗi khung nên `cold` = 0 (`docs/measurements/ram.md` §1). Một bộ FFT 512 điểm dùng chung cho STFT, iSTFT và `aec`; FFT mặc định là bản viết tay, bảng của nó nằm trong `hot` (ADR-0020) |
| Kiểm | `test_apps/host`: dựng hai lần, mọi module tắt và mọi module bật, chạy trên máy tính |
| Giới hạn | `"M"`, `"MR"` một micro, `"MM"`, `"MMR"` hai micro (`GEN_ARRAY_N_MICS` = 2, ADR-0042); một micro bỏ `balance`, `doa` và trộn trần; `R` cần `aec`, `gsc`/`bss` cần module của chúng và hai micro, thiếu thì `ESP_ERR_NOT_SUPPORTED` |
