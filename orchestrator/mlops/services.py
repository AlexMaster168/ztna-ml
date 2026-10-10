"""Model discovery and deployment.

Deployment is a Redis hash `ml:model` {version, mode} that the ML workers poll; they load
the artifact from their own read-only copy of MODELS_DIR.
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

import redis
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from audit.services import record

from .models import ModelStage, ModelVersion

log = logging.getLogger(__name__)

REDIS_KEY = "ml:model"
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
SUMMARY_KEYS = ["fpr", "precision", "recall", "f1", "roc_auc"]


def _summary(report: dict) -> dict:
    # "dense_ae" is the key used by reports written before LSTM support.
    ae = report.get("model") or report.get("dense_ae", {})
    base = report.get("isolation_forest", {})
    return {
        "model": {k: ae.get(k) for k in SUMMARY_KEYS},
        "baseline": {k: base.get(k) for k in SUMMARY_KEYS},
        "per_kind": ae.get("per_kind", {}),
        "train_windows": report.get("train_windows") or report.get("train_sequences"),
        "live_holdout": {
            k: (report.get("live_holdout") or {}).get(k)
            for k in ["normal_windows", "normal_fpr", "attack_windows", "attack_recall"]
        },
    }


def sync_models() -> None:
    """Upsert ModelVersion rows from MODELS_DIR/*/meta.json; flag vanished ones."""
    root = Path(settings.MODELS_DIR)
    seen = set()
    for meta_path in sorted(root.glob("*/meta.json")) if root.exists() else []:
        try:
            meta = json.loads(meta_path.read_text())
            version = meta["version"]
            if not VERSION_RE.match(version) or meta_path.parent.name != version:
                continue
            report_path = meta_path.parent / "report.json"
            report = json.loads(report_path.read_text()) if report_path.exists() else {}
            md_path = meta_path.parent / "report.md"
            ModelVersion.objects.update_or_create(
                version=version,
                defaults={
                    "kind": meta.get("kind", "unknown"),
                    "threshold": float(meta["threshold"]),
                    "metrics": _summary(report),
                    "report_md": md_path.read_text() if md_path.exists() else "",
                    "trained_at": datetime.fromisoformat(meta["created_at"])
                    if meta.get("created_at")
                    else None,
                    "missing": False,
                },
            )
            seen.add(version)
        except (OSError, ValueError, KeyError) as exc:
            log.warning("skipping %s: %s", meta_path, exc)
    ModelVersion.objects.exclude(version__in=seen).update(missing=True)


def _redis() -> redis.Redis:
    return redis.Redis.from_url(settings.REDIS_URL, socket_timeout=2)


def deploy(model: ModelVersion, stage: ModelStage, actor, request=None) -> ModelVersion:
    if stage == ModelStage.AVAILABLE:
        raise ValueError("use undeploy()")
    if model.missing:
        raise ValueError("model artifact is missing on disk")
    with transaction.atomic():
        now = timezone.now()
        ModelVersion.objects.exclude(pk=model.pk).exclude(stage=ModelStage.AVAILABLE).update(
            stage=ModelStage.AVAILABLE, stage_changed_at=now
        )
        model.stage = stage
        model.stage_changed_at = now
        model.save(update_fields=["stage", "stage_changed_at"])
        record(actor, f"model.{stage}", model, {"version": model.version}, request)
        transaction.on_commit(
            lambda: _redis().hset(REDIS_KEY, mapping={"version": model.version, "mode": stage})
        )
    return model


def undeploy(actor, request=None) -> None:
    with transaction.atomic():
        for m in ModelVersion.objects.exclude(stage=ModelStage.AVAILABLE):
            m.stage = ModelStage.AVAILABLE
            m.stage_changed_at = timezone.now()
            m.save(update_fields=["stage", "stage_changed_at"])
            record(actor, "model.undeploy", m, {"version": m.version}, request)
        transaction.on_commit(lambda: _redis().delete(REDIS_KEY))
