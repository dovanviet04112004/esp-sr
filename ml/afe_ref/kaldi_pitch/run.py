"""Kaldi's own pitch through kalpy, inside the aligner's image of common/tts.yaml: the reference of dsp_spec/pitch.

Usage: run.py <requests.json>; each request is {wav, out, pitch, process} with pitch and process the fields of
Kaldi's PitchExtractionOptions and ProcessPitchOptions to set; out gets a float32 .npy of (frames, 4) from
ComputeAndProcessKaldiPitch: [POV feature, normalised log pitch, delta, raw log pitch].
"""

import json
import sys
from pathlib import Path

import _kalpy.feat as feat
import numpy as np
import soundfile as sf


def options(kind, fields: dict):
    opts = kind()
    for name, value in fields.items():
        setattr(opts, name, value)
    return opts


def main() -> int:
    for r in json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")):
        x, rate = sf.read(r["wav"], dtype="float32")
        pitch = options(feat.PitchExtractionOptions, {"samp_freq": float(rate), **r["pitch"]})
        process = options(feat.ProcessPitchOptions, r["process"])
        np.save(r["out"], feat.compute_pitch(x, pitch, process).numpy().astype(np.float32))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
