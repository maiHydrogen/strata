// Copy the STRATA Python code and data the API needs from the repo into api/_lib, mirroring the
// repo layout (src/..., data/knowledge_graph) so the modules find their files unchanged.
// Runs before `next dev` and `next build`; the copy is committed so Vercel can bundle it.
import { cpSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const web = join(dirname(fileURLToPath(import.meta.url)), "..");
const repo = join(web, "..");
const lib = join(web, "api", "_lib");
const skip = (p) => !p.includes("__pycache__") && !p.endsWith("library.py");
rmSync(lib, { recursive: true, force: true });
mkdirSync(join(lib, "src"), { recursive: true });
writeFileSync(join(lib, "src", "__init__.py"), "");
for (const pkg of ["brief", "inference", "spatial"]) {
  cpSync(join(repo, "src", pkg), join(lib, "src", pkg), { recursive: true, filter: skip });
}
cpSync(join(repo, "data", "knowledge_graph"), join(lib, "data", "knowledge_graph"), { recursive: true });
console.log("copied src/{brief,inference,spatial} and data/knowledge_graph into api/_lib");
