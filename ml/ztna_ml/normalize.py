"""Feature normalisation shared by training and inference."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

Z_CLIP = 10.0  # keeps one exploding feature from swamping the reconstruction error
STD_FLOOR = 0.15


@dataclass
class Normalizer:
    mean: np.ndarray
    std: np.ndarray

    @classmethod
    def fit(cls, X: np.ndarray) -> Normalizer:
        mean = X.mean(axis=0)
        std = np.maximum(X.std(axis=0), STD_FLOOR)
        return cls(mean.astype(np.float32), std.astype(np.float32))

    def transform(self, X: np.ndarray) -> np.ndarray:
        return np.clip((X - self.mean) / self.std, -Z_CLIP, Z_CLIP).astype(np.float32)
