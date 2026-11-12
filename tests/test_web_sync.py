"""The web API ships a copy of the STRATA code and knowledge graph (web/api/_lib) so Vercel can bundle it; it must match."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "web" / "api" / "_lib"
FIX = "run: cd web && npm run copy-strata"


def _files(base, pattern):
    return sorted(p.relative_to(base) for p in base.rglob(pattern) if "__pycache__" not in p.parts)


def test_web_copy_of_the_python_packages_is_current():
    for pkg in ("brief", "inference", "spatial"):
        src, copy = ROOT / "src" / pkg, LIB / "src" / pkg
        names = [p for p in _files(src, "*.*") if p.name != "library.py"]  # the plan library stays local
        assert names == _files(copy, "*.*"), f"{pkg}: {FIX}"
        for name in names:
            assert (src / name).read_bytes() == (copy / name).read_bytes(), f"{pkg}/{name} differs: {FIX}"


def test_web_copy_of_the_knowledge_graph_is_current():
    src, copy = ROOT / "data" / "knowledge_graph", LIB / "data" / "knowledge_graph"
    names = _files(src, "*.ttl")
    assert names == _files(copy, "*.ttl"), FIX
    for name in names:
        assert (src / name).read_bytes() == (copy / name).read_bytes(), f"{name} differs: {FIX}"
