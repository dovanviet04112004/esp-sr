# dsp_spec

Tầng L1, thuật toán thuần: FFT thực, cửa sổ, STFT chạy dòng, log-mel và cao độ trên lưới `contracts/grid.yaml`
(KẾ HOẠCH §3.1, §3.11). Khớp bản tham chiếu Python `ml/src/srpipe/dsp/spec/` từng bit với FFT mặc định (KẾ HOẠCH §3.14,
ADR-0020); bộ vàng ở E6-T4, E6-T5, E6-T8.

| Mục | Nội dung |
|---|---|
| Phụ thuộc | `common`; riêng tư `espressif/dl_fft` `==0.7.0`, luôn khai, chỉ dựng vào khi Kconfig chọn nó |
| Số học | float32, cả component dựng với `-ffp-contract=off`: mỗi phép làm tròn một lần như bản soi gương |
| FFT, Kconfig `DSP_SPEC_FFT_BACKEND` | **`DSP_SPEC_FFT_RADIX4`** (mặc định): `src/fft.c` viết tay, n điểm thực gói thành n/2 điểm phức theo thứ tự đảo bit, các lượt DIT cơ số 4 (một tầng cơ số 2 trước khi log2(n/2) lẻ), rồi tách ra n/2 + 1 vạch, vạch k và n/2 − k cùng lúc; chiều nghịch ghép vạch lại, liên hợp, chạy đúng các lượt ấy rồi liên hợp lần nữa. Nhanh ngang `dl_fft`. **`DSP_SPEC_FFT_DL_FFT`**: `src/fft_dl.c` bọc `dl_rfft_f32_run`/`dl_irfft_f32_run` của `dl_fft`, không khớp bản soi gương từng bit; giữ để so |
| Hợp đồng FFT | thuận không chia, nghịch chia 1/n, n/2 + 1 vạch, bỏ phần ảo của DC và Nyquist; luỹ thừa 2 từ 64 tới 2048 |
| Bộ nhớ | người gọi cấp mọi vùng làm việc (`*_workspace_bytes`), kể cả bảng xoay pha và bảng đảo bit của FFT mặc định; **ngoại lệ duy nhất**: khi Kconfig chọn `dl_fft`, thư viện tự cấp bảng một lần lúc `dsp_spec_fft_init` ở RAM nội (KẾ HOẠCH §4.5.3 luật 7) |
| Bảng dựng một lần | `cos`, `sin`, trọng số mel tính ở double rồi làm tròn một lần sang float32 |
| STFT | căn Hann tuần hoàn dạng `sin(pi i / n)`; ra trễ vào đúng một bước |
| Mel | thang Slaney, tam giác chuẩn hoá diện tích, lưu thưa; log tự nhiên có sàn bằng hàm float32 của module (số mũ đọc từ bit, phần định trị qua chuỗi atanh); MFCC là DCT-II trực chuẩn tra bảng cos |
| Kiểm | `test_apps/unit`: FFT so với DFT `double`, dựng lại STFT, mel và MFCC, rồi đo µs và RAM từng hàm ở `-O2`, với FFT Kconfig chọn; `test_apps/parity` so với bộ vàng, FFT mặc định |
| Số đo | `docs/measurements/latency.md` §1, §23; `parity.md` |
