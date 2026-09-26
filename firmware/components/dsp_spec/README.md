# dsp_spec

Tầng L1, thuật toán thuần: FFT thực, cửa sổ, STFT chạy dòng và log-mel trên lưới `contracts/grid.yaml`
(KẾ HOẠCH §3.1, §3.11). Khớp bản tham chiếu Python `ml/src/srpipe/dsp/spec/`; bộ vàng ở E6-T4, E6-T5.

| Mục | Nội dung |
|---|---|
| Phụ thuộc | `common`; riêng tư `espressif/dl_fft` `==0.7.0` (ADR-0002) |
| FFT | `src/fft_dl.c` bọc `dl_rfft_f32_run`/`dl_irfft_f32_run`, đổi khuôn xếp chặt của `dl_fft` (DC, Nyquist, rồi re, im) ra n/2 + 1 vạch; `esp-dsp` đã được đo và loại ở ADR-0002 |
| Hợp đồng FFT | thuận không chia, nghịch chia 1/n, n/2 + 1 vạch, bỏ phần ảo của DC và Nyquist; luỹ thừa 2 từ 64 tới 2048 |
| Bộ nhớ | người gọi cấp mọi vùng làm việc (`*_workspace_bytes`); **ngoại lệ duy nhất**: bảng của thư viện FFT, `dl_fft` tự cấp một lần lúc `dsp_spec_fft_init` ở RAM nội (KẾ HOẠCH §4.5.3 luật 7) |
| STFT | căn Hann tuần hoàn dạng `sinf(pi i / n)`; ra trễ vào đúng một bước |
| Mel | thang Slaney, tam giác chuẩn hoá diện tích dựng bằng `double` lúc init, lưu thưa; log tự nhiên có sàn; MFCC là DCT-II trực chuẩn tra bảng cos |
| Kiểm | `test_apps/unit`: FFT so với DFT `double`, dựng lại STFT, mel và MFCC, rồi đo µs và RAM từng hàm ở `-O2` |
| Số đo | `docs/measurements/latency.md` §1 |
