#!/usr/bin/env python3
"""Separate the lead vocal from a song with Demucs (htdemucs). Runs in the separator's own Python
(MEDIA/vocal-venv), started by vocalguide.py.

Usage: separate.py INPUT OUTPUT.wav    Writes the vocal as 16 kHz mono wav. Prints "progress 0..1" lines.
       separate.py --serve             Loads the model, prints "ready", then separates one "INPUT<TAB>OUTPUT"
                                       line from stdin at a time, ending each with "done" or "error ...".
                                       An import starts it early, so the model is loaded while the songs
                                       download.
Audio goes in and out through ffmpeg, so torchaudio's file backends are never needed.
On the GPU, several 7.8 s pieces go through the model at once in half precision (about twice as fast;
the vocal differs from the one-at-a-time result by less than -60 dB). When the GPU runs out of memory
(for example while Ollama holds a vision model on it) it takes fewer pieces at a time, then carries on on
the CPU.
"""
import subprocess, sys

import numpy as np
import torch
from demucs.pretrained import get_model


class Separator:
    def __init__(self, name='htdemucs'):
        bag = get_model(name)
        bag.eval()
        if len(bag.models) != 1:
            raise SystemExit(f'{name} is a bag of several models; only single models are supported')
        self.model = bag.models[0]         # htdemucs is a bag of one model
        self.sr = self.model.samplerate
        self.vi = self.model.sources.index('vocals')
        self.dev = 'cuda' if torch.cuda.is_available() else 'cpu'
        if self.dev == 'cuda':
            # the work is on the graphics card; a few CPU threads feed it, and a thread per core only fights the
            # other steps of the import running at the same time
            torch.set_num_threads(min(4, torch.get_num_threads()))
        self.batch = 8 if self.dev == 'cuda' else 1
        try:
            self.model.to(self.dev)
        except Exception as e:
            if not self.short_of_gpu(e):
                raise
            self.to_cpu()

    @staticmethod
    def short_of_gpu(e):
        # cuFFT and cuBLAS report a full card as an internal or allocation error, not as OutOfMemoryError
        return isinstance(e, torch.OutOfMemoryError) or (
            isinstance(e, RuntimeError) and any(k in str(e) for k in ('CUFFT_INTERNAL_ERROR', 'CUFFT_ALLOC_FAILED', 'CUBLAS_STATUS_ALLOC_FAILED', 'CUDA out of memory', 'CUDNN_STATUS_ALLOC_FAILED')))

    def to_cpu(self):
        print('cpu the GPU is out of memory, carrying on with the CPU (slower)', flush=True)
        self.dev, self.batch = 'cpu', 1
        self.model.to('cpu')
        torch.cuda.empty_cache()

    def run(self, xb):
        with torch.no_grad(), torch.autocast('cuda', dtype=torch.float16, enabled=self.dev == 'cuda'):
            return self.model(xb.to(self.dev))[:, self.vi].float().cpu()

    def separate(self, src, dst):
        raw = subprocess.check_output(['ffmpeg', '-loglevel', 'error', '-i', src, '-ac', '2', '-ar', str(self.sr), '-f', 'f32le', '-'])
        x = torch.from_numpy(np.frombuffer(raw, np.float32).reshape(-1, 2).T.copy())
        ref = x.mean(0)
        mean, std = ref.mean(), ref.std() + 1e-8
        x = (x - mean) / std
        # the model's own training length, overlapping by a quarter, blended with triangular weights (as
        # demucs.apply does), but several pieces per call
        seg = int(self.sr * float(self.model.segment))
        stride = int(0.75 * seg)
        n = x.shape[1]
        starts = list(range(0, n, stride))
        w = torch.cat([torch.arange(1, seg // 2 + 1), torch.arange(seg - seg // 2, 0, -1)]).float()
        w /= w.max()
        xp = torch.nn.functional.pad(x, (0, seg))
        out = torch.zeros(2, n + seg)
        weight = torch.zeros(n + seg)
        k = 0
        while k < len(starts):
            group = starts[k:k + self.batch]
            try:
                y = self.run(torch.stack([xp[:, s:s + seg] for s in group]))
            except Exception as e:
                if self.dev != 'cuda' or not self.short_of_gpu(e):
                    raise
                torch.cuda.empty_cache()
                if self.batch > 1:
                    self.batch //= 2
                else:
                    self.to_cpu()
                continue
            for j, s in enumerate(group):
                out[:, s:s + seg] += y[j] * w
                weight[s:s + seg] += w
            k += len(group)
            print(f'progress {k / len(starts):.3f}', flush=True)
        out = out[:, :n] / weight[:n].clamp(min=1e-6) * std + mean
        pcm = out.T.contiguous().numpy().astype(np.float32).tobytes()
        p = subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'f32le', '-ar', str(self.sr), '-ac', '2', '-i', '-',
                            '-ac', '1', '-ar', '16000', dst], input=pcm)
        if p.returncode != 0:
            raise RuntimeError('ffmpeg could not write the vocal')


def main():
    if sys.argv[1] == '--serve':
        sep = Separator()
        print('ready', flush=True)
        for line in sys.stdin:
            if not line.strip():
                continue
            src, dst = line.rstrip('\n').split('\t')
            try:
                sep.separate(src, dst)
                print('done', flush=True)
            except Exception as e:
                print('error', str(e).replace('\n', ' '), flush=True)
        return
    sep = Separator(sys.argv[3] if len(sys.argv) > 3 else 'htdemucs')
    sep.separate(sys.argv[1], sys.argv[2])


if __name__ == '__main__':
    main()
