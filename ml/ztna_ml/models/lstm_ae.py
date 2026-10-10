"""LSTM autoencoder over a peer's last `seq_len` windows (training side, needs torch).

Catches behaviour that is unremarkable in any single window but unusual over time for the
same peer, e.g. a browser user who keeps pulling large files for minutes.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch
from torch import nn

from ..datagen import Dataset
from ..normalize import Normalizer

SEQ_LEN = 10  # 5 minutes of 30 s windows


class LstmAE(nn.Module):
    def __init__(self, n_features: int, hidden: int = 32, latent: int = 12):
        super().__init__()
        self.encoder = nn.LSTM(n_features, hidden, batch_first=True)
        self.to_latent = nn.Linear(hidden, latent)
        self.from_latent = nn.Linear(latent, hidden)
        self.decoder = nn.LSTM(hidden, hidden, batch_first=True)
        self.out = nn.Linear(hidden, n_features)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        _, (h, _) = self.encoder(x)
        z = torch.relu(self.to_latent(h[-1]))
        seq = self.from_latent(z).unsqueeze(1).expand(-1, x.shape[1], -1).contiguous()
        dec, _ = self.decoder(seq)
        return self.out(dec)


def sequences(ds: Dataset, seq_len: int = SEQ_LEN) -> tuple[np.ndarray, np.ndarray]:
    """Per-peer sliding sequences ending at each window that has enough history.
    Returns (S: (n, seq_len, F), target index of the last window in ds)."""
    seqs, targets = [], []
    for peer in np.unique(ds.peers):
        idx = np.where(ds.peers == peer)[0]
        idx = idx[np.argsort(ds.starts[idx])]
        for i in range(seq_len - 1, len(idx)):
            seqs.append(ds.X[idx[i - seq_len + 1 : i + 1]])
            targets.append(idx[i])
    if not seqs:
        return np.zeros((0, seq_len, ds.X.shape[1]), np.float32), np.zeros(0, int)
    return np.stack(seqs).astype(np.float32), np.array(targets)


@dataclass
class TrainResult:
    model: LstmAE
    normalizer: Normalizer
    threshold: float
    history: list[tuple[float, float]]


def _norm(normalizer: Normalizer, S: np.ndarray) -> torch.Tensor:
    n, k, f = S.shape
    return torch.from_numpy(normalizer.transform(S.reshape(-1, f)).reshape(n, k, f))


def sequence_errors(model: LstmAE, normalizer: Normalizer, S: np.ndarray) -> np.ndarray:
    model.eval()
    out = []
    with torch.no_grad():
        for i in range(0, len(S), 2048):
            z = _norm(normalizer, S[i : i + 2048])
            out.append(((model(z) - z) ** 2).mean(dim=(1, 2)).numpy())
    return np.concatenate(out) if out else np.zeros(0)


def train(
    S_train: np.ndarray,
    S_val: np.ndarray,
    *,
    epochs: int = 60,
    batch_size: int = 128,
    lr: float = 2e-3,
    patience: int = 8,
    quantile: float = 0.995,
    seed: int = 0,
) -> TrainResult:
    torch.manual_seed(seed)
    normalizer = Normalizer.fit(S_train.reshape(-1, S_train.shape[-1]))
    zt, zv = _norm(normalizer, S_train), _norm(normalizer, S_val)
    model = LstmAE(S_train.shape[-1])
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.MSELoss()
    best, best_state, bad, history = float("inf"), None, 0, []
    for _ in range(epochs):
        model.train()
        perm = torch.randperm(len(zt))
        total = 0.0
        for i in range(0, len(zt), batch_size):
            batch = zt[perm[i : i + batch_size]]
            opt.zero_grad()
            loss = loss_fn(model(batch), batch)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            total += loss.item() * len(batch)
        model.eval()
        with torch.no_grad():
            val = loss_fn(model(zv), zv).item()
        history.append((total / len(zt), val))
        if val < best - 1e-5:
            best, bad = val, 0
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
            if bad >= patience:
                break
    model.load_state_dict(best_state)
    threshold = float(np.quantile(sequence_errors(model, normalizer, S_val), quantile))
    return TrainResult(model, normalizer, threshold, history)


def export_onnx(model: LstmAE, seq_len: int, n_features: int, path: str) -> None:
    model.eval()
    torch.onnx.export(
        model,
        torch.zeros(1, seq_len, n_features),
        path,
        input_names=["x"],
        output_names=["recon"],
        dynamic_axes={"x": {0: "batch"}, "recon": {0: "batch"}},
        opset_version=17,
        dynamo=False,
    )
