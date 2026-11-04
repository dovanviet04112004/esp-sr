# `svc_speak` — L4

`noi_task`: phát tiếng ra loa qua `drv_audio`. Hiện có bíp theo `contracts/tones.yaml` (KẾ HOẠCH §3.19); câu nói tới ở E12.

- Phụ thuộc: `common`, `drv_audio`.
- Board không có loa thì `APP_SPEAKER_ENABLE` tắt, component rỗng.
