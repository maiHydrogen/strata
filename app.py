"""STRATA Prototype — Text to BIM Chat Interface.

Run with: streamlit run app.py
"""

import streamlit as st
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

from src.execution.orchestrator import run_po_agent, run_architect_agent, run_coder_agent, execute_ifc_script, validate_ifc_file

# Page config
st.set_page_config(
    page_title="STRATA — Text to BIM",
    page_icon="🏗️",
    layout="wide"
)

# --- Sidebar ---
with st.sidebar:
    st.header("⚙️ Configuration")
    
    model_choice = st.selectbox(
        "LLM Provider",
        ["gpt", "claude", "gemini", "mistral"],
        index=0,
        help="Select the LLM provider for the agents"
    )
    
    use_architect = st.checkbox("Use Architect Agent", value=True,
        help="Enable the architect agent for detailed floor plan design before code generation")
    
    st.divider()
    st.subheader("🔑 API Keys")
    st.caption("Set in .env file or enter below (session only)")
    
    api_key_input = st.text_input(
        f"API Key for {model_choice}",
        type="password",
        help="Overrides the .env file for this session"
    )
    
    if api_key_input:
        key_map = {
            "gpt": "OPENAI_API_KEY",
            "claude": "ANTHROPIC_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "mistral": "MISTRAL_API_KEY"
        }
        os.environ[key_map.get(model_choice, "")] = api_key_input
    
    st.divider()
    st.caption("STRATA: Automated Extraction of Engineering Intent from Text for Parametric and Standard-Compliant BIM")

# --- Main ---
st.title("🏗️ STRATA — Text to BIM")
st.caption("Describe a building in natural language → Get a 3D IFC model")

# Chat state
if "messages" not in st.session_state:
    st.session_state.messages = []
if "ifc_path" not in st.session_state:
    st.session_state.ifc_path = None
if "generated_code" not in st.session_state:
    st.session_state.generated_code = None

# Display chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Chat input
if prompt := st.chat_input("Describe a building... (e.g., 'Create a single-story rectangular building with 3 rooms')"):
    # Add user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    
    # Process
    with st.chat_message("assistant"):
        # Phase 1: Product Owner
        with st.status("🏗️ Product Owner analyzing requirements...", expanded=True) as status:
            try:
                po_output = run_po_agent(prompt, model=model_choice)
                st.markdown("**Product Owner's Specification:**")
                st.markdown(po_output)
                status.update(label="✅ Requirements analyzed", state="complete")
            except Exception as e:
                st.error(f"PO Agent failed: {e}")
                status.update(label="❌ PO Agent failed", state="error")
                st.stop()
        
        # Phase 2 (optional): Architect
        architect_output = ""
        if use_architect:
            with st.status("📐 Architect designing floor plan...", expanded=True) as status:
                try:
                    architect_output = run_architect_agent(prompt, model=model_choice)
                    st.markdown("**Architect's Design:**")
                    st.markdown(architect_output)
                    status.update(label="✅ Floor plan designed", state="complete")
                except Exception as e:
                    st.warning(f"Architect Agent failed (continuing without): {e}")
                    status.update(label="⚠️ Architect skipped", state="complete")
        
        # Phase 3: Coder
        with st.status("💻 Coder generating IFC script...", expanded=True) as status:
            try:
                coder_input = po_output
                if architect_output:
                    coder_input = f"{po_output}\n\nArchitectural Plan:\n{architect_output}"
                code = run_coder_agent(coder_input, model=model_choice)
                st.markdown("**Generated IfcOpenShell Script:**")
                st.code(code, language="python")
                st.session_state.generated_code = code
                status.update(label="✅ Script generated", state="complete")
            except Exception as e:
                st.error(f"Coder Agent failed: {e}")
                status.update(label="❌ Coder Agent failed", state="error")
                st.stop()
        
        # Phase 4: Execute
        with st.status("⚡ Building IFC model...", expanded=True) as status:
            ifc_path, log, success = execute_ifc_script(code)
            if success:
                st.success(f"✅ Model generated successfully!")
                st.session_state.ifc_path = ifc_path
                if "Builder adjustments" in log:
                    st.warning(log)
                checks = validate_ifc_file(ifc_path)
                counts = ", ".join(f"{n} {k[3:]}" for k, n in checks["counts"].items() if n)
                st.caption(f"Model contains: {counts}")
                if checks["errors"]:
                    st.error("Revit-readiness check found problems:\n\n" + "\n".join(f"- {e}" for e in checks["errors"]))
                else:
                    st.success("✅ Revit-readiness checks passed")
                for w in checks["warnings"]:
                    st.warning(w)
                status.update(label="✅ IFC model built", state="complete")
            else:
                st.error(f"❌ Execution failed:")
                st.code(log)
                status.update(label="❌ Build failed", state="error")
        
        # Summary message
        if success:
            summary = f"""**Building model generated successfully!** 🎉

**Pipeline Summary:**
- Product Owner decomposed requirements into architectural spec
{"- Architect designed detailed floor plan" if use_architect else ""}
- Coder generated IfcOpenShell Python script
- Script executed and produced `building.ifc`

Use the buttons below to download or open the IFC file."""
        else:
            summary = f"Generation failed. Check the error log above."
        
        st.session_state.messages.append({"role": "assistant", "content": summary})

# --- Action buttons ---
if st.session_state.ifc_path and os.path.exists(st.session_state.ifc_path):
    st.divider()
    col1, col2, col3 = st.columns(3)
    
    with col1:
        with open(st.session_state.ifc_path, "rb") as f:
            st.download_button(
                "📥 Download IFC File",
                f,
                file_name="building.ifc",
                mime="application/x-step",
                use_container_width=True
            )
    
    with col2:
        if st.button("🚀 Open in Default Viewer", use_container_width=True):
            try:
                os.startfile(st.session_state.ifc_path)
            except AttributeError:  # not on Windows (e.g. WSL): no default-viewer hook
                st.info(f"Open this file manually: {st.session_state.ifc_path}")
    
    with col3:
        if st.session_state.generated_code:
            st.download_button(
                "📄 Download Script",
                st.session_state.generated_code,
                file_name="generate_building.py",
                mime="text/x-python",
                use_container_width=True
            )

# --- Footer ---
st.divider()
st.caption("STRATA Prototype | IFC models can be opened in Revit, BIMvision, or Bonsai (BlenderBIM)")
