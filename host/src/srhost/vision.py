"""Keep what board C's camera sends: join up/image chunks into a JPEG and print up/detections (KEHOACH 3.19).

Images land under the data root, never in the repo: a photo of a room is a person's data (KEHOACH 7.5).
Run: uv run python -m srhost.vision [--device <deviceId>] [--capture PHOTO|DETECT [--once] | --live]
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
from itertools import count
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from srhost.config import ConfigError, MqttConfig, live_port, mqtt_config, record_config
from srhost.generated import topics, vision
from srhost.mqtt_rx import Message, Receiver

IMAGES_DIR = Path("raw") / "images"
PERMILLE = 1000
CONNECT_TIMEOUT_S = 10.0
STATUS_WAIT_S = 2.0  # retained statuses come right after subscribing
ONCE_WAIT_S = 30.0
REFUSED = "image refused"
FPS_WINDOW_FRAMES = 20
BOXES_SUFFIX = "_boxes"
FULL_SUFFIX = "_full"
PLACE_SUFFIX = "_place"
BOX_COLOUR = (255, 0, 0)
BOX_LINE_PX = 4
BOX_LABEL_PX = 36
BOX_JPEG_QUALITY = 90
MS_PER_S = 1000
KEEP_ALIVES_PER_HOLD = 2
PARTS_KEPT_S = 60.0  # past the 30 s esp-mqtt keeps a chunk
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
    opened_s: float = field(default_factory=time.monotonic)

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
    """Write the picture as a viewer shows it, with detect's boxes and names on it; boxes are in the payload's frame.

    The payload's frame spans the picture's height and sits centred across it: the 720p frame is the full picture's
    middle band (KEHOACH 3.19), and on the 720p picture itself the offset is 0.
    """
    with Image.open(picture) as image:
        upright = ImageOps.exif_transpose(image).convert("RGB")
    scale = upright.height / payload["height"]
    offset = (upright.width - payload["width"] * scale) / 2
    pen = ImageDraw.Draw(upright)
    font = ImageFont.load_default(size=BOX_LABEL_PX)
    for box in payload["boxes"]:
        # The board's corners can cross: a side the network gave as negative.
        left, right = sorted((offset + box["x1"] * scale, offset + box["x2"] * scale))
        top, bottom = sorted((box["y1"] * scale, box["y2"] * scale))
        corners = [left, top, right, bottom]
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
        self._drop_stale()
        key = (device, header["boot"], header["seq"], header["flags"])
        capture = self._open.setdefault(key, Capture(header["count"], header["total_bytes"]))
        if (capture.count, capture.total_bytes) != (header["count"], header["total_bytes"]):
            raise ChunkError(f"chunk says {header['count']} chunks, its picture {capture.count}")
        capture.parts[header["index"]] = data
        image = capture.whole()
        if image is not None:
            del self._open[key]
        return header, image

    def _drop_stale(self) -> None:
        now = time.monotonic()
        for key in [k for k, c in self._open.items() if now - c.opened_s > PARTS_KEPT_S]:
            del self._open[key]

    def _make_way(self, path: Path, stem: str, content: bytes) -> None:
        """Set a stem's files aside when other content claims it: a lost NVS hands a board's names out again."""
        if not path.exists() or path.read_bytes() == content:
            return
        older = next(n for n in count(1) if not any(path.parent.glob(f"{stem}_old{n}*")))
        for file in [f for f in path.parent.glob(f"{stem}*") if not f.name.startswith(f"{stem}_old")]:
            file.rename(file.with_name(f"{stem}_old{older}{file.name[len(stem) :]}"))

    def keep(self, device: str, header: dict, image: bytes) -> Path:
        """Write one whole picture under the data root, the full one beside the 720p one under the same stem."""
        stem = stem_of(header["boot"], header["seq"])
        path = self.folder(device) / f"{stem}{suffix_of(header)}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._make_way(path, stem, image)
        path.write_bytes(image)
        if device in self._fixes:
            place = path.with_name(stem + PLACE_SUFFIX + ".json")
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

    def mark(self, device: str, boot: int, seq: int, suffix: str = "") -> Path | None:
        """Draw a capture's boxes on its 720p or, with FULL_SUFFIX, full picture once both are in, whichever came
        first; the path when it did."""
        stem = self.folder(device) / stem_of(boot, seq)
        picture, boxes = stem.with_name(stem.name + suffix + ".jpg"), stem.with_suffix(".json")
        if not picture.exists() or not boxes.exists():
            return None
        out = stem.with_name(stem.name + suffix + BOXES_SUFFIX + ".jpg")
        draw_boxes(picture, json.loads(boxes.read_text(encoding="utf-8")), out)
        return out

    def keep_boxes(self, device: str, payload: dict) -> Path:
        """Write one up/detections payload beside its picture."""
        path = self.folder(device) / f"{stem_of(payload['boot'], payload['captureSeq'])}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=1)
        self._make_way(path, path.stem, text.encode("utf-8"))
        path.write_text(text, encoding="utf-8")
        return path


def suffix_of(header: dict) -> str:
    """The file suffix of the picture a chunk belongs to: FULL_SUFFIX for the full picture, none for the 720p one."""
    return FULL_SUFFIX if header["flags"] & vision.CHUNK_FLAG_FULL else ""


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
    while True:
        if receiver.connected.wait(CONNECT_TIMEOUT_S):
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
            return [f"{message.device} {REFUSED}: {err}"]
        if image is None:
            return []
        if header["flags"] & vision.CHUNK_FLAG_PREVIEW:
            if feed is not None:
                feed.take(image)
            return []
        path = assembler.keep(message.device, header, image)
        kind = "full image" if suffix_of(header) else "image"
        done.append(f"{message.device} {kind} {path.stat().st_size} bytes: {path}")
        marked = [assembler.mark(message.device, header["boot"], header["seq"], suffix_of(header))]
    elif message.kind == "sensors":
        assembler.note_place(message.device, message.payload)
        return []
    elif message.kind == "detections":
        assembler.keep_boxes(message.device, message.payload)
        done.append(f"{message.device} {describe_boxes(message.payload)}")
        boot, seq = message.payload["boot"], message.payload["captureSeq"]
        marked = [assembler.mark(message.device, boot, seq, suffix) for suffix in ("", FULL_SUFFIX)]
    else:
        return []
    done += [f"{message.device} boxes drawn: {path}" for path in marked if path is not None]
    return done


class OneShot:
    """One capture asked from the command line: it waits for the full picture and, in DETECT, the 720p picture and
    the boxes too, or a refusal."""

    def __init__(self, assembler: Assembler, mode: str, device: str | None) -> None:
        self.assembler = assembler
        self.mode = mode
        self.device = device
        self.online: set[str] = set()
        self.done = threading.Event()
        self.problem: str | None = None
        self._got: set[str] = set()

    def handle(self, message: Message) -> None:
        if message.kind == "status" and message.payload is not None:
            (self.online.add if message.payload["state"] == "ONLINE" else self.online.discard)(message.device)
        if self.device is None or message.device != self.device:
            return
        lines = keep(self.assembler, message)
        for line in lines:
            print(line, flush=True)
        self._note(message, lines)

    def _note(self, message: Message, lines: list[str]) -> None:
        payload = message.payload or {}
        if message.kind == "event" and payload.get("kind") == "ERROR":
            self.problem = f"the board answered ERROR {payload.get('code', '?')}"
            self.done.set()
        elif message.kind == "image" and lines and REFUSED not in lines[0]:
            self._got.add("full image" if suffix_of(parse_chunk(message.body or b"")[0]) else "image")
        elif message.kind == "detections" and not message.rejected:
            self._got.add("detections")
        if self._got >= self.needed():
            self.done.set()

    def needed(self) -> set[str]:
        return {"image", "detections", "full image"} if self.mode == "DETECT" else {"full image"}

    def missing(self) -> str:
        return " and ".join(sorted(self.needed() - self._got)) or "nothing"


def sole_board(online: set[str]) -> str:
    """The one board that is online; ValueError naming the others when there are none or several."""
    if len(online) == 1:
        return next(iter(online))
    raise ValueError(f"{len(online)} boards online ({', '.join(sorted(online)) or 'none'}): pass DEVICE=<deviceId>")


def run_once(cfg: MqttConfig, assembler: Assembler, mode: str, device: str | None) -> int:
    """Ask a board for one capture and wait for all of it; 0 when it came, 1 and the reason when it did not."""
    shot = OneShot(assembler, mode, device)
    receiver = Receiver(cfg, shot.handle, device)
    receiver.start()
    try:
        if not receiver.connected.wait(CONNECT_TIMEOUT_S):
            print(f"no broker at {cfg.host}:{cfg.port} in {CONNECT_TIMEOUT_S:.0f} s: make broker-up")
            return 1
        if device is None:
            time.sleep(STATUS_WAIT_S)
            try:
                shot.device = sole_board(set(shot.online))
            except ValueError as err:
                print(err)
                return 1
        cmd = topics.BY_ID["cmd"]
        started_s = time.monotonic()
        receiver.publish(cmd.build(shot.device), capture_command(mode), cmd.qos)
        shot.done.wait(ONCE_WAIT_S)
    finally:
        receiver.stop()
    if shot.problem:
        print(shot.problem)
        return 1
    if shot.missing() != "nothing":
        print(
            f"{shot.device}: no {shot.missing()} in {ONCE_WAIT_S:.0f} s: is it online, and busy with another capture?"
        )
        return 1
    print(f"{shot.device}: {mode} came back in {time.monotonic() - started_s:.1f} s")
    return 0


def on_message(assembler: Assembler, message: Message, feed: LiveFeed | None = None) -> None:
    for line in keep(assembler, message, feed):
        print(line, flush=True)


def capture_command(mode: str) -> str:
    """The down/cmd payload that takes one picture without a word said."""
    return json.dumps({"op": "CAPTURE", "captureMode": mode})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--device", help="one deviceId; every board otherwise, or the one online with --once")
    parser.add_argument("--capture", choices=("PHOTO", "DETECT"), help="ask --device for one picture first")
    parser.add_argument("--live", action="store_true", help="the board's preview frames on a local page")
    parser.add_argument("--once", action="store_true", help="with --capture: wait for what it brings, then exit")
    args = parser.parse_args(argv)
    if args.once and not args.capture:
        parser.error("--once needs --capture")
    if (args.capture or args.live) and not args.device and not args.once:
        parser.error("--capture and --live need --device")
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")
    try:
        cfg = mqtt_config()
        assembler = Assembler(record_config().data_root / IMAGES_DIR)
        port = live_port() if args.live else 0
    except ConfigError as err:
        print(f"vision: {err}", file=sys.stderr)
        return 2
    if args.once:
        return run_once(cfg, assembler, args.capture, args.device)
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
