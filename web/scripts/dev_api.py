"""Local stand-in for the Vercel Python runtime: serves api/brief.py and api/layout.py on http://127.0.0.1:8001.

Run from web/: npm run dev:api (reads GEMINI_API_KEY from the repo's .env). `next dev`
forwards /api/* here (see next.config.mjs).
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


ROUTES = {"/api/brief": load("brief"), "/api/layout": load("layout")}


class Router(ROUTES["/api/brief"]):
    def do_POST(self):  # noqa: N802
        target = ROUTES.get(self.path.rstrip("/"))
        if target is None:
            return self._send(404, {"error": "not found"})
        return target.do_POST(self)


if __name__ == "__main__":
    print("STRATA dev API on http://127.0.0.1:8001 (/api/brief, /api/layout)")
    ThreadingHTTPServer(("127.0.0.1", 8001), Router).serve_forever()
