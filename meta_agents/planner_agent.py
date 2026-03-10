# meta_agents/planner_agent.py

from openai import OpenAI
import json
import os
from utils.logger_config import get_logger

logger = get_logger("PlannerAgent")

class PlannerAgent:
    def __init__(self):
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY environment variable not set.")
        self.client = OpenAI()

    def generate_plan(self, user_prompt: str) -> dict:
        """Generates a project name and a sequence of agents to fulfill the user's request."""
        logger.info(f"Generating a workflow plan for prompt: '{user_prompt[:50]}...'")

        system_prompt = """
        You are a pragmatic AI workflow designer. Your only job is to provide a project name and a simple sequence of agent names to solve the user's request.

        **CRITICAL RULE**: For any request to build a runnable software application, you MUST respond with this exact sequence of agent names:
        ["CodeGeneratorAgent", "CodeReviewAndHealingAgent", "LaunchAgent"]

        You MUST respond with a single, valid JSON object with two keys:
        1. "project_name": A unique, descriptive, PascalCase name for the project (e.g., 'SimpleChecklistApp', 'StockVisualizer').
        2. "agent_sequence": A Python list of agent name strings.

        **EXAMPLE OUTPUT for a prompt like 'build a todo list app':**
        {
            "project_name": "TodoListApp",
            "agent_sequence": [
                "CodeGeneratorAgent",
                "CodeReviewAndHealingAgent",
                "LaunchAgent"
            ]
        }
        """
        
        try:
            response = self.client.chat.completions.create(
                model="gpt-4-turbo",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"}
            )
            plan_str = response.choices[0].message.content
            return json.loads(plan_str)
        except Exception as e:
            logger.error(f"Failed to generate a valid plan from AI: {e}", exc_info=True)
            raise ValueError(f"PlannerAgent failed to generate a valid plan. Error: {e}")
