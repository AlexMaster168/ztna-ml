"""Compile locale/*/LC_MESSAGES/django.po to .mo with polib (no GNU gettext needed).

    python scripts/compilemessages.py

The .mo files are committed; tests/test_i18n.py fails when they are out of date.
"""

from pathlib import Path

import polib

ROOT = Path(__file__).resolve().parent.parent


def compile_all() -> list[Path]:
    out = []
    for po_path in sorted(ROOT.glob("locale/*/LC_MESSAGES/*.po")):
        mo_path = po_path.with_suffix(".mo")
        polib.pofile(str(po_path)).save_as_mofile(str(mo_path))
        out.append(mo_path)
    return out


if __name__ == "__main__":
    for path in compile_all():
        print(f"compiled {path.relative_to(ROOT)}")
