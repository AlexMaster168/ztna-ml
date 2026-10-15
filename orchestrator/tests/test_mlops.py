import json
from unittest import mock

import fakeredis
import pytest

from accounts.models import Role
from mlops import services
from mlops.models import ModelStage, ModelVersion


def write_model(root, version, threshold=0.1):
    d = root / version
    d.mkdir()
    (d / "meta.json").write_text(
        json.dumps(
            {
                "version": version,
                "kind": "dense_ae",
                "threshold": threshold,
                "created_at": "2026-10-10T12:00:00+00:00",
            }
        )
    )
    (d / "report.json").write_text(
        json.dumps(
            {
                "dense_ae": {"recall": 0.8, "fpr": 0.004, "per_kind": {}},
                "isolation_forest": {"recall": 0.4},
            }
        )
    )
    (d / "report.md").write_text("# report")


@pytest.fixture
def models_dir(tmp_path, settings):
    settings.MODELS_DIR = str(tmp_path)
    return tmp_path


@pytest.fixture
def fake_redis():
    r = fakeredis.FakeRedis()
    with mock.patch.object(services, "_redis", return_value=r):
        yield r


def test_sync_discovers_and_flags_missing(models_dir, db):
    write_model(models_dir, "dense-ae-1")
    write_model(models_dir, "dense-ae-2")
    (models_dir / "junk").mkdir()
    (models_dir / "junk" / "meta.json").write_text("{not json")
    services.sync_models()
    assert set(ModelVersion.objects.values_list("version", flat=True)) == {
        "dense-ae-1",
        "dense-ae-2",
    }
    m = ModelVersion.objects.get(version="dense-ae-1")
    assert m.metrics["model"]["recall"] == 0.8 and m.metrics["baseline"]["recall"] == 0.4
    (models_dir / "dense-ae-2" / "meta.json").unlink()
    services.sync_models()
    assert ModelVersion.objects.get(version="dense-ae-2").missing


def test_meta_version_must_match_directory(models_dir, db):
    write_model(models_dir, "dense-ae-1")
    (models_dir / "renamed").mkdir()
    (models_dir / "renamed" / "meta.json").write_text(
        (models_dir / "dense-ae-1" / "meta.json").read_text()
    )
    services.sync_models()
    assert ModelVersion.objects.count() == 1


def deployment(r):
    return {k.decode(): v.decode() for k, v in r.hgetall(services.REDIS_KEY).items()}


def test_active_and_shadow_run_side_by_side(
    models_dir, db, fake_redis, django_capture_on_commit_callbacks
):
    for v in "abc":
        write_model(models_dir, v)
    services.sync_models()
    a, b, c = (ModelVersion.objects.get(version=v) for v in "abc")
    with django_capture_on_commit_callbacks(execute=True):
        services.deploy(a, ModelStage.ACTIVE, actor="t")
    with django_capture_on_commit_callbacks(execute=True):
        services.deploy(b, ModelStage.SHADOW, actor="t")
    assert deployment(fake_redis) == {"active": "a", "shadow": "b"}
    with django_capture_on_commit_callbacks(execute=True):
        services.deploy(c, ModelStage.SHADOW, actor="t")  # replaces the challenger only
    b.refresh_from_db()
    assert b.stage == ModelStage.AVAILABLE
    assert deployment(fake_redis) == {"active": "a", "shadow": "c"}
    with django_capture_on_commit_callbacks(execute=True):
        services.deploy(c, ModelStage.ACTIVE, actor="t")  # promotion
    assert deployment(fake_redis) == {"active": "c", "shadow": ""}
    with django_capture_on_commit_callbacks(execute=True):
        services.undeploy(actor="t")
    assert deployment(fake_redis) == {"active": "", "shadow": ""}
    assert not ModelVersion.objects.exclude(stage=ModelStage.AVAILABLE).exists()


def test_training_result_deploys_challenger_and_auto_promotion(
    models_dir, db, fake_redis, django_capture_on_commit_callbacks
):
    from mlops.models import MLOpsSettings

    write_model(models_dir, "old")
    write_model(models_dir, "new")
    services.sync_models()
    with django_capture_on_commit_callbacks(execute=True):
        services.deploy(ModelVersion.objects.get(version="old"), ModelStage.ACTIVE, actor="t")
    with django_capture_on_commit_callbacks(execute=True):
        services.handle_training_result({"version": "failed-gates", "passed": "1"})  # unknown
        services.handle_training_result({"version": "new", "passed": "1", "trigger": "drift"})
    assert ModelVersion.objects.get(version="new").stage == ModelStage.SHADOW

    cfg = MLOpsSettings.get()
    cfg.auto_promote, cfg.promote_min_windows, cfg.promote_max_rate = True, 100, 0.05
    cfg.save()
    fake_redis.hset("ml:stats:old", mapping={"windows": 1000, "anomalies": 30})
    fake_redis.hset("ml:stats:new", mapping={"windows": 50, "anomalies": 0})
    assert services.maybe_promote() is None  # not enough evidence yet
    fake_redis.hset("ml:stats:new", mapping={"windows": 400, "anomalies": 20})
    assert services.maybe_promote() is None  # 5% > champion's 3%
    fake_redis.hset("ml:stats:new", mapping={"windows": 400, "anomalies": 8})
    with django_capture_on_commit_callbacks(execute=True):
        assert services.maybe_promote().version == "new"
    assert deployment(fake_redis) == {"active": "new", "shadow": ""}


def test_mlops_settings_and_retrain_api(make_client, fake_redis):
    admin, viewer = make_client(Role.ADMIN), make_client(Role.VIEWER)
    assert viewer.patch("/api/settings/mlops", {"auto_promote": True}).status_code == 403
    resp = admin.patch("/api/settings/mlops", {"retrain_interval_hours": 6}, format="json")
    assert resp.status_code == 200 and resp.json()["retrain_interval_hours"] == 6
    assert fake_redis.hget(services.TRAINER_CONFIG, "retrain_interval_hours") == b"6"
    assert (
        admin.patch("/api/settings/mlops", {"promote_max_rate": 2}, format="json").status_code
        == 400
    )
    assert viewer.post("/api/models/retrain/").status_code == 403
    assert admin.post("/api/models/retrain/").status_code == 202
    assert fake_redis.llen(services.TRAIN_REQUESTS) == 1
    fake_redis.hset("ml:worker:w1", mapping={"active": "x", "shadow": "", "shards": "0,3"})
    live = viewer.get("/api/models/live/").json()
    assert live["workers"][0]["shards"] == [0, 3]


def test_models_api_permissions(models_dir, make_client, fake_redis):
    write_model(models_dir, "a")
    viewer, admin = make_client(Role.VIEWER), make_client(Role.ADMIN)
    models = viewer.get("/api/models/").json()["results"]
    assert models[0]["version"] == "a"
    mid = models[0]["id"]
    assert viewer.get(f"/api/models/{mid}/").json()["report_md"] == "# report"
    assert viewer.post(f"/api/models/{mid}/deploy/", {"stage": "active"}).status_code == 403
    assert admin.post(f"/api/models/{mid}/deploy/", {"stage": "bogus"}).status_code == 400
    assert admin.post(f"/api/models/{mid}/deploy/", {"stage": "active"}).json()["stage"] == "active"
    assert admin.post("/api/models/undeploy/").status_code == 204
