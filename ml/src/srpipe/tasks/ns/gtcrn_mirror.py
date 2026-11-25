"""Float32 mirror of ai_engine/src/ns_gtcrn/ (KEHOACH 3.9, ADR-0047): one hop of GTCRN's streaming net on the ns
slot's complex bins, every multiply-add fused and rounded once as madd.s and fmaf give it, every sum in the order
the C takes, its own exp, reciprocal and square root, so the C matches it bit for bit. The weights are the pinned
checkpoint's, batch norms folded into the convolutions in float64, the ERB filter banks kept sparse."""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field
from pathlib import Path

import numba
import numpy as np

from srpipe.core.config import ML_ROOT
from srpipe.golden.gold import write_gold

BINS, LOW, ERB_BINS, F1, F2 = 257, 65, 129, 65, 33  # slot bins, unbanded bins, ERB bins, en0's and en1's widths
CH, HALF, HIDDEN = 16, 8, 16
DILATIONS = (1, 2, 5)
MAG_EPS = np.float32(1e-12)
LN_EPS = np.float32(1e-8)
EXP_LIMIT = np.float32(80.0)  # e^±80 stays normal: no subnormal on any chip
LOG2E, LN2_HI, LN2_LO = np.float32(1.442695041), np.float32(0.693145752), np.float32(1.42860677e-06)
EXP_POLY = tuple(np.float32(1.0 / math.factorial(k)) for k in range(6, -1, -1))
NEWTON_STEPS = 3  # from a linear first guess: float32 converged
RECIP_C0, RECIP_C1 = np.float32(48.0 / 17.0), np.float32(32.0 / 17.0)  # 1/m ~ c0 - c1 m on [0.5, 1)
RSQRT_LOW = (np.float32(1.8284271), np.float32(0.8284271))  # 1/sqrt(m) ~ c0 - c1 m on [0.5, 1)
RSQRT_HIGH = (np.float32(1.2928932), np.float32(0.2928932))  # and on [1, 2)
TINY_SQUARE = np.float32(2.0**-100)  # a square under this has a root of 0 for the gains
BLOB_MAGIC, BLOB_VERSION = 0x31435447, 1  # "GTC1", as ns_gtcrn/gtcrn.c reads it
GOLDEN_ROOT = ML_ROOT.parent / "contracts" / "golden"
BLOCK = "ns_gtcrn"
GOLDEN_SEED, GOLDEN_HOPS = 20261125, 40


@numba.njit(cache=True, inline="always")
def fma32(a, b, c):
    """a * b + c rounded once to float32: the exact product summed in float64 and rounded to odd there."""
    p = np.float64(a) * np.float64(b)
    q = np.float64(c)
    t = p + q
    back = t - p
    err = (p - (t - back)) + (q - back)
    if err != 0.0:
        m, _ = math.frexp(t)
        if (np.int64(m * 9007199254740992.0) & 1) == 0:
            t = np.nextafter(t, math.inf if err > 0.0 else -math.inf)
    return np.float32(t)


@numba.njit(cache=True)
def exp32(x):
    x = min(max(np.float32(x), -EXP_LIMIT), EXP_LIMIT)
    n = np.float32(math.floor(x * LOG2E + np.float32(0.5)))
    r = fma32(-n, LN2_LO, fma32(-n, LN2_HI, x))
    p = EXP_POLY[0]
    for k in range(1, len(EXP_POLY)):
        p = fma32(p, r, EXP_POLY[k])
    return np.float32(p * np.float32(2.0 ** int(n)))


@numba.njit(cache=True)
def recip32(d):
    """1 / d for a normal d > 0 without a division: d's mantissa from frexp, a linear first guess, NEWTON_STEPS fused
    Newton steps, and the exponent put back by a power of two."""
    m64, e = math.frexp(np.float64(d))
    m = np.float32(m64)
    y = fma32(-RECIP_C1, m, RECIP_C0)
    for _ in range(NEWTON_STEPS):
        y = fma32(y, fma32(-m, y, np.float32(1.0)), y)
    return np.float32(y * np.float32(2.0 ** (-e)))


@numba.njit(cache=True)
def rsqrt32(v):
    """1 / sqrt(v) for a normal v > 0 without a division: the mantissa in [0.5, 2) with an even exponent, a linear first
    guess on its half of that range, NEWTON_STEPS fused Newton steps, and half the exponent put back by a power of
    two."""
    m64, e = math.frexp(np.float64(v))
    m = np.float32(m64)
    if e % 2 != 0:
        m = m * np.float32(2.0)
        e -= 1
    c0, c1 = RSQRT_LOW if m < np.float32(1.0) else RSQRT_HIGH
    y = fma32(-c1, m, c0)
    h = np.float32(0.5) * m
    for _ in range(NEWTON_STEPS):
        y = y * fma32(-h, y * y, np.float32(1.5))
    return np.float32(y * np.float32(2.0 ** (-(e // 2))))


@numba.njit(cache=True)
def sqrt32(v):
    """v rsqrt32(v); 0 for v at or under TINY_SQUARE."""
    if v <= TINY_SQUARE:
        return np.float32(0.0)
    return np.float32(v * rsqrt32(v))


@numba.njit(cache=True, inline="always")
def sigmoid32(v):
    return recip32(np.float32(1.0) + exp32(-v))


@numba.njit(cache=True, inline="always")
def tanh32(v):
    return np.float32(1.0) - np.float32(2.0) * recip32(exp32(np.float32(2.0) * v) + np.float32(1.0))


@numba.njit(cache=True)
def conv(x, w, b, slope, act, stride, pad, dil, groups, fout, out):
    """out[co][fo] = act(b[co] + every tap, in (ci, t, k) order over the group's channels, of
    x[ci][t*dil][fo*stride-pad+k]), taps over the zero padding included; x is (cin, span, fin), w is
    (cout, cin/groups, kt, kf); act 1 is PReLU of slope, 2 tanh, 0 none."""
    cout, per_in, kt, kf = w.shape
    fin = x.shape[2]
    per_out = cout // groups
    for co in range(cout):
        g = co // per_out
        for fo in range(fout):
            acc = b[co]
            for ci in range(per_in):
                for t in range(kt):
                    row = x[g * per_in + ci, t * dil]
                    for k in range(kf):
                        f = fo * stride - pad + k
                        v = row[f] if 0 <= f < fin else np.float32(0.0)
                        acc = fma32(w[co, ci, t, k], v, acc)
            if act == 1:
                acc = acc if acc > 0.0 else slope * acc
            elif act == 2:
                acc = tanh32(acc)
            out[co, fo] = acc


@numba.njit(cache=True)
def deconv(x, w, b, slope, act, stride, pad, groups, fout, out):
    """A ConvTranspose2d along frequency, kernel (1, kf): out[co][fo] = act(b[co] + x[ci][fi] w[ci][co'][k] over the
    group's ci ascending, then k ascending, for each fi = (fo + pad - k) / stride that is whole and in range)."""
    cin, per_out, _, kf = w.shape
    fin = x.shape[1]
    per_in = cin // groups
    for co in range(per_out * groups):
        g, local = co // per_out, co % per_out
        for fo in range(fout):
            acc = b[co]
            for ci in range(g * per_in, (g + 1) * per_in):
                for k in range(kf):
                    num = fo + pad - k
                    if num >= 0 and num % stride == 0 and num // stride < fin:
                        acc = fma32(x[ci, num // stride], w[ci, local, 0, k], acc)
            if act == 1:
                acc = acc if acc > 0.0 else slope * acc
            elif act == 2:
                acc = tanh32(acc)
            out[co, fo] = acc


@numba.njit(cache=True)
def gru_step(wih, whh, bih, bhh, x, h, out):
    """One GRU step of PyTorch's gates r, z, n: n = tanh(gi_n + r gh_n), h' = n + z (h - n), into out."""
    rows = wih.shape[0]
    hid = rows // 3
    gi = np.empty(rows, np.float32)
    gh = np.empty(rows, np.float32)
    for r in range(rows):
        a, c = bih[r], bhh[r]
        for i in range(wih.shape[1]):
            a = fma32(wih[r, i], x[i], a)
        for j in range(hid):
            c = fma32(whh[r, j], h[j], c)
        gi[r], gh[r] = a, c
    for j in range(hid):
        rg = sigmoid32(gi[j] + gh[j])
        zg = sigmoid32(gi[hid + j] + gh[hid + j])
        n = tanh32(fma32(rg, gh[2 * hid + j], gi[2 * hid + j]))
        out[j] = fma32(zg, h[j] - n, n)


@numba.njit(cache=True)
def dense(w, b, x, out):
    for o in range(w.shape[0]):
        a = b[o]
        for i in range(w.shape[1]):
            a = fma32(w[o, i], x[i], a)
        out[o] = a


@numba.njit(cache=True)
def layer_norm(x, w, b):
    """x normalised over all of its values in place: sequential sums, one division each, own square root."""
    n = x.size
    s = np.float32(0.0)
    for i in range(n):
        s = s + x[i]
    mean = s / np.float32(n)
    v = np.float32(0.0)
    for i in range(n):
        d = x[i] - mean
        v = fma32(d, d, v)
    inv = rsqrt32(v / np.float32(n) + LN_EPS)
    for i in range(n):
        x[i] = fma32((x[i] - mean) * inv, w[i], b[i])


@numba.njit(cache=True)
def sfe(x, out):
    """out[c*3+j][i] = x[c][i+j-1], zero past either edge."""
    ch, f = x.shape
    for c in range(ch):
        for j in range(3):
            for i in range(f):
                src = i + j - 1
                out[c * 3 + j, i] = x[c, src] if 0 <= src < f else np.float32(0.0)


@numba.njit(cache=True)
def banded(x, ptr, idx, coef, low, out):
    """out[c][:low] = x[c][:low]; out[c][low+j] = the sum over band j's taps, ascending, of coef * x[c][low+idx]."""
    for c in range(x.shape[0]):
        for i in range(low):
            out[c, i] = x[c, i]
        for j in range(ptr.size - 1):
            a = np.float32(0.0)
            for p in range(ptr[j], ptr[j + 1]):
                a = fma32(coef[p], x[c, low + idx[p]], a)
            out[c, low + j] = a


@dataclass
class Block:
    """A grouped temporal block: point, depthwise, point convolutions, then the temporal attention's GRU and dense."""

    pc1_w: np.ndarray
    pc1_b: np.ndarray
    pc1_slope: np.float32
    dw_w: np.ndarray
    dw_b: np.ndarray
    dw_slope: np.float32
    pc2_w: np.ndarray
    pc2_b: np.ndarray
    att: tuple
    att_w: np.ndarray
    att_b: np.ndarray
    dil: int


@dataclass
class DualPath:
    intra: tuple  # [group][direction] GRU weights
    intra_fc: tuple
    intra_ln: tuple
    inter: tuple  # [group] GRU weights
    inter_fc: tuple
    inter_ln: tuple


@dataclass
class Weights:
    erb: tuple  # ptr, idx, coef over 192 bins into 64 bands
    ierb: tuple  # over 64 bands into 192 bins
    en0: tuple
    en1: tuple
    enc: list
    dps: list
    dec: list
    de3: tuple
    de4: tuple


@dataclass
class State:
    caches: list = field(
        default_factory=lambda: [np.zeros((CH, 2 * d, F2), np.float32) for d in DILATIONS + DILATIONS[::-1]]
    )
    tra: list = field(default_factory=lambda: [np.zeros(2 * HALF, np.float32) for _ in range(6)])
    inter: list = field(default_factory=lambda: [np.zeros((F2, HIDDEN), np.float32) for _ in range(2)])


def run_block(blk: Block, x: np.ndarray, cache: np.ndarray, tra: np.ndarray) -> np.ndarray:
    s = np.empty((3 * HALF, 1, F2), np.float32)
    sfe(x[:HALF], s[:, 0])
    h = np.empty((CH, F2), np.float32)
    conv(s, blk.pc1_w, blk.pc1_b, blk.pc1_slope, 1, 1, 0, 1, 1, F2, h)
    span = np.concatenate([cache, h[:, None]], axis=1)
    d = np.empty((CH, F2), np.float32)
    conv(span, blk.dw_w, blk.dw_b, blk.dw_slope, 1, 1, 1, blk.dil, CH, F2, d)
    cache[:] = span[:, 1:]
    y = np.empty((HALF, F2), np.float32)
    conv(d[:, None], blk.pc2_w, blk.pc2_b, np.float32(0.0), 0, 1, 0, 1, 1, F2, y)
    z = np.empty(HALF, np.float32)
    for c in range(HALF):
        z[c] = _mean_square(y[c])
    new = np.empty(2 * HALF, np.float32)
    gru_step(*blk.att, z, tra, new)
    tra[:] = new
    at = np.empty(HALF, np.float32)
    dense(blk.att_w, blk.att_b, tra, at)
    out = np.empty((CH, F2), np.float32)
    for c in range(HALF):
        out[2 * c] = y[c] * sigmoid32(at[c])
        out[2 * c + 1] = x[HALF + c]
    return out


@numba.njit(cache=True)
def _mean_square(v):
    a = np.float32(0.0)
    for i in range(v.size):
        a = fma32(v[i], v[i], a)
    return a / np.float32(v.size)


def run_dual(dp: DualPath, x: np.ndarray, inter: np.ndarray) -> np.ndarray:
    t = np.ascontiguousarray(x.T)  # (F2, CH)
    y = np.empty((F2, CH), np.float32)
    for g in range(2):
        for direction, order in ((0, range(F2)), (1, range(F2 - 1, -1, -1))):
            h = np.zeros(HALF // 2, np.float32)
            for f in order:
                new = np.empty(HALF // 2, np.float32)
                gru_step(*dp.intra[g][direction], t[f, g * HALF : (g + 1) * HALF], h, new)
                h = new
                y[f, g * HALF + direction * (HALF // 2) : g * HALF + (direction + 1) * (HALF // 2)] = h
    u = np.empty((F2, CH), np.float32)
    for f in range(F2):
        dense(*dp.intra_fc, y[f], u[f])
    flat = u.reshape(-1)
    layer_norm(flat, *dp.intra_ln)
    intra = t + u
    hn = np.empty((F2, CH), np.float32)
    for f in range(F2):
        for g in range(2):
            gru_step(
                *dp.inter[g],
                intra[f, g * HALF : (g + 1) * HALF],
                inter[f, g * HALF : (g + 1) * HALF],
                hn[f, g * HALF : (g + 1) * HALF],
            )
    inter[:] = hn
    for f in range(F2):
        dense(*dp.inter_fc, hn[f], u[f])
    layer_norm(u.reshape(-1), *dp.inter_ln)
    return np.ascontiguousarray((intra + u).T)


def step(w: Weights, st: State, bins: np.ndarray) -> np.ndarray:
    """The slot's gains for one hop of complex bins: the magnitude of GTCRN's complex mask, at most 1."""
    x = np.empty((3, BINS), np.float32)
    _features(bins.real.astype(np.float32), bins.imag.astype(np.float32), x)
    e = np.empty((3, ERB_BINS), np.float32)
    banded(x, *w.erb, LOW, e)
    s = np.empty((9, 1, ERB_BINS), np.float32)
    sfe(e, s[:, 0])
    o0 = np.empty((CH, F1), np.float32)
    conv(s, *w.en0, 1, 2, 2, 1, 1, F1, o0)
    o1 = np.empty((CH, F2), np.float32)
    conv(o0[:, None], *w.en1, 1, 2, 2, 1, 2, F2, o1)
    skips, y = [], o1
    for k in range(3):
        y = run_block(w.enc[k], y, st.caches[k], st.tra[k])
        skips.append(y)
    for k in range(2):
        y = run_dual(w.dps[k], y, st.inter[k])
    for k in range(3):
        y = run_block(w.dec[k], y + skips[2 - k], st.caches[3 + k], st.tra[3 + k])
    m1 = np.empty((CH, F1), np.float32)
    deconv(y + o1, *w.de3, 1, 2, 2, 2, F1, m1)
    m2 = np.empty((2, ERB_BINS), np.float32)
    deconv(m1 + o0, *w.de4, 2, 2, 2, 1, ERB_BINS, m2)
    mask = np.empty((2, BINS), np.float32)
    banded(m2, *w.ierb, LOW, mask)
    return _gains(mask)


@numba.njit(cache=True)
def _features(re, im, x):
    for i in range(re.size):
        x[0, i] = sqrt32(fma32(re[i], re[i], fma32(im[i], im[i], MAG_EPS)))
        x[1, i] = re[i]
        x[2, i] = im[i]


@numba.njit(cache=True)
def _gains(mask):
    g = np.empty(mask.shape[1], np.float32)
    for i in range(mask.shape[1]):
        g[i] = min(sqrt32(fma32(mask[0, i], mask[0, i], mask[1, i] * mask[1, i])), np.float32(1.0))
    return g


def _sparse(dense_rows: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    ptr, idx, coef = [0], [], []
    for row in dense_rows:
        nz = np.flatnonzero(row)
        idx += nz.tolist()
        coef += row[nz].tolist()
        ptr.append(len(idx))
    return np.array(ptr, np.int32), np.array(idx, np.int32), np.array(coef, np.float32)


def _bn(sd: dict, name: str) -> tuple[np.ndarray, np.ndarray]:
    g, b = sd[f"{name}.weight"].astype(np.float64), sd[f"{name}.bias"].astype(np.float64)
    mean, var = sd[f"{name}.running_mean"].astype(np.float64), sd[f"{name}.running_var"].astype(np.float64)
    scale = g / np.sqrt(var + 1e-5)
    return scale, b - mean * scale


def _folded(sd, conv_name, bn_name, transposed=False):
    w, b = sd[f"{conv_name}.weight"].astype(np.float64), sd[f"{conv_name}.bias"].astype(np.float64)
    scale, shift = _bn(sd, bn_name)
    if transposed:
        cin, per_out = w.shape[:2]
        per_in = cin // (scale.size // per_out)
        out_of = (np.arange(cin)[:, None] // per_in) * per_out + np.arange(per_out)[None, :]
        w = w * scale[out_of][:, :, None, None]
    else:
        w = w * scale[:, None, None, None]
    return w.astype(np.float32), (b * scale + shift).astype(np.float32)


def _point(sd, conv_name, bn_name, transposed):
    w, b = _folded(sd, conv_name, bn_name, transposed)
    return (np.ascontiguousarray(w.transpose(1, 0, 2, 3)) if transposed else w), b


def _slope(sd, name):
    return np.float32(sd[f"{name}.weight"][0])


def _gru(sd, name, suffix=""):
    return tuple(
        np.ascontiguousarray(sd[f"{name}.{k}_l0{suffix}"].astype(np.float32))
        for k in ("weight_ih", "weight_hh", "bias_ih", "bias_hh")
    )


def _block(sd, p, dil, transposed):
    pc1 = _point(sd, f"{p}.point_conv1", f"{p}.point_bn1", transposed)
    dw = _folded(sd, f"{p}.depth_conv.{'ConvTranspose2d' if transposed else 'Conv2d'}", f"{p}.depth_bn")
    pc2 = _point(sd, f"{p}.point_conv2", f"{p}.point_bn2", transposed)
    fc_w, fc_b = (sd[f"{p}.tra.att_fc.{k}"].astype(np.float32) for k in ("weight", "bias"))
    return Block(
        *pc1,
        _slope(sd, f"{p}.point_act"),
        *dw,
        _slope(sd, f"{p}.depth_act"),
        *pc2,
        _gru(sd, f"{p}.tra.att_gru"),
        fc_w,
        fc_b,
        dil,
    )


def _dual(sd, p):
    intra = tuple(tuple(_gru(sd, f"{p}.intra_rnn.rnn{g + 1}", s) for s in ("", "_reverse")) for g in range(2))

    def lin(n):
        return tuple(
            np.ascontiguousarray(sd[f"{p}.{n}.{k}"].astype(np.float32)).reshape(
                -1 if n.endswith("ln") else sd[f"{p}.{n}.{k}"].shape
            )
            for k in ("weight", "bias")
        )

    inter = tuple(_gru(sd, f"{p}.inter_rnn.rnn{g + 1}") for g in range(2))
    return DualPath(intra, lin("intra_fc"), lin("intra_ln"), inter, lin("inter_fc"), lin("inter_ln"))


def weights(state_dict: dict) -> Weights:
    """The mirror's weights from a StreamGTCRN state dict of numpy arrays."""
    sd = state_dict
    en0 = (*_folded(sd, "encoder.en_convs.0.conv", "encoder.en_convs.0.bn"), _slope(sd, "encoder.en_convs.0.act"))
    en1 = (*_folded(sd, "encoder.en_convs.1.conv", "encoder.en_convs.1.bn"), _slope(sd, "encoder.en_convs.1.act"))
    de3 = (*_folded(sd, "decoder.de_convs.3.conv", "decoder.de_convs.3.bn", True), _slope(sd, "decoder.de_convs.3.act"))
    de4 = (*_folded(sd, "decoder.de_convs.4.conv", "decoder.de_convs.4.bn", True), np.float32(0.0))
    return Weights(
        _sparse(sd["erb.erb_fc.weight"].astype(np.float32)),
        _sparse(sd["erb.ierb_fc.weight"].astype(np.float32)),
        en0,
        en1,
        [_block(sd, f"encoder.en_convs.{k + 2}", DILATIONS[k], False) for k in range(3)],
        [_dual(sd, f"dpgrnn{k + 1}") for k in range(2)],
        [_block(sd, f"decoder.de_convs.{k}", DILATIONS[2 - k], True) for k in range(3)],
        de3,
        de4,
    )


def _block_arrays(b: Block) -> list:
    return [b.pc1_w, b.pc1_b, [b.pc1_slope], b.dw_w, b.dw_b, [b.dw_slope], b.pc2_w, b.pc2_b, *b.att, b.att_w, b.att_b]


def _dual_arrays(d: DualPath) -> list:
    out = [a for g in range(2) for direction in range(2) for a in d.intra[g][direction]]
    out += [*d.intra_fc, *d.intra_ln]
    out += [a for g in range(2) for a in d.inter[g]]
    return [*out, *d.inter_fc, *d.inter_ln]


def pack(w: Weights) -> bytes:
    """w as the blob ns_gtcrn reads: magic, version and both banks' tap counts, then every array in the order of
    gtcrn.c's take_*, int32 for the banks' pointers and indices, float32 for the rest."""
    arrays = [*w.erb, *w.ierb, *w.en0[:2], [w.en0[2]], *w.en1[:2], [w.en1[2]]]
    arrays += [a for b in w.enc for a in _block_arrays(b)]
    arrays += [a for d in w.dps for a in _dual_arrays(d)]
    arrays += [a for b in w.dec for a in _block_arrays(b)]
    arrays += [*w.de3[:2], [w.de3[2]], *w.de4[:2], [w.de4[2]]]
    head = np.array([BLOB_MAGIC, BLOB_VERSION, w.erb[1].size, w.ierb[1].size], np.uint32).tobytes()
    body = (np.asarray(a, np.int32 if np.asarray(a).dtype.kind == "i" else np.float32).tobytes() for a in arrays)
    return head + b"".join(body)


def random_weights(rng: np.random.Generator) -> Weights:
    """Weights of GTCRN's shapes drawn from rng, each layer scaled by its fan-in, banks of two or three taps a band:
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
        return _sparse(dense_rows)

    def block(dil):
        return Block(
            arr((CH, 3 * HALF, 1, 1), 24),
            arr(CH, 10),
            np.float32(0.25),
            arr((CH, 1, 3, 3), 9),
            arr(CH, 10),
            np.float32(0.2),
            arr((HALF, CH, 1, 1), 16),
            arr(HALF, 10),
            gru(HALF, 2 * HALF),
            arr((HALF, 2 * HALF), 16),
            arr(HALF, 10),
            dil,
        )

    def dual():
        ln = (np.float32(1.0) + arr(F2 * CH, 100), arr(F2 * CH, 100))
        return DualPath(
            tuple(tuple(gru(HALF, HALF // 2) for _ in range(2)) for _ in range(2)),
            (arr((CH, CH), 16), arr(CH, 10)),
            ln,
            tuple(gru(HALF, HALF) for _ in range(2)),
            (arr((CH, CH), 16), arr(CH, 10)),
            (np.float32(1.0) + arr(F2 * CH, 100), arr(F2 * CH, 100)),
        )

    return Weights(
        bank(ERB_BINS - LOW, BINS - LOW),
        bank(BINS - LOW, ERB_BINS - LOW),
        (arr((CH, 9, 1, 5), 45), arr(CH, 10), np.float32(0.25)),
        (arr((CH, HALF, 1, 5), 40), arr(CH, 10), np.float32(0.25)),
        [block(d) for d in DILATIONS],
        [dual(), dual()],
        [block(d) for d in DILATIONS[::-1]],
        (arr((CH, HALF, 1, 5), 40), arr(CH, 10), np.float32(0.25)),
        (arr((CH, 2, 1, 5), 80), arr(2, 10), np.float32(0.0)),
    )


def case(w: Weights, bins: np.ndarray, off: float = 0.0) -> dict[str, np.ndarray]:
    """A golden case: the packed weights, every hop's bins as re, im pairs, and the mirror's gains from fresh state;
    off is added to the first encoder bias before the gains are taken, for a negative control."""
    blob = np.frombuffer(pack(w), np.uint8).copy()
    if off:
        w.en0[1][0] += np.float32(off)
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
