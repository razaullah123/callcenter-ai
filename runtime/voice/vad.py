"""Silero VAD v5 via onnxruntime (no torch). ~1 ms per 32 ms window on CPU."""

from pathlib import Path

import numpy as np
import onnxruntime as ort

from runtime.config import ROOT_DIR

MODEL_PATH = ROOT_DIR / "models" / "silero_vad.onnx"
SAMPLE_RATE = 16_000
WINDOW = 512          # samples per inference at 16 kHz (32 ms); 256 at 8 kHz
CONTEXT = 64          # samples of left context the v5 model expects at 16 kHz; 32 at 8 kHz


class SileroVAD:
    _session: ort.InferenceSession | None = None   # shared across calls; state is per instance

    def __init__(self, model_path: Path = MODEL_PATH, sample_rate: int = SAMPLE_RATE) -> None:
        if sample_rate not in (8000, 16000):
            raise ValueError("Silero VAD supports 8000 or 16000 Hz")
        self.rate = sample_rate
        self.window = WINDOW * sample_rate // SAMPLE_RATE
        self.context = CONTEXT * sample_rate // SAMPLE_RATE
        if SileroVAD._session is None:
            opts = ort.SessionOptions()
            opts.inter_op_num_threads = opts.intra_op_num_threads = 1
            SileroVAD._session = ort.InferenceSession(str(model_path), opts, providers=["CPUExecutionProvider"])
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, self.context), dtype=np.float32)
        self._sr = np.array(self.rate, dtype=np.int64)

    def __call__(self, window: np.ndarray) -> float:
        """Speech probability for one window (512 samples @ 16 kHz / 256 @ 8 kHz), float32 in [-1, 1]."""
        x = np.concatenate([self._context, window.reshape(1, -1).astype(np.float32)], axis=1)
        out, self._state = self._session.run(None, {"input": x, "state": self._state, "sr": self._sr})
        self._context = x[:, -self.context:]
        return float(out[0][0])
