workflow = {
    "start_node": "developer",
    "nodes": {
        "developer": {
            "config": {
                "path": "agents.developer_agent.DeveloperAgent"
            },
            "inputs": {
                "prompt": "prompt"
            },
            "next": "codereview"
        },
        "codereview": {
            "config": {
                "path": "agents.code_review_agent.CodeReviewAgent"
            },
            "inputs": {
                "project_path": "{{developer.project_path}}",
                "prompt": "prompt"
            },
            "next": "launch"
        },
        "launch": {
            "config": {
                "path": "agents.launch_agent.LaunchAgent"
            },
            "inputs": {
                "project_path": "project_path",
                "app_port": "app_port"
            },
            "next": None
        }
    }
}