# lang_vi

Tầng L1, luật ngôn ngữ thuần: chữ tiếng Việt → đơn vị nhận dạng theo luật chính tả, mỗi vùng Bắc, Trung, Nam một
cách đọc (KẾ HOẠCH §3.12). Bản soi gương Python là `ml/src/srpipe/lang/`; hai bên khớp tuyệt đối trên bộ vàng.

| Mục | Nội dung |
|---|---|
| Phụ thuộc | `common` |
| Hàm | `lang_vi_normalize` → `lang_vi_g2p` một vùng → `lang_vi_lexicon_entry` mọi vùng, gộp cách đọc trùng; `lang_vi_unit_name` cho log và golden |
| Bảng luật | `contracts/lang_vi.yaml` → `priv_include/gen_lang_vi.h` (`tools/gen_contracts.py`); không bảng nào gõ tay trong `src/` |
| Đơn vị | 44 ký hiệu X-SAMPA: 23 phụ âm đầu, âm đệm `w`, 14 âm chính, 6 thanh `T1`…`T6`; âm tiết ra theo thứ tự đầu, đệm, chính, cuối, thanh |
| `normalize` | NFC từ chữ dựng sẵn hoặc dấu rời theo thứ tự bất kỳ, chữ thường, đọc số (nhóm ba chữ số, mốt, tư, lăm, linh/lẻ, nghìn/ngàn, dấu chấm hàng nghìn, dấu phẩy thập phân, thứ nhất/thứ tư), ký hiệu `% & +`, từ điển viết tắt và từ mượn; `lang_vi_normalize` đọc số theo giọng Bắc |
| `g2p` | tách âm tiết theo chính tả (âm đầu dài nhất, `gi` dùng chung `i`, vần sau `qu`, luật `c/k/q` `g/gh` `ng/ngh`, vần tắc chỉ sắc và nặng); âm tiết chính tả không dựng được thì `ESP_ERR_INVALID_ARG` |
| Bộ nhớ | không trạng thái, không vùng làm việc, không heap; người gọi cấp đệm ra (`lang_vi_pron_t` 197 B). Ngăn xếp: 256 mục đã ghép, 4 B mỗi mục, cộng đệm chuẩn hoá 256 B trong `lexicon_entry` |
| Kiểm | `test_apps/host`: CMake thường, các ca biên của API, rồi bộ vàng `contracts/golden/{g2p,normalize,lexicon}` qua bộ so của `test_apps/parity`; `make parity-host` và CI chạy nó, `make parity-board` chạy cùng bộ vàng trên board B |
| Số đo | board B, profile `bench`: một dòng lệnh có số, ba vùng, 1 183 µs (`budget.md`); ngăn xếp 2 844 B cả vòng đo (`ram.md` §2); parity ở `parity.md` |
