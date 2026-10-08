# ADR-0002 — `dsp_spec` chạy FFT bằng `dl_fft`, bỏ backend `esp-dsp`

- **Trạng thái**: Thay bởi ADR-0020: `dl_fft` thôi là FFT mặc định, còn chọn được bằng Kconfig
- **Ngày**: 2026-09-26
- **Liên quan**: KẾ HOẠCH §3.1, §3.3, §4.5.1, §4.5.3 luật 7; TASKS E6-T2, E6-T3; `docs/measurements/latency.md` §1

---

## Bối cảnh

Mỗi khung 16 ms, `dsp_spec` chạy hai FFT thực 512 điểm (hai micro) và một FFT nghịch (khung sạch), và
`mel` chạy thêm một FFT nữa ở nhân 0. KẾ HOẠCH §4.5.1 cho hai thư viện mã mở của Espressif làm backend,
đo cả hai trên board rồi giữ một (E6-T3). Cả hai đều tự cấp bảng trong hàm init, nên ngoại lệ của §4.5.3
luật 7 áp như nhau cho hai bên.

## Các phương án

Cùng lớp bọc `dsp_spec`, cùng test app, cùng bản dựng `-O2`, trên board B.

| Phương án | Được | Mất | Số đo, 512 điểm float32 |
|---|---|---|---|
| **`dl_fft` 0.7.0** | có sẵn FFT nghịch cho tín hiệu thực; nhanh nhất; chính xác nhất | bảng thư viện 6,2 KB RAM nội | thuận 118,2 µs, nghịch 135,9 µs; STFT + iSTFT một khung 451 µs; sai số so với DFT 1,2e-7; dựng lại 136,3 dB |
| `esp-dsp` 1.8.2 | bảng xoay pha nằm sẵn trong flash, bảng thư viện 2 KB | không có FFT nghịch thực: repo phải tự viết bước tách phổ cả hai chiều; chậm hơn 21–26%; sai số lớn gấp ~7 lần | thuận 148,9 µs, nghịch 160,7 µs; một khung 538 µs; sai số 8,1e-7; dựng lại 120,1 dB |

Bản int16 nhanh gấp tám (13,6 µs với `dl_fft`) nhưng §3.1 đã loại vì SNR ~58 dB; số đo mới không đổi kết
luận ấy.

## Quyết định

Giữ `dl_fft`, ghim `==0.7.0`. Nó thắng ở tốc độ (nhanh hơn 87 µs mỗi khung, ~0,5% một nhân), ở độ chính
xác (16 dB dựng lại), và bớt cho repo một đoạn tách phổ tự viết phải bảo trì. Cái giá là ~2,2 KB RAM nội
tính cả vùng làm việc (8,3 KB so với 6,1 KB) — nhỏ so với ~30 KB mà Wi-Fi vượt ước lượng ở `ram.md` §3.

## Hệ quả

- `esp-dsp` rời khỏi `dsp_spec`: bỏ `src/fft_dsp.c`, bỏ Kconfig `DSP_SPEC_FFT_BACKEND`, bỏ khỏi
  `idf_component.yml`; `check_purity.py` chỉ còn miễn `fft_dl.c`. Mã và phép đo của backend cũ nằm ở
  commit `e0ed592`.
- KẾ HOẠCH §3.1, §3.3 dùng số đo thay số của hãng; §4.5.1, §4.5.4 bỏ `esp-dsp` khỏi `dsp_spec`.
- Xét lại khi: `dl_fft` đổi bản làm số đo lệch, hoặc RAM nội thiếu tới mức 2 KB là thứ phải cắt.
