# core/workflow_engine.py

import logging
import importlib
from typing import Dict, Any

logger = logging.getLogger(__name__)

class WorkflowEngine:
    def __init__(self, workflow: Dict[str, Any]):
        self.workflow = workflow
        self.agents = self._load_agents(workflow['nodes'])

    def _load_agents(self, nodes: Dict[str, Any]) -> Dict[str, Any]:
        """Dynamically loads agent classes from their specified paths."""
        loaded_agents = {}
        for node_id, node_info in nodes.items():
            config = node_info.get('config', {})
            path = config.get('path')
            if not path:
                logger.warning(f"Node '{node_id}' is missing a path. Skipping.")
                continue

            try:
                # The path is in the format 'agents.agent_name.ClassName'
                module_path, class_name = path.rsplit('.', 1)
                
                # Import the module and get the class
                module = importlib.import_module(module_path)
                agent_class = getattr(module, class_name)
                loaded_agents[node_id] = agent_class()
                logger.info(f"Successfully loaded agent '{class_name}' for node '{node_id}'.")

            except (ImportError, AttributeError) as e:
                logger.error(f"Failed to load agent for node '{node_id}' from path '{path}': {e}", exc_info=True)
                raise

        return loaded_agents

    def execute(self, initial_inputs: Dict[str, Any], status_ui: Any = None) -> Dict[str, Any]:
        """Executes the workflow from the start node."""
        node_outputs = {}
        # This dictionary holds all data available to the workflow,
        # starting with the initial inputs.
        workflow_context = initial_inputs.copy()

        current_node_id = self.workflow.get('start_node')

        while current_node_id:
            if status_ui:
                status_ui.write(f"▶️ **Executing Node:** `{current_node_id}`")

            node_info = self.workflow['nodes'].get(current_node_id, {})
            agent = self.agents.get(current_node_id)

            if not agent:
                raise ValueError(f"Agent for node '{current_node_id}' not found.")

            # Map inputs for the current node from the workflow_context
            inputs_for_node = {}
            for key, value in node_info.get('inputs', {}).items():
                if isinstance(value, str) and value.startswith("{{") and value.endswith("}}"):
                    # Dynamic input from a previous node's output
                    source_node, source_key = value.strip('{}').split('.')
                    previous_output = node_outputs.get(source_node, {})
                    inputs_for_node[key] = previous_output.get(source_key)
                else:
                    # Static input from the initial payload
                    inputs_for_node[key] = workflow_context.get(value)

            try:
                # Run the agent's main method
                output = agent.run(inputs_for_node, status_ui)
                if output:
                    node_outputs[current_node_id] = output
            except Exception as e:
                logger.error(f"Error executing node {current_node_id}: {e}", exc_info=True)
                if status_ui:
                    status_ui.update(label=f"🚨 Error at node '{current_node_id}'!", state="error")
                raise

            current_node_id = node_info.get('next')

        return node_outputs
