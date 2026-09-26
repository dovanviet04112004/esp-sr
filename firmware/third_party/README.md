# third_party

Mã và dữ liệu của bên thứ ba không có trên ESP Component Registry (KẾ HOẠCH §4.5.1). Mỗi nguồn một thư mục
`<name>/` kèm `LICENSE` nguyên văn và `UPSTREAM.md`: lấy từ đâu, bản nào, lấy phần gì, đã vá gì. Không sửa mã trong
`<name>/src/`; bản vá để ở `<name>/patches/` (CLAUDE.md §2.10).

| Thư mục | Nguồn | Giấy phép | Dùng ở |
|---|---|---|---|
| `webrtc_vad/` | thuật toán và bảng số của VAD WebRTC; không vendor mã | BSD-3 | `dsp_afe` `vad`, `srpipe.dsp.afe.vad`, `vad:` của `contracts/afe.yaml` |
