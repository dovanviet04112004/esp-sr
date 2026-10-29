# svc_listen

Tầng L4, dịch vụ ghép: nửa `nhan` của chuỗi nghe (KẾ HOẠCH §3.12, §5.2, §5.4). Mọi lời gọi trừ `svc_listen_init`
chạy trong `nhan_task` ở nhân 0.

| Mục | Nội dung |
|---|---|
| Phụ thuộc | `common`, `lang_vi`; riêng: `ai_engine`, `dsp_spec` |
| Đặc trưng | mỗi bước sạch: STFT, log-mel theo `contracts/listen.yaml` (`gen_listen.h`); cao độ chạy trên mẫu sạch của cửa sổ, bộ dò đặt lại ở đầu cửa sổ |
| Cắt câu | đoạn `vad` cách nhau không quá `utterance.gap_s` là một câu, câu ngắn hơn `utterance.min_s` bị bỏ; cửa sổ kết thúc ở bước sau đoạn `vad` cuối, lùi tối đa `window_s`, không lấn cửa sổ trước, đúng như Cửa 3 cắt (`srpipe.tasks.command.eval`). Ảnh không có `wake`: mọi câu có cửa sổ. Ảnh có `wake`: mọi bước qua `ai_engine_wake_step` ở mọi trạng thái; `NGHE` không mở cửa sổ; điểm vượt `kws/wake_th` ở bước k (mỗi lần vượt tính một lần) ra sự kiện `WAKE` và vào `LENH` với mốc chặn k + 1, lần vượt ngưỡng trong `LENH` không tính; câu chạy qua k có đúng một cửa sổ, bắt đầu ở k + 1 (cửa sổ nó đã có thì dời về đó), và chỉ giữ khi phần sau k dài hơn `min_s` cộng hangover của `vad`; một quyết định, hay `SVC_LISTEN_COMMAND_WAIT_MS` không có câu, đưa về `NGHE` (KẾ HOẠCH §5.4) |
| Chạy | `svc_listen_feed` mỗi bước; `svc_listen_work` chạy một bước của cửa sổ đang chờ (cao độ, rồi mạng, mỗi khối một lần chạy) xen giữa các bước mới, nên `q_clean` không đầy |
| Bộ lệnh | `lang_vi_lexicon_entry` dựng mọi cách đọc của từng dòng; lúc init từ `/lfs/cmd/set.json` do `main` đọc, lúc chạy qua `svc_listen_set_commands` khi không còn cửa sổ chờ chấm: bộ mới đọc vào bảng dự phòng, đổi bảng chỉ khi đọc được mọi dòng, không thì bảng cũ ở lại và dòng hỏng được báo ra |
| Bộ nhớ | vòng 512 bước log-mel và mẫu sạch, hai bảng lexicon, đầu đặc trưng: PSRAM, cấp một lần lúc init; bảng `dl_fft` của FFT riêng ở RAM nội |
| Không làm | không đọc NVS (`δ₁`, `δ₂`, ngưỡng `wake` do `main` đọc và trao vào), không tạo task, không tạo hàng đợi, không gửi MQTT |
| Kiểm | trên board qua `main`: mỗi quyết định in bước đầu, bước cuối của cửa sổ để so với Python trên luồng tiếng ghi cùng lúc |
