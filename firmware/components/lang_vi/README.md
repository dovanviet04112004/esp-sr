# lang_vi

Tầng L1, luật ngôn ngữ thuần: chữ tiếng Việt → đơn vị nhận dạng theo luật chính tả, kèm biến thể phương ngữ
(KẾ HOẠCH §3.12). Bản soi gương Python sẽ là `ml/src/srpipe/lang/`, golden phải **khớp tuyệt đối**.

| Mục | Nội dung |
|---|---|
| Phụ thuộc | `common` |
| Hàm | `lang_vi_normalize` → `lang_vi_g2p` một phương ngữ → `lang_vi_lexicon_entry` gộp mọi biến thể; `lang_vi_unit_name` cho log và golden |
| Hiện có | ba file `src/{normalize,g2p,lexicon}.c` là **bản giả trung tính** của E3-T4: `normalize` kiểm UTF-8 (RFC 3629) rồi chép nguyên, `g2p` và `lexicon` ra rỗng, mọi đơn vị tên `"?"`; thuật toán thật ở E11-T4 sau khi E11-T3 chốt bộ đơn vị |
| Bộ nhớ | không trạng thái, không vùng làm việc; người gọi cấp đệm ra (`lang_vi_pron_t` 197 B) |
| Kiểm | `test_apps/host`: CMake thường trên máy tính |
