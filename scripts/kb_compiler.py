import os
import json
import hashlib
import psycopg2
from psycopg2.extras import Json
from dotenv import load_dotenv
from pathlib import Path
from openai import OpenAI

# ============================================================
# ENVIRONMENT SETUP (ROOT .env)
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]
env_path = PROJECT_ROOT / ".env"

if not env_path.exists():
    raise RuntimeError(".env file not found in project root")

load_dotenv(dotenv_path=env_path)

LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_API_BASE_URL = os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1")

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

if not LLM_API_KEY:
    raise RuntimeError("LLM_API_KEY not found in environment.")

client = OpenAI(
    api_key=LLM_API_KEY,
    base_url=LLM_API_BASE_URL
)

# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_connection():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )

# ============================================================
# FETCH RAW UPLOAD (L1 INGESTION)
# ============================================================

def fetch_latest_raw(cursor):
    cursor.execute("""
        SELECT id, document_id, markdown_content, version_label
        FROM kirana_kart.knowledge_base_raw_uploads
        WHERE registry_status = 'draft'
        ORDER BY uploaded_at DESC
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    """)

    row = cursor.fetchone()

    if not row:
        raise ValueError("No raw drafts pending compilation.")

    raw_id, document_id, markdown_content, version_label = row

    if not version_label:
        raise ValueError("Raw upload missing version_label.")

    return raw_id, document_id, markdown_content, version_label

# ============================================================
# FETCH VALID ACTION CODES
# ============================================================

def fetch_valid_action_codes(cursor):
    cursor.execute("""
        SELECT action_code_id
        FROM kirana_kart.master_action_codes
    """)
    rows = cursor.fetchall()
    return [r[0] for r in rows]

# ============================================================
# HASH GENERATION
# ============================================================

def compute_artifact_hash(data: dict):
    encoded = json.dumps(data, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()

# ============================================================
# GPT STRUCTURING (CONSTRAINED)
# ============================================================

def build_prompt(markdown_text: str, valid_actions: list):

    action_list = "\n".join(valid_actions)

    return f"""
Convert the following business rules document into STRICT JSON.

IMPORTANT:
You MUST choose action_code_id ONLY from this list:

{action_list}

Do NOT invent new action codes.

Output JSON only.

Structure:
{{
  "modules": [
    {{
      "module_name": "Module Name",
      "rules": [
        {{
          "rule_id": "RP-001",
          "rule_type": "resolution",
          "priority": 100,
          "rule_scope": "ticket",
          "filters": {{}},
          "numeric_constraints": {{}},
          "flags": {{}},
          "conditions": {{}},
          "action": {{
            "action_code_id": "MUST_BE_FROM_LIST",
            "payload": {{}}
          }},
          "overrideable": false
        }}
      ]
    }}
  ]
}}

Document:
\"\"\"
{markdown_text}
\"\"\"
"""

def call_gpt(markdown_text: str, valid_actions: list):

    response = client.chat.completions.create(
        model="gpt-4.1",
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": "Output strict JSON only."},
            {"role": "user", "content": build_prompt(markdown_text, valid_actions)}
        ],
    )

    return json.loads(response.choices[0].message.content)

# ============================================================
# VALIDATION
# ============================================================

def validate_structure(data: dict):
    if "modules" not in data:
        raise ValueError("Missing modules")

    for module in data["modules"]:
        if "module_name" not in module:
            raise ValueError("Module missing module_name")

        for rule in module["rules"]:
            required = [
                "rule_id","rule_type","priority",
                "rule_scope","filters",
                "numeric_constraints","flags",
                "conditions","action","overrideable"
            ]
            for field in required:
                if field not in rule:
                    raise ValueError(f"Rule missing {field}")

# ============================================================
# ACTION RESOLUTION
# ============================================================

def resolve_action_id(action_code_id: str, cursor):
    cursor.execute("""
        SELECT id
        FROM kirana_kart.master_action_codes
        WHERE action_code_id = %s
    """, (action_code_id,))
    result = cursor.fetchone()
    if not result:
        raise ValueError(f"Invalid action_code_id: {action_code_id}")
    return result[0]

# ============================================================
# MAIN COMPILER
# ============================================================

def main():

    conn = get_connection()
    conn.autocommit = False
    cur = conn.cursor()

    raw_id = None

    try:
        print("[1] Fetching raw upload...")
        raw_id, document_id, markdown_text, version_label = fetch_latest_raw(cur)

        print(f"[2] Compiling {document_id} | Version {version_label}")

        valid_actions = fetch_valid_action_codes(cur)

        structured_json = call_gpt(markdown_text, valid_actions)

        print("[3] Validating structure...")
        validate_structure(structured_json)

        print("[4] Computing artifact hash...")
        artifact_hash = compute_artifact_hash(structured_json)

        print("[5] Inserting snapshot...")
        cur.execute("""
            INSERT INTO kirana_kart.knowledge_base_versions
            (version_label, status, created_by, snapshot_data)
            VALUES (%s, 'compiled', 'compiler', %s)
        """, (version_label, Json(structured_json)))

        print("[6] Upserting policy version...")
        cur.execute("""
            INSERT INTO kirana_kart.policy_versions
            (policy_version, artifact_hash, description, is_active)
            VALUES (%s, %s, %s, FALSE)
            ON CONFLICT (policy_version)
            DO UPDATE SET artifact_hash = EXCLUDED.artifact_hash
        """, (
            version_label,
            artifact_hash,
            "Compiled via GPT policy compiler"
        ))

        print("[7] Rebuilding deterministic rule registry...")
        cur.execute("""
            DELETE FROM kirana_kart.rule_registry
            WHERE policy_version = %s
        """, (version_label,))

        for module in structured_json["modules"]:
            for rule in module["rules"]:

                action_id = resolve_action_id(
                    rule["action"]["action_code_id"],
                    cur
                )

                cur.execute("""
                    INSERT INTO kirana_kart.rule_registry (
                        rule_id,
                        policy_version,
                        module_name,
                        rule_type,
                        priority,
                        rule_scope,
                        issue_type_l1,
                        issue_type_l2,
                        business_line,
                        fraud_segment,
                        min_order_value,
                        max_order_value,
                        min_repeat_count,
                        max_repeat_count,
                        sla_breach_required,
                        evidence_required,
                        conditions,
                        action_id,
                        action_payload,
                        overrideable
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (
                    rule["rule_id"],
                    version_label,
                    module["module_name"],
                    rule["rule_type"],
                    rule["priority"],
                    rule["rule_scope"],
                    rule["filters"].get("issue_type_l1"),
                    rule["filters"].get("issue_type_l2"),
                    rule["filters"].get("business_line"),
                    rule["filters"].get("fraud_segment"),
                    rule["numeric_constraints"].get("min_order_value"),
                    rule["numeric_constraints"].get("max_order_value"),
                    rule["numeric_constraints"].get("min_repeat_count"),
                    rule["numeric_constraints"].get("max_repeat_count"),
                    rule["flags"].get("sla_breach_required"),
                    rule["flags"].get("evidence_required"),
                    Json(rule["conditions"]),
                    action_id,
                    Json(rule["action"].get("payload")),
                    rule["overrideable"]
                ))

        print("[8] Marking raw upload as compiled...")
        cur.execute("""
            UPDATE kirana_kart.knowledge_base_raw_uploads
            SET registry_status = 'compiled',
                compiled_hash = %s,
                compile_errors = NULL,
                updated_at = NOW()
            WHERE id = %s
        """, (artifact_hash, raw_id))

        conn.commit()
        print("✅ PRODUCTION COMPILATION COMPLETE")

    except Exception as e:
        conn.rollback()

        print("❌ COMPILATION FAILED — ROLLED BACK")

        if raw_id:
            cur.execute("""
                UPDATE kirana_kart.knowledge_base_raw_uploads
                SET registry_status = 'failed',
                    compile_errors = %s,
                    updated_at = NOW()
                WHERE id = %s
            """, (Json({"error": str(e)}), raw_id))
            conn.commit()

        raise e

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()