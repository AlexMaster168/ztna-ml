import re
import tempfile
from pathlib import Path

import polib
from django.utils import translation

from accounts.models import Role
from notify.services import render

ROOT = Path(__file__).resolve().parent.parent
PO = ROOT / "locale" / "uk" / "LC_MESSAGES" / "django.po"
MSG = re.compile(r'\b_\(\s*"((?:[^"\\]|\\.)*)"', re.S)


def test_every_message_has_a_ukrainian_translation():
    po = {e.msgid: e.msgstr for e in polib.pofile(str(PO))}
    missing = []
    for path in ROOT.glob("*/**/*.py"):
        if ".venv" in path.parts or "migrations" in path.parts or "tests" in path.parts:
            continue
        for msgid in MSG.findall(path.read_text(encoding="utf-8")):
            if not po.get(msgid):
                missing.append(f"{path.relative_to(ROOT)}: {msgid}")
    assert not missing, "untranslated:\n" + "\n".join(missing)


def test_compiled_catalog_is_up_to_date():
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp) / "django.mo"
        polib.pofile(str(PO)).save_as_mofile(str(fresh))
        assert fresh.read_bytes() == PO.with_suffix(".mo").read_bytes(), (
            "run: python scripts/compilemessages.py"
        )


def test_api_errors_follow_accept_language(make_client, gateway):
    admin = make_client(Role.ADMIN)
    body = {"name": "gw-x", "endpoint": "1.2.3.4:51820", "wg_subnet": "10.13.0.0/16"}
    uk = admin.post("/api/gateways/", body, HTTP_ACCEPT_LANGUAGE="uk").json()["detail"]
    en = admin.post("/api/gateways/", body, HTTP_ACCEPT_LANGUAGE="en").json()["detail"]
    assert "перетинається" in uk and "overlaps" in en


def test_notifications_render_in_configured_language(settings):
    settings.NOTIFY_LANGUAGE = "uk"
    text = render("peer.quarantined", {"peer": "laptop", "reason": "x"})
    assert text == "ZTNA: Вузол laptop поміщено в карантин: x"
    with translation.override("en"):  # the request language does not leak into it
        assert "карантин" in render("peer.quarantined", {"peer": "laptop", "reason": "x"})
