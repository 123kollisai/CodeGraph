# main_app.py

import streamlit as st
import os
import shutil
import psutil
import time
import subprocess
import json
import importlib.util
import sys
import re

from utils.logger_config import get_logger
from core.workflow_engine import WorkflowEngine
from meta_agents.planner_agent import PlannerAgent
from meta_agents.workflow_file_generator_agent import WorkflowFileGeneratorAgent
from database.db_utils import setup_database, log_project_to_history

# --- Setup and Constants ---
logger = get_logger("MainApp")
APP_PORT = 8502
GENERATED_PROJECTS_DIR = "generated_projects"
WORKFLOWS_DIR = "project_workflows"

# --- Static Agent Definitions ---
# This dictionary defines the STATIC paths and data dependencies to your reliable agents.
STATIC_AGENTS = {
    "DeveloperAgent": {
        "description": "A developer agent that takes a user prompt and generates all necessary files for a complete Streamlit application.",
        "path": "agents.developer_agent.DeveloperAgent",
        "inputs": ["prompt"], "outputs": ["project_path"], "dependencies": []
    },
    "CodeReviewAgent": {
        "description": "A specialized agent that tests generated code, finds errors, and uses an LLM to fix them.",
        "path": "agents.code_review_agent.CodeReviewAgent",
        "inputs": ["project_path", "prompt"], "outputs": ["review_passed"], "dependencies": ["DeveloperAgent"]
    },
    "LaunchAgent": {
        "description": "A simple agent that runs the final application in a subprocess.",
        "path": "agents.launch_agent.LaunchAgent",
        "inputs": ["project_path", "app_port"], "outputs": ["process", "app_url"],
        "dependencies": ["DeveloperAgent"]
    }
}

# --- Helper Functions ---
def stop_running_app():
    if 'running_app_process' in st.session_state and st.session_state.running_app_process:
        pid = st.session_state.running_app_process.pid
        # *** THE FIX IS HERE: Check if the process still exists before trying to kill it. ***
        if psutil.pid_exists(pid):
            try:
                parent = psutil.Process(pid)
                for child in parent.children(recursive=True):
                    child.kill()
                parent.kill()
                st.success(f"Stopped the running application (PID: {pid}).")
            except psutil.NoSuchProcess:
                # This handles the case where the process disappears between the check and the kill.
                st.warning(f"Process with PID {pid} was not found, it may have already been terminated.")
        else:
            st.warning(f"Process with PID {pid} was already stopped.")
        
        # Always clean up the session state
        st.session_state.running_app_process = None
        st.session_state.app_url = None
        st.rerun()

def hydrate_plan(simple_plan: dict) -> dict:
    hydrated_steps = []
    # We now map agent names to their static definitions
    for agent_name in simple_plan.get("agent_sequence", []):
        if agent_name in STATIC_AGENTS:
            agent_def = STATIC_AGENTS[agent_name]
            hydrated_steps.append({
                "agent_name": agent_name,
                "path": agent_def["path"],
                "inputs": agent_def["inputs"],
                "outputs": agent_def["outputs"],
                "dependencies": agent_def["dependencies"]
            })
    return {"project_name": simple_plan.get("project_name", "default_project"), "plan": hydrated_steps}

# --- Page Configuration and Sidebar ---
st.set_page_config(page_title="Hybrid Agent Framework", page_icon="🤖", layout="wide")
if 'running_app_process' not in st.session_state: st.session_state.running_app_process = None
if 'app_url' not in st.session_state: st.session_state.app_url = None

with st.sidebar:
    st.title("🔑 Configuration")
    api_key = st.text_input("OpenAI API Key", type="password", help="Your API key is required.")
    if api_key: os.environ["OPENAI_API_KEY"] = api_key
    st.title("📦 Project Controls")
    if st.session_state.running_app_process:
        st.button("🔴 Stop Running App", on_click=stop_running_app, use_container_width=True)
    st.title("📊 Database")
    try:
        setup_database()
        st.success("Logging DB connection verified.")
    except Exception as e:
        st.error(f"DB connection failed: {e}")
        logger.error(f"DB connection failed: {e}", exc_info=True)

# --- Main UI ---
st.title("🤖 Hybrid Agent Framework")
st.markdown("Provide a goal, and the framework will dynamically create a workflow that uses a pre-defined team of static agents.")

prompt = st.text_area("Enter your high-level goal:", height=150, key="main_prompt",
                      placeholder="e.g., 'Build a simple checklist app'")

if st.button("🚀 Generate and Execute", disabled=(not api_key), use_container_width=True):
    if not prompt:
        st.warning("Please enter your goal.")
    else:
        if st.session_state.running_app_process: stop_running_app()
        workflow_file_path = ""
        # --- Phase 1: Dynamic Workflow Generation ---
        with st.status("Phase 1: Meta-Agents are building the workflow...", expanded=True) as status:
            try:
                status.write("🧠 **Planner Agent:** Designing plan...")
                planner = PlannerAgent()
                simple_plan_from_ai = planner.generate_plan(prompt)
                
                # We trust the AI for the project name, but enforce the correct agent sequence.
                corrected_plan_data = {
                    "project_name": simple_plan_from_ai.get("project_name", "DefaultProject"),
                    "agent_sequence": ["DeveloperAgent", "CodeReviewAgent", "LaunchAgent"]
                }
                logger.info(f"Enforcing correct agent sequence: {corrected_plan_data['agent_sequence']}")
                
                # The plan now gets hydrated with static paths and the correct sequence
                plan = hydrate_plan(corrected_plan_data)
                project_name = plan['project_name']

                if not project_name or not plan['plan']:
                    st.error("Planner returned an invalid plan.")
                    st.stop()
                status.write(f"✅ Plan created for: **{project_name}**")
                
                workflow_path_dir = os.path.join(WORKFLOWS_DIR, project_name)
                if os.path.exists(workflow_path_dir): shutil.rmtree(workflow_path_dir)
                os.makedirs(workflow_path_dir, exist_ok=True)

                status.write("📜 **Workflow Generator:** Assembling blueprint...")
                workflow_generator = WorkflowFileGeneratorAgent()
                workflow_content = workflow_generator.generate_workflow_file_content(plan)
                workflow_file_path = os.path.join(workflow_path_dir, "workflow.py")
                with open(workflow_file_path, "w") as f: f.write(workflow_content)
                status.write("✅ Workflow blueprint ready.")

                status.update(label="✅ Generation Phase Complete.", state="complete", expanded=False)
            except Exception as e:
                st.error(f"Workflow generation process failed: {e}")
                logger.error("Generation failed.", exc_info=True)
                status.update(label="🚨 Generation Failed!", state="error")
                st.stop()
        
        # --- Phase 2: Static Agent Execution ---
        with st.status(f"Phase 2: Executing '{project_name}' workflow...", expanded=True) as status:
            try:
                spec = importlib.util.spec_from_file_location("generated_workflow", workflow_file_path)
                generated_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(generated_module)
                dynamic_workflow = generated_module.workflow
                
                # The engine uses the workflow with static agent paths
                engine = WorkflowEngine(dynamic_workflow)
                # Pass the original prompt from the user input
                initial_inputs = {"prompt": prompt, "app_port": APP_PORT}
                final_outputs = engine.execute(initial_inputs, status_ui=status)
                
                logger.info("Workflow execution completed.")
                launch_info = final_outputs.get('launch', {})
                st.session_state.running_app_process = launch_info.get('process')
                st.session_state.app_url = launch_info.get('app_url')
                
                status.update(label="✅ Workflow Executed Successfully!", state="complete")
                st.rerun()
            except Exception as e:
                st.error(f"Workflow execution failed: {e}")
                logger.error("Execution failed.", exc_info=True)
                status.update(label="🚨 Execution Failed!", state="error")

if st.session_state.running_app_process and st.session_state.app_url:
    st.success("🎉 **Autonomous process complete! Your application is running.**")
    st.markdown(f"### 👉 Open your new app: [{st.session_state.app_url}]({st.session_state.app_url})")
