# agents/code_review_agent.py
import os
import json
import time
import subprocess
from openai import OpenAI
from typing import Tuple, Any, Dict
from utils.logger_config import get_logger
from database.db_utils import log_error_to_db

logger = get_logger("CodeReviewAgent")

class CodeReviewAgent:
    def __init__(self):
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY environment variable not set.")
        self.client = OpenAI()

    def _request_code_correction(self, original_prompt: str, files: Dict[str, str], feedback: str) -> str:
        system_prompt = """
        You are an expert code reviewer and debugger. You will be given a user's prompt, the full code for a project, and a list of FATAL BUGS or RUNTIME TRACEBACKS that you MUST fix.
        Your task is to rewrite all the necessary files to fix every reported issue without exception.
        You MUST return a single JSON object where keys are filenames and values are the complete, corrected file contents.
        """
        code_to_review = "\n\n---\n\n".join([f"### File: {name}\n```python\n{content}\n```" for name, content in files.items()])
        response = self.client.chat.completions.create(
            model="gpt-4-turbo",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"### User Prompt\n{original_prompt}\n\n### Code to Fix\n{code_to_review}\n\n### FATAL BUGS & TRACEBACKS - FIX THESE\n{feedback}"}
            ],
            response_format={"type": "json_object"}
        )
        return response.choices[0].message.content

    def review_and_heal(self, project_path: str, original_prompt: str, max_retries: int, status_ui: Any) -> Tuple[bool, str]:
        for attempt in range(max_retries):
            if status_ui: status_ui.write(f"🔬 **Review & Heal Cycle #{attempt + 1}...**")

            current_files = {}
            for root, _, files in os.walk(project_path):
                for name in files:
                    file_path = os.path.join(root, name)
                    relative_path = os.path.relpath(file_path, project_path).replace("\\", "/")
                    with open(file_path, 'r', encoding='utf-8') as f: current_files[relative_path] = f.read()

            process, feedback = None, ""
            try:
                command = ["streamlit", "run", "app.py", "--server.headless", "true"]
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=project_path)
                stdout, stderr = process.communicate(timeout=25)
                
                if process.returncode != 0:
                    feedback = f"- RUNTIME TRACEBACK: The application crashed on startup.\n{stderr}"
                else:
                    feedback = f"- RUNTIME ERROR: The app exited prematurely.\nSTDOUT:\n{stdout}\n\nSTDERR:\n{stderr}"
            except subprocess.TimeoutExpired:
                if process: process.kill()
                if status_ui: status_ui.write("✅ Code passed dynamic review.")
                return True, "App ran without crashing."
            except Exception as e:
                feedback = f"- DYNAMIC TEST FAILED: Could not run the application. Error: {e}\n"

            if status_ui: status_ui.write(f"⚠️ Found issues, requesting correction...")
            
            try:
                fixed_files_json = self._request_code_correction(original_prompt, current_files, feedback)
                fixed_files = json.loads(fixed_files_json)
                
                for file_name, new_content in fixed_files.items():
                    file_path = os.path.join(project_path, file_name)
                    with open(file_path, 'w', encoding='utf-8') as f: f.write(new_content)
                
                # *** THE FIX IS HERE ***
                # Derive the project name from the path before logging.
                project_name = os.path.basename(project_path)
                log_error_to_db(project_name, str(current_files), feedback, str(fixed_files))
                # *************************

                if status_ui: status_ui.write("Applied AI's corrections. Re-verifying...")
            except Exception as e:
                logger.error(f"Failed to apply correction on attempt #{attempt + 1}: {e}")
                return False, f"Failed to apply correction: {e}"

        return False, "Code review failed after multiple attempts."
        
    def run(self, inputs: dict, status_ui=None) -> dict:
        project_path = inputs.get("project_path")
        original_prompt = inputs.get("prompt")
        if not project_path or not original_prompt:
            raise ValueError("CodeReviewAgent requires 'project_path' and 'prompt'.")

        review_passed, logs = self.review_and_heal(project_path, original_prompt, 3, status_ui)
        if not review_passed:
            raise Exception(f"Code Review Agent failed to heal the code. Last logs:\n{logs}")
        
        return {"review_passed": True}