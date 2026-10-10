"""Runtime scoring of exported autoencoders (ONNX), no torch needed at inference.

A model directory contains:
  model.onnx  - autoencoder over normalised features
  meta.json   - {"version", "kind", "features", "mean", "std", "threshold", "seq_len"?}

kind "dense_ae": input (batch, n_features), scores the latest window.
kind "lstm_ae":  input (batch, seq_len, n_features), scores the peer's last seq_len windows.
Both scorers take the peer's window history (oldest first, current last).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import numpy as np

from .features import FEATURES
from .normalize import Normalizer

log = logging.getLogger(__name__)


class Scorer(Protocol):
    version: str
    history_needed: int

    def score(self, history: Sequence[np.ndarray]) -> float: ...

    def explain(self, history: Sequence[np.ndarray], top: int = 3) -> list[str]: ...


class _OnnxScorer:
    history_needed = 1

    def __init__(self, model_dir: Path, meta: dict):
        import onnxruntime as ort

        if meta["features"] != FEATURES:
            raise ValueError("model was trained on a different feature set")
        self.version: str = meta["version"]
        self.normalizer = Normalizer(
            np.asarray(meta["mean"], dtype=np.float32), np.asarray(meta["std"], dtype=np.float32)
        )
        self.threshold = float(meta["threshold"])
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(model_dir / "model.onnx"), opts, providers=["CPUExecutionProvider"]
        )
        self.input_name = self.session.get_inputs()[0].name

    def _input(self, history: Sequence[np.ndarray]) -> np.ndarray:
        raise NotImplementedError

    def _errors(self, history: Sequence[np.ndarray]) -> np.ndarray:
        """Squared reconstruction error per feature."""
        x = self._input(history)
        recon = self.session.run(None, {self.input_name: x})[0]
        err = (x - recon) ** 2
        return err.reshape(-1, err.shape[-1]).mean(axis=0)

    def score(self, history: Sequence[np.ndarray]) -> float:
        """Reconstruction error relative to the threshold (>= 1 is anomalous)."""
        return float(self._errors(history).mean() / self.threshold)

    def explain(self, history: Sequence[np.ndarray], top: int = 3) -> list[str]:
        errors = self._errors(history)
        return [FEATURES[i] for i in np.argsort(errors)[::-1][:top]]


class DenseScorer(_OnnxScorer):
    def _input(self, history):
        return self.normalizer.transform(np.asarray(history[-1])[None, :])


class SequenceScorer(_OnnxScorer):
    def __init__(self, model_dir: Path, meta: dict):
        super().__init__(model_dir, meta)
        self.history_needed = int(meta["seq_len"])

    def _input(self, history):
        # history may be a deque (the worker keeps one per peer), which has no slicing
        seq = np.asarray(list(history)[-self.history_needed :])
        return self.normalizer.transform(seq)[None, :, :]


KINDS = {"dense_ae": DenseScorer, "lstm_ae": SequenceScorer}


def OnnxAutoencoder(model_dir: Path) -> _OnnxScorer:  # noqa: N802 - factory kept for callers
    meta = json.loads((Path(model_dir) / "meta.json").read_text())
    try:
        cls = KINDS[meta.get("kind", "dense_ae")]
    except KeyError as exc:
        raise ValueError(f"unknown model kind {meta.get('kind')!r}") from exc
    return cls(Path(model_dir), meta)


def load_scorer(path: str | Path) -> Scorer | None:
    model_dir = Path(path)
    if not (model_dir / "model.onnx").exists():
        log.warning("no model at %s; running rules only", model_dir)
        return None
    return OnnxAutoencoder(model_dir)
