# database/db_utils.py

import pyodbc
import pandas as pd
from datetime import datetime
from utils.logger_config import get_logger

logger = get_logger("DBUtils")

# IMPORTANT: Connection string updated with your specific server details.
CONNECTION_STRING = (
    "Driver={SQL Server};"
    "Server=DESKTOP-9NP710O\\MSSQLSERVER04;"
    "Database=NLG;"
    "Trusted_Connection=yes;"
)
ERROR_TABLE_NAME = "AgentDebugLogs"
HISTORY_TABLE_NAME = "ProjectHistory"

def get_db_connection():
    """Establishes and returns a database connection."""
    try:
        conn = pyodbc.connect(CONNECTION_STRING)
        return conn
    except Exception as e:
        logger.error(f"Database connection error: {e}", exc_info=True)
        raise

def setup_database():
    """
    Ensures the necessary database and tables exist, creating them if they don't.
    """
    try:
        # Connect without specifying a database first to check if the DB exists.
        # This string connects to the server instance itself.
        master_conn_str = (
            "Driver={SQL Server};"
            "Server=DESKTOP-9NP710O\\MSSQLSERVER04;"
            "Trusted_Connection=yes;"
            "autocommit=True" # Required for CREATE DATABASE
        )
        with pyodbc.connect(master_conn_str, autocommit=True) as conn:
            cursor = conn.cursor()
            db_name = "NLG"
            cursor.execute("SELECT name FROM sys.databases WHERE name=?", (db_name,))
            if cursor.fetchone() is None:
                logger.info(f"Database '{db_name}' not found. Creating it...")
                cursor.execute(f"CREATE DATABASE {db_name}")
                logger.info(f"Database '{db_name}' created successfully.")
    except Exception as e:
        logger.warning(f"Could not check/create database. This might be a permission or driver issue. Error: {e}")
        # Re-raise the exception to make the error visible in the Streamlit UI
        raise


    # Now connect to the specific database to set up tables
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Check/create AgentDebugLogs table
        if not cursor.tables(table=ERROR_TABLE_NAME, tableType='TABLE').fetchone():
            create_error_table_query = f"""
            CREATE TABLE {ERROR_TABLE_NAME} (
                LogID INT IDENTITY(1,1) PRIMARY KEY,
                Timestamp DATETIME NOT NULL,
                ProjectName NVARCHAR(255),
                OriginalCode NVARCHAR(MAX),
                ErrorMessage NVARCHAR(MAX),
                FixedCode NVARCHAR(MAX),
                Status VARCHAR(50)
            );"""
            cursor.execute(create_error_table_query)
            conn.commit()
            logger.info(f"Table '{ERROR_TABLE_NAME}' created in database '{conn.getinfo(pyodbc.SQL_DATABASE_NAME)}'.")

        # Check/create ProjectHistory table
        if not cursor.tables(table=HISTORY_TABLE_NAME, tableType='TABLE').fetchone():
            create_history_table_query = f"""
            CREATE TABLE {HISTORY_TABLE_NAME} (
                ID INT IDENTITY(1,1) PRIMARY KEY,
                UserPrompt NVARCHAR(MAX) NOT NULL,
                ProjectName NVARCHAR(255) NOT NULL,
                FolderStructure NVARCHAR(MAX) NULL
            );"""
            cursor.execute(create_history_table_query)
            conn.commit()
            logger.info(f"Table '{HISTORY_TABLE_NAME}' created in database '{conn.getinfo(pyodbc.SQL_DATABASE_NAME)}'.")
            
    finally:
        cursor.close()
        conn.close()
        
def log_error_to_db(project_name: str, original_code: str, error_message: str, fixed_code: str):
    """Logs details of a self-healing event to the database."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        insert_query = f"""
        INSERT INTO {ERROR_TABLE_NAME} (Timestamp, ProjectName, OriginalCode, ErrorMessage, FixedCode, Status) 
        VALUES (?, ?, ?, ?, ?, ?);
        """
        try:
            cursor.execute(insert_query, datetime.now(), project_name, original_code, error_message, fixed_code, "Fixed")
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to log error to DB: {e}", exc_info=True)


def log_project_to_history(user_prompt: str, project_name: str, folder_structure: str):
    """Logs the details of a newly generated project to the ProjectHistory table."""
    logger.info(f"Logging project '{project_name}' to history.")
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            insert_query = f"""
            INSERT INTO {HISTORY_TABLE_NAME} (Timestamp, UserPrompt, ProjectName, FolderStructure)
            VALUES (?, ?, ?, ?);
            """
            cursor.execute(insert_query, datetime.now(), user_prompt, project_name, folder_structure)
            # This commit is crucial to save the data permanently.
            conn.commit() 
            logger.info("Transaction for ProjectHistory committed successfully.")

    except Exception as e:
        logger.error(f"Failed to log project history to database: {e}", exc_info=True)
        raise

