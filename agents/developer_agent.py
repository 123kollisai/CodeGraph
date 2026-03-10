# agents/developer_agent.py
import os
import json
import shutil
from openai import OpenAI
from utils.logger_config import get_logger
from database.db_utils import log_project_to_history

logger = get_logger("DeveloperAgent")
PROJECTS_DIR = "generated_projects"

class DeveloperAgent:
    def __init__(self):
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY environment variable not set.")
        self.client = OpenAI()

    def run(self, inputs: dict, status_ui=None) -> dict:
        prompt = inputs.get("prompt")
        if not prompt:
            raise ValueError("DeveloperAgent requires a 'prompt' input.")

        if status_ui: status_ui.write("🤖 **Developer Agent:** Writing the first draft...")

        system_prompt = """
        You are a senior software architect creating a complete, multi-file Streamlit application.
        Your response MUST be a single, valid JSON object with `project_name` (e.g., `ChecklistApp`) and `files` keys.
        The `files` key MUST be a list of dictionaries, each with a `name` and `content`.
        You MUST include a `requirements.txt` and a main `app.py`.
        """
        
        try:
            response = self.client.chat.completions.create(
                model="gpt-4-turbo",
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}],
                response_format={"type": "json_object"}
            )
            response_data = json.loads(response.choices[0].message.content)
        except Exception as e:
            logger.error(f"Failed to get valid JSON from AI: {e}", exc_info=True)
            raise

        project_name = response_data.get('project_name', 'default_project')
        files = response_data.get('files', [])
        project_path = os.path.join(PROJECTS_DIR, project_name)

        if os.path.exists(project_path): shutil.rmtree(project_path)
        os.makedirs(project_path)
        
        file_tree = f"📁 {project_name}/\n"
        for file_data in files:
            file_name, file_content = file_data['name'], file_data['content']
            file_path = os.path.join(project_path, file_name)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w", encoding='utf-8') as f: f.write(file_content)
            file_tree += f"  L {file_name}\n"

        try:
            log_project_to_history(prompt, project_name, file_tree)
        except Exception as e:
            logger.error(f"Could not log project to history: {e}")

        # This is the output that gets passed to the next agent
        return {"project_path": project_path}