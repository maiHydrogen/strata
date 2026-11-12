"""The web API ships a copy of src/brief (web/api/_lib) so Vercel can bundle it; it must match."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_web_copy_of_brief_package_is_current():
    src, copy = ROOT / "src" / "brief", ROOT / "web" / "api" / "_lib" / "src" / "brief"
    names = sorted(p.name for p in src.glob("*.py"))
    assert names == sorted(p.name for p in copy.glob("*.py")), "run: cd web && npm run copy-strata"
    for name in names:
        assert (src / name).read_text() == (copy / name).read_text(), f"{name} differs: cd web && npm run copy-strata"
