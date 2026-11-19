"""The STRATA workstation API: serves web/api/*.py on http://127.0.0.1:8001.

Run from web/: npm run dev:api (reads the LLM keys and STRATA_ACCESS_CODE from the repo's .env).
Locally `next dev` forwards /api/* here (see next.config.mjs). For the hosted demo, ngrok publishes this
port and the Vercel build calls it straight from the browser (NEXT_PUBLIC_STRATA_WORKER_URL), so it
answers CORS preflights; STRATA_ALLOWED_ORIGINS (comma-separated) limits which sites may call it.
"""

import importlib.util
import os
from http.server import ThreadingHTTPServer

WEB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(WEB, "..", ".env"))
except ImportError:
    pass

def load(name):
    spec = importlib.util.spec_from_file_location(f"{name}_api", os.path.join(WEB, "api", f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.handler


ROUTES = {f"/api/{n}": load(n) for n in ("brief", "layout", "spec", "structure", "ifc", "revit")}
GET_ROUTES = {f"/api/{n}": load(n) for n in ("health", "files")}
HEADERS = "Content-Type, X-Strata-Code, ngrok-skip-browser-warning"


class Router(ROUTES["/api/brief"]):
    def _origin(self):
        origin = self.headers.get("Origin")
        allowed = [o.strip().rstrip("/") for o in os.environ.get("STRATA_ALLOWED_ORIGINS", "").split(",") if o.strip()]
        if not origin:
            return None
        return origin if not allowed or origin.rstrip("/") in allowed else None

    def end_headers(self):
        origin = self._origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", HEADERS)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Expose-Headers", "Content-Disposition")
        super().end_headers()

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204 if self._origin() else 403)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):  # noqa: N802
        target = ROUTES.get(self.path.split("?")[0].rstrip("/"))
        if target is None:
            return self._send(404, {"error": "not found"})
        return target.do_POST(self)

    def do_GET(self):  # noqa: N802
        target = GET_ROUTES.get(self.path.split("?")[0].rstrip("/"))
        if target is None:
            return self._send(404, {"error": "not found"})
        return target.do_GET(self)


if __name__ == "__main__":
    print("STRATA API on http://127.0.0.1:8001 (" + ", ".join([*ROUTES, *GET_ROUTES]) + ")")
    ThreadingHTTPServer(("127.0.0.1", 8001), Router).serve_forever()
