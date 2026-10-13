#!/usr/bin/env python3
"""Separate the lead vocal from a song with Demucs (htdemucs). Runs in the separator's own Python
(MEDIA/vocal-venv), started by vocalguide.py.

Usage: separate.py INPUT OUTPUT.wav    Writes the vocal as 16 kHz mono wav. Prints "progress 0..1" lines.
Audio goes in and out through ffmpeg, so torchaudio's file backends are never needed.
Uses the GPU when it has room, and carries on on the CPU when it runs out of memory (for example
while Ollama holds a vision model on it).
"""
import subprocess, sys

import numpy as np
import torch
from demucs.apply import apply_model
from demucs.pretrained import get_model


def run(model, x, dev, vi):
    with torch.no_grad():
        return apply_model(model, x[None].to(dev), split=True, overlap=0.25, progress=False)[0, vi].cpu()


def main():
    src, dst = sys.argv[1], sys.argv[2]
    model = get_model(sys.argv[3] if len(sys.argv) > 3 else 'htdemucs')
    model.eval()
    sr = model.samplerate
    raw = subprocess.check_output(['ffmpeg', '-loglevel', 'error', '-i', src, '-ac', '2', '-ar', str(sr), '-f', 'f32le', '-'])
    x = torch.from_numpy(np.frombuffer(raw, np.float32).reshape(-1, 2).T.copy())
    dev = 'cuda' if torch.cuda.is_available() else 'cpu'
    if dev == 'cuda':
        # the work is on the graphics card; a few CPU threads feed it, and a thread per core only fights the
        # other steps of the import running at the same time
        torch.set_num_threads(min(4, torch.get_num_threads()))
    try:
        model.to(dev)
    except torch.OutOfMemoryError:
        dev = 'cpu'
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
        try:
            y = run(model, x[:, s:e], dev, vi)
        except torch.OutOfMemoryError:
            print('cpu the GPU is out of memory, carrying on with the CPU (slower)', flush=True)
            dev = 'cpu'
            model.to(dev)
            torch.cuda.empty_cache()
            y = run(model, x[:, s:e], dev, vi)
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
