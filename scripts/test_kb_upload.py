#!/usr/bin/env python3

import os
import sys
from pathlib import Path
from sqlalchemy import create_engine
from dotenv import load_dotenv

# ------------------------------------------------------------
# Project Root Setup
# ------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

# ------------------------------------------------------------
# Load .env from project root
# ------------------------------------------------------------
env_path = PROJECT_ROOT / ".env"

if not env_path.exists():
    raise Exception(".env file not found in project root")

load_dotenv(dotenv_path=env_path)

# ------------------------------------------------------------
# Read DB config from .env
# ------------------------------------------------------------
def clean(value):
    if not value:
        return value
    return value.strip().strip('"').strip("'")

DB_HOST = clean(os.getenv("DB_HOST"))
DB_PORT = clean(os.getenv("DB_PORT", "5432"))
DB_USER = clean(os.getenv("DB_USER"))
DB_PASSWORD = clean(os.getenv("DB_PASSWORD"))
DB_NAME = clean(os.getenv("DB_NAME"))

missing = [k for k, v in {
    "DB_HOST": DB_HOST,
    "DB_USER": DB_USER,
    "DB_PASSWORD": DB_PASSWORD,
    "DB_NAME": DB_NAME
}.items() if not v]

if missing:
    raise Exception(f"Missing required environment variables in .env: {missing}")

DATABASE_URL = (
    f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}"
    f"@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

print("Connecting to:", DATABASE_URL.replace(DB_PASSWORD, "****"))

engine = create_engine(DATABASE_URL)

# ------------------------------------------------------------
# Import after path setup
# ------------------------------------------------------------
from app.l1_ingestion.kb_registry.kb_registry_service import KBRegistryService

registry = KBRegistryService(engine)

# ------------------------------------------------------------
# Read test.md from root
# ------------------------------------------------------------
test_file_path = PROJECT_ROOT / "test.md"

if not test_file_path.exists():
    raise Exception("test.md not found in project root")

with open(test_file_path, "r", encoding="utf-8") as f:
    file_content = f.read()

print("\nUploading Raw Document...\n")

result = registry.upload_document(
    document_id="TEST_POLICY_V1",
    original_filename="test.md",
    original_format="md",
    raw_content=file_content,
    uploaded_by="admin_user",
    version_label="v1.0.0"
)

print("Upload Result:")
print(result)