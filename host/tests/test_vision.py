"""vision joins chunks into the picture the board sent and refuses what is not a chunk."""

from __future__ import annotations

import json
import time
import zlib
from pathlib import Path

import pytest
from PIL import Image

from srhost import mqtt_rx, vision
from srhost.generated import vision as contract

DEVICE = "sr-30eda01e4630"
PICTURE = bytes(range(256)) * 3


def chunk(data: bytes, index: int, count: int, total: int = len(PICTURE), **change: int) -> bytes:
    fields = {
        "magic": contract.CHUNK_MAGIC_U32,
        "version": contract.CHUNK_VERSION,
        "index": index,
        "count": count,
        "flags": 0,
        "boot": 7,
        "seq": 2,
        "total_bytes": total,
        "crc32": zlib.crc32(data),
    }
    fields.update(change)
    return contract.CHUNK_HEADER.pack(*(fields[name] for name in contract.CHUNK_HEADER_FIELDS)) + data


def test_chunks_in_any_order_make_the_picture(tmp_path: Path) -> None:
    assembler = vision.Assembler(tmp_path)
    parts = [PICTURE[:300], PICTURE[300:600], PICTURE[600:]]
    assert assembler.take(DEVICE, chunk(parts[2], 2, 3)) is None
    assert assembler.take(DEVICE, chunk(parts[0], 0, 3)) is None
    path = assembler.take(DEVICE, chunk(parts[1], 1, 3))
    assert path == tmp_path / DEVICE / "B7_000002.jpg"
    assert path.read_bytes() == PICTURE


def test_a_chunk_sent_twice_changes_nothing(tmp_path: Path) -> None:
    assembler = vision.Assembler(tmp_path)
    assert assembler.take(DEVICE, chunk(PICTURE[:300], 0, 2)) is None
    assert assembler.take(DEVICE, chunk(PICTURE[:300], 0, 2)) is None
    assert assembler.take(DEVICE, chunk(PICTURE[300:], 1, 2)).read_bytes() == PICTURE


def test_a_picture_missing_a_chunk_never_lands(tmp_path: Path) -> None:
    assembler = vision.Assembler(tmp_path)
    assert assembler.take(DEVICE, chunk(PICTURE[:300], 0, 2)) is None
    assert not list(tmp_path.rglob("*.jpg"))


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"short", "shorter"),
        (chunk(PICTURE, 0, 1, magic=1), "magic"),
        (chunk(PICTURE, 0, 1, version=99), "version"),
        (chunk(PICTURE, 3, 3), "chunk 3 of 3"),
        (chunk(PICTURE, 0, 1, crc32=1), "crc32"),
    ],
)
def test_what_is_not_a_chunk_is_refused(body: bytes, reason: str) -> None:
    with pytest.raises(vision.ChunkError, match=reason):
        vision.parse_chunk(body)


def test_chunks_that_do_not_add_up_are_refused(tmp_path: Path) -> None:
    assembler = vision.Assembler(tmp_path)
    with pytest.raises(vision.ChunkError, match="header says"):
        assembler.take(DEVICE, chunk(PICTURE, 0, 1, total=len(PICTURE) + 1))


def test_an_image_message_keeps_its_bytes_and_stays_out_of_the_log() -> None:
    message = mqtt_rx.parse(f"sr/{DEVICE}/up/image", b"\xff\xd8 not text", rx_utc="now")
    assert (message.kind, message.device, message.body) == ("image", DEVICE, b"\xff\xd8 not text")
    assert not message.rejected
    assert "body" not in json.loads(message.to_json())


def test_a_capture_command_passes_its_own_schema() -> None:
    command = json.loads(vision.capture_command("DETECT"))
    assert mqtt_rx.schema_error("device_cmd", command) is None


def test_boxes_land_beside_their_picture_and_read_as_one_line(tmp_path: Path) -> None:
    payload = {
        "deviceId": DEVICE,
        "boot": 7,
        "captureSeq": 2,
        "width": 640,
        "height": 360,
        "detectMs": 1840,
        "boxes": [{"name": "cup", "scorePermille": 812, "x1": 1, "y1": 2, "x2": 3, "y2": 4}],
    }
    path = vision.Assembler(tmp_path).keep_boxes(DEVICE, payload)
    assert path == tmp_path / DEVICE / "B7_000002.json"
    assert json.loads(path.read_text(encoding="utf-8")) == payload
    assert vision.describe_boxes(payload) == "B7_000002: 1 found in 1840 ms: cup 0.81"
    assert vision.describe_boxes({**payload, "boxes": []}).endswith("nothing")


def test_the_live_feed_serves_the_newest_picture_and_counts_them() -> None:
    feed = vision.LiveFeed()
    now_s = time.monotonic()
    feed.take(b"first", at_s=now_s - 3.5)
    feed.take(b"second one", at_s=now_s - 3.0)
    assert feed.jpeg() == b"second one"
    state = feed.state()
    assert (state["serial"], state["fps"]) == (2, 2.0)
    assert state["ageS"] >= 3.0


def test_a_preview_frame_reaches_the_page_and_no_file(tmp_path: Path) -> None:
    assembler = vision.Assembler(tmp_path)
    feed = vision.LiveFeed()
    body = chunk(PICTURE, 0, 1, flags=contract.CHUNK_FLAG_PREVIEW)
    vision.on_message(assembler, mqtt_rx.parse(f"sr/{DEVICE}/up/image", body, rx_utc="now"), feed)
    assert feed.jpeg() == PICTURE
    assert not list(tmp_path.rglob("*.jpg"))


def test_a_picture_lands_in_a_file_beside_a_live_page(tmp_path: Path) -> None:
    assembler = vision.Assembler(tmp_path)
    feed = vision.LiveFeed()
    vision.on_message(assembler, mqtt_rx.parse(f"sr/{DEVICE}/up/image", chunk(PICTURE, 0, 1), rx_utc="now"), feed)
    assert (tmp_path / DEVICE / "B7_000002.jpg").read_bytes() == PICTURE
    assert feed.state()["serial"] == 0


def sensors_report(has_fix: bool) -> bytes:
    place = {"latE7": 210285000, "lonE7": 1058542000}
    gps = {"hasFix": has_fix, **place, "satellites": 7, "inView": 9, "antennaOpen": False}
    report = {"deviceId": DEVICE, "boot": 7, "uptimeS": 40, "gps": gps, "ir": {"active": False, "hits": 0}}
    return json.dumps(report).encode()


def test_a_picture_taken_on_a_fix_gets_the_place_beside_it(tmp_path: Path) -> None:
    assembler = vision.Assembler(tmp_path)
    vision.on_message(assembler, mqtt_rx.parse(f"sr/{DEVICE}/up/sensors", sensors_report(True), rx_utc="now"))
    vision.on_message(assembler, mqtt_rx.parse(f"sr/{DEVICE}/up/image", chunk(PICTURE, 0, 1), rx_utc="now"))
    place = json.loads((tmp_path / DEVICE / "B7_000002_place.json").read_text(encoding="utf-8"))
    assert place == {"latE7": 210285000, "lonE7": 1058542000}


def test_a_lost_fix_leaves_the_next_picture_without_a_place(tmp_path: Path) -> None:
    assembler = vision.Assembler(tmp_path)
    for has_fix in (True, False):
        report = mqtt_rx.parse(f"sr/{DEVICE}/up/sensors", sensors_report(has_fix), rx_utc="now")
        vision.on_message(assembler, report)
    vision.on_message(assembler, mqtt_rx.parse(f"sr/{DEVICE}/up/image", chunk(PICTURE, 0, 1), rx_utc="now"))
    assert (tmp_path / DEVICE / "B7_000002.jpg").exists()
    assert not (tmp_path / DEVICE / "B7_000002_place.json").exists()


def picture_turned_a_quarter(path: Path) -> None:
    """A black 400 x 200 file that viewers show as 200 x 400, as board C's are."""
    image = Image.new("RGB", (400, 200))
    exif = image.getexif()
    exif[0x0112] = 6
    image.save(path, exif=exif)


BOXES = {
    "deviceId": DEVICE,
    "boot": 7,
    "captureSeq": 2,
    "width": 100,
    "height": 200,
    "detectMs": 1920,
    "boxes": [{"name": "cup", "scorePermille": 880, "x1": 10, "y1": 20, "x2": 60, "y2": 120}],
}


@pytest.mark.parametrize("boxes_first", [True, False])
def test_the_boxes_land_drawn_on_the_upright_picture_whichever_came_first(tmp_path: Path, boxes_first: bool) -> None:
    assembler = vision.Assembler(tmp_path)
    picture = assembler.folder(DEVICE) / "B7_000002.jpg"
    picture.parent.mkdir(parents=True)
    if boxes_first:
        assembler.keep_boxes(DEVICE, BOXES)
        assert assembler.mark(DEVICE, 7, 2) is None
        picture_turned_a_quarter(picture)
    else:
        picture_turned_a_quarter(picture)
        assert assembler.mark(DEVICE, 7, 2) is None
        assembler.keep_boxes(DEVICE, BOXES)
    drawn = assembler.mark(DEVICE, 7, 2)
    assert drawn == assembler.folder(DEVICE) / "B7_000002_boxes.jpg"
    with Image.open(drawn) as image:
        assert image.size == (200, 400)
        red, green, _ = image.convert("RGB").getpixel((20 + 1, 150))
    assert red > 150 > green


def test_a_detect_that_found_nothing_still_leaves_its_picture(tmp_path: Path) -> None:
    assembler = vision.Assembler(tmp_path)
    picture = assembler.folder(DEVICE) / "B7_000002.jpg"
    picture.parent.mkdir(parents=True)
    picture_turned_a_quarter(picture)
    assembler.keep_boxes(DEVICE, {**BOXES, "boxes": []})
    assert assembler.mark(DEVICE, 7, 2).exists()
