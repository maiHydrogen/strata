"""The Revit side of the workstation API, against a mock STRATA listener (no Revit needed): the build job,
file downloads, health, and the workstation API's CORS and access-code handling."""

import importlib.util
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from src import webapi as W

ROOT = Path(__file__).resolve().parents[1]


class MockRevit(BaseHTTPRequestHandler):
    """Stands in for revit/STRATA.extension/startup.py: writes a fake .rvt and .ifc beside the JSON."""
    fail = False
    calls = []

    def log_message(self, *a):
        pass

    def _json(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        self._json(200, {"ok": True, "revit": "Autodesk Revit 2027", "template": True, "busy": False})

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        MockRevit.calls.append(body)
        path = body["building_path"]
        if MockRevit.fail:
            return self._json(500, {"ok": False, "error": "RuntimeError: template has no column family",
                                    "report": {"warnings": ["x"]}})
        rvt, ifc = path.replace(".building.json", ".rvt"), path.replace(".building.json", ".ifc")
        Path(rvt).write_bytes(b"RVT")
        Path(ifc).write_text("ISO-10303-21;")
        self._json(200, {"ok": True, "report": {"walls": 12, "rvt": rvt, "ifc": ifc}, "text": "STRATA build complete",
                         "rvt": rvt, "ifc": ifc})


@pytest.fixture
def revit(monkeypatch, tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockRevit)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(W, "REVIT_URL", f"http://127.0.0.1:{server.server_address[1]}/strata")
    monkeypatch.setenv("STRATA_WORKDIR", str(tmp_path))
    MockRevit.fail, MockRevit.calls = False, []
    (tmp_path / "house.building.json").write_text(json.dumps({"schema_version": "0.5", "walls": []}))
    yield tmp_path
    server.shutdown()


def _wait(jid, timeout=10):
    t = time.time()
    while time.time() - t < timeout:
        st = W.job_status(jid)
        if st["state"] not in ("queued", "running"):
            return st
        time.sleep(0.05)
    raise AssertionError("revit job did not finish")


def test_paths_between_wsl_and_windows():
    assert W.windows_path("/mnt/c/Users/Public/Documents/STRATA/a.building.json") == \
        r"C:\Users\Public\Documents\STRATA\a.building.json"
    assert W.local_path(r"C:\Users\Public\Documents\STRATA\a.ifc") == "/mnt/c/Users/Public/Documents/STRATA/a.ifc"
    assert W.local_path("/tmp/x.ifc") == "/tmp/x.ifc" and W.windows_path("/tmp/x") == "/tmp/x"


def test_revit_job_builds_downloads_and_checks_the_ifc(revit, monkeypatch):
    monkeypatch.setattr(W, "ifc_view", lambda data, building: {"schema": "IFC4", "passed": True,
                                                                "checks": [{"name": "walls", "passed": True, "detail": "0/0"}],
                                                                "info": {"bytes": len(data)}})
    st = _wait(W.start_revit("house.building.json"))
    assert st["state"] == "complete", st
    assert st["files"] == {"rvt": "house.rvt", "ifc": "house.ifc"}
    assert st["ifc_check"]["passed"] and st["ifc_check"]["info"]["bytes"] == len("ISO-10303-21;")
    assert st["text"] == "STRATA build complete" and st["report"]["walls"] == 12
    assert MockRevit.calls == [{"building_path": str(revit / "house.building.json")}]
    assert W.safe_file("house.rvt") == str(revit / "house.rvt")


def test_revit_job_failure_carries_the_listener_error(revit):
    MockRevit.fail = True
    st = _wait(W.start_revit("house.building.json"))
    assert st["state"] == "failed" and "column family" in st["reason"] and st["report"] == {"warnings": ["x"]}


def test_revit_job_refuses_bad_names(revit):
    for bad in ("../etc/passwd", "house.json", "a b.building.json", ""):
        with pytest.raises(ValueError):
            W.start_revit(bad)
    with pytest.raises(FileNotFoundError):
        W.start_revit("missing.building.json")


def test_safe_file_allows_only_bare_model_files(revit):
    (revit / "notes.txt").write_text("x")
    assert W.safe_file("house.building.json")
    for bad in ("notes.txt", "../house.building.json", "/etc/passwd", "house.rvt", ""):
        assert W.safe_file(bad) is None


def test_revit_status_and_health(revit, monkeypatch):
    assert W.revit_status()["ok"] is True
    monkeypatch.setattr(W, "staad_available", lambda: True)
    h = W.health()
    assert h["workstation"] and h["staad"] and h["revit"] and h["revit_template"] and h["llm_model"]
    monkeypatch.setattr(W, "REVIT_URL", "http://127.0.0.1:9/strata")
    assert W.revit_status(timeout=0.5)["ok"] is False and W.health()["revit"] is False


# ---------------------------------------------------------------- the workstation API server

def _dev_api():
    spec = importlib.util.spec_from_file_location("dev_api_test", ROOT / "web" / "scripts" / "dev_api.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def api(revit, monkeypatch):
    monkeypatch.setenv("STRATA_ACCESS_CODE", "secret")
    monkeypatch.setattr(W, "staad_available", lambda: True)
    mod = _dev_api()
    server = ThreadingHTTPServer(("127.0.0.1", 0), mod.Router)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_api_health_needs_no_code_and_answers_cors(api, monkeypatch):
    r = httpx.get(f"{api}/api/health", headers={"Origin": "https://strata.vercel.app"})
    assert r.status_code == 200 and r.json()["workstation"] is True
    assert r.headers["access-control-allow-origin"] == "https://strata.vercel.app"
    pre = httpx.options(f"{api}/api/revit", headers={"Origin": "https://strata.vercel.app",
                                                    "Access-Control-Request-Headers": "x-strata-code"})
    assert pre.status_code == 204 and "X-Strata-Code" in pre.headers["access-control-allow-headers"]


def test_api_allowed_origins_limit_who_may_call(api, monkeypatch):
    monkeypatch.setenv("STRATA_ALLOWED_ORIGINS", "https://strata.vercel.app")
    assert httpx.options(f"{api}/api/brief", headers={"Origin": "https://evil.example"}).status_code == 403
    r = httpx.get(f"{api}/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_api_files_need_the_code(api):
    assert httpx.get(f"{api}/api/files?name=house.building.json").status_code == 401
    r = httpx.get(f"{api}/api/files?name=house.building.json", headers={"X-Strata-Code": "secret"})
    assert r.status_code == 200 and r.json()["schema_version"] == "0.5"
    assert "house.building.json" in r.headers["content-disposition"]
    assert httpx.get(f"{api}/api/files?name=../x.ifc", headers={"X-Strata-Code": "secret"}).status_code == 404


def test_api_revit_start_and_status(api, monkeypatch):
    monkeypatch.setattr(W, "ifc_view", lambda data, building: {"schema": "IFC4", "passed": True, "checks": [], "info": {}})
    h = {"X-Strata-Code": "secret"}
    assert httpx.post(f"{api}/api/revit", json={"action": "start", "building_file": "house.building.json"}).status_code == 401
    r = httpx.post(f"{api}/api/revit", json={"action": "start", "building_file": "house.building.json"}, headers=h)
    assert r.status_code == 200
    jid = r.json()["job"]
    st = _wait(jid)
    assert st["state"] == "complete"
    s2 = httpx.post(f"{api}/api/revit", json={"action": "status", "job": jid}, headers=h).json()
    assert s2["files"]["rvt"] == "house.rvt"
    bad = httpx.post(f"{api}/api/revit", json={"action": "start", "building_file": "nope.building.json"}, headers=h)
    assert bad.status_code == 404
