"""Keep what board C's camera sends: join up/image chunks into a JPEG and print up/detections (KEHOACH 3.19).

Images land under the data root, never in the repo: a photo of a room is a person's data (KEHOACH 7.5).
Run: uv run python -m srhost.vision [--device <deviceId>] [--capture PHOTO|DETECT | --live]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
import zlib
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from srhost.config import ConfigError, live_port, mqtt_config, record_config
from srhost.generated import topics, vision
from srhost.mqtt_rx import Message, Receiver

IMAGES_DIR = Path("raw") / "images"
PERMILLE = 1000
CONNECT_TIMEOUT_S = 10.0
FPS_WINDOW_FRAMES = 20
BOXES_SUFFIX = "_boxes"
PLACE_SUFFIX = "_place"
BOX_COLOUR = (255, 0, 0)
BOX_LINE_PX = 4
BOX_LABEL_PX = 36
BOX_JPEG_QUALITY = 90
MS_PER_S = 1000
KEEP_ALIVES_PER_HOLD = 2
LIVE_BIND = "127.0.0.1"  # a photo of a room stays on this machine (KEHOACH 7.5)
LIVE_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Board C live</title><style>
body { margin: 0; background: #111; color: #eee; font: 16px system-ui, sans-serif; text-align: center; }
#bar { padding: 10px; font-size: 26px; } button { font-size: 16px; margin-left: 16px; padding: 4px 12px; }
.lost { background: #b00; }
#view { height: calc(100vh - 66px); display: flex; align-items: center; justify-content: center; overflow: hidden; }
img { max-width: 100vh; max-height: 100vw; }
</style></head><body>
<div id="bar"><span id="now">waiting for a frame</span><button id="turn">Rotate</button></div>
<div id="view"><img id="frame" alt=""></div>
<canvas id="probe" width="160" height="90" hidden></canvas>
<script>
let serial = 0, turn = 90, fps = 0, best = 0;
const frame = document.getElementById("frame"), now = document.getElementById("now");
const probe = document.getElementById("probe").getContext("2d", {willReadFrequently: true});
const show = () => { frame.style.transform = "rotate(" + turn + "deg)"; };
document.getElementById("turn").onclick = () => { turn = (turn + 90) % 360; show(); };
show();
const level = (counts, total, share) => {
  let seen = 0;
  for (let i = 0; i < 256; i++) { seen += counts[i]; if (seen >= total * share) return i; }
  return 255;
};
// Steepest edges over the contrast, on a small copy: noise averages out there, and neither light nor scene size counts.
frame.onload = () => {
  const w = 160, h = 90, steps = new Uint32Array(256), values = new Uint32Array(256);
  probe.drawImage(frame, 0, 0, w, h);
  const d = probe.getImageData(0, 0, w, h).data;
  for (let y = 0; y < h - 1; y++) for (let x = 0; x < w - 1; x++) {
    const i = (y * w + x) * 4 + 1;
    values[d[i]]++;
    steps[Math.max(Math.abs(d[i] - d[i + 4]), Math.abs(d[i] - d[i + w * 4]))]++;
  }
  const n = (w - 1) * (h - 1);
  const sharp = Math.round(100 * level(steps, n, 0.995) / (level(values, n, 0.99) - level(values, n, 0.01) + 1));
  best = Math.max(best, sharp);
  now.textContent = "Sharpness " + sharp + ", best " + best + ", " + fps.toFixed(1) + " frames/s";
};
setInterval(async () => {
  const state = await (await fetch("/state")).json();
  const lost = state.ageS > 2;
  document.getElementById("bar").className = lost ? "lost" : "";
  if (lost) now.textContent = "NO SIGNAL for " + Math.round(state.ageS) + " s: this picture is old";
  if (state.serial === serial) return;
  serial = state.serial;
  fps = state.fps;
  frame.src = "/frame.jpg?n=" + serial;
}, 100);
</script></body></html>
"""

log = logging.getLogger(__name__)


class ChunkError(ValueError):
    """A message on up/image is not a chunk of contracts/vision.yaml; the text says why."""


def parse_chunk(body: bytes) -> tuple[dict, bytes]:
    """The header and the data of one message on up/image."""
    if len(body) < vision.CHUNK_HEADER_BYTES:
        raise ChunkError(f"{len(body)} bytes is shorter than a header")
    header = dict(zip(vision.CHUNK_HEADER_FIELDS, vision.CHUNK_HEADER.unpack_from(body), strict=True))
    data = body[vision.CHUNK_HEADER_BYTES :]
    if header["magic"] != vision.CHUNK_MAGIC_U32:
        raise ChunkError(f"magic 0x{header['magic']:08x}")
    if header["version"] != vision.CHUNK_VERSION:
        raise ChunkError(f"version {header['version']}")
    if header["index"] >= header["count"]:
        raise ChunkError(f"chunk {header['index']} of {header['count']}")
    if zlib.crc32(data) != header["crc32"]:
        raise ChunkError(f"chunk {header['index']} fails its crc32")
    return header, data


@dataclass
class Capture:
    """The chunks of one picture so far."""

    count: int
    total_bytes: int
    parts: dict[int, bytes] = field(default_factory=dict)

    def whole(self) -> bytes | None:
        """The picture once every chunk is in and they add up; None before."""
        if len(self.parts) != self.count:
            return None
        image = b"".join(self.parts[i] for i in range(self.count))
        if len(image) != self.total_bytes:
            raise ChunkError(f"{len(image)} bytes where the header says {self.total_bytes}")
        return image


def stem_of(boot: int, seq: int) -> str:
    return f"B{boot}_{seq:06d}"


def draw_boxes(picture: Path, payload: dict, out: Path) -> None:
    """Write the picture as a viewer shows it, with detect's boxes and names on it; boxes are in the payload's frame."""
    with Image.open(picture) as image:
        upright = ImageOps.exif_transpose(image).convert("RGB")
    scale = upright.width / payload["width"]
    pen = ImageDraw.Draw(upright)
    font = ImageFont.load_default(size=BOX_LABEL_PX)
    for box in payload["boxes"]:
        corners = [box[side] * scale for side in ("x1", "y1", "x2", "y2")]
        pen.rectangle(corners, outline=BOX_COLOUR, width=BOX_LINE_PX)
        label = f"{box['name']} {box['scorePermille'] / PERMILLE:.2f}"
        pen.text((corners[0] + 2 * BOX_LINE_PX, corners[1] + 2 * BOX_LINE_PX), label, fill=BOX_COLOUR, font=font)
    upright.save(out, quality=BOX_JPEG_QUALITY)


class Assembler:
    """Joins chunks per device and picture; a picture whose chunks never all come stays out of the folder."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._open: dict[tuple[str, int, int], Capture] = {}
        self._fixes: dict[str, dict] = {}

    def folder(self, device: str) -> Path:
        return self._root / device

    def join(self, device: str, body: bytes) -> tuple[dict, bytes | None]:
        """Add one message; its header, and the JPEG when this message finished it."""
        header, data = parse_chunk(body)
        key = (device, header["boot"], header["seq"], header["flags"])
        capture = self._open.setdefault(key, Capture(header["count"], header["total_bytes"]))
        capture.parts[header["index"]] = data
        image = capture.whole()
        if image is not None:
            del self._open[key]
        return header, image

    def keep(self, device: str, header: dict, image: bytes) -> Path:
        """Write one whole picture under the data root."""
        path = self.folder(device) / f"{stem_of(header['boot'], header['seq'])}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image)
        if device in self._fixes:
            place = path.with_name(path.stem + PLACE_SUFFIX + ".json")
            place.write_text(json.dumps(self._fixes[device]), encoding="utf-8")
        return path

    def note_place(self, device: str, sensors: dict) -> None:
        """Remember where an up/sensors report puts the board, for the pictures that follow."""
        gps = sensors["gps"]
        if gps["hasFix"]:
            self._fixes[device] = {"latE7": gps["latE7"], "lonE7": gps["lonE7"]}
        else:
            self._fixes.pop(device, None)

    def take(self, device: str, body: bytes) -> Path | None:
        """Add one message; the path of the JPEG when this message finished it."""
        header, image = self.join(device, body)
        return None if image is None else self.keep(device, header, image)

    def mark(self, device: str, boot: int, seq: int) -> Path | None:
        """Draw a capture's boxes on its picture once both are in, whichever came first; the path when it did."""
        stem = self.folder(device) / stem_of(boot, seq)
        picture, boxes = stem.with_suffix(".jpg"), stem.with_suffix(".json")
        if not picture.exists() or not boxes.exists():
            return None
        out = stem.with_name(stem.name + BOXES_SUFFIX + ".jpg")
        draw_boxes(picture, json.loads(boxes.read_text(encoding="utf-8")), out)
        return out

    def keep_boxes(self, device: str, payload: dict) -> Path:
        """Write one up/detections payload beside its picture."""
        path = self.folder(device) / f"{stem_of(payload['boot'], payload['captureSeq'])}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        return path


def describe_boxes(payload: dict) -> str:
    """One line for one up/detections payload."""
    found = ", ".join(f"{b['name']} {b['scorePermille'] / PERMILLE:.2f}" for b in payload["boxes"])
    stem = stem_of(payload["boot"], payload["captureSeq"])
    return f"{stem}: {len(payload['boxes'])} found in {payload['detectMs']} ms: {found or 'nothing'}"


class LiveFeed:
    """The newest preview frame for the live page and how fast they come; none reaches the disk."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jpeg = b""
        self._serial = 0
        self._arrivals: deque[float] = deque(maxlen=FPS_WINDOW_FRAMES)

    def take(self, jpeg: bytes, at_s: float | None = None) -> None:
        with self._lock:
            self._jpeg = jpeg
            self._serial += 1
            self._arrivals.append(time.monotonic() if at_s is None else at_s)

    def jpeg(self) -> bytes:
        with self._lock:
            return self._jpeg

    def state(self) -> dict:
        with self._lock:
            span_s = self._arrivals[-1] - self._arrivals[0] if len(self._arrivals) > 1 else 0.0
            fps = (len(self._arrivals) - 1) / span_s if span_s > 0 else 0.0
            age_s = time.monotonic() - self._arrivals[-1] if self._arrivals else 0.0
            return {"serial": self._serial, "fps": fps, "ageS": age_s}


def live_handler(feed: LiveFeed) -> type[BaseHTTPRequestHandler]:
    """The page, its newest picture and its state, for ThreadingHTTPServer."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            route = self.path.split("?")[0]
            if route == "/frame.jpg":
                self.reply(feed.jpeg(), "image/jpeg")
            elif route == "/state":
                self.reply(json.dumps(feed.state()).encode(), "application/json")
            else:
                self.reply(LIVE_PAGE.encode(), "text/html; charset=utf-8")

        def reply(self, body: bytes, kind: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    return Handler


def run_live(receiver: Receiver, device: str, feed: LiveFeed, port: int) -> None:
    """Serve the page and keep the board's preview alive; it ends by itself when these requests stop."""
    server = ThreadingHTTPServer((LIVE_BIND, port), live_handler(feed))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"open http://localhost:{port}", flush=True)
    cmd = topics.BY_ID["cmd"]
    while receiver.connected.wait(CONNECT_TIMEOUT_S):
        receiver.publish(cmd.build(device), capture_command("PREVIEW"), cmd.qos)
        time.sleep(vision.PREVIEW_HOLD_MS / MS_PER_S / KEEP_ALIVES_PER_HOLD)


def keep(assembler: Assembler, message: Message, feed: LiveFeed | None = None) -> list[str]:
    """Do what one message asks of the disk or the live page; a line for each thing done."""
    if message.rejected or message.device is None:
        return []
    done: list[str] = []
    if message.kind == "image":
        try:
            header, image = assembler.join(message.device, message.body or b"")
        except ChunkError as err:
            return [f"{message.device} image refused: {err}"]
        if image is None:
            return []
        if header["flags"] & vision.CHUNK_FLAG_PREVIEW:
            if feed is not None:
                feed.take(image)
            return []
        path = assembler.keep(message.device, header, image)
        done.append(f"{message.device} image {path.stat().st_size} bytes: {path}")
        marked = assembler.mark(message.device, header["boot"], header["seq"])
    elif message.kind == "sensors":
        assembler.note_place(message.device, message.payload)
        return []
    elif message.kind == "detections":
        assembler.keep_boxes(message.device, message.payload)
        done.append(f"{message.device} {describe_boxes(message.payload)}")
        marked = assembler.mark(message.device, message.payload["boot"], message.payload["captureSeq"])
    else:
        return []
    if marked is not None:
        done.append(f"{message.device} boxes drawn: {marked}")
    return done


def on_message(assembler: Assembler, message: Message, feed: LiveFeed | None = None) -> None:
    for line in keep(assembler, message, feed):
        print(line, flush=True)


def capture_command(mode: str) -> str:
    """The down/cmd payload that takes one picture without a word said."""
    return json.dumps({"op": "CAPTURE", "captureMode": mode})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--device", help="one deviceId; every board otherwise")
    parser.add_argument("--capture", choices=("PHOTO", "DETECT"), help="ask --device for one picture first")
    parser.add_argument("--live", action="store_true", help="the board's preview frames on a local page")
    args = parser.parse_args(argv)
    if (args.capture or args.live) and not args.device:
        parser.error("--capture and --live need --device")
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")
    try:
        cfg = mqtt_config()
        assembler = Assembler(record_config().data_root / IMAGES_DIR)
        port = live_port() if args.live else 0
    except ConfigError as err:
        print(f"vision: {err}", file=sys.stderr)
        return 2
    feed = LiveFeed() if args.live else None
    receiver = Receiver(cfg, lambda message: on_message(assembler, message, feed), args.device)
    receiver.start()
    try:
        if args.capture and receiver.connected.wait(CONNECT_TIMEOUT_S):
            cmd = topics.BY_ID["cmd"]
            receiver.publish(cmd.build(args.device), capture_command(args.capture), cmd.qos)
        if feed is not None:
            run_live(receiver, args.device, feed, port)
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        receiver.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
