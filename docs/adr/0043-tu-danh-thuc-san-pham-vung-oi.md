# ADR-0043 — Sản phẩm thức bằng "vừng ơi", thay "happy"

- **Trạng thái**: Chấp nhận (chủ repo 03/11: "Chốt vừng ơi"). Thay ADR-0033 và điểm 3 của ADR-0040
- **Ngày**: 2026-11-03
- **Liên quan**: KẾ HOẠCH §0.2, §1.2, §3.11, §5.4; ADR-0033, ADR-0040; TASKS E11-T41, E11-T49;
  `docs/measurements/wake.md` §12

---

## Bối cảnh

ADR-0040 điểm 3 chỉ đưa "vừng ơi" vào sản phẩm khi nó bắt câu "vừng ơi" của chủ repo qua board nhiều hơn "happy" bắt câu
"happy". Chưa có phiên nào chủ repo nói "happy", nên phép so ấy chưa làm được.

Board B thức bằng "vừng ơi" từ 01/11 và chủ repo dùng nó hằng ngày. Lock của lượt ấy nằm ngoài git, nên `make product`
từ HEAD nạp lại "happy".

## Các phương án

| Phương án | Được | Mất | Số đo |
|---|---|---|---|
| Giữ "happy" tới khi có phiên "happy" để so | đúng điểm 3 của ADR-0040 | bản trong git khác bản chủ repo dùng; lock nằm ngoài git | "happy": chưa có phiên chủ repo; `test` 4,29 báo nhầm/giờ |
| **"vừng ơi" là từ của sản phẩm, bỏ phép so** | từ tiếng Việt chủ repo muốn; git khớp board | không biết "vừng ơi" bắt hơn hay kém "happy" | lượt 6, hàng `percentile` 800‰: 32/35 đoạn chưa học của chủ repo, thức 1 lần trong 1,04 giờ phiên khác; `test` 5,69/giờ trên cùng `test_neg` |

Số của cả hai dòng ở `wake.md` §12.

## Quyết định

1. Sản phẩm thức bằng "vừng ơi". Lượt deploy, hàng int8 và ngưỡng ghi ở `contracts/models.lock.json` và
   `firmware/models/wake/meta.json` như mọi nhánh; lúc chốt là lượt 6, hàng `percentile`, 800‰.
2. Bỏ phép so tỉ lệ bắt với "happy" của ADR-0040 điểm 3. `wake/happy1` còn là mốc báo nhầm, vì `test_neg` của hai split
   trùng byte.

## Hệ quả

- KẾ HOẠCH §0.2 dòng 11, dòng Speech Commands ở §1.2 và đoạn hai từ ở §3.11 ghi từ mới; README cũng vậy.
- Hằng mốc của từ đánh thức ở `svc_listen` đo trên "happy" và lượt 4 "vừng ơi", chưa đo cho lượt 6 (E11-T49) 🔬.
- Xét lại khi có giọng thật nói "trợ lý", đích của KẾ HOẠCH §0.2 dòng 11 và §3.11.
