# ml/data/

Bố cục đầy đủ và lý do ở KẾ HOẠCH §4.4.1. Tóm tắt để làm việc hằng ngày:

| Ở đâu | Chứa | Git |
|---|---|---|
| `ml/data/manifests/` | một file cho một kho: nguồn, ngày tải, giấy phép, sha256, số giờ, nhánh dùng; `device/board_b.csv` mỗi phiên thu một dòng | ✅ |
| `ml/data/splits/<nhánh>/vN/` | file split TSV `item  spk  room  origin` + `SPLIT.md` (luật, seed, lệnh sinh, sha256) | ✅ |
| `$SRPIPE_DATA_ROOT/raw/` | **chỉ đọc**: `speech/`, `noise/`, `rir/` đúng như lúc tải; `device/board_b/<phiên>/` từ `host/session.py` | ❌ |
| `$SRPIPE_DATA_ROOT/interim/` | `scenes/` và `{ns, wake, command, synth}/`: cắt, lấy mẫu lại 16 kHz, trộn, TTS; sinh lại được từ `raw/` | ❌ |
| `$SRPIPE_DATA_ROOT/processed/` | `{ns, wake, command, synth}/`: đặc trưng, shard sẵn sàng nạp | ❌ |
| `$SRPIPE_DATA_ROOT/cache/` | xoá lúc nào cũng được | ❌ |

Dữ liệu nặng đặt ở ổ khác vì ổ C: gần đầy:

```bash
mkdir -p /mnt/e/esp-sr-data/{raw/{speech,noise,rir,device/board_b},interim,processed,cache}
echo "SRPIPE_DATA_ROOT=/mnt/e/esp-sr-data" >> ml/.env
```

Một phiên thu qua board có `kind` là `wake`, `cmd`, `neg`, `noise` hoặc `probe`; `probe` là bản thu thử đường
thu và **không vào split nào**. Thu tiếng người cần phiếu đồng ý trước, kể cả thu thử (KẾ HOẠCH §1.4); bảng tên
thật ↔ `spk_NNN` nằm ngoài repo và ngoài `SRPIPE_DATA_ROOT`.

Mọi split đã commit phải qua `uv run pytest tests/test_splits.py` (luật KẾ HOẠCH §1.3). Nguồn nào đã thật sự
nằm trên đĩa, số giờ và giấy phép thì tóm lại ở `docs/DU_LIEU.md`.
