# ADR-0027 — Op riêng của `ai_engine` chia việc sang nhân 1, chọn bằng Kconfig

- **Trạng thái**: Chấp nhận (chủ repo, 2026-10-28)
- **Ngày**: 2026-10-28
- **Liên quan**: KẾ HOẠCH §3.14, §5.1, §5.2, §5.3, §5.5, §6.5; ADR-0021, ADR-0025, ADR-0026; TASKS E11-T35;
  `docs/measurements/latency.md` §31, `docs/measurements/budget.md`

---

## Bối cảnh

Sau mọi bước một nhân, b3 (E11-T35) chạy 1 399,6 ms một cửa sổ trên nhân 0, khớp từng bit (`latency.md` §31). Điều
kiện cắm vào sản phẩm là ~1 s (ADR-0021). Trên một nhân chỉ còn các mục 1–3 ms.

Nhân 1 chỉ có `thu_task` và `sach_task`: trung bình 16,3%, đỉnh 20,6% một khung (`budget.md`). Khoảng bốn phần năm
nhân 1 để không.

Bốn op riêng chiếm 1 218 ms của cửa sổ:

| Op | ms | Việc tách được |
|---|---|---|
| `ConvBlock` (21 khối) | ~728 | các cột tần số, mỗi cột đọc cột bên cạnh qua depthwise 3×3 |
| `PointwiseS8` | ~285 | các hàng, mỗi lát trọng số |
| `GroupNormS8` | ~105 | các lô nhóm kênh |
| `StageSum` | ~103 | các hàng |

Chia đôi việc của mỗi op mà mỗi giá trị ra vẫn do đúng nhân PIE ấy tính trên đúng các số ấy, nên khớp từng bit như
chạy một nhân.

Chủ repo yêu cầu ngày 28/10: bản hai nhân phải có cờ cấu hình đổi qua lại giữa một và hai nhân.

## Các phương án

| Phương án | Được | Mất |
|---|---|---|
| Như cũ, một nhân | không đụng nhân 1 | b3 1,4 s |
| Chạy cả mạng speaker ở nhân 1 | nhân 0 rảnh | không nhanh hơn; ở ưu tiên dưới `sach` vẫn chung nhân với hạn cứng |
| Task phụ ở nhân 1, ưu tiên trên `sach` | nhanh nhất | chen vào khung, phá §5.1 |
| **Task phụ `phu_task` ở nhân 1, ưu tiên 1; mỗi op chia đôi** | dùng phần nhân 1 thừa; `thu`, `sach` không bao giờ chờ nó; khớp từng bit | chung bus MSPI và dcache với `sach`; mỗi op một lần trao việc; `ConvBlock` tính thêm cột đệm ở chỗ chia |
| Chia mạng theo đoạn đồ thị | ít đồng bộ | một cửa sổ không nhanh hơn: mạng chỉ một nhánh |

## Quyết định

Chủ repo duyệt ngày 28/10. Chi tiết ở KẾ HOẠCH §3.14 và §5, mã ở `espdl_split`.

- **Cờ:** Kconfig `AI_ENGINE_CORES` chọn `ONE` (mặc định) hay `TWO`. Cờ đổi danh sách nguồn: `espdl_split_one.cpp`
  hay `espdl_split_two.cpp`. Op gọi cùng `split_run`, không rẽ nhánh theo cờ.
- **Task:** `phu_task` ghim nhân 1, ưu tiên 1, chỉ có ở `TWO`, tạo trong `main/app_tasks.c`. Nó chờ việc trong
  `ai_engine_helper_serve`.
- **Trao việc:**
  - Mỗi op chia việc thành hai phần độc lập. Task chạy mạng làm phần 0 và giao phần 1 cho `phu_task`.
  - Xong phần 0 mà `phu_task` chưa nhận phần 1, vì nhân 1 đang bận khung, thì task giao tự làm luôn. Đã nhận thì
    chờ nó xong.
  - Op trả về khi cả hai phần xong.
  - Khi một task khác đang giữ `phu_task`, op chạy cả hai phần ở chỗ.
- **Cách chia:**
  - `ConvBlock` theo nửa tần số. Mỗi nửa tự tính cột đệm của mình, nên trong op hai nửa không chờ nhau.
  - `PointwiseS8` theo hàng trong mỗi lát.
  - `GroupNormS8` và `StageSum` chạy trọn ở task gọi, theo luật dưới. `GroupNormS8` giữ bảng và hàng nháp ở PSRAM,
    và khi chia thì ba phép 148 khung chậm 12–23% vì hai nửa giành dcache. `StageSum` cộng dồn từng số hạng vào chính
    hàng ra.
  - Op khác chia sau, khi số đo cho thấy đáng.
- **Luật của phần chạy song song:** phần ấy chỉ ghi PSRAM mà không đọc lại thứ nó vừa ghi trong op. Vùng nháp, mảng
  tham số của nhân và hàng đợi preload nằm ở RAM nội: vùng mượn hay ngăn xếp của task chạy phần ấy. Trên board B,
  khi hai nhân chạy cùng lúc, `ConvBlock` có vòng hàng ở PSRAM ra số sai rải khắp hàng và cột, còn khối có vòng hàng
  trong vùng mượn thì không lần nào. Bài thử hai nhân ghi xen hai nửa mọi dòng cache PSRAM không mất byte nào, nên
  nguyên nhân nằm ở lượt đọc lại, chưa rõ cơ chế (`latency.md` §31).
- **Vùng mượn** (ADR-0025) giữ 79 680 B. Hai nửa của khối chạy khi vùng mượn chứa vòng hàng, đệm dải và hàng vào
  conv sau của cả hai; không thì khối chạy cả hàng ở task gọi, như bản một nhân. Ở b3 vì thế stage 1 và 5 không chia.
- **Bộ preload** (ADR-0026): cả chip chỉ có một bộ. Phần chạy song song giữ bước "xem rảnh rồi khởi vùng" dưới
  một spinlock, mỗi nhân chỉ kết thúc vùng của mình, và mỗi nửa chỉ nạp trước các dòng hàng y nằm trọn trong phần
  của mình. Phần chạy một mình không khoá, như bản một nhân.

## Hệ quả

- **Đo trên board B:** b3 từ ~1 400 ms xuống ~1 310 ms một cửa sổ, khớp từng bit ở cả hai cờ (`latency.md` §31).
  Ước 0,9–1,0 s lúc lập ADR là sai: khoảng nửa giờ của các op lớn là đọc ghi PSRAM, mà hai nhân dùng chung một bus.
  b0 gần như không đổi, 342,7 → 341,0 ms (`latency.md` §25): khối 24 và 48 kênh của nó không dùng vùng mượn nên
  không chia, vì vậy sản phẩm chạy b0 không có lý do bật hai nhân.
- **`sach` chung bus và dcache với `phu_task`.** Để đo, app thử của `ai_engine` chạy chuỗi `dsp_afe` mỗi 16 ms ở
  nhân 1, ưu tiên của `sach`, trong lúc speaker chạy hai nhân. Đỉnh một khung phải dưới 12,8 ms (KẾ HOẠCH §5.6).
- **IDLE1** chạy giữa hai op: `phu_task` chặn sau mỗi phần.
- **Ghi flash:** nhân kia đứng trong lúc ghi, nên `phu_task` đứng theo. Lượt thử ghi NVS song song của ADR-0026 chạy
  ở cả hai cờ.
- **Đo:** mặc định vẫn một nhân tới khi chủ repo chọn. Mỗi bước đo cả hai cờ trên board B, khớp từng bit ở cả hai.
- **Còn mở:** stage 1 của b3 (ba khối, ~95 ms mỗi khối) chia được khi vùng mượn rộng thêm ~4 KB; việc ấy cần đo RAM
  nội trên firmware sản phẩm và chủ repo duyệt.
- **Xét lại** khi:
  - `sach` vượt 12,8 ms đỉnh;
  - nhân 1 thêm một task;
  - một op chia mới cần vùng nháp ngoài RAM nội.
