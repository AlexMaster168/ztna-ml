import json
from unittest import mock

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
    with mock.patch.object(services, "_redis") as r:
        yield r.return_value


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


def test_deploy_is_exclusive_and_published(
    models_dir, db, fake_redis, django_capture_on_commit_callbacks
):
    write_model(models_dir, "a")
    write_model(models_dir, "b")
    services.sync_models()
    a, b = ModelVersion.objects.get(version="a"), ModelVersion.objects.get(version="b")
    with django_capture_on_commit_callbacks(execute=True):
        services.deploy(a, ModelStage.ACTIVE, actor="t")
    with django_capture_on_commit_callbacks(execute=True):
        services.deploy(b, ModelStage.SHADOW, actor="t")
    a.refresh_from_db()
    assert a.stage == ModelStage.AVAILABLE
    fake_redis.hset.assert_called_with(
        services.REDIS_KEY, mapping={"version": "b", "mode": "shadow"}
    )
    with django_capture_on_commit_callbacks(execute=True):
        services.undeploy(actor="t")
    fake_redis.delete.assert_called_with(services.REDIS_KEY)
    assert not ModelVersion.objects.exclude(stage=ModelStage.AVAILABLE).exists()


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
