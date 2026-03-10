import os
import subprocess
import psutil
from utils.logger_config import get_logger

logger = get_logger("LaunchAgent")

class LaunchAgent:
    def __init__(self):
        pass

    def run(self, inputs: dict, status_ui=None) -> dict:
        project_path = inputs.get("project_path")
        port = inputs.get("app_port")
        if not project_path or not port:
            raise ValueError("LaunchAgent requires 'project_path' and 'app_port'.")
            
        if status_ui: status_ui.write("🚀 **Launch Agent:** Starting the application...")
        
        # Check if port is in use and stop the old process
        for conn in psutil.net_connections():
            if conn.laddr.port == port and conn.status == 'LISTEN':
                try:
                    # *** THE FIX IS HERE ***
                    # This gracefully handles the case where the process has already terminated.
                    process_to_kill = psutil.Process(conn.pid)
                    process_to_kill.kill()
                    process_to_kill.wait() # Wait for the process to terminate
                    logger.warning(f"Killed existing process {conn.pid} on port {port}.")
                except psutil.NoSuchProcess:
                    logger.warning(f"Process {conn.pid} on port {port} already terminated.")
                except Exception as e:
                    logger.error(f"Error killing process {conn.pid} on port {port}: {e}")
                
        main_app_file = os.path.join(project_path, "app.py")
        
        process = subprocess.Popen(
            ["streamlit", "run", main_app_file, "--server.port", str(port)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )

        app_url = f"http://localhost:{port}"
        logger.info(f"Application launched at {app_url} with PID {process.pid}")
        
        return {"process": process, "app_url": app_url}
