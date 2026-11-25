"""The float32 mirror of ns_ulunas: its own log10 is float32-close, the causal transposed convolution and the
attention compute what PyTorch's layers compute, and a whole hop matches StreamULUNAS when its pinned files are
cached."""

import math

import numpy as np
import pytest

from srpipe.core.config import CONFIGS, data_paths, load_yaml
from srpipe.tasks.ns import ulunas_mirror


def test_log10_is_float32_close() -> None:
    for v in np.geomspace(1e-12, 1e12, 6001).astype(np.float32):
        assert abs(float(ulunas_mirror.log10_32(v)) - math.log10(float(v))) < 4e-7 * max(1.0, abs(math.log10(float(v))))
    assert ulunas_mirror.log10_32(np.float32(1.0)) == 0.0
    assert ulunas_mirror.log10_32(np.float32(10.0)) != np.float32(0.0)


def test_window_scale_is_the_hann_sum_over_the_sqrt_hann_sum() -> None:
    n = np.arange(512)
    hann = 0.5 - 0.5 * np.cos(2.0 * np.pi * n / 512)
    assert abs(float(ulunas_mirror.window_scale()) - hann.sum() / np.sqrt(hann).sum()) < 1e-6


@pytest.mark.parametrize(
    ("cin", "cout", "groups", "kt", "stride", "fin"),
    [(24, 24, 24, 2, 1, 33), (12, 12, 12, 2, 2, 33), (12, 1, 1, 3, 2, 65)],
)
def test_deconv_t_is_the_last_frame_of_a_causal_conv_transpose2d(
    cin: int, cout: int, groups: int, kt: int, stride: int, fin: int
) -> None:
    torch = pytest.importorskip("torch")
    rng = np.random.default_rng(3)
    kf, pad = 3, 1
    span = rng.standard_normal((cin, kt, fin)).astype(np.float32)
    w = rng.standard_normal((cin, cout // groups, kt, kf)).astype(np.float32)
    b = rng.standard_normal(cout).astype(np.float32)
    fout = (fin - 1) * stride - 2 * pad + kf
    out = np.empty((cout, fout), np.float32)
    ulunas_mirror.deconv_t(span, w, b, stride, pad, groups, fout, out)
    x = torch.nn.functional.pad(torch.from_numpy(span[None]).double(), (0, 0, kt - 1, 0))
    ref = torch.nn.functional.conv_transpose2d(
        x, torch.from_numpy(w).double(), torch.from_numpy(b).double(), (1, stride), (kt - 1, pad), groups=groups
    )[0, :, -1].numpy()
    np.testing.assert_allclose(out, ref, rtol=1e-5, atol=1e-5)


def test_negative_control_a_wrong_time_order_is_caught() -> None:
    torch = pytest.importorskip("torch")
    rng = np.random.default_rng(4)
    span = rng.standard_normal((4, 2, 9)).astype(np.float32)
    w = rng.standard_normal((4, 1, 2, 3)).astype(np.float32)
    b = np.zeros(4, np.float32)
    out = np.empty((4, 9), np.float32)
    ulunas_mirror.deconv_t(span[:, ::-1].copy(), w, b, 1, 1, 4, 9, out)
    x = torch.nn.functional.pad(torch.from_numpy(span[None]).double(), (0, 0, 1, 0))
    ref = torch.nn.functional.conv_transpose2d(x, torch.from_numpy(w).double(), None, 1, (1, 1), groups=4)[0, :, -1]
    assert np.abs(out - ref.numpy()).max() > 1e-2


def test_a_hop_matches_stream_ulunas_on_random_weights() -> None:
    torch = pytest.importorskip("torch")
    from srpipe.tasks.ns import ulunas

    spec = load_yaml(CONFIGS / "models" / "ns.yaml")["ulunas"]
    folder = data_paths()["cache"] / "afe_ref" / "ulunas"
    if not all((folder / path).exists() for path in spec["files"]):
        pytest.skip("UL-UNAS's pinned files are not cached: run a SRPIPE_NS=ulunas command once")
    module = ulunas.stream_module(folder)
    torch.manual_seed(5)
    net = module.StreamULUNAS().eval()
    with torch.no_grad():
        for name, p in net.named_parameters():
            if "affine" in name or "slope" in name:
                p.add_(0.1 * torch.randn_like(p))
        for m in net.modules():
            if isinstance(m, torch.nn.BatchNorm2d):
                m.running_mean.uniform_(-0.1, 0.1)
                m.running_var.uniform_(0.5, 1.5)
    w = ulunas_mirror.weights({k: v.detach().numpy() for k, v in net.state_dict().items()})
    slot, st = ulunas.UlunasSlot(net, module.StreamULUNAS.init_caches(1)), ulunas_mirror.State()
    rng = np.random.default_rng(6)
    for _ in range(12):
        bins = ((rng.standard_normal(257) + 1j * rng.standard_normal(257)) * 0.1).astype(np.complex64)
        np.testing.assert_allclose(ulunas_mirror.step(w, st, bins), slot.gains(bins, None), rtol=1e-4, atol=1e-5)
