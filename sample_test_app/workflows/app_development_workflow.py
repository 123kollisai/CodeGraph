# workflows/app_development_workflow.py

workflow = {
    "start_node": "generate_code",
    "nodes": {
        "generate_code": {
            "config": {"path": "agents.developer_agent.DeveloperAgent"},
            "inputs": {"prompt": "user_prompt"},
            "next": "review_and_heal_code"
        },
        "review_and_heal_code": {
            "config": {"path": "agents.code_review_agent.CodeReviewAgent"},
            "inputs": {
                "project_path": "{{generate_code.project_path}}",
                "original_prompt": "user_prompt"
            },
            "next": "launch_application"
        },
        "launch_application": {
            "config": {"path": "agents.launch_agent.LaunchAgent"},
            "inputs": {
                "project_path": "{{generate_code.project_path}}",
                "port": "app_port"
            },
            "next": None
        }
    }
}