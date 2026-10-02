# ADR-0016 — `command` `rnnt`: cùng encoder với `ctc`, giải bằng tìm chùm RNN-T theo cây lệnh như MultiNet7

- **Trạng thái**: Chấp nhận (chủ repo 02/10: làm cả RNN-T, đúng như Espressif)
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

## Quyết định

**C, giữ A.** Một lượt học RNN-T cộng CTC ra encoder có cả hai đầu; `command` có hai đường giải `ctc` và `rnnt` trên
cùng encoder, chọn bằng Cửa 3 sau int8 trên tập thu qua board. Thiết kế ở KẾ HOẠCH §3.12: mạng dự đoán 384 chiều ngữ
cảnh 2, bộ nối 384 chiều như MultiNet7; tìm chùm có sửa `N` giả thuyết theo cây lệnh; luật từ chối như `ctc`; mạng dự
đoán và bộ nối là hai mạng int8 qua esp-dl, tìm chùm là C thuần có bản soi gương.

Khác MultiNet7 ở bốn chỗ, cùng lý do với ADR-0013 hay vì bộ lệnh nhỏ:

- 45 lớp (44 đơn vị `lang_vi` có thanh cộng blank) thay 496 mảnh SentencePiece;
- cây tiền tố thay FST tối giản: cùng tập chuỗi được nhận, giả thuyết so theo chuỗi đơn vị như `rnnt_path_cmp`;
  tối giản chỉ bớt bộ nhớ, mà bộ lệnh tối đa 64 lệnh × 4 biến thể;
- phần của lệnh nằm ngay trong cây, đánh dấu không phải lệnh, thay phép kiểm tiền tố và hậu tố riêng;
- luật từ chối `δ₁` `δ₂` và phần như `ctc` thay một ngưỡng phát hiện, để hai đường cùng thước và cùng khoá NVS.

## Hệ quả

- KẾ HOẠCH §3.12, hai bảng mô hình, cây §4.1, Kconfig `AI_ENGINE_COMMAND_BACKEND` (`kws` | `ctc` | `rnnt`), §6.3;
  TASKS E11-T20.
- Ảnh model của `rnnt` có bốn mục: `command_rnnt` (`ESPDL`, `NORM`), `rnnt_predictor`, `rnnt_joiner`; cùng `ns` và
  `wake` vẫn trong tám mục của §6.3.
- Học RNN-T cần cả lưới khung × đơn vị của bộ nối, tốn bộ nhớ GPU hơn CTC nhiều 🔬.
- Xét lại khi `rnnt` không hơn `ctc` sau int8, hay chi phí tìm chùm vượt ngân sách §3.3.

## Nguồn

1. ESP-SR 2.5.5, `lib/esp32s3/libmultinet.a`, ký hiệu đọc bằng `xtensa-esp32s3-elf-nm` ngày 02/10; gói ghim ở
   `firmware/test_apps/espsr_compare/main/idf_component.yml`.
2. ADR-0013, mục MultiNet7 đọc từ file.
3. M. Ghodsi, X. Liu, J. Apfel, R. Cabrera, E. Weinstein, *RNN-Transducer with Stateless Prediction Network*, ICASSP
   2020.
4. A. Graves, *Sequence Transduction with Recurrent Neural Networks*, 2012, arXiv:1211.3711.
