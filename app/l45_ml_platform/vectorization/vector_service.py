"""
Vector Service
==============

Production-grade vectorization orchestration layer.

Responsibilities:
- Fetch pending vector jobs
- Lock job (in_progress)
- Fetch rules for policy_version
- Build semantic text
- Generate embeddings
- Upsert into Weaviate
- Update job + policy version status

Safe for:
- CLI runner
- API invocation
- Cron execution
"""

import os
import json
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import List, Dict, Any
from dotenv import load_dotenv
from pathlib import Path

from .embedding_service import EmbeddingService
from .weaviate_client import WeaviateClient


# ============================================================
# CONFIG
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[4]
load_dotenv(PROJECT_ROOT / ".env")

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

logger = logging.getLogger("vector_service")
logger.setLevel(logging.INFO)


# ============================================================
# VECTOR SERVICE
# ============================================================

class VectorService:

    def __init__(self):

        self.embedding_service = EmbeddingService()
        self.weaviate_client = WeaviateClient()

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

    # ========================================================
    # PUBLIC METHODS
    # ========================================================

    def run_pending_jobs(self):

        conn = self._get_connection()
        conn.autocommit = False

        policy_version = None

        try:

            with conn.cursor(cursor_factory=RealDictCursor) as cur:

                cur.execute("""
                    UPDATE kirana_kart.kb_vector_jobs
                    SET status = 'in_progress',
                        started_at = NOW()
                    WHERE id = (
                        SELECT id
                        FROM kirana_kart.kb_vector_jobs
                        WHERE status = 'pending'
                        ORDER BY id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    RETURNING *;
                """)

                job = cur.fetchone()

                if not job:
                    logger.info("No pending vector jobs.")
                    conn.commit()
                    return

                policy_version = job["version_label"]

                logger.info(
                    f"Starting vectorization for {policy_version}"
                )

                rule_count = self._vectorize_policy(conn, policy_version)

                cur.execute("""
                    UPDATE kirana_kart.kb_vector_jobs
                    SET status = 'completed',
                        completed_at = NOW()
                    WHERE id = %s;
                """, (job["id"],))

                cur.execute("""
                    UPDATE kirana_kart.policy_versions
                    SET vector_status = 'completed'
                    WHERE policy_version = %s;
                """, (policy_version,))

                conn.commit()

                logger.info(
                    f"Vectorization complete for {policy_version}. "
                    f"{rule_count} rules processed."
                )

        except Exception as e:

            conn.rollback()

            logger.error(f"Vectorization failed: {str(e)}")

            if policy_version:
                self._mark_failed_job(policy_version, str(e))

            raise

        finally:
            conn.close()

    # --------------------------------------------------------
    # Force Specific Version Vectorization
    # --------------------------------------------------------

    def vectorize_specific_version(self, version_label: str):

        conn = self._get_connection()
        conn.autocommit = False

        try:

            with conn.cursor() as cur:

                cur.execute("""
                    SELECT 1
                    FROM kirana_kart.policy_versions
                    WHERE policy_version = %s;
                """, (version_label,))

                if not cur.fetchone():
                    raise ValueError(
                        f"Policy version {version_label} does not exist."
                    )

                cur.execute("""
                    UPDATE kirana_kart.policy_versions
                    SET vector_status = 'in_progress'
                    WHERE policy_version = %s;
                """, (version_label,))

            rule_count = self._vectorize_policy(conn, version_label)

            with conn.cursor() as cur:

                cur.execute("""
                    UPDATE kirana_kart.policy_versions
                    SET vector_status = 'completed'
                    WHERE policy_version = %s;
                """, (version_label,))

            conn.commit()

            logger.info(
                f"Manual vectorization complete for {version_label}. "
                f"{rule_count} rules processed."
            )

        except Exception as e:

            conn.rollback()

            logger.error(f"Manual vectorization failed: {str(e)}")

            with conn.cursor() as cur:

                cur.execute("""
                    UPDATE kirana_kart.policy_versions
                    SET vector_status = 'failed'
                    WHERE policy_version = %s;
                """, (version_label,))

                conn.commit()

            raise

        finally:
            conn.close()

    # --------------------------------------------------------
    # Get Vector Status
    # --------------------------------------------------------

    def get_vector_status(self, version_label: str) -> str:

        conn = self._get_connection()

        try:

            with conn.cursor() as cur:

                cur.execute("""
                    SELECT vector_status
                    FROM kirana_kart.policy_versions
                    WHERE policy_version = %s;
                """, (version_label,))

                row = cur.fetchone()

                if not row:
                    raise ValueError(
                        f"Policy version {version_label} not found."
                    )

                return row[0]

        finally:
            conn.close()

    # ========================================================
    # INTERNAL LOGIC
    # ========================================================

    def _vectorize_policy(self, conn, policy_version: str) -> int:

        rules = self._fetch_rules(conn, policy_version)

        if not rules:
            raise ValueError(
                f"No rules found for policy_version={policy_version}"
            )

        logger.info(f"Vectorizing {len(rules)} rules")

        self.weaviate_client.delete_by_policy_version(policy_version)

        texts = [
            self._build_semantic_text(rule)
            for rule in rules
        ]

        embeddings = self.embedding_service.create_embeddings_batch(texts)

        if len(embeddings) != len(texts):
            raise RuntimeError(
                "Embedding count mismatch with rule count."
            )

        vector_payload = []

        for rule, vector, semantic_text in zip(rules, embeddings, texts):

            vector_payload.append({
                "rule_id": rule["rule_id"],
                "module_name": rule["module_name"],
                "rule_type": rule["rule_type"],
                "action_code_id": rule["action_code_id"],
                "action_name": rule["action_name"],
                "semantic_text": semantic_text,
                "vector": vector
            })

        self.weaviate_client.upsert_rules(
            policy_version,
            vector_payload
        )

        return len(vector_payload)

    # --------------------------------------------------------
    # Fetch Rules
    # --------------------------------------------------------

    def _fetch_rules(self, conn, policy_version: str):

        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                SELECT r.rule_id,
                       r.module_name,
                       r.rule_type,
                       r.conditions,
                       r.numeric_constraints,
                       r.filters,
                       r.flags,
                       m.action_code_id,
                       m.action_name
                FROM kirana_kart.rule_registry r
                JOIN kirana_kart.master_action_codes m
                  ON r.action_id = m.id
                WHERE r.policy_version = %s
                ORDER BY r.priority DESC;
            """, (policy_version,))

            return cur.fetchall()

    # --------------------------------------------------------
    # Build Semantic Text
    # --------------------------------------------------------

    def _build_semantic_text(self, rule: Dict[str, Any]) -> str:

        return f"""
Rule ID: {rule['rule_id']}
Module: {rule['module_name']}
Rule Type: {rule['rule_type']}

Filters:
{json.dumps(rule.get('filters'), indent=2)}

Flags:
{json.dumps(rule.get('flags'), indent=2)}

Conditions:
{json.dumps(rule.get('conditions'), indent=2)}

Numeric Constraints:
{json.dumps(rule.get('numeric_constraints'), indent=2)}

Action:
{rule['action_code_id']} - {rule['action_name']}
"""

    # --------------------------------------------------------
    # Mark Failed Job
    # --------------------------------------------------------

    def _mark_failed_job(self, policy_version: str, error_message: str):

        conn = self._get_connection()

        try:

            with conn.cursor() as cur:

                cur.execute("""
                    UPDATE kirana_kart.kb_vector_jobs
                    SET status = 'failed',
                        error_message = %s
                    WHERE version_label = %s
                      AND status = 'in_progress';
                """, (error_message, policy_version))

                cur.execute("""
                    UPDATE kirana_kart.policy_versions
                    SET vector_status = 'failed'
                    WHERE policy_version = %s;
                """, (policy_version,))

                conn.commit()

        finally:
            conn.close()