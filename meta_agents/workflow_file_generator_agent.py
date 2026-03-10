# meta_agents/workflow_file_generator_agent.py

from utils.logger_config import get_logger
import json

logger = get_logger("WorkflowFileGeneratorAgent")

class WorkflowFileGeneratorAgent:
    def generate_workflow_file_content(self, plan: dict) -> str:
        """
        Takes a hydrated plan and constructs the content for a 'workflow.py' file.
        This version uses the static paths provided in the plan.
        """
        logger.info("Generating workflow file content using static agent paths.")
        
        nodes = {}
        start_node = None
        
        # This mapping is for the node keys (e.g., "developer", "reviewer")
        agent_name_to_node_id = {
            step_info['agent_name']: step_info['agent_name'].lower().replace("agent", "") 
            for step_info in plan['plan']
        }
        
        for i, step_info in enumerate(plan['plan']):
            agent_name = step_info['agent_name']
            node_id = agent_name_to_node_id[agent_name]
            
            if i == 0:
                start_node = node_id

            next_node_id = None
            if i + 1 < len(plan['plan']):
                next_agent_name = plan['plan'][i+1]['agent_name']
                next_node_id = agent_name_to_node_id[next_agent_name]

            inputs = {}
            for inp in step_info.get('inputs', []):
                source_node_found = False
                for dep_agent_name in step_info.get('dependencies', []):
                    dep_step = next((s for s in plan['plan'] if s['agent_name'] == dep_agent_name), None)
                    if dep_step and inp in dep_step.get('outputs', []):
                        source_node_id = agent_name_to_node_id[dep_agent_name]
                        inputs[inp] = f"{{{{{source_node_id}.{inp}}}}}"
                        source_node_found = True
                        break
                
                if not source_node_found:
                    inputs[inp] = inp

            # The agent path is now taken directly from the plan, which contains the static path.
            agent_code_path = step_info['path']
            
            nodes[node_id] = {
                "config": {"path": agent_code_path},
                "inputs": inputs,
                "next": next_node_id
            }

        workflow_dict = {
            "start_node": start_node,
            "nodes": nodes
        }
        
        workflow_string = f"workflow = {json.dumps(workflow_dict, indent=4)}"
        workflow_string_fixed = workflow_string.replace('null', 'None')
        
        return workflow_string_fixed
