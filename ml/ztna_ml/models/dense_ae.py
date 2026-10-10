"""Dense autoencoder over a single window's feature vector (training side, needs torch)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch
from torch import nn

from ..normalize import Normalizer


class DenseAE(nn.Module):
    def __init__(self, n_features: int, hidden: int = 32, bottleneck: int = 6):
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Linear(n_features, hidden),
            nn.ReLU(),
            nn.Linear(hidden, bottleneck),
            nn.ReLU(),
        )
        self.decoder = nn.Sequential(
            nn.Linear(bottleneck, hidden),
            nn.ReLU(),
            nn.Linear(hidden, n_features),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.decoder(self.encoder(x))


@dataclass
class TrainResult:
    model: DenseAE
    normalizer: Normalizer
    threshold: float
    history: list[tuple[float, float]]  # (train_loss, val_loss) per epoch


def window_errors(model: DenseAE, normalizer: Normalizer, X: np.ndarray) -> np.ndarray:
    model.eval()
    with torch.no_grad():
        z = torch.from_numpy(normalizer.transform(X))
        return ((model(z) - z) ** 2).mean(dim=1).numpy()


def train(
    X_train: np.ndarray,
    X_val: np.ndarray,
    *,
    epochs: int = 200,
    batch_size: int = 256,
    lr: float = 1e-3,
    patience: int = 15,
    quantile: float = 0.995,
    seed: int = 0,
) -> TrainResult:
    """Fit on normal windows only; the threshold is a high quantile of validation error."""
    torch.manual_seed(seed)
    normalizer = Normalizer.fit(X_train)
    zt = torch.from_numpy(normalizer.transform(X_train))
    zv = torch.from_numpy(normalizer.transform(X_val))
    model = DenseAE(X_train.shape[1])
    opt = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-5)
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
    threshold = float(np.quantile(window_errors(model, normalizer, X_val), quantile))
    return TrainResult(model, normalizer, threshold, history)


def export_onnx(model: DenseAE, n_features: int, path: str) -> None:
    model.eval()
    torch.onnx.export(
        model,
        torch.zeros(1, n_features),
        path,
        input_names=["x"],
        output_names=["recon"],
        dynamic_axes={"x": {0: "batch"}, "recon": {0: "batch"}},
        opset_version=17,
        dynamo=False,
    )
