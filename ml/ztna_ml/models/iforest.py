"""IsolationForest baseline on the same window features."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.ensemble import IsolationForest

from ..normalize import Normalizer


@dataclass
class IForestModel:
    forest: IsolationForest
    normalizer: Normalizer
    threshold: float

    def scores(self, X: np.ndarray) -> np.ndarray:
        """Higher = more anomalous."""
        return -self.forest.score_samples(self.normalizer.transform(X))


def train(X_train: np.ndarray, X_val: np.ndarray, quantile: float = 0.995, seed: int = 0):
    normalizer = Normalizer.fit(X_train)
    forest = IsolationForest(n_estimators=300, random_state=seed, n_jobs=-1)
    forest.fit(normalizer.transform(X_train))
    model = IForestModel(forest, normalizer, 0.0)
    model.threshold = float(np.quantile(model.scores(X_val), quantile))
    return model
