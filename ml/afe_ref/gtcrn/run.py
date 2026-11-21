"""GTCRN of Xiaobin-Rong/gtcrn (MIT) for srpipe.scenes.refs: `run.py <weights> <jobs.jsonl> [gain]` cleans each line
{in, out}, mono 16 kHz, into a file of the same length. weights holds gtcrn.py and the DNS3 checkpoint of the pinned
commit, imported from there; the STFT is the repo's infer.py: 512 points, hop 256, a square-root Hann window. gain
keeps only each bin's magnitude ratio of the network's output to the noisy spectrum, at most 1, on the noisy spectrum,
as a gain of the ns slot does."""

import json
import sys
from pathlib import Path

import soundfile as sf
import torch

RATE_HZ = 16000
N_FFT = 512
HOP = 256
CHECKPOINT = "checkpoints/model_trained_on_dns3.tar"
TINY = 1e-12


def main() -> int:
    weights, jobs = Path(sys.argv[1]), Path(sys.argv[2])
    gain_only = sys.argv[3:] == ["gain"]
    sys.path.insert(0, str(weights))
    from gtcrn import GTCRN

    model = GTCRN().eval()
    model.load_state_dict(torch.load(weights / CHECKPOINT, map_location="cpu")["model"])
    window = torch.hann_window(N_FFT).pow(0.5)
    for line in jobs.read_text(encoding="utf-8").splitlines():
        job = json.loads(line)
        x, rate = sf.read(job["in"], dtype="float32")
        if rate != RATE_HZ or x.ndim != 1:
            raise ValueError(f"{job['in']}: {rate} Hz, {x.ndim}-d; GTCRN takes mono {RATE_HZ} Hz")
        noisy = torch.stft(torch.from_numpy(x), N_FFT, HOP, N_FFT, window, return_complex=True)
        with torch.no_grad():
            out = torch.view_as_complex(model(torch.view_as_real(noisy)[None])[0].contiguous())
        if gain_only:
            out = noisy * torch.clamp(out.abs() / (noisy.abs() + TINY), max=1.0)
        y = torch.istft(out, N_FFT, HOP, N_FFT, window, length=len(x)).numpy()
        sf.write(job["out"], y, RATE_HZ, subtype="PCM_16")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
