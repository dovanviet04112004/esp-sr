"""Float32 mirror of ai_engine/src/ns_ulunas/ (KEHOACH 3.9, ADR-0048): one hop of UL-UNAS's streaming net on the ns
slot's complex bins, with gtcrn_mirror's fused multiply-adds, own exp, reciprocal and square root, its own log10, and
every sum in the order the C takes, so the C matches it bit for bit. The weights are the pinned checkpoint's, batch
norms folded into the convolutions in float64, the ERB banks kept sparse; the architecture is the upstream default."""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field
from pathlib import Path

import numba
import numpy as np

from srpipe.dsp.spec.window import sqrt_hann
from srpipe.generated import grid
from srpipe.golden.gold import write_gold
from srpipe.tasks.ns import gtcrn_mirror as gm
from srpipe.tasks.ns.gtcrn_mirror import fma32, recip32, sigmoid32, sqrt32

BINS, LOW, ERB_BINS = 257, 65, 129
TYPES = (0, 2, 1, 2, 1)  # 0 XConvBlock, 1 XDWSBlock, 2 XMBBlocks, encoder order
STRIDES = (2, 2, 1, 1, 1)
GROUPS = (1, 2, 2, 2, 2)
CHANNELS = (12, 24, 24, 32, 16)
KERNELS = ((3, 3), (2, 3), (2, 3), (1, 5), (1, 5))  # (time, frequency)
WIDTHS = (65, 33, 33, 33, 33)
FA_CHUNK = 4  # frequency bins a step of the attention's frequency GRU
MAG_FLOOR = np.float32(1e-12)
SQRT_HALF = np.float32(math.sqrt(0.5))
LOG10_2_HI = np.float32(math.log10(2.0))
LOG10_2_LO = np.float32(math.log10(2.0) - float(LOG10_2_HI))
LOG10_E2 = np.float32(2.0 / math.log(10.0))  # log10(m) = LOG10_E2 atanh(s)
ATANH_POLY = tuple(np.float32(1.0 / k) for k in (9, 7, 5, 3, 1))  # atanh(s) / s in powers of s^2
GOLDEN_ROOT = gm.GOLDEN_ROOT
BLOCK = "ns_ulunas"
GOLDEN_SEED, GOLDEN_HOPS = 20261126, 40
BLOB_MAGIC, BLOB_VERSION = 0x31554C55, 1  # "ULU1", as ns_ulunas/ulunas.c reads it


def window_scale() -> np.float32:
    """Hann's sum over the grid window's: a tone in the slot's sqrt-Hann bins at the level the Hann bins UL-UNAS
    learnt on give it."""
    w = sqrt_hann(grid.FFT_SIZE).astype(np.float64)
    return np.float32((w * w).sum() / w.sum())


@numba.njit(cache=True)
def log10_32(v):
    """log10 of a normal v > 0: v = m 2^e with m in [sqrt(1/2), sqrt(2)), log10(m) as LOG10_E2 atanh(s) for
    s = (m - 1) / (m + 1) by recip32 and its odd series to s^9, e log10(2) in two parts."""
    m64, e = math.frexp(np.float64(v))
    m = np.float32(m64)
    if m < SQRT_HALF:
        m = m * np.float32(2.0)
        e -= 1
    s = (m - np.float32(1.0)) * recip32(m + np.float32(1.0))
    s2 = s * s
    p = ATANH_POLY[0]
    for k in range(1, len(ATANH_POLY)):
        p = fma32(p, s2, ATANH_POLY[k])
    ef = np.float32(e)
    return fma32(ef, LOG10_2_HI, fma32(ef, LOG10_2_LO, (s * p) * LOG10_E2))


@numba.njit(cache=True)
def features(re, im, scale, out):
    """out[i] = log10 of bin i's magnitude, the bin scaled first, the magnitude at least MAG_FLOOR."""
    for i in range(re.size):
        r, j = re[i] * scale, im[i] * scale
        out[i] = log10_32(max(sqrt32(fma32(r, r, j * j)), MAG_FLOOR))


@numba.njit(cache=True)
def affine_prelu(x, a, b, slope):
    """x[c][f] = (a[c][f] x + b[c][f]) + PReLU(x) of slope[c], in place."""
    for c in range(x.shape[0]):
        for f in range(x.shape[1]):
            v = x[c, f]
            x[c, f] = fma32(a[c, f], v, b[c, f]) + (v if v > 0.0 else slope[c] * v)


@numba.njit(cache=True)
def deconv_t(span, w, b, stride, pad, groups, fout, out):
    """The last frame of a causal ConvTranspose2d of kernel (kt, kf), stride (1, stride), over span, its kt frames
    oldest first: out[co][fo] = b[co] + x[ci][fi] w[ci][co'][kt-1-j][k] for span frame j, over the group's ci
    ascending, then kernel time index ascending, then k ascending, each fi = (fo + pad - k) / stride whole and in
    range."""
    cin, per_out, kt, kf = w.shape
    fin = span.shape[2]
    per_in = cin // groups
    for co in range(per_out * groups):
        g, local = co // per_out, co % per_out
        for fo in range(fout):
            acc = b[co]
            for ci in range(g * per_in, (g + 1) * per_in):
                for t in range(kt):
                    row = span[ci, kt - 1 - t]
                    for k in range(kf):
                        num = fo + pad - k
                        if num >= 0 and num % stride == 0 and num // stride < fin:
                            acc = fma32(row[num // stride], w[ci, local, t, k], acc)
            out[co, fo] = acc


@numba.njit(cache=True)
def channel_mean_square(x, out):
    """out[f] = the sum over channels, ascending, of x[c][f]^2, over the channel count."""
    ch, width = x.shape
    for f in range(width):
        a = np.float32(0.0)
        for c in range(ch):
            a = fma32(x[c, f], x[c, f], a)
        out[f] = a / np.float32(ch)


@numba.njit(cache=True)
def attend(x, at, af):
    """x[c][f] = (sigmoid(at[c]) x[c][f]) sigmoid(af[f]), in place."""
    for c in range(x.shape[0]):
        sc = sigmoid32(at[c])
        for f in range(x.shape[1]):
            x[c, f] = (sc * x[c, f]) * sigmoid32(af[f])


@dataclass
class Layer:
    """A convolution or transposed one with its batch norm folded in, and its AffinePReLU (a, b, slope) or None."""

    w: np.ndarray
    b: np.ndarray
    act: tuple | None


@dataclass
class Tfa:
    """cTFA: the time GRU of 2C and its dense to C, the frequency GRU both ways of FA_CHUNK and its dense."""

    gru: tuple
    fc: tuple
    fa: tuple  # (forward, backward) GRU weights
    fa_fc: tuple


@dataclass
class Unit:
    """One encoder or decoder block: kind of TYPES; for XConvBlock conv alone, for XDWSBlock pconv then dconv, for
    XMBBlocks pconv, dconv and pconv2; kt, kf, stride and groups of its own row of the config."""

    kind: int
    layers: list
    tfa: Tfa
    kt: int
    kf: int
    stride: int
    groups: int
    deconv: bool
    last: bool


@dataclass
class Weights:
    erb: tuple
    ierb: tuple
    scale: np.float32
    enc: list
    dps: list
    dec: list


def _dconv_in(i: int) -> int:
    """Width of decoder row i's depthwise input: its output width, halved and one more for a stride of 2."""
    width = WIDTHS[i - 1] if i > 0 else ERB_BINS
    return width // 2 + 1 if STRIDES[i] == 2 else width


@dataclass
class State:
    caches: list = field(default_factory=lambda: [None] * 10)  # each block's past kt - 1 frames, zero at its first hop
    tfa: list = field(default_factory=lambda: [np.zeros(2 * c, np.float32) for c in (*CHANNELS, *CHANNELS[-2::-1], 1)])
    inter: list = field(default_factory=lambda: [np.zeros((gm.F2, gm.HIDDEN), np.float32) for _ in range(2)])


def _point(layer: Layer, x: np.ndarray, groups: int) -> np.ndarray:
    out = np.empty((layer.w.shape[0], x.shape[1]), np.float32)
    gm.conv(x[:, None], layer.w, layer.b, np.float32(0.0), 0, 1, 0, 1, groups, x.shape[1], out)
    if layer.act is not None:
        affine_prelu(out, *layer.act)
    return out


def _shuffle(x: np.ndarray) -> np.ndarray:
    half = x.shape[0] // 2
    out = np.empty_like(x)
    out[0::2], out[1::2] = x[:half], x[half:]
    return out


def _temporal(u: Unit, layer: Layer, x: np.ndarray, cache: np.ndarray | None, groups: int) -> np.ndarray:
    """layer over this hop's x and the block's cache, its cache moved on a frame; the last frame of the causal
    convolution or of the transposed one."""
    span = x[:, None] if cache is None else np.concatenate([cache, x[:, None]], axis=1)
    pad = u.kf // 2
    if u.deconv:
        fout = (x.shape[1] - 1) * u.stride - 2 * pad + u.kf
        out = np.empty((layer.w.shape[1] * groups, fout), np.float32)
        deconv_t(span, layer.w, layer.b, u.stride, pad, groups, fout, out)
    else:
        fout = (x.shape[1] + 2 * pad - u.kf) // u.stride + 1
        out = np.empty((layer.w.shape[0], fout), np.float32)
        gm.conv(span, layer.w, layer.b, np.float32(0.0), 0, u.stride, pad, 1, groups, fout, out)
    if cache is not None:
        cache[:] = span[:, 1:]
    if layer.act is not None:
        affine_prelu(out, *layer.act)
    return out


def run_tfa(t: Tfa, x: np.ndarray, h: np.ndarray) -> np.ndarray:
    """cTFA over x in place: the time GRU on each channel's mean square moves h a hop, FA's GRU runs both ways over
    the padded frequency chunks of the channels' mean square."""
    ch, width = x.shape
    zt = np.empty(ch, np.float32)
    for c in range(ch):
        zt[c] = gm._mean_square(x[c])
    new = np.empty(h.size, np.float32)
    gm.gru_step(*t.gru, zt, h, new)
    h[:] = new
    at = np.empty(ch, np.float32)
    gm.dense(*t.fc, h, at)
    chunks = -(-width // FA_CHUNK)
    zf = np.zeros(chunks * FA_CHUNK, np.float32)
    channel_mean_square(x, zf[:width])
    both = np.empty((chunks, 2 * FA_CHUNK), np.float32)
    for direction, order in ((0, range(chunks)), (1, range(chunks - 1, -1, -1))):
        state = np.zeros(FA_CHUNK, np.float32)
        for p in order:
            step_h = np.empty(FA_CHUNK, np.float32)
            gm.gru_step(*t.fa[direction], zf[p * FA_CHUNK : (p + 1) * FA_CHUNK], state, step_h)
            state = step_h
            both[p, direction * FA_CHUNK : (direction + 1) * FA_CHUNK] = state
    af = np.empty((chunks, FA_CHUNK), np.float32)
    for p in range(chunks):
        gm.dense(*t.fa_fc, both[p], af[p])
    attend(x, at, af.reshape(-1)[:width].copy())
    return x


def _cache(st: State, at: int, u: Unit, x: np.ndarray) -> np.ndarray | None:
    if u.kt > 1 and st.caches[at] is None:
        st.caches[at] = np.zeros((x.shape[0], u.kt - 1, x.shape[1]), np.float32)
    return st.caches[at]


def run_unit(u: Unit, x: np.ndarray, st: State, at: int) -> np.ndarray:
    """Block at of the stream, encoder blocks first, on this hop's x."""
    h = st.tfa[at]
    if u.kind == 0:
        y = _temporal(u, u.layers[0], x, _cache(st, at, u, x), u.groups)
        return run_tfa(u.tfa, y, h)
    y = _point(u.layers[0], x, u.groups)
    if u.groups == 2:
        y = _shuffle(y)
    y = _temporal(u, u.layers[1], y, _cache(st, at, u, y), y.shape[0])
    if u.kind == 1:
        return run_tfa(u.tfa, y, h)
    y = run_tfa(u.tfa, _point(u.layers[2], y, u.groups), h)
    return _shuffle(y) if u.groups == 2 and not u.last else y


def step(w: Weights, st: State, bins: np.ndarray) -> np.ndarray:
    """The slot's gains for one hop of complex bins: UL-UNAS's real mask, at most 1."""
    feat = np.empty(BINS, np.float32)
    features(bins.real.astype(np.float32), bins.imag.astype(np.float32), w.scale, feat)
    x = np.empty((1, ERB_BINS), np.float32)
    gm.banded(feat[None], *w.erb, LOW, x)
    skips = []
    for i, u in enumerate(w.enc):
        x = run_unit(u, x, st, i)
        skips.append(x)
    for k in range(2):
        x = gm.run_dual(w.dps[k], x, st.inter[k])
    for i, u in enumerate(w.dec):
        x = run_unit(u, x + skips[4 - i], st, 5 + i)
    m = np.empty((1, ERB_BINS), np.float32)
    for f in range(ERB_BINS):
        m[0, f] = sigmoid32(x[0, f])
    mask = np.empty((1, BINS), np.float32)
    gm.banded(m, *w.ierb, LOW, mask)
    return np.minimum(mask[0], np.float32(1.0))


def _aprelu(sd: dict, name: str) -> tuple:
    return tuple(
        np.ascontiguousarray(sd[f"{name}.{k}"].astype(np.float32))
        for k in ("affine_weight", "affine_bias", "slope_weight")
    )


def _linear(sd: dict, name: str) -> tuple:
    return tuple(np.ascontiguousarray(sd[f"{name}.{k}"].astype(np.float32)) for k in ("weight", "bias"))


def _tfa(sd: dict, p: str) -> Tfa:
    fa = tuple(gm._gru(sd, f"{p}.fa.gru", s) for s in ("", "_reverse"))
    return Tfa(gm._gru(sd, f"{p}.ta_gru"), _linear(sd, f"{p}.ta_fc"), fa, _linear(sd, f"{p}.fa.fc"))


def _layer(sd: dict, conv: str, bn: str, act: str | None, transposed: bool) -> Layer:
    w, b = gm._folded(sd, conv, bn, transposed)
    return Layer(np.ascontiguousarray(w), b, None if act is None else _aprelu(sd, act))


def _unit(sd: dict, p: str, row: int, deconv: bool, last: bool) -> Unit:
    kind, (kt, kf) = TYPES[row], KERNELS[row]
    if kind == 0:
        layers = [_layer(sd, f"{p}.ops.1", f"{p}.ops.2", None if last else f"{p}.ops.3", deconv)]
        tfa = _tfa(sd, f"{p}.ops.4")
    elif kind == 1:
        layers = [
            _layer(sd, f"{p}.pconv.0", f"{p}.pconv.1", f"{p}.pconv.2", False),
            _layer(sd, f"{p}.dconv.1", f"{p}.dconv.2", f"{p}.dconv.3", deconv),
        ]
        tfa = _tfa(sd, f"{p}.dconv.4")
    else:
        layers = [
            _layer(sd, f"{p}.pconv1.0", f"{p}.pconv1.1", f"{p}.pconv1.2", False),
            _layer(sd, f"{p}.dconv.1", f"{p}.dconv.2", f"{p}.dconv.3", deconv),
            _layer(sd, f"{p}.pconv2.0", f"{p}.pconv2.1", None, False),
        ]
        tfa = _tfa(sd, f"{p}.pconv2.2")
    return Unit(kind, layers, tfa, kt, kf, STRIDES[row], GROUPS[row], deconv, last)


def weights(state_dict: dict) -> Weights:
    """The mirror's weights from a StreamULUNAS state dict of numpy arrays."""
    sd = state_dict
    enc = [_unit(sd, f"encoder.en_convs.{i}", i, False, False) for i in range(5)]
    dec = [_unit(sd, f"decoder.de_convs.{k}", 4 - k, True, k == 4) for k in range(5)]
    return Weights(
        gm._sparse(sd["erb.erb_fc.weight"].astype(np.float32)),
        gm._sparse(sd["erb.ierb_fc.weight"].astype(np.float32)),
        window_scale(),
        enc,
        [gm._dual(sd, f"dpgrnn.{k}") for k in range(2)],
        dec,
    )


def _unit_arrays(u: Unit) -> list:
    out = []
    for layer in u.layers:
        out += [layer.w, layer.b, *(layer.act or ())]
    t = u.tfa
    return [*out, *t.gru, *t.fc, *t.fa[0], *t.fa[1], *t.fa_fc]


def pack(w: Weights) -> bytes:
    """w as the blob ns_ulunas reads: magic, version and both banks' tap counts, then every array in the order of
    ulunas.c's take_*, int32 for the banks' pointers and indices, float32 for the rest."""
    arrays = [*w.erb, *w.ierb, [w.scale]]
    arrays += [a for u in w.enc for a in _unit_arrays(u)]
    arrays += [a for d in w.dps for a in gm._dual_arrays(d)]
    arrays += [a for u in w.dec for a in _unit_arrays(u)]
    head = np.array([BLOB_MAGIC, BLOB_VERSION, w.erb[1].size, w.ierb[1].size], np.uint32).tobytes()
    body = (np.asarray(a, np.int32 if np.asarray(a).dtype.kind == "i" else np.float32).tobytes() for a in arrays)
    return head + b"".join(body)


def random_weights(rng: np.random.Generator) -> Weights:
    """Weights of UL-UNAS's shapes drawn from rng, each layer scaled by its fan-in, banks of two or three taps a band:
    test data for the golden, not a net."""

    def arr(shape, fan_in=1):
        return (rng.standard_normal(shape) / np.sqrt(fan_in)).astype(np.float32)

    def gru(inp, hid):
        return (arr((3 * hid, inp), inp), arr((3 * hid, hid), hid), arr(3 * hid, 10), arr(3 * hid, 10))

    def bank(rows, cols):
        dense_rows = np.zeros((rows, cols), np.float32)
        for r in range(rows):
            at, taps = int(rng.integers(0, cols - 3)), int(rng.integers(2, 4))
            dense_rows[r, at : at + taps] = rng.uniform(0.1, 1.0, taps)
        return gm._sparse(dense_rows)

    def act(ch, width):
        return (np.float32(1.0) + arr((ch, width), 25), arr((ch, width), 25), (0.25 + 0.1 * arr(ch)).astype(np.float32))

    def tfa(ch):
        return Tfa(
            gru(ch, 2 * ch),
            (arr((ch, 2 * ch), 2 * ch), arr(ch, 10)),
            (gru(4, 4), gru(4, 4)),
            (arr((4, 8), 8), arr(4, 10)),
        )

    def unit(row, deconv, last):
        kind, (kt, kf), g = TYPES[row], KERNELS[row], GROUPS[row]
        cin = 1 if row == 0 else CHANNELS[row - 1]
        cout = CHANNELS[row]
        width_out = WIDTHS[row]
        if deconv:
            cin, cout = cout, (1 if row == 0 else CHANNELS[row - 1])
            width_out = ERB_BINS if row == 0 else WIDTHS[row - 1]
        width_in = _dconv_in(row) if deconv else (WIDTHS[row] * 2 - 1 if STRIDES[row] == 2 else WIDTHS[row])
        if row == 0:
            width_in = WIDTHS[0] if deconv else ERB_BINS

        def temporal(ch_in, ch_out, groups):
            per = ch_in // groups
            if deconv:
                return Layer(arr((ch_in, ch_out // groups, kt, kf), per * kt * kf), arr(ch_out, 10), None)
            return Layer(arr((ch_out, per, kt, kf), per * kt * kf), arr(ch_out, 10), None)

        if kind == 0:
            layer = temporal(cin, cout, 1)
            layer.act = None if last else act(cout, width_out)
            return Unit(kind, [layer], tfa(cout), kt, kf, STRIDES[row], g, deconv, last)
        p1 = Layer(arr((cout, cin // g, 1, 1), cin // g), arr(cout, 10), act(cout, width_in))
        d = temporal(cout, cout, cout)
        d.act = act(cout, width_out)
        if kind == 1:
            return Unit(kind, [p1, d], tfa(cout), kt, kf, STRIDES[row], g, deconv, last)
        p2 = Layer(arr((cout, cout // g, 1, 1), cout // g), arr(cout, 10), None)
        return Unit(kind, [p1, d, p2], tfa(cout), kt, kf, STRIDES[row], g, deconv, last)

    def dual():
        ch, f2, half = gm.CH, gm.F2, gm.HALF
        return gm.DualPath(
            tuple(tuple(gru(half, half // 2) for _ in range(2)) for _ in range(2)),
            (arr((ch, ch), 16), arr(ch, 10)),
            (np.float32(1.0) + arr(f2 * ch, 100), arr(f2 * ch, 100)),
            tuple(gru(half, half) for _ in range(2)),
            (arr((ch, ch), 16), arr(ch, 10)),
            (np.float32(1.0) + arr(f2 * ch, 100), arr(f2 * ch, 100)),
        )

    return Weights(
        bank(ERB_BINS - LOW, BINS - LOW),
        bank(BINS - LOW, ERB_BINS - LOW),
        window_scale(),
        [unit(i, False, False) for i in range(5)],
        [dual(), dual()],
        [unit(4 - k, True, k == 4) for k in range(5)],
    )


def case(w: Weights, bins: np.ndarray, off: float = 0.0) -> dict[str, np.ndarray]:
    """A golden case: the packed weights, every hop's bins as re, im pairs, and the mirror's gains from fresh state;
    off is added to the first encoder bias before the gains are taken, for a negative control."""
    blob = np.frombuffer(pack(w), np.uint8).copy()
    if off:
        w.enc[0].layers[0].b[0] += np.float32(off)
    st = State()
    gains = np.stack([step(w, st, b) for b in bins])
    pairs = np.stack([bins.real, bins.imag], axis=-1).reshape(len(bins), -1).astype(np.float32)
    return {"weights": blob, "bins": pairs, "gain": gains.astype(np.float32)}


def emit(root: Path) -> list[Path]:
    """Two streams of random bins through random weights, one from board-like levels with silent hops between, then
    a negative control whose gains come from weights one bias off those stored."""
    rng = np.random.default_rng(GOLDEN_SEED)

    def stream(level_db):
        scale = 10.0 ** (rng.uniform(*level_db, (GOLDEN_HOPS, 1)) / 20.0)
        bins = (rng.standard_normal((GOLDEN_HOPS, BINS)) + 1j * rng.standard_normal((GOLDEN_HOPS, BINS))) * scale
        return bins.astype(np.complex64)

    quiet = stream((-60.0, -20.0))
    quiet[5:8] = 0.0
    cases = {
        "case_000": case(random_weights(rng), stream((-40.0, 20.0))),
        "case_001": case(random_weights(rng), quiet),
        "case_neg_000": case(random_weights(rng), stream((-40.0, 20.0)), off=1e-3),
    }
    written = []
    for name, tensors in cases.items():
        path = root / BLOCK / f"{name}.gold"
        write_gold(path, tensors)
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=GOLDEN_ROOT)
    for path in emit(parser.parse_args().out):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
