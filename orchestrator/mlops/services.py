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
from django.utils.translation import gettext as _

from audit.services import record
from notify.services import enqueue as notify

from .models import MLOpsSettings, ModelStage, ModelVersion

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
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            version = meta["version"]
            if not VERSION_RE.match(version) or meta_path.parent.name != version:
                continue
            report_path = meta_path.parent / "report.json"
            report = (
                json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
            )
            md_path = meta_path.parent / "report.md"
            ModelVersion.objects.update_or_create(
                version=version,
                defaults={
                    "kind": meta.get("kind", "unknown"),
                    "threshold": float(meta["threshold"]),
                    "metrics": _summary(report),
                    "report_md": md_path.read_text(encoding="utf-8") if md_path.exists() else "",
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


def _publish_deployment() -> None:
    """Write the full deployment (active + shadow) for the workers to follow."""
    slots = {
        stage: ModelVersion.objects.filter(stage=stage).values_list("version", flat=True).first()
        for stage in (ModelStage.ACTIVE, ModelStage.SHADOW)
    }
    r = _redis()
    pipe = r.pipeline()
    pipe.delete(REDIS_KEY)
    pipe.hset(
        REDIS_KEY,
        mapping={str(k): v or "" for k, v in slots.items()},
    )
    pipe.execute()


def deploy(model: ModelVersion, stage: ModelStage, actor, request=None) -> ModelVersion:
    """Put a model into a slot. One active and one shadow model can run side by side;
    the model previously in that slot is retired. AVAILABLE retires this model."""
    if model.missing and stage != ModelStage.AVAILABLE:
        raise ValueError(_("model artifact is missing on disk"))
    with transaction.atomic():
        now = timezone.now()
        if stage != ModelStage.AVAILABLE:
            ModelVersion.objects.filter(stage=stage).exclude(pk=model.pk).update(
                stage=ModelStage.AVAILABLE, stage_changed_at=now
            )
        was_shadow = model.stage == ModelStage.SHADOW
        model.stage = stage
        model.stage_changed_at = now
        model.save(update_fields=["stage", "stage_changed_at"])
        record(actor, f"model.{stage}", model, {"version": model.version}, request)

        def publish():
            if stage == ModelStage.SHADOW or (stage == ModelStage.ACTIVE and not was_shadow):
                # live statistics count from the moment a model starts serving
                _redis().delete(f"ml:stats:{model.version}")
            _publish_deployment()

        transaction.on_commit(publish)
    return model


def undeploy(actor, request=None) -> None:
    with transaction.atomic():
        for m in ModelVersion.objects.exclude(stage=ModelStage.AVAILABLE):
            m.stage = ModelStage.AVAILABLE
            m.stage_changed_at = timezone.now()
            m.save(update_fields=["stage", "stage_changed_at"])
            record(actor, "model.undeploy", m, {"version": m.version}, request)
        transaction.on_commit(_publish_deployment)


# --- live comparison, drift and the trainer -------------------------------------------

TRAINER_CONFIG = "ml:trainer:config"
TRAINER_STATUS = "ml:trainer:status"
TRAIN_REQUESTS = "ml:train:requests"
TRAIN_RESULTS = "ml:train:results"
DRIFT_LATEST = "ml:drift:latest"
SYSTEM_TRAINER = "system:trainer"
SYSTEM_PROMOTER = "system:auto-promote"


def _decode(h: dict) -> dict[str, str]:
    return {k.decode(): v.decode() for k, v in h.items()}


def live_stats(r: redis.Redis, version: str | None) -> dict | None:
    if not version:
        return None
    h = _decode(r.hgetall(f"ml:stats:{version}"))
    windows = int(h.get("windows", 0))
    anomalies = int(h.get("anomalies", 0))
    return {
        "version": version,
        "windows": windows,
        "anomalies": anomalies,
        "anomaly_rate": round(anomalies / windows, 4) if windows else None,
        "disagreements": int(h.get("disagreements", 0)),
    }


def live_status() -> dict:
    r = _redis()
    slot = lambda s: (  # noqa: E731
        ModelVersion.objects.filter(stage=s).values_list("version", flat=True).first()
    )
    workers = []
    for key in sorted(r.scan_iter("ml:worker:*")):
        h = _decode(r.hgetall(key))
        workers.append(
            {
                "name": key.decode().split(":", 2)[2],
                "active": h.get("active") or None,
                "shadow": h.get("shadow") or None,
                "shards": [int(x) for x in h.get("shards", "").split(",") if x],
                "seen_at": int(h.get("seen_at", 0)),
            }
        )
    drift = _decode(r.hgetall(DRIFT_LATEST))
    return {
        "active": live_stats(r, slot(ModelStage.ACTIVE)),
        "shadow": live_stats(r, slot(ModelStage.SHADOW)),
        "workers": workers,
        "drift": json.loads(drift["report"]) if drift.get("report") else None,
        "trainer": _decode(r.hgetall(TRAINER_STATUS)) or None,
    }


def push_trainer_config(cfg) -> None:
    _redis().hset(
        TRAINER_CONFIG,
        mapping={
            "auto_retrain": int(cfg.auto_retrain),
            "retrain_interval_hours": cfg.retrain_interval_hours,
            "drift_threshold": cfg.drift_threshold,
            "model_kind": cfg.model_kind,
            "train_real_minutes": cfg.train_real_minutes,
        },
    )


def request_retrain(actor, request=None) -> None:
    payload = {"by": str(getattr(actor, "username", actor)), "at": timezone.now().isoformat()}
    _redis().rpush(TRAIN_REQUESTS, json.dumps(payload))
    record(actor, "model.retrain_requested", MLOpsSettings.get(), payload, request)


def handle_training_result(fields: dict[str, str]) -> ModelVersion | None:
    """A trainer run finished: register the model and, if it passed the quality gates,
    deploy it as the shadow challenger."""
    sync_models()
    model = ModelVersion.objects.filter(version=fields.get("version", "")).first()
    if model is None:
        log.warning("training result for unknown model %s", fields.get("version"))
        return None
    passed = fields.get("passed") == "1"
    record(
        SYSTEM_TRAINER,
        "model.trained",
        model,
        {"passed": passed, **{k: fields.get(k, "") for k in ("reason", "trigger")}},
    )
    notify(
        "model.trained",
        "low" if passed else "medium",
        {
            "version": model.version,
            "trigger": fields.get("trigger", ""),
            "result": fields.get("reason", ""),
            "passed": passed,
        },
    )
    if passed and MLOpsSettings.get().auto_shadow:
        deploy(model, ModelStage.SHADOW, SYSTEM_TRAINER)
    return model


def maybe_promote() -> ModelVersion | None:
    """Promote the challenger once it has scored enough live windows with an anomaly
    rate below the cap and not above the champion's."""
    cfg = MLOpsSettings.get()
    if not cfg.auto_promote:
        return None
    shadow = ModelVersion.objects.filter(stage=ModelStage.SHADOW).first()
    if shadow is None or shadow.missing:
        return None
    r = _redis()
    s = live_stats(r, shadow.version)
    if not s or s["windows"] < cfg.promote_min_windows or s["anomaly_rate"] is None:
        return None
    if s["anomaly_rate"] > cfg.promote_max_rate:
        return None
    active = ModelVersion.objects.filter(stage=ModelStage.ACTIVE).first()
    a = live_stats(r, active.version) if active else None
    if a and a["anomaly_rate"] is not None and s["anomaly_rate"] > a["anomaly_rate"] * 1.1 + 0.005:
        return None
    log.info("promoting challenger %s (rate %s)", shadow.version, s["anomaly_rate"])
    with transaction.atomic():
        notify("model.promoted", "medium", {"version": shadow.version, **s})
        return deploy(shadow, ModelStage.ACTIVE, SYSTEM_PROMOTER)
