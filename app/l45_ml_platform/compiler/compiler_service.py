"""
Compiler Service
================

LLM-driven policy compiler.

Responsibilities:
- Fetch raw draft
- Constrain action codes
- Call LLM
- Validate schema
- Insert rule_registry
- Insert knowledge_base_versions
- Update policy_versions
- Mark raw upload compiled
"""

import os
import json
import hashlib
import logging
import psycopg2
from psycopg2.extras import Json, RealDictCursor
from dotenv import load_dotenv
from pathlib import Path
from typing import Dict, List

from openai import OpenAI


# ============================================================
# CONFIG
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[4]
load_dotenv(PROJECT_ROOT / ".env")

LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_API_BASE_URL = os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1")

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

logger = logging.getLogger("compiler_service")
logger.setLevel(logging.INFO)

client = OpenAI(
    api_key=LLM_API_KEY,
    base_url=LLM_API_BASE_URL
)


# ============================================================
# SERVICE
# ============================================================

class CompilerService:


    # --------------------------------------------------------
    # DB CONNECTION
    # --------------------------------------------------------

    def _get_connection(self):

        return psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
        )


    # --------------------------------------------------------
    # PUBLIC: Compile Latest Draft
    # --------------------------------------------------------

    def compile_latest_draft(self):
        """
        Fetches the most recently uploaded draft (any version)
        and compiles it. Delegates to compile_version internally.
        """

        conn = self._get_connection()

        try:

            with conn.cursor(cursor_factory=RealDictCursor) as cur:

                cur.execute("""
                    SELECT version_label
                    FROM kirana_kart.knowledge_base_raw_uploads
                    WHERE registry_status = 'draft'
                    ORDER BY uploaded_at DESC
                    LIMIT 1
                """)

                row = cur.fetchone()

        finally:

            conn.close()

        if not row:
            raise ValueError("No draft documents found to compile")

        return self.compile_version(row["version_label"])


    # --------------------------------------------------------
    # PUBLIC: Compile Specific Version
    # --------------------------------------------------------

    def compile_version(self, version_label: str):

        conn = self._get_connection()
        conn.autocommit = False

        try:

            with conn.cursor(cursor_factory=RealDictCursor) as cur:

                cur.execute("""
                    SELECT id, markdown_content, version_label
                    FROM kirana_kart.knowledge_base_raw_uploads
                    WHERE version_label=%s
                    AND registry_status='draft'
                    FOR UPDATE
                """, (version_label,))

                raw = cur.fetchone()

                if not raw:
                    raise ValueError(
                        f"Draft version '{version_label}' not found"
                    )

                logger.info(f"Compiling version {version_label}")

            # _compile_raw executes all writes but does NOT commit.
            # Commit happens here so the entire operation is one atomic transaction.
            result = self._compile_raw(conn, raw)
            conn.commit()
            return result

        except Exception as e:

            conn.rollback()
            logger.exception("Compilation failed")
            raise

        finally:

            conn.close()


    # --------------------------------------------------------
    # INTERNAL: CORE COMPILE LOGIC
    # --------------------------------------------------------

    def _compile_raw(self, conn, raw_row):
        """
        Executes all DB writes for compilation but does NOT commit.
        Transaction ownership belongs entirely to compile_version.
        """

        raw_id = raw_row["id"]
        markdown = raw_row.get("markdown_content")
        version_label = raw_row["version_label"]

        if not markdown:
            raise ValueError("Markdown content missing")

        markdown = markdown.strip()

        if not markdown:
            raise ValueError("Markdown document empty")

        logger.info(f"Markdown length: {len(markdown)}")

        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            action_map = self._load_action_map(cur)

        structured_json = self._call_llm(
            markdown,
            list(action_map.keys())
        )

        self._validate_structure(structured_json)

        artifact_hash = self._hash(structured_json)

        with conn.cursor() as cur:

            cur.execute("""
                DELETE FROM kirana_kart.rule_registry
                WHERE policy_version=%s
            """, (version_label,))

            rule_count = self._insert_rules(
                cur,
                structured_json,
                version_label,
                action_map
            )

            logger.info(f"{rule_count} rules inserted")

            cur.execute("""
                INSERT INTO kirana_kart.policy_versions
                (policy_version, artifact_hash, description, is_active)
                VALUES (%s,%s,%s,FALSE)
                ON CONFLICT (policy_version)
                DO UPDATE SET artifact_hash=EXCLUDED.artifact_hash
            """, (
                version_label,
                artifact_hash,
                "Compiled via CompilerService"
            ))

            cur.execute("""
                UPDATE kirana_kart.knowledge_base_raw_uploads
                SET registry_status='compiled',
                    compile_errors=NULL
                WHERE id=%s
            """, (raw_id,))

        # NOTE: No conn.commit() here. compile_version owns the transaction.

        return {
            "status": "compiled",
            "version_label": version_label,
            "artifact_hash": artifact_hash,
            "rules_created": rule_count
        }


    # --------------------------------------------------------
    # LOAD ACTION MAP
    # --------------------------------------------------------

    def _load_action_map(self, cur) -> Dict[str, int]:

        cur.execute("""
            SELECT id, action_code_id
            FROM kirana_kart.master_action_codes
        """)

        rows = cur.fetchall()

        action_map = {}

        for r in rows:
            action_map[r["action_code_id"]] = r["id"]

        return action_map


    # --------------------------------------------------------
    # LLM CALL
    # --------------------------------------------------------

    def _call_llm(self, markdown: str, valid_actions: List[str]):

        action_list = "\n".join(valid_actions)

        prompt = f"""
You are a deterministic policy compiler.

STRICT RULES:

1. Every rule MUST contain an action_code_id.
2. action_code_id MUST be selected ONLY from the allowed list below.
3. DO NOT invent action codes.
4. DO NOT return null action_code_id.
5. If a rule cannot map to a valid action_code_id, DO NOT generate that rule.

Allowed action_code_id values:

{action_list}

Return STRICT JSON.

Schema:

{{
 "modules":[
  {{
   "module_name":"",
   "rules":[
    {{
     "rule_id":"",
     "rule_type":"",
     "priority":100,
     "rule_scope":"ticket",

     "issue_type_l1":null,
     "issue_type_l2":null,
     "business_line":null,
     "customer_segment":null,
     "fraud_segment":null,

     "min_order_value":null,
     "max_order_value":null,
     "min_repeat_count":null,
     "max_repeat_count":null,

     "sla_breach_required":false,
     "evidence_required":false,

     "conditions":{{}},

     "action_code_id":"",
     "action_payload":{{}},

     "deterministic":true,
     "overrideable":false
    }}
   ]
  }}
 ]
}}

Document:
\"\"\"
{markdown}
\"\"\"
"""

        response = client.chat.completions.create(

            model="gpt-4.1",
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "Return strict JSON only."},
                {"role": "user", "content": prompt}
            ]

        )

        content = response.choices[0].message.content

        logger.info("LLM response received")

        try:

            parsed = json.loads(content)

        except Exception:

            logger.error("LLM returned invalid JSON")
            logger.error(content)
            raise

        return parsed


    # --------------------------------------------------------
    # VALIDATE STRUCTURE
    # --------------------------------------------------------

    def _validate_structure(self, data: dict):

        if "modules" not in data:
            raise ValueError("Missing modules key")

        if not isinstance(data["modules"], list):
            raise ValueError("modules must be list")

        total_rules = 0

        for module in data["modules"]:

            if "rules" not in module:
                raise ValueError("module missing rules")

            if not isinstance(module["rules"], list):
                raise ValueError("rules must be list")

            for rule in module["rules"]:

                if not rule.get("action_code_id"):
                    raise ValueError(
                        "Rule missing action_code_id"
                    )

            total_rules += len(module["rules"])

        if total_rules == 0:
            raise ValueError("Compiler produced zero rules")


    # --------------------------------------------------------
    # INSERT RULES
    # --------------------------------------------------------

    def _insert_rules(self, cur, structured_json, version_label, action_map):

        inserted = 0

        for module in structured_json["modules"]:

            module_name = module.get("module_name", "default")

            for rule in module["rules"]:

                action_code = rule["action_code_id"]

                if action_code not in action_map:

                    raise ValueError(
                        f"Invalid action_code_id: {action_code}"
                    )

                action_id = action_map[action_code]

                cur.execute("""
                    INSERT INTO kirana_kart.rule_registry
                    (
                        rule_id,
                        policy_version,
                        module_name,
                        rule_type,
                        priority,
                        rule_scope,

                        issue_type_l1,
                        issue_type_l2,
                        business_line,
                        customer_segment,
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

                        deterministic,
                        overrideable
                    )
                    VALUES
                    (%s,%s,%s,%s,%s,%s,
                     %s,%s,%s,%s,%s,
                     %s,%s,%s,%s,
                     %s,%s,
                     %s,
                     %s,%s,
                     %s,%s)
                """, (

                    rule["rule_id"],
                    version_label,
                    module_name,
                    rule["rule_type"],
                    rule.get("priority", 100),
                    rule.get("rule_scope", "ticket"),

                    rule.get("issue_type_l1"),
                    rule.get("issue_type_l2"),
                    rule.get("business_line"),
                    rule.get("customer_segment"),
                    rule.get("fraud_segment"),

                    rule.get("min_order_value"),
                    rule.get("max_order_value"),
                    rule.get("min_repeat_count"),
                    rule.get("max_repeat_count"),

                    rule.get("sla_breach_required", False),
                    rule.get("evidence_required", False),

                    Json(rule.get("conditions") or {}),

                    action_id,
                    Json(rule.get("action_payload") or {}),

                    rule.get("deterministic", True),
                    rule.get("overrideable", False)

                ))

                inserted += 1

        return inserted


    # --------------------------------------------------------
    # HASH
    # --------------------------------------------------------

    def _hash(self, data: dict) -> str:

        encoded = json.dumps(
            data,
            sort_keys=True
        ).encode()

        return hashlib.sha256(encoded).hexdigest()