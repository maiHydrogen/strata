"""Multi-agent orchestrator for text-to-IFC generation.

Adapted from Text2BIM's multi_agents_workflow.py.
Pipeline: User prompt -> PO Agent -> (optional Architect) -> Coder Agent -> exec() -> .ifc file
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


def execute_ifc_script(code: str) -> tuple:
    """Execute the generated IfcOpenShell script.
    
    Returns:
        (ifc_path: str | None, log: str, success: bool)
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # Inject the output directory into the code
    # Replace any hardcoded output path with our output directory
    output_path = os.path.join(OUTPUT_DIR, "building.ifc")
    
    # Make output dir available to the exec'd code
    modified_code = f'import os\nos.makedirs("{OUTPUT_DIR}", exist_ok=True)\n' + code
    # Replace common output paths
    modified_code = modified_code.replace(
        '"output/building.ifc"', f'"{output_path}"'
    ).replace(
        "'output/building.ifc'", f"'{output_path}'"
    )
    
    try:
        exec_globals = {"__builtins__": __builtins__}
        exec(modified_code, exec_globals)
        
        # Check if IFC file was generated
        if os.path.exists(output_path):
            return output_path, "Code executed successfully!", True
        
        # Check for any .ifc file in output dir
        for f in os.listdir(OUTPUT_DIR):
            if f.endswith(".ifc"):
                found_path = os.path.join(OUTPUT_DIR, f)
                return found_path, "Code executed successfully!", True
        
        return None, "Script ran but no .ifc file was generated.", False
    except Exception as e:
        error_msg = traceback.format_exc()
        return None, f"Execution error:\n{error_msg}", False


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
    
    return result
