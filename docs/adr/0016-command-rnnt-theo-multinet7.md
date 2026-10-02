# ADR-0016 — `command` `rnnt`: cùng encoder với `ctc`, mạng RNN-T như MultiNet7, chấm chính xác mọi lệnh trên cây lệnh

- **Trạng thái**: Chấp nhận (chủ repo 02/10: làm cả RNN-T, đúng như Espressif; chủ repo 02/10 tối: chấm chính xác thay tìm chùm, làm cách chuẩn nhất)
- **Ngày**: 2026-10-02
- **Liên quan**: KẾ HOẠCH §3.12, §4.1, §4.5.2, §6.3; TASKS E11-T20; ADR-0012, ADR-0013

---

## Bối cảnh

ADR-0013 dựng encoder của `command` theo MultiNet7 và học CTC cộng RNN-T phụ trợ, nhưng giải bằng CTC. Ký hiệu của
`libmultinet.a` trong ESP-SR 2.5.5 cho thấy MultiNet7 trên chip giải bằng RNN-T, không bằng CTC:

| Đối tượng | Gọi |
|---|---|
| `multinet7_quantized.c.obj` | `rnnt_beam_search_with_fst`, `rnnt_beams_{alloc, copy_topk, add_to_finished, filter_finished, remove_finished, free}`, `rnnt_path_is_prefix`, `rnnt_path_is_qualified`, `fst_compile_from_commands`, `fst_compile_from_commands_reversed`, `fst_determinize`, `fst_minimize`, `fst_command_list_contains_prefix`, `fst_command_list_contains_suffix`; hàm riêng `decoder_joiner_run`, `build_fsts`, `model_set_det_threshold`, `update_results_by_rnnt_path`. Không hàm giải CTC nào |
| `multinet6_quantized.c.obj` | cả `ctc_decode_with_fst` lẫn `rnnt_beam_search_with_fst` |
| `rnnt_decoder.c.obj` | định nghĩa `rnnt_beam_search`, `rnnt_beam_search_with_fst`, `rnnt_greedy_search` |

Trọng số của MultiNet7 (ADR-0013): mạng dự đoán không trạng thái nhúng 496 × 384 với tích chập ngữ cảnh 2 theo chiều
sâu, bộ nối chiếu phía encoder và phía dự đoán về 384 rồi ra 496 lớp, tổng 580 nghìn tham số; cấu hình `rnnt_ctc_2.0`.

Chủ repo chọn làm cả RNN-T như Espressif. Buổi demo 02/10 cũng cho thấy giải CTC theo khung để lọt nửa lệnh, đã vá bằng
luật phần (KẾ HOẠCH §3.12); RNN-T vẫn phải có luật từ chối, vì đường đi vẫn ép đơn vị thiếu vào vài khung.

## Các phương án

| Phương án | Được | Mất |
|---|---|---|
| A. Chỉ giải CTC | đã khớp từng bit trên board; chấm cộng đủ mọi cách căn | mạng không biết đơn vị đã phát; khác cách MultiNet7 giải |
| B. Học RNN-T phụ trợ, vẫn giải CTC | chip không đổi gì | không đo được cách giải MultiNet7 thật sự dùng |
| C. Giải RNN-T như MultiNet7, cùng encoder | đúng cách Espressif giải trên S3; mạng dự đoán đọc đơn vị đã phát | thêm hai mạng nhỏ qua esp-dl, tìm chùm C thuần, bộ vàng mới; tìm gần đúng theo chùm; chi phí trên chip 🔬 |
| D. Mạng RNN-T như MultiNet7, chấm chính xác mọi lệnh như `ctc` | không rơi lệnh đúng giữa chừng; nhiều đơn vị mỗi khung như lưới học | bộ nối chạy mỗi khung × mỗi ngữ cảnh của cây, nhiều hơn tìm chùm; khác cách MultiNet7 giải |

## Quyết định

**D, giữ A.** Một lượt học RNN-T cộng CTC ra encoder có cả hai đầu; `command` có hai đường giải `ctc` và `rnnt` trên
cùng encoder, chọn bằng Cửa 3 sau int8 trên tập thu qua board. Thiết kế ở KẾ HOẠCH §3.12: mạng theo đúng những gì
MultiNet7 để lộ, mạng dự đoán 384 chiều ngữ cảnh 2 và bộ nối 384 chiều như trọng số của nó, học RNN-T cộng CTC như
`rnnt_ctc`; cây tiền tố của mọi biến thể và mọi phần dựng từ bộ lệnh lúc nạp; mỗi biến thể và mỗi phần chấm bằng tổng
xác suất mọi cách căn trên lưới RNN-T, tính xuôi theo khung trên cây, như `ctc` chấm bằng thuật toán tiến. Mạng dự đoán
và bộ nối là hai mạng int8 qua esp-dl, cây và phép chấm là C thuần có bản soi gương.

C được làm trước: FST tối giản, tìm chùm có sửa của k2, một đơn vị mỗi khung. Cửa 3 float của run E11-T20 cho thấy tìm
chùm làm rơi lệnh đúng giữa chừng khi một phần của nó tạm điểm cao hơn, rồi không giả thuyết nào còn trọn lệnh: 53/112
câu lệnh nhận đúng ở chùm 4, 66/112 ở chùm 16, "tăng âm lượng" 0/13 và 1/13. Chấm chính xác trên cùng cửa sổ có 81/112
lệnh đúng đứng đầu, "tăng âm lượng" 8/13; `ctc` có 84/112, cộng điểm hai đầu 85/112. Một đơn vị mỗi khung còn đặt trần
352 ms cho "tăng âm lượng" (11 đơn vị) trong khi mạng phát hai, ba đơn vị trong một khung ở 287 trên 930 khung có phát
(`measurements/command.md` §4).

Khác MultiNet7 ở bốn chỗ, có lý do:

- 45 lớp (44 đơn vị `lang_vi` có thanh cộng blank) thay 496 mảnh SentencePiece: tiếng Việt cần thanh (ADR-0010);
- chấm chính xác mọi lệnh trên cây tiền tố thay tìm chùm theo FST tối giản, vì tìm chùm làm rơi lệnh đúng (trên);
- mọi phần của lệnh (đoạn âm tiết liền nhau) được chấm như một chuỗi, vì buổi demo 02/10 cho thấy nửa lệnh phải bị từ
  chối; MultiNet7 có phép kiểm tiền tố, hậu tố riêng mà thư viện không cho đọc cách dùng;
- luật từ chối `δ₁` `δ₂` và phần như `ctc` thay ngưỡng phát hiện của `model_set_det_threshold`, để hai đường cùng
  thước và cùng khoá NVS.

Thư viện đóng nên không đọc được: MultiNet7 dùng FST đảo ngược (`fst_compile_from_commands_reversed`) vào việc gì, bề
rộng chùm, số ký hiệu tối đa mỗi khung, và công thức ngưỡng phát hiện của nó. Ở đây các ngưỡng đặt riêng cho đường
`rnnt`, chọn trên `val`; một khung phát bao nhiêu đơn vị cũng được, như lưới mà `rnnt_loss` học.

## Hệ quả

- KẾ HOẠCH §3.12, hai bảng mô hình, cây §4.1, Kconfig `AI_ENGINE_COMMAND_BACKEND` (`kws` | `ctc` | `rnnt`), §6.3;
  TASKS E11-T20.
- Ảnh model của `rnnt` có bốn mục: `command_rnnt` (`ESPDL`, `NORM`), `rnnt_predictor`, `rnnt_joiner`; cùng `ns` và
  `wake` vẫn trong tám mục của §6.3.
- Học RNN-T cần cả lưới khung × đơn vị của bộ nối, tốn bộ nhớ GPU hơn CTC nhiều 🔬.
- Bộ nối chạy mỗi khung × mỗi ngữ cảnh khác nhau của cây, khoảng 150 ngữ cảnh với bộ 10 lệnh mặc định 🔬, thay vì
  mỗi khung × chùm; vượt ngân sách thì bỏ nút kém nút tốt nhất của khung quá một ngưỡng nat, không theo số giả thuyết.
- Xét lại khi `rnnt` không hơn `ctc` sau int8, hay chi phí chấm vượt ngân sách §3.3.

## Nguồn

1. ESP-SR 2.5.5, `lib/esp32s3/libmultinet.a`, ký hiệu đọc bằng `xtensa-esp32s3-elf-nm` ngày 02/10; gói ghim ở
   `firmware/test_apps/espsr_compare/main/idf_component.yml`.
2. ADR-0013, mục MultiNet7 đọc từ file.
3. M. Ghodsi, X. Liu, J. Apfel, R. Cabrera, E. Weinstein, *RNN-Transducer with Stateless Prediction Network*, ICASSP
   2020.
4. A. Graves, *Sequence Transduction with Recurrent Neural Networks*, 2012, arXiv:1211.3711.
