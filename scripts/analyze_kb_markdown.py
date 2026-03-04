#!/usr/bin/env python3

import os
import sys
from pathlib import Path
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from openai import OpenAI


# ============================================================
# 1️⃣ Project Setup
# ============================================================

print("[1/6] Initializing project environment...")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

env_path = PROJECT_ROOT / ".env"

if not env_path.exists():
    raise Exception(".env file not found in project root")

load_dotenv(dotenv_path=env_path)

print("      ✔ Environment loaded")


# ============================================================
# 2️⃣ Load DB Configuration
# ============================================================

print("[2/6] Loading database configuration...")

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
    raise Exception(f"Missing required DB variables in .env: {missing}")

DATABASE_URL = (
    f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}"
    f"@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

engine = create_engine(DATABASE_URL)

print("      ✔ Database connection configured")


# ============================================================
# 3️⃣ Load LLM Configuration
# ============================================================

print("[3/6] Loading LLM configuration...")

LLM_API_BASE_URL = clean(os.getenv("LLM_API_BASE_URL"))
LLM_API_KEY = clean(os.getenv("LLM_API_KEY"))

if not LLM_API_KEY:
    raise Exception("LLM_API_KEY missing in .env")

client = OpenAI(
    api_key=LLM_API_KEY,
    base_url=LLM_API_BASE_URL
)

print("      ✔ LLM client initialized")


# ============================================================
# 4️⃣ Fetch Active Markdown From DB
# ============================================================

print("[4/6] Fetching active markdown draft from database...")

DOCUMENT_ID = "TEST_POLICY_V1"

with engine.connect() as conn:
    markdown_content = conn.execute(text("""
        SELECT markdown_content
        FROM kirana_kart.knowledge_base_raw_uploads
        WHERE document_id = :doc_id
        AND is_active = TRUE
        ORDER BY uploaded_at DESC
        LIMIT 1;
    """), {"doc_id": DOCUMENT_ID}).scalar()

if not markdown_content:
    raise Exception("No active draft found for document")

print("      ✔ Markdown fetched successfully")
print(f"      • Length: {len(markdown_content)} characters")


# ============================================================
# 5️⃣ Sending Markdown To LLM For Structural Analysis
# ============================================================

print("[5/6] Sending document to LLM for structural analysis...")

prompt = f"""
You are a policy compiler assistant.

Analyze this markdown business rules document.

Provide:

1. Identified metadata fields
2. Structural sections detected
3. Rule blocks detected
4. Ambiguities or risks
5. Suggested structured JSON layout for compiler

Return structured, clean output.

Markdown:
---------------------
{markdown_content}
---------------------
"""

try:
    response = client.chat.completions.create(
        model="gpt-4.1",
        temperature=0.1,
        messages=[
            {"role": "system", "content": "You are a deterministic policy analysis engine."},
            {"role": "user", "content": prompt}
        ]
    )

    analysis = response.choices[0].message.content

    print("      ✔ LLM analysis complete")

except Exception as e:
    print("      ❌ LLM call failed")
    raise


# ============================================================
# 6️⃣ Display Structured Analysis Output
# ============================================================

print("[6/6] Displaying analysis results...\n")

print("=" * 70)
print("LLM STRUCTURAL ANALYSIS")
print("=" * 70)
print(analysis)
print("=" * 70)

print("\n✔ Analysis complete.")