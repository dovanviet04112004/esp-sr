# ADR-0020 — FFT mặc định của `dsp_spec` là bản cơ số 4 viết tay khớp bản soi gương từng bit; `dl_fft` chọn được

- **Trạng thái**: Chấp nhận (chủ repo 08/10: `dsp_spec` khớp từng bit; FFT tự viết, tối ưu cơ số 4; giữ `dl_fft`,
  đổi qua lại được)
- **Ngày**: 2026-10-08
- **Liên quan**: KẾ HOẠCH §3.1, §3.3, §3.14, §4.5.1, §4.5.3 luật 7, §4.5.4; TASKS E6-T8, E11-T14; ADR-0002, ADR-0006;
  `docs/measurements/latency.md` §1, §22, §23; `parity.md`

---

## Bối cảnh

`make listen-unit` với `command/v8` (80 dải log-mel, lưới vào int8 bước 1/16 độ lệch chuẩn) chấm khác Python vài ‰ ở
14/319 cửa sổ. Mỗi cửa sổ lệch vì đúng một phần tử log-mel nằm cách ranh làm tròn của lưới int8 từ 2,5e-7 tới 8,5e-6:
lật phần tử ấy sang ô bên cạnh thì Python ra đúng số của chip (`latency.md` §22). Mạng esp-dl và phép chấm không lệch.
Log-mel của chip khác bản soi gương ở ba chỗ:

- FFT là `dl_fft` 0.7.0. Trên S3 nó chạy hợp ngữ `dl_fft4r_fc32_aes3`, có `madd.s`/`msub.s`: nhân rồi cộng chỉ làm
  tròn một lần. Bảng xoay pha lấy `cosf`, `sinf` của newlib. Trên máy tính nó chạy bản C thường, số học khác hợp ngữ,
  nên không bản soi gương nào khớp được cả board lẫn CI. ADR-0006 để `dsp_spec` ngoài luật khớp từng bit vì lý do này.
- `mel.c` dựng không có `-ffp-contract=off`: mã máy của bản `bench` gộp `re·re + im·im` và `energy + w·p` thành `madd.s`.
- Log là `logf` của newlib, còn bản soi gương lấy log double rồi làm tròn một lần.

Model cũ đọc 40 dải trên bước 1/8 và trùng 198/198 lần 03/10, nhờ số phần tử sát ranh ít hơn chừng bốn lần, không phải
nhờ khớp. Quyết định của chip phải bằng quyết định Python từng trường (KẾ HOẠCH §3.12), nên đặc trưng vào mạng phải khớp
từng bit.

## Các phương án

Board B, app `dsp_spec/test_apps/unit` dựng ở `-O2`, 240 MHz, IDF 6.0.2, cùng ngày 08/10; 512 điểm float32
(`latency.md` §23).

| Phương án | Được | Mất | Thuận / nghịch | STFT / iSTFT một bước | Sai số so với DFT double |
|---|---|---|---|---|---|
| `dl_fft` 0.7.0 như cũ | thư viện có sẵn | không khớp từng bit ở đâu cả; giả lập hợp ngữ trong Python chỉ khớp board, vỡ khi đổi bản | 118,2 / 135,9 µs | 143,3 / 164,7 µs | 1,2e-7 |
| FFT viết tay cơ số 2 | khớp từng bit trên máy tính và board | chậm hơn 60% | 189,0 / 203,1 µs | 214,1 / 238,3 µs | 1,1e-7 |
| **FFT viết tay cơ số 4** | khớp từng bit trên máy tính và board; nhanh ngang `dl_fft`; bảng nằm trong vùng người gọi cấp | repo tự bảo trì FFT; vùng làm việc 5 680 B thay 2 080 B (nhưng hết 6 244 B bảng thư viện ở RAM nội) | **116,9 / 123,0 µs** | **142,0 / 158,3 µs** | 9,7e-8 |

Phương án giữ `dl_fft` mà nới phép so của `listen-unit` là vá, không sửa gốc: chip và Python vẫn chấm khác nhau.

## Quyết định

FFT thực mặc định của `dsp_spec` là bản viết tay: n điểm thực gói thành n/2 điểm phức theo thứ tự đảo bit, các lượt DIT cơ số 4
(mỗi lượt bằng hai tầng cơ số 2, ba phép nhân phức cho bốn điểm; một tầng cơ số 2 trước khi log2(n/2) lẻ; bướm có hệ số
xoay bằng 1 không nhân), rồi tách ra n/2 + 1 vạch, vạch k và n/2 − k cùng lúc. Chiều nghịch ghép vạch lại, liên hợp,
chạy đúng các lượt ấy, liên hợp lần nữa và nhân 1/n. Bảng `cos`, `sin` cho k < 3n/4 lấy double rồi làm tròn một lần,
dựng lúc khởi tạo trong vùng người gọi cấp. Cả component dựng `-ffp-contract=off`; cửa sổ căn Hann lấy `sin` double làm
tròn một lần; log của mel là hàm float32 của module (số mũ đọc từ bit, phần định trị qua chuỗi atanh). Một khung STFT
442 µs so với 451 µs của `dl_fft`.

`dl_fft` 0.7.0 ở lại, chọn bằng Kconfig `DSP_SPEC_FFT_BACKEND` (CMake dựng `src/fft.c` hay `src/fft_dl.c`, phụ thuộc
khai không điều kiện): để so, và để quay lại thư viện khi nó có đường khớp được. Chọn nó thì bộ vàng `stft`, `mel` và Cửa
3 trên chip thôi khớp từng bit.

## Hệ quả

- `dsp_spec` có `Kconfig` chọn FFT; `dl_fft` vẫn ghim `==0.7.0` ở `idf_component.yml`, và ghim ấy giữ luôn bản `dl_fft` riêng
  của `esp-dl`. Ngoại lệ luật 7 và chỗ miễn của `check_purity.py` chỉ còn cho `src/fft_dl.c`. Bản dựng máy tính chỉ có
  FFT mặc định.
- Bản soi gương `srpipe.dsp.spec.fft` viết bằng numba theo đúng thứ tự phép của `fft.c`; `mel` lấy log và MFCC theo
  `mel.c`. Bộ vàng `stft`, `mel` khớp tuyệt đối, có đối chứng âm; mọi bộ vàng đi qua STFT sinh lại. Trên máy tính, PCM
  của `chain` và `chain_modules` cũng thôi lệch 1 LSB.
- Shard đặc trưng đã dựng lệch bản mới ở bit cuối; model không phải học lại.
- Xét lại mặc định khi một bản `dl_fft` có đường khớp được bản soi gương từng bit, hoặc nhân 0 hay nhân 1 chật tới mức
  FFT là thứ phải cắt.
