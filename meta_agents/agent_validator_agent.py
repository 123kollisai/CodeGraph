# meta_agents/agent_validator_agent.py

import os
import importlib.util
import traceback
from openai import OpenAI
import re

from utils.logger_config import get_logger

logger = get_logger("AgentValidatorAgent")

class AgentValidatorAgent:
    def __init__(self):
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY environment variable not set.")
        self.client = OpenAI()

    def _request_code_correction(self, agent_code: str, error_traceback: str) -> str:
        """Sends the broken agent code and traceback to the AI for correction."""
        logger.warning(f"Requesting AI correction for agent code due to error: {error_traceback}")
        
        system_prompt = f"""
        You are an expert Python debugger. You will be given a Python script for an AI agent that failed to load or run, along with the full traceback of the error. Your task is to fix the script so the error is resolved.

        **CRITICAL ANALYSIS:**
        1.  **Read the Traceback Carefully**: The traceback is your most important clue.
        2.  **Common Errors to Fix**:
            - `TypeError: ...takes X positional arguments but Y were given`: This means the method signature is wrong. The `run` method for all agents MUST be `def run(self, inputs: dict, status_ui=None):`. The `__init__` method MUST be `def __init__(self):`.
            - `SyntaxError`: There is invalid Python syntax in the file. Correct it.
            - `ModuleNotFoundError`: The script is using an incorrect import statement. Fix it (e.g., the only valid OpenAI import is `from openai import OpenAI`).
            - `AttributeError`: The code is trying to access a method or property that doesn't exist, often due to API changes (e.g., using `response['choices']` instead of `response.choices`).

        **BROKEN CODE:**
        ```python
        {agent_code}
        ```

        **ERROR TRACEBACK:**
        ```
        {error_traceback}
        ```

        You MUST respond with only the complete, raw, corrected Python code for the entire file. Do not include any explanations, apologies, or markdown formatting.
        """
        
        response = self.client.chat.completions.create(
            model="gpt-4-turbo",
            messages=[
                {"role": "system", "content": system_prompt}
            ]
        )
        corrected_code = response.choices[0].message.content
        
        # Clean the response
        match = re.search(r"```python\s*([\s\S]+?)\s*```", corrected_code)
        if match:
            return match.group(1).strip()
        return corrected_code.strip()

    def run(self, inputs: dict, status_ui=None) -> dict:
        """
        Validates and heals the generated agent code in a loop.
        """
        project_path = inputs.get("project_path")
        plan = inputs.get("plan")
        if not project_path or not plan:
            raise ValueError("AgentValidatorAgent requires 'project_path' and 'plan' inputs.")
            
        agents_dir = os.path.join(project_path, "agents")
        max_retries = 3

        for attempt in range(max_retries):
            all_agents_valid = True
            agent_files = [f for f in os.listdir(agents_dir) if f.endswith(".py") and not f.startswith("__")]
            
            for agent_file in agent_files:
                agent_file_path = os.path.join(agents_dir, agent_file)
                module_name = agent_file[:-3] # e.g., 'codegeneratoragent'
                
                try:
                    if status_ui: status_ui.write(f"  - Validating `{module_name}`...")
                    
                    # 1. Try to import the module
                    spec = importlib.util.spec_from_file_location(module_name, agent_file_path)
                    agent_module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(agent_module)
                    
                    # *** THE FIX IS HERE: Look up the class name from the plan ***
                    # Find the agent step in the plan that corresponds to the current file
                    agent_file_lowercase = agent_file.replace(".py", "")
                    step = next((s for s in plan['plan'] if s['agent_name'].lower() == agent_file_lowercase), None)
                    
                    if not step:
                        raise LookupError(f"Could not find agent definition in plan for file: {agent_file}")

                    # Use the correct PascalCase name from the plan
                    class_name = step['agent_name']
                    # *************************************************************

                    agent_class = getattr(agent_module, class_name, None)
                    if not agent_class:
                        raise AttributeError(f"Class '{class_name}' not found in module '{module_name}'.")

                    # 3. Try to instantiate
                    agent_instance = agent_class()

                    logger.info(f"Agent '{class_name}' in '{agent_file}' passed validation.")

                except Exception as e:
                    all_agents_valid = False
                    error_traceback = traceback.format_exc()
                    logger.error(f"Validation failed for {agent_file} on attempt {attempt + 1}: {e}")
                    
                    if status_ui: status_ui.write(f"  - ⚠️ Error in `{agent_file}`. Requesting self-correction...")
                    
                    with open(agent_file_path, "r", encoding="utf-8") as f:
                        broken_code = f.read()
                    
                    corrected_code = self._request_code_correction(broken_code, error_traceback)
                    
                    with open(agent_file_path, "w", encoding="utf-8") as f:
                        f.write(corrected_code)
                    
                    if status_ui: status_ui.write(f"  - ✅ Correction applied to `{agent_file}`. Re-validating...")
                    break 
            
            if all_agents_valid:
                if status_ui: status_ui.write("✅ All generated agents passed validation!")
                return {"agents_validated": True, "project_path": project_path}
        
        raise Exception(f"Failed to validate and heal agents in '{project_path}' after {max_retries} attempts.")
