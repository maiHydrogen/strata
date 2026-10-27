"""Multi-agent orchestrator for text-to-IFC generation.

Adapted from Text2BIM's multi_agents_workflow.py.
Pipeline: User prompt -> PO Agent -> (optional Architect) -> Coder Agent -> IfcBuilder script -> .ifc file -> checks
"""

import os
import re
import traceback

PROMPT_DIR = os.path.join(os.path.dirname(__file__), "prompts")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "output")


def _load_prompt(filename: str) -> str:
    with open(os.path.join(PROMPT_DIR, filename), "r", encoding="utf-8") as f:
        return f.read()


def _extract_code(response: str) -> str:
    """Extract Python code block from LLM response."""
    # Try to find ```python or ```py blocks
    match = re.search(r'```(?:python|py)?\n(.*?)```', response, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Fallback: if the whole response looks like code (starts with import), use it
    if response.strip().startswith("import ") or response.strip().startswith("from "):
        return response.strip()
    return response.strip()


def run_po_agent(user_prompt: str, model: str, chat_history: str = "") -> str:
    """Run the Product Owner agent to decompose user requirements."""
    from src.execution.llm_clients import call_llm

    po_template = _load_prompt("po_prompt.txt")
    prompt = po_template.replace("<<task>>", user_prompt)
    prompt = prompt.replace("<<chat_history>>", chat_history.strip() + "\n" if chat_history else "")
    return call_llm(prompt, model=model)


def run_architect_agent(query: str, model: str) -> str:
    """Run the Architect agent for detailed floor plan design."""
    from src.execution.llm_clients import call_llm

    architect_template = _load_prompt("architect_prompt.txt")
    prompt = architect_template.replace("<<task>>", query)
    return call_llm(prompt, model=model)


def run_coder_agent(po_output: str, model: str, chat_history: str = "") -> str:
    """Run the Coder agent to generate IfcOpenShell Python script."""
    from src.execution.llm_clients import call_llm

    coder_template = _load_prompt("coder_ifc_prompt.txt")
    prompt = coder_template.replace("<<task>>", po_output)
    prompt = prompt.replace("<<chat_history>>", chat_history.strip() + "\n" if chat_history else "")
    response = call_llm(prompt, model=model)
    return _extract_code(response)


ALLOWED_IMPORTS = {"math"}
SAFE_BUILTINS = (
    "abs all any bool dict enumerate float int len list max min pow divmod range reversed round "
    "set sorted str sum tuple zip isinstance ValueError Exception True False None print"
).split()


def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split(".")[0] not in ALLOWED_IMPORTS:
        raise ImportError(f"Import of '{name}' is not allowed; the script may only import {sorted(ALLOWED_IMPORTS)}")
    return __import__(name, globals, locals, fromlist, level)


def execute_ifc_script(code: str) -> tuple:
    """Execute a generated IfcBuilder script and write output/building.ifc.

    The script runs with IfcBuilder, m(), math and OUTPUT_PATH pre-defined and
    a restricted import hook. This is a guard against accidents, not a security
    sandbox: only run scripts from LLMs you trust.

    Returns:
        (ifc_path: str | None, log: str, success: bool)
    """
    import builtins
    import math

    from src.execution.ifc_builder import IfcBuilder

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, "building.ifc")
    if os.path.exists(output_path):
        os.remove(output_path)  # never report a stale file as this run's result

    builders = []

    class _TrackedBuilder(IfcBuilder):
        """Records instances and pins the output location."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            builders.append(self)

        def save(self, path=None):
            return super().save(output_path)

    safe_builtins = {name: getattr(builtins, name) for name in SAFE_BUILTINS}
    safe_builtins["__import__"] = _restricted_import
    exec_globals = {
        "__builtins__": safe_builtins,
        "IfcBuilder": _TrackedBuilder,
        "OUTPUT_PATH": output_path,
        "math": math,
        "m": lambda mm_value: mm_value / 1000.0,
    }

    try:
        exec(code, exec_globals)
        if not os.path.exists(output_path) and builders:
            builders[-1].save()  # script forgot to save; write the last model built
        if not os.path.exists(output_path):
            return None, "Script ran but no .ifc file was generated.", False
        log = "Code executed successfully!"
        warnings = [w for b in builders for w in b.warnings]
        if warnings:
            log += "\nBuilder adjustments:\n- " + "\n- ".join(warnings)
        return output_path, log, True
    except Exception:
        return None, f"Execution error:\n{traceback.format_exc()}", False


def validate_ifc_file(ifc_path: str) -> dict:
    """Run Revit-readiness checks; returns {"errors", "warnings", "counts"}."""
    from src.execution.ifc_checks import check_ifc

    return check_ifc(ifc_path)


def run_pipeline(user_prompt: str, model: str = "gpt",
                 use_architect: bool = True,
                 chat_history: str = "") -> dict:
    """Run the full text-to-IFC pipeline.
    
    Args:
        user_prompt: Natural language building description
        model: LLM provider - "gpt", "claude", "gemini", or "mistral"
        use_architect: Whether to use the architect agent for floor plan design
        chat_history: Previous conversation context
    
    Returns:
        dict with keys: po_output, architect_output, code, ifc_path, log, success
    """
    result = {
        "po_output": "",
        "architect_output": "",
        "code": "",
        "ifc_path": None,
        "log": "",
        "validation": None,
        "success": False
    }
    
    # Step 1: Product Owner decomposes the prompt
    result["po_output"] = run_po_agent(user_prompt, model, chat_history)
    
    # Step 2 (optional): Architect generates detailed floor plan
    if use_architect:
        result["architect_output"] = run_architect_agent(user_prompt, model)
        # Feed architect output to coder along with PO output
        coder_input = f"{result['po_output']}\n\nArchitectural Plan:\n{result['architect_output']}"
    else:
        coder_input = result["po_output"]
    
    # Step 3: Coder generates IfcOpenShell script
    result["code"] = run_coder_agent(coder_input, model, chat_history)
    
    # Step 4: Execute the generated script
    ifc_path, log, success = execute_ifc_script(result["code"])
    result["ifc_path"] = ifc_path
    result["log"] = log
    result["success"] = success
    if success:
        result["validation"] = validate_ifc_file(ifc_path)
    
    return result
