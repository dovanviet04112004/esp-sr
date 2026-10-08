# Parity C ↔ Python

Kết quả `test_apps/parity` trên board, đọc `contracts/golden/`. Ngưỡng lấy từ `tolerance.yaml` của từng khối.
App dựng bằng cờ trình biên dịch của `bench`, cũng là của `prod` (`-O2`, KẾ HOẠCH §3.14), hai lần: mọi module tắt với bộ
vàng `chain`, và profile `modules` (đúng `firmware/sdkconfig.afe`) với bộ vàng của từng module thật và `chain_modules`.
Chạy: `make parity-board`; trên máy tính: `make parity-host`. Số trong bảng lấy từ `parity_report.txt` mà lượt board để
lại trong thư mục build, qua `python3 pytest_parity.py --report <file>`.

| Khối | Số ca | Sai số tuyệt đối lớn nhất | SNR nhỏ nhất dB | Ngưỡng | Đối chứng âm đỏ | Commit | Ngày |
|---|---|---|---|---|---|---|---|
| `stft` — phổ phân tích, FFT cơ số 4 mặc định (ADR-0020); máy tính và board B | 4 | 0 | — | khớp tuyệt đối | — | f277696 | 08/10 |
| `stft` — tổng hợp từ phổ vàng; máy tính và board B | 4 | 0 | — | khớp tuyệt đối | lệch một mẫu: 0,958, −3,1 dB → đỏ | f277696 | 08/10 |
| `mel` — log-mel, ba cấu hình (40, 80, 24 dải); máy tính và board B | 3 | 0 | — | khớp tuyệt đối | lệch một dải: 2,24, 3,0 dB → đỏ | f277696 | 08/10 |
| `mel` — MFCC (13, 20, 24 hệ số); máy tính và board B | 3 | 0 | — | khớp tuyệt đối | — | f277696 | 08/10 |
| `chain` — `pcm` ra của mặt tiền `dsp_afe`, mọi module tắt (int16); máy tính và board B | 4 | 0 | — | ≤ 1 LSB, ≥ 60 dB | lệch một mẫu: 15 090 LSB, −2,9 dB → đỏ | f277696 | 08/10 |
| `chain` — `seq`, `doa_deg`, `doa_conf`, `vad`, `level_dbfs`, `gain_db`, `flags` | 4 | 0 | — | khớp tuyệt đối (`level_dbfs` ≤ 1) | — | f277696 | 08/10 |
| `chain_modules` — `pcm` ra với `hpf`, `balance`, `ns_omlsa`, `vad`, `agc` của `sdkconfig.afe`, hiệu chuẩn và số gieo của ca (int16); máy tính và board B | 5 | 0 | — | ≤ 1 LSB, ≥ 60 dB | bỏ qua `calib/bal`: 473 LSB, 16,4 dB → đỏ | f277696 | 08/10 |
| `chain_modules` — `seq`, `doa_deg`, `doa_conf`, `vad`, `level_dbfs`, `gain_db`, `flags` | 5 | 0 | — | khớp tuyệt đối (`level_dbfs` ≤ 1) | cùng ca: `level_dbfs` 2 → đỏ | f277696 | 08/10 |
| `hpf` — biquad dạng II chuyển vị viết tay (ADR-0004), máy tính và board B | 4 | 0 | — | ≤ 1e-6, ≥ 120 dB | trễ một mẫu: 0,996, −3,1 dB → đỏ | a934a6a | 27/09 |
| `balance` — nhân phức viết tay (ADR-0005), máy tính và board B | 4 | 0 | — | ≤ 1e-6, ≥ 120 dB | hệ số liên hợp: 26,0, −3,5 dB → đỏ | a934a6a | 27/09 |
| `vad` — mức sáu dải, quyết định thô, `speech` sau kéo dài; máy tính và board B | 4 | 0 | — | mức ≤ 1e-6, ≥ 120 dB; quyết định khớp tuyệt đối | `speech` trễ một bước: 1 → đỏ | a934a6a | 27/09 |
| `agc` — mọi mẫu ra, `gain_db` mỗi bước; máy tính và board B | 3 | 0 (ra), 4,8e-7 dB (`gain_db`) | 143,2 (`gain_db`) | ra ≤ 1e-6, ≥ 120 dB; `gain_db` ≤ 1e-5 | bỏ qua cờ nói: 1,5e-3, 19,4 dB → đỏ | a934a6a | 27/09 |
| `ns_omlsa` — gain mỗi vạch và xác suất có tiếng nói mỗi bước; máy tính và board B | 5 | 0 | — | ≤ 1e-6, ≥ 120 dB | hệ số làm trơn không bình phương ở bước 16 ms: 0,75, 13,1 dB → đỏ | 5636ed9 | 27/09 |
| `g2p` — mã trả về và đơn vị của 16 742 âm tiết hợp lệ và 38 cụm từ, mỗi vùng một lượt; máy tính và board B | 29 | 0 | — | khớp tuyệt đối | `d` giọng Nam đọc z: 1 → đỏ | d259771 | 27/09 |
| `normalize` — mã trả về và từng byte ra của 64 đầu vào; máy tính và board B | 1 | 0 | — | khớp tuyệt đối | 105 đọc giọng Nam: 120 → đỏ | d259771 | 27/09 |
| `lexicon` — mã trả về và mọi trường `lang_vi_pron_t` của 27 dòng; máy tính và board B | 1 | 0 | — | khớp tuyệt đối | giọng Nam giữ ngã của nguyễn: 1 → đỏ | d259771 | 27/09 |

## Bản tham chiếu Python: STFT phân tích rồi tổng hợp (E6-T1)

`srpipe.dsp.spec.stft`, float32, lưới §3.1 (512 / 256, căn Hann tuần hoàn), tín hiệu dài 64 bước. Ra trễ vào đúng **một bước (256 mẫu)**; trễ thuật toán từ lúc một mẫu tới cho tới lúc nó ra là tới một cửa sổ (32 ms).

| Tín hiệu | SNR dựng lại dB | Sai số tuyệt đối lớn nhất |
|---|---|---|
| ồn trắng ±0,5 | 138,8 | 1,79e-7 |
| sin 440 Hz biên độ 0,5 | 138,6 | 1,79e-7 |
| chirp 50 Hz → 7,9 kHz | 139,0 | 2,09e-7 |
| chuỗi xung 120 Hz biên độ 0,9 | 139,2 | 2,38e-7 |

Đây là trần float32 của chính bản tham chiếu; bộ vàng C ↔ Python (E6-T4) so với bảng trên.

## Chuỗi mặt tiền `dsp_afe` (E3-T4)

`pcm` lệch tới 1 LSB là bản chất của phép so, không phải lỗi: trung bình hai mẫu int16 có tổng lẻ rơi đúng vào nửa
LSB, và sai số float32 của hai thư viện FFT (`dl_fft` trên board, numpy ở Python) quyết định làm tròn lên hay xuống.
Ca có tổng hai kênh luôn chẵn (chirp giống nhau hai kênh, tiếng gần im) khớp tuyệt đối. Trên máy tính: 78,1 dB.

## Dựng lại trên phiên thu thật của board (E5-T11)

App khung rỗng (`main` bản `dev`, mọi module `dsp_afe` tắt), luồng `mode 5` (`ch0 ch1 clean`) 10 phút về
`srhost.stream_rx`; `srhost.score` chạy `srpipe.dsp.afe.chain` trên `ch0 ch1` rồi so với `clean` của board, bỏ hai
bước đầu, phán theo `contracts/golden/chain/tolerance.yaml`.

| Phiên | Bản dựng | Bước so | Sai số lớn nhất | Mẫu vượt ngưỡng | Mẫu lệch 1 LSB | SNR dB | Ngày |
|---|---|---|---|---|---|---|---|
| `20260926_home_002` | `dev` @ `3d3335d` | 37 498 | 1 LSB | 0 | 21,1 % | 25,1 | 26/09 |

Phòng yên, không nguồn âm: `clean` chỉ khoảng −72 dBFS (RMS ~8 LSB), nên sai số làm tròn 1 LSB của mục trên chiếm phần
lớn và kéo SNR xuống 25 dB. Ngưỡng SNR 60 dB của bộ vàng áp cho tín hiệu có biên độ của bộ vàng, không áp ở đây; phép
phán của phiên thật là sai số lớn nhất ≤ 1 LSB.

## `hpf` (E7-T1)

Bản C (dạng II chuyển vị viết tay, ADR-0004) và bản Python làm cùng các bước float32 theo cùng thứ tự, hệ số tính cos và
sin ở double rồi làm tròn một lần, và `dsp_afe` dựng không gộp nhân-cộng (ADR-0006): máy tính và board B ở `-O2` đều
khớp **từng bit** trên cả bốn ca. Bản gộp nhân-cộng lệch tới 2,0e-6 (`latency.md` §6), ngoài ngưỡng. Kernel `esp-dsp` dạng II mà
ADR-0003 chọn trước đó lệch tới 1,2e-4 (68,7 dB) trên board ở ca hum 50 Hz −10 dBFS trên một chiều, vì bản hợp ngữ làm
tròn khác và dạng II khuếch đại chênh lệch ở tần số thấp. Chạy lại bằng `make parity-board`.

## `balance` (E7-T2)

Bốn ca, mỗi ca 16 bước phổ `ch1` do bộ phân tích cho: hệ số dáng board B (−11 dB, đường pha −0,1 mẫu và −2,2°) trên ồn
đều, hệ số đơn vị trên chirp (ra phải bằng vào), hệ số ngẫu nhiên −40 … +20 dB với pha bất kỳ trên phổ gần tràn thang,
và hệ số dáng board trên phổ gần im lặng. Máy tính và board B ở `-O2` khớp **từng bit** cả bốn ca. Đối chứng âm nhân
với hệ số liên hợp, lỗi dấu dễ mắc nhất của phép nhân phức: lệch 26,0, −3,5 dB, đỏ. Chạy lại bằng `make parity-board`.

## `vad` (E7-T3)

Bốn ca đầu vào `int16` (chia 32 768 ở cả hai phía nên là cùng một số float), mỗi ca 160 bước, dài hơn cửa sổ 100 bước
của bộ dò tối thiểu: tiếng tổng hợp giống lời nói trên ồn trắng; tiếng nhỏ trên ồn đỏ ở `aggressiveness` 2; 40 bước im
tuyệt đối, không được mô hình hoá, rồi các đoạn to ở mức 3; nhiễu nhảy mức 28 dB ở mức 1. Máy tính và board B ở `-O2` khớp **từng
bit** cả ba tensor của cả bốn ca. Đối chứng âm cho `speech` trễ một bước: đỏ.

## `agc` (E7-T4)

Ba ca đầu vào `int16` kèm cờ nói mỗi bước: tiếng nhỏ để gain leo 64 bước, đóng băng 32 bước rồi leo tiếp (128 bước); tiếng
to có sáu cụm tràn thang để bộ chặn đỉnh giữ dưới −3 dBFS trong lúc gain hạ; nhiễu dưới đích −20 dBFS, cờ nói bốn trên
sáu bước. Máy tính và board B khớp **từng bit** mọi mẫu ra của
cả ba ca. `gain_db` là số báo: Python lấy `log10` ở double rồi làm tròn một lần (§3.14), bản C gọi `log10f`, và cả glibc
lẫn newlib lệch giá trị làm tròn đúng ấy tới một bit cuối (4,8e-7 dB). Đối chứng âm bỏ qua cờ nói nên gain không nhúc
nhích: đỏ.

## `ns_omlsa` (E9-T1)

Năm ca công suất mỗi bước, mỗi ca quá hai cửa sổ cực tiểu của IMCRA: tiếng nói từng cụm trên ồn trắng; tiếng nhỏ trên ồn
đỏ ở sàn −6 dB; nhiễu tăng 20 dB; im số rồi tiếng rất nhỏ ở sàn −18 dB, để sàn bắt đầu ở trạng thái chờ; tiếng nói có vọng
dư đưa vào làm phổ vọng. Module và bản soi gương làm cùng từng phép float32 theo cùng thứ tự, exp2, log2, nghịch đảo và
nghịch đảo căn là của riêng module, hằng số dựng bằng phép tính cơ bản ở double: máy tính và board B ở `-O2` khớp **từng
bit** cả gain lẫn xác suất ở cả năm ca. Đối chứng âm giữ hệ số làm trơn của bài ở 8 ms mà không bình phương ở bước 16 ms,
lỗi dễ mắc nhất khi đem bài sang lưới này: đỏ.

## `lang_vi` (E11-T4)

Ba khối, một cho mỗi hàm. `g2p` có một ca cho mỗi âm đầu (cộng ca không âm đầu), gồm mọi âm tiết mà chính tả tiếng Việt
dựng được trên âm đầu ấy, mỗi âm tiết đọc theo ba vùng, và một ca cụm từ có nhãn: chính tả không dựng được, dấu rời, UTF-8
hỏng, cách đọc dài quá `LANG_VI_UNITS_MAX`. `normalize` gồm dấu rời theo cả hai thứ tự, số, ký hiệu, từ điển, tràn số chữ
và tràn đệm ra. `lexicon` gồm bộ lệnh mặc định và các dòng có nhãn với mọi mặt nạ. Luật là rời rạc nên ngưỡng bằng 0.
Bản C chép từng bước của `srpipe.lang` trên cùng bảng sinh từ `contracts/lang_vi.yaml`, và khớp ngay lượt đầu, trên máy
tính và trên board B ở cả hai bản dựng của `make parity-board`.

## Chuỗi với các module sản phẩm (E7-T5)

`chain_modules`: mặt tiền dựng với đúng các module `firmware/sdkconfig.afe` bật (`hpf`, `balance`, `ns_omlsa`, `vad`, `agc`; `ns` từ E9-T1), mỗi ca
mang `config` (sàn `ns` dB, đích `agc` dBFS, mức `vad`) và, nếu board của ca đã hiệu chuẩn, `gains` làm `calib/bal`. Bốn
ca 192 bước: tiếng nhỏ có quãng nghỉ trên board có ch1 thấp 11 dB, đã hiệu chuẩn (gain leo 5 dB trong 3 s); tiếng to có
cụm vượt toàn thang trên board chưa hiệu chuẩn, đích −20 dBFS, `vad` mức 3 (bộ chặn đỉnh giữ ở −3 dBFS, cờ `clipped`);
tiếng trên hum 50 Hz và một chiều, reset giữa ca, `vad` mức 0; chỉ ồn, đích −30 dBFS. Từng module khớp từng bit riêng
lẻ; hai FFT (`dl_fft` trên board, numpy ở Python) lệch ở mức nhiễu float32, chỉ đủ đẩy một phép làm tròn `pcm` đi 1 LSB,
không đổi quyết định `vad` hay `gain_db` nào. Máy tính và board B như nhau: tới 1 LSB, SNR nhỏ nhất 77,1 dB ở ca tiếng
nhỏ (tín hiệu ra chỉ khoảng −60 dBFS), mọi trường số nguyên khớp tuyệt đối. Đối chứng âm mang `gains` dáng board nhưng
được tính như chưa hiệu chuẩn: lệch 697 LSB, đỏ.

## Chỗ chứa bộ vàng

App parity có bảng phân vùng riêng (KẾ HOẠCH §4.3) với `storage` 8 MB. Bộ vàng hiện 6 624 992 B, 6 848 512 B tính theo
khối 4 KB của LittleFS, 82% phân vùng; ba khối của `lang_vi` thêm 538 756 B. Mọi ca giữ đủ độ dài và đủ mẫu, ca lớn nhất
483 KB (`ns_omlsa`, 240 bước công suất và gain) trong bộ đệm đọc 512 KB.

## Bộ vàng không phụ thuộc máy

Workflow `ml` tại `1c460d2` thấy bộ `agc` đã commit khác bản máy chạy CI sinh ra ở bit cuối của `gain_db`: numpy lấy
`log10` float32 theo đường SVML trên máy AVX-512, làm tròn khác. Cùng loại lỗi nằm ở cửa sổ (`sin` float32) và log-mel
(tổng qua BLAS, `log` float32), chưa đỏ chỉ vì máy ấy tình cờ ra cùng số. Từ `a934a6a` bản soi gương theo luật mới của
KẾ HOẠCH §3.14: hàm siêu việt tính ở double rồi làm tròn một lần, tổng float32 cộng theo thứ tự của bản C. `log_mel` nhờ
đó sát C hơn: 9,5e-7 (143,1 dB) trên board, 4,8e-7 (163 dB) trên máy tính.

## `dsp_spec` khớp từng bit (E6-T8, ADR-0020)

Phần lệch còn lại của `stft` và `mel` đến từ ba chỗ: FFT `dl_fft` (hợp ngữ S3 gộp nhân-cộng, bảng `cosf`/`sinf` của
newlib), `mel.c` dựng không có `-ffp-contract=off`, và `logf`. Đủ để lưới int8 của `command/v8` lật một ô ở 14/319 cửa sổ
của `make listen-unit` (`latency.md` §22). Với FFT cơ số 4 viết tay mặc định, cả component dựng `-ffp-contract=off` và log
float32 của module, `make parity-host` và `make parity-board` (08/10, `f277696`, hai bản dựng) ra `max_abs 0` ở mọi ca
của `stft`, `mel`, `chain`, `chain_modules`; PCM của `chain` và `chain_modules` thôi lệch 1 LSB. `agc` `gain_db` vẫn
4,8e-7 vì `log10f`, trong ngưỡng của nó. Chọn Kconfig `DSP_SPEC_FFT_DL_FFT` thì `stft`, `mel` không còn qua ngưỡng
tuyệt đối.

## Đường nối tiếp

Cầu CH340 sau usbipd có lúc rơi byte: có lượt mất cả dòng, có lượt một dòng cụt đuôi dính vào dòng sau; nhật ký kernel có
các URB bị huỷ của `vhci_hcd`. Kết quả vì thế đi qua `test_report` (KẾ HOẠCH §4.5.7): mỗi dòng có số thứ tự và CRC32,
dòng cuối là `end <n> lines`, máy tính bỏ dòng sai CRC và xin lại dòng thiếu. Lượt parity `modules` tại `9e56e13` gặp đúng
chuyện ấy: dòng 42 mất đuôi, dòng 43 mất hẳn, dòng 44 mất đầu; bộ gom xin lại `42 43 44`, board in lại cả ba với CRC đúng,
và lượt qua mà không phải chạy lại. Đổi một chữ số trong một dòng của log, hay xoá một dòng, bộ chấm đều từ chối. Lượt
`modules` tại `229c321` gặp hai chỗ: dòng 14 cụt đuôi dính vào dòng 15; dòng 47 cụt đuôi, 48 tới 50 mất hẳn, dính vào 51.
Bộ gom bỏ hai dòng sai CRC, xin lại bảy dòng thiếu, đủ 94 trên 94, và lượt qua.
