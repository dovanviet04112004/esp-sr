# webrtc_vad

| Mục | Nội dung |
|---|---|
| Nguồn | WebRTC, `common_audio/vad/` (`vad_core.c`, `vad_filterbank.c`, `vad_gmm.c`, `vad_sp.c`), đọc từ bản đi kèm `py-webrtcvad` |
| Bản | `wiseman/py-webrtcvad` commit `e283ca41df3a84b0e87fb1f5cb9b21580a286b09` (2021-02-15) |
| Giấy phép | BSD-3, `LICENSE` ở thư mục này, nguyên văn của dự án WebRTC |
| Lấy gì | thiết kế thuật toán (hạ mẫu và cây lọc thông tất, log năng lượng sáu dải, GMM hai lớp, phép thử tỉ số hợp lý cục bộ và toàn cục, cập nhật mô hình, dò mức tối thiểu) và các bảng số của nó: trọng số, trung bình, độ lệch chuẩn khởi đầu, ngưỡng, giới hạn, hệ số lọc, bước cập nhật |
| Không lấy | mã nguồn; không tệp nào của WebRTC được vendor hay biên dịch |
| Viết lại thành | `firmware/components/dsp_afe/src/vad.c` và `ml/src/srpipe/dsp/afe/vad.py`, float32; bảng số chuyển từ dạng Q của WebRTC sang giá trị thực ở `vad:` của `contracts/afe.yaml` |
| Khác bản gốc | xem KẾ HOẠCH §3.10: bước 16 ms (128 mẫu ở 8 kHz), ngưỡng cột 20 ms, kéo dài 240 ms thay bộ đếm kéo dài, số học float32 thay dấu phẩy tĩnh |
