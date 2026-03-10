import pyodbc

# Use the exact same details as in your Streamlit app
CONNECTION_STRING = (
    "Driver={SQL Server};"
    "Server=DESKTOP-9NP710O\\MSSQLSERVER04;"
    "Database=NLG;"
    "Trusted_Connection=yes;"
)

try:
    print("Attempting to connect to the database...")
    conn = pyodbc.connect(CONNECTION_STRING)
    print("SUCCESS: The connection was successful!")
    conn.close()
except pyodbc.OperationalError as e:
    print("\n--- CONNECTION FAILED ---")
    print("This confirms the issue is with the SQL Server environment, not the Streamlit app.")
    print("Please check the troubleshooting steps for firewall, services, and TCP/IP.")
    print(f"\nError details: {e}")
except Exception as e:
    print(f"An unexpected error occurred: {e}")