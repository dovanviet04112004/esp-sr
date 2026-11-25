"""The float32 mirror of ns_gtcrn: its fused multiply-add rounds once, its own exp and square root are
float32-close, each kernel computes what PyTorch's layer computes, and a whole hop matches StreamGTCRN when its
pinned files are cached."""

import math
import sys
from fractions import Fraction

import numpy as np
import pytest

from srpipe.core.config import data_paths
from srpipe.tasks.ns import gtcrn_mirror


def nearest_f32(exact: Fraction) -> np.float32:
    x = np.float32(float(exact))
    best = min(
        (np.nextafter(x, np.float32(-np.inf)), x, np.nextafter(x, np.float32(np.inf))),
        key=lambda v: (abs(Fraction(float(v)) - exact), int(np.float32(v).view(np.int32)) & 1),
    )
    return np.float32(best)


def test_fma32_rounds_the_exact_sum_once() -> None:
    rng = np.random.default_rng(0)
    cases = [(np.float32(1 + 2**-12), np.float32(1 + 2**-12), np.float32(-1.0))]
    cases += [
        tuple(np.float32(v) for v in rng.standard_normal(3) * 10.0 ** rng.integers(-3, 3, 3)) for _ in range(2000)
    ]
    for a, b, c in cases:
        want = nearest_f32(Fraction(float(a)) * Fraction(float(b)) + Fraction(float(c)))
        assert gtcrn_mirror.fma32(a, b, c) == want, (a, b, c)
    a, b, c = cases[0]
    assert np.float32(a * b) + c != gtcrn_mirror.fma32(a, b, c)  # two roundings land elsewhere on a tie


def test_exp_reciprocal_and_square_root_are_float32_close() -> None:
    for x in np.linspace(-80.0, 80.0, 4001, dtype=np.float32):
        assert abs(float(gtcrn_mirror.exp32(x)) / math.exp(float(x)) - 1.0) < 4e-7
    for v in np.geomspace(1e-30, 1e30, 3001).astype(np.float32):
        assert abs(float(gtcrn_mirror.sqrt32(v)) / math.sqrt(float(v)) - 1.0) < 3e-7
        assert abs(float(gtcrn_mirror.rsqrt32(v)) * math.sqrt(float(v)) - 1.0) < 3e-7
        assert abs(float(gtcrn_mirror.recip32(v)) * float(v) - 1.0) < 2e-7
    assert gtcrn_mirror.sqrt32(np.float32(0.0)) == 0.0


@pytest.mark.parametrize(("stride", "pad", "dil", "groups"), [(2, 2, 1, 1), (2, 2, 1, 2), (1, 1, 2, 16), (1, 0, 1, 1)])
def test_conv_is_a_conv2d_over_the_last_time_step(stride: int, pad: int, dil: int, groups: int) -> None:
    torch = pytest.importorskip("torch")
    rng = np.random.default_rng(1)
    cin, cout, kt, kf, fin = 16, 16, 3 if dil > 1 else 1, 5 if stride == 2 else (3 if groups == 16 else 1), 33
    span = (kt - 1) * dil + 1
    x = rng.standard_normal((cin, span, fin)).astype(np.float32)
    w = rng.standard_normal((cout, cin // groups, kt, kf)).astype(np.float32)
    b = rng.standard_normal(cout).astype(np.float32)
    fout = (fin + 2 * pad - kf) // stride + 1
    out = np.empty((cout, fout), np.float32)
    gtcrn_mirror.conv(x, w, b, np.float32(0.25), 1, stride, pad, dil, groups, fout, out)
    ref = torch.nn.functional.conv2d(
        torch.from_numpy(x[None]).double(),
        torch.from_numpy(w).double(),
        torch.from_numpy(b).double(),
        (1, stride),
        (0, pad),
        (dil, 1),
        groups,
    )[0, :, -1]
    ref = torch.where(ref > 0, ref, 0.25 * ref).numpy()
    np.testing.assert_allclose(out, ref, rtol=1e-5, atol=1e-5)


@pytest.mark.parametrize(("cin", "cout", "groups", "fin"), [(16, 16, 2, 33), (16, 2, 1, 65)])
def test_deconv_is_a_conv_transpose2d(cin: int, cout: int, groups: int, fin: int) -> None:
    torch = pytest.importorskip("torch")
    rng = np.random.default_rng(2)
    x = rng.standard_normal((cin, fin)).astype(np.float32)
    w = rng.standard_normal((cin, cout // groups, 1, 5)).astype(np.float32)
    b = rng.standard_normal(cout).astype(np.float32)
    fout = (fin - 1) * 2 - 4 + 5
    out = np.empty((cout, fout), np.float32)
    gtcrn_mirror.deconv(x, w, b, np.float32(0.0), 0, 2, 2, groups, fout, out)
    ref = torch.nn.functional.conv_transpose2d(
        torch.from_numpy(x[None, :, None]).double(),
        torch.from_numpy(w).double(),
        torch.from_numpy(b).double(),
        (1, 2),
        (0, 2),
        groups=groups,
    )[0, :, 0].numpy()
    np.testing.assert_allclose(out, ref, rtol=1e-5, atol=1e-5)


def test_gru_step_and_layer_norm_follow_pytorch() -> None:
    torch = pytest.importorskip("torch")
    torch.manual_seed(3)
    cell = torch.nn.GRUCell(8, 16).double()
    x, h = torch.randn(8, dtype=torch.float64), torch.randn(16, dtype=torch.float64)
    weights = tuple(p.detach().float().numpy() for p in (cell.weight_ih, cell.weight_hh, cell.bias_ih, cell.bias_hh))
    out = np.empty(16, np.float32)
    gtcrn_mirror.gru_step(*weights, x.float().numpy(), h.float().numpy(), out)
    np.testing.assert_allclose(out, cell(x[None], h[None])[0].detach().numpy(), rtol=1e-5, atol=1e-6)
    v = torch.randn(528, dtype=torch.float64)
    g, b = torch.randn(528, dtype=torch.float64), torch.randn(528, dtype=torch.float64)
    y = v.float().numpy().copy()
    gtcrn_mirror.layer_norm(y, g.float().numpy(), b.float().numpy())
    np.testing.assert_allclose(y, torch.nn.functional.layer_norm(v, (528,), g, b, 1e-8).numpy(), rtol=1e-4, atol=1e-5)


def test_a_hop_matches_stream_gtcrn_on_random_weights() -> None:
    torch = pytest.importorskip("torch")
    folder = data_paths()["cache"] / "afe_ref" / "gtcrn" / "stream"
    if not (folder / "gtcrn_stream.py").exists():
        pytest.skip("GTCRN's pinned stream files are not cached: run a SRPIPE_NS=gtcrn command once")
    sys.path.insert(0, str(folder))
    from gtcrn_stream import StreamGTCRN

    torch.manual_seed(4)
    net = StreamGTCRN().eval()
    with torch.no_grad():
        for m in net.modules():
            if isinstance(m, torch.nn.BatchNorm2d):
                m.running_mean.uniform_(-0.1, 0.1)
                m.running_var.uniform_(0.5, 1.5)
    w = gtcrn_mirror.weights({k: v.detach().numpy() for k, v in net.state_dict().items()})
    st = gtcrn_mirror.State()
    caches = [torch.zeros(2, 1, 16, 16, 33), torch.zeros(2, 3, 1, 1, 16), torch.zeros(2, 1, 33, 16)]
    rng = np.random.default_rng(5)
    for _ in range(12):
        bins = (rng.standard_normal(257) + 1j * rng.standard_normal(257)).astype(np.complex64)
        with torch.inference_mode():
            y, *caches = net(torch.view_as_real(torch.from_numpy(bins))[None, :, None, :], *caches)
        ref = np.minimum(np.abs(torch.view_as_complex(y[0, :, 0].contiguous()).numpy()) / (np.abs(bins) + 1e-12), 1.0)
        np.testing.assert_allclose(gtcrn_mirror.step(w, st, bins), ref, rtol=1e-4, atol=1e-5)
