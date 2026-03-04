import os
import subprocess
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv


# ============================================================
# PROJECT ROOT
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Load environment variables
load_dotenv(PROJECT_ROOT / ".env")


# ============================================================
# DATABASE CONFIG
# ============================================================

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

SCHEMA_NAME = "kirana_kart"


# ============================================================
# EXPORT DIRECTORY
# ============================================================

EXPORT_DIR = PROJECT_ROOT / "exports"
EXPORT_DIR.mkdir(exist_ok=True)

timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

output_file = EXPORT_DIR / f"{SCHEMA_NAME}_full_export_{timestamp}.sql"


# ============================================================
# PG_DUMP COMMAND
# ============================================================

command = [
    "pg_dump",
    "-h", DB_HOST,
    "-p", DB_PORT,
    "-U", DB_USER,
    "-d", DB_NAME,
    "-n", SCHEMA_NAME,
    "-f", str(output_file)
]


# ============================================================
# RUN EXPORT
# ============================================================

print("\nStarting Kirana Kart schema export...\n")

try:

    env = os.environ.copy()

    # Pass password so pg_dump doesn't prompt
    if DB_PASSWORD:
        env["PGPASSWORD"] = DB_PASSWORD

    subprocess.run(
        command,
        check=True,
        env=env
    )

    print("Export completed successfully.\n")
    print(f"File saved at:\n{output_file}\n")

except subprocess.CalledProcessError as e:

    print("Export failed.")
    print(str(e))