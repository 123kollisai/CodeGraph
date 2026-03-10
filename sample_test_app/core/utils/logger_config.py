# utils/logger_config.py

import logging
import sys
import os

LOGS_DIR = "framework_logs"

def get_logger(name: str):
    """
    Configures and returns a logger that writes to both the console and a central log file.
    """
    logger = logging.getLogger(name)
    
    # Set level and prevent logs from propagating to the root logger
    logger.propagate = False
    logger.setLevel(logging.INFO)

    # Add handlers only if they haven't been added before
    if not logger.handlers:
        # Create the central logs directory if it doesn't exist
        os.makedirs(LOGS_DIR, exist_ok=True)
        log_file_path = os.path.join(LOGS_DIR, 'main_framework.log')

        # --- Handler Configuration ---
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )

        # Console Handler
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

        # File Handler
        file_handler = logging.FileHandler(log_file_path, mode='a', encoding='utf-8')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger
