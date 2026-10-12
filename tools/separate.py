#!/usr/bin/env python3
"""Separate the lead vocal from a song with Demucs (htdemucs). Runs in the separator's own Python
(MEDIA/vocal-venv), started by vocalguide.py.

Usage: separate.py INPUT OUTPUT.wav    Writes the vocal as 16 kHz mono wav. Prints "progress 0..1" lines.
Audio goes in and out through ffmpeg, so torchaudio's file backends are never needed.
"""
import subprocess, sys

import numpy as np
import torch
from demucs.apply import apply_model
from demucs.pretrained import get_model


def main():
    src, dst = sys.argv[1], sys.argv[2]
    model = get_model('htdemucs')
    model.eval()
    sr = model.samplerate
    raw = subprocess.check_output(['ffmpeg', '-loglevel', 'error', '-i', src, '-ac', '2', '-ar', str(sr), '-f', 'f32le', '-'])
    x = torch.from_numpy(np.frombuffer(raw, np.float32).reshape(-1, 2).T.copy())
    dev = 'cuda' if torch.cuda.is_available() else 'cpu'
    model.to(dev)
    ref = x.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    x = (x - mean) / std
    vi = model.sources.index('vocals')
    # work through the song in pieces with a short crossfade, reporting progress as it goes
    seg, fade = 40 * sr, 2 * sr
    n = x.shape[1]
    out = torch.zeros(2, n)
    weight = torch.zeros(n)
    starts = list(range(0, max(1, n - fade), seg - fade))
    for k, s in enumerate(starts):
        e = min(n, s + seg)
        with torch.no_grad():
            y = apply_model(model, x[None, :, s:e].to(dev), split=True, overlap=0.25, progress=False)[0, vi].cpu()
        w = torch.ones(e - s)
        if s > 0:
            w[:fade] = torch.linspace(0, 1, fade)
        if e < n:
            w[-fade:] = torch.minimum(w[-fade:], torch.linspace(1, 0, fade))
        out[:, s:e] += y * w
        weight[s:e] += w
        print(f'progress {(k + 1) / len(starts):.3f}', flush=True)
        if e >= n:
            break
    out = out / weight.clamp(min=1e-6) * std + mean
    pcm = out.T.contiguous().numpy().astype(np.float32).tobytes()
    p = subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'f32le', '-ar', str(sr), '-ac', '2', '-i', '-',
                        '-ac', '1', '-ar', '16000', dst], input=pcm)
    sys.exit(p.returncode)


if __name__ == '__main__':
    main()
