"""Arm A of the Phase 9 ablation: the Text2BIM-style baseline on the `strata_demo` branch.

The baseline is run as it is: a product-owner agent restates the request, an architect agent drafts
the floor plan, a coder agent writes an IfcOpenShell script, and the script is executed to give an
IFC file. Only the LLM call is swapped for STRATA's client so that all arms use the same model.

The baseline's code is read from the `strata_demo` branch with `git archive` into an ignored folder
(the branch is never checked out or changed). Its prompts are filled exactly as its orchestrator
fills them; the generated script runs in a separate Python process (with the baseline's own
restricted executor) so a hanging or crashing script cannot take the evaluation down.
"""

import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BRANCH = "strata_demo"
SRC = os.path.join(ROOT, "output", "runs", "_baseline_src")  # output/runs/ is git-ignored
SCRIPT_TIMEOUT = 180  # s

RUNNER = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from src.execution.orchestrator import execute_ifc_script, validate_ifc_file
code = open(sys.argv[2]).read()
path, log, ok = execute_ifc_script(code)
val = None
if ok:
    try:
        val = validate_ifc_file(path)
    except Exception as e:
        val = {"errors": [f"validation crashed: {e}"], "warnings": [], "counts": {}}
print("@@RESULT@@" + json.dumps({"ifc": path, "log": log[-4000:], "ok": ok, "validation": val}))
"""


def baseline_src():
    """The baseline's src/execution, extracted from the branch once."""
    if not os.path.exists(os.path.join(SRC, "src", "execution", "orchestrator.py")):
        os.makedirs(SRC, exist_ok=True)
        archive = subprocess.run(["git", "-C", ROOT, "archive", BRANCH, "src/__init__.py", "src/execution"],
                                 check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", SRC], input=archive, check=True)
    return SRC


def _prompt(name):
    with open(os.path.join(baseline_src(), "src", "execution", "prompts", name), encoding="utf-8") as f:
        return f.read()


def extract_code(response):
    """The baseline's own code extraction (orchestrator._extract_code)."""
    match = re.search(r'```(?:python|py)?\n(.*?)```', response, re.DOTALL)
    if match:
        return match.group(1).strip()
    return response.strip()


def _call(llm, agent, text, log):
    t = time.time()
    out = llm.complete_text(None, text) or ""
    log(f"    {agent} agent: {len(out)} chars in {time.time() - t:.0f} s")
    return out


def generate(task, llm, log=lambda *a: None, partial=None):
    """The three agents of the baseline's run_pipeline (use_architect=True, no chat history).
    `partial` (a dict, saved by the caller after each agent) keeps finished agents across a retry."""
    done = partial if partial is not None else {}
    if "po_output" not in done:
        done["po_output"] = _call(llm, "product owner", _prompt("po_prompt.txt").replace("<<task>>", task)
                                  .replace("<<chat_history>>", ""), log)
        yield done
    if "architect_output" not in done:
        done["architect_output"] = _call(llm, "architect", _prompt("architect_prompt.txt").replace("<<task>>", task), log)
        yield done
    if "code" not in done:
        coder_input = f"{done['po_output']}\n\nArchitectural Plan:\n{done['architect_output']}"
        done["code"] = extract_code(_call(llm, "coder", _prompt("coder_ifc_prompt.txt").replace("<<task>>", coder_input)
                                          .replace("<<chat_history>>", ""), log))
        yield done


def execute(code, out_dir):
    """Run the generated script with the baseline's executor; copy the IFC to out_dir/model.ifc."""
    src = baseline_src()
    os.makedirs(out_dir, exist_ok=True)
    code_path = os.path.join(out_dir, "script.py")
    with open(code_path, "w") as f:
        f.write(code)
    try:
        p = subprocess.run([sys.executable, "-c", RUNNER, src, code_path], cwd=src, capture_output=True,
                           text=True, timeout=SCRIPT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"ok": False, "ifc": None, "log": f"script ran over {SCRIPT_TIMEOUT} s", "validation": None}
    line = next((ln for ln in p.stdout.splitlines() if ln.startswith("@@RESULT@@")), None)
    if line is None:
        return {"ok": False, "ifc": None, "log": (p.stderr or p.stdout)[-4000:], "validation": None}
    r = json.loads(line[len("@@RESULT@@"):])
    if r["ok"] and r["ifc"]:
        dest = os.path.join(out_dir, "model.ifc")
        os.replace(r["ifc"], dest)
        r["ifc"] = dest
    return r


def cache_name(stem, llm):
    """One cache per model, so replies from two models never mix in one design (Gemini keeps the plain name)."""
    return f"{stem}.json" if getattr(llm, "name", "gemini") == "gemini" else \
        f"{stem}_{getattr(llm, 'model', 'llm').replace('/', '_')}.json"


def run_arm_a(task, llm, out_dir, refresh=False, log=lambda *a: None):
    """Generate (or reuse the cached agents' output) and execute. Returns the record for the scorer."""
    cache = os.path.join(out_dir, cache_name("agents", llm))
    t0, calls = time.time(), 0
    agents = {}
    if os.path.exists(cache) and not refresh:
        with open(cache) as f:
            agents = json.load(f)
    if "code" not in agents:
        os.makedirs(out_dir, exist_ok=True)
        spent = agents.get("seconds", 0)
        for agents in generate(task, llm, log, partial=agents):
            calls += 1
            agents["seconds"] = round(spent + time.time() - t0, 1)
            with open(cache, "w") as f:  # after each agent: a failed call does not lose the ones before it
                json.dump(agents, f, indent=1)
    t1 = time.time()
    r = execute(agents["code"], out_dir)
    r["llm_calls"] = 3
    r["new_llm_calls"] = calls
    r["seconds"] = round(agents.get("seconds", 0) + time.time() - t1, 1)
    return r
