// Copy the STRATA Python packages the API needs from the repo (../src) into api/_lib,
// so the Vercel Python functions can import them. Runs before `next dev` and `next build`.
import { cpSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const web = join(dirname(fileURLToPath(import.meta.url)), "..");
const repo = join(web, "..");
const lib = join(web, "api", "_lib");
rmSync(lib, { recursive: true, force: true });
mkdirSync(join(lib, "src"), { recursive: true });
writeFileSync(join(lib, "src", "__init__.py"), "");
cpSync(join(repo, "src", "brief"), join(lib, "src", "brief"), {
  recursive: true,
  filter: (p) => !p.includes("__pycache__"),
});
console.log("copied src/brief into api/_lib");
