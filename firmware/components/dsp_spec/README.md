# dsp_spec

Tầng L1, thuật toán thuần: FFT thực, cửa sổ, STFT chạy dòng và log-mel trên lưới `contracts/grid.yaml`
(KẾ HOẠCH §3.1, §3.11). Khớp bản tham chiếu Python `ml/src/srpipe/dsp/spec/`; bộ vàng ở E6-T4, E6-T5.

| Mục | Nội dung |
|---|---|
| Phụ thuộc | `common`; riêng tư `espressif/dl_fft` `==0.7.0`, `espressif/esp-dsp` `==1.8.2` |
| Backend FFT | Kconfig `DSP_SPEC_FFT_BACKEND`: `src/fft_dl.c` bọc `dl_rfft_f32_run`/`dl_irfft_f32_run`; `src/fft_dsp.c` dùng FFT phức `dsps_fft2r_fc32` nửa độ dài cộng bước tách phổ thực của repo, vì `esp-dsp` không có FFT nghịch cho tín hiệu thực |
| Hợp đồng FFT | thuận không chia, nghịch chia 1/n, n/2 + 1 vạch, bỏ phần ảo của DC và Nyquist; luỹ thừa 2 từ 64 tới 2048 |
| Bộ nhớ | người gọi cấp mọi vùng làm việc (`*_workspace_bytes`); **ngoại lệ duy nhất**: bảng của thư viện FFT, thư viện tự cấp một lần lúc `dsp_spec_fft_init` ở RAM nội (KẾ HOẠCH §4.5.3 luật 7) |
| STFT | căn Hann tuần hoàn dạng `sinf(pi i / n)`; ra trễ vào đúng một bước |
| Mel | thang Slaney, tam giác chuẩn hoá diện tích dựng bằng `double` lúc init, lưu thưa; log tự nhiên có sàn; MFCC là DCT-II trực chuẩn tra bảng cos |
| Kiểm | `test_apps/unit`: FFT so với DFT `double`, dựng lại STFT, mel và MFCC, rồi đo µs và RAM từng hàm; dựng lần hai với `SDKCONFIG_DEFAULTS=sdkconfig.defaults.esp_dsp` cho backend kia |
| Số đo | `docs/measurements/latency.md` §1 |
