from typing import Dict, Any, List
from sqlalchemy import text
from sqlalchemy.engine import Engine
import json

from .raw_storage_service import KBRawStorageService


class KBRegistryService:
    """
    Production-Grade KB Registry Service

    Responsibilities:
    - Raw upload storage
    - Publish compiled policy versions
    - Runtime version switching
    - Safe rollback

    IMPORTANT:
    Compilation is handled by CompilerService.
    This service only manages lifecycle.
    """

    def __init__(self, db_engine: Engine):
        self.engine = db_engine
        self.raw_service = KBRawStorageService(db_engine)

    # ============================================================
    # 1️⃣ RAW UPLOAD (NO COMPILATION)
    # ============================================================

    def upload_document(
        self,
        document_id: str,
        original_filename: str,
        original_format: str,
        raw_content: str,
        uploaded_by: str,
        version_label: str
    ) -> Dict[str, Any]:

        document_id = document_id.strip()
        version_label = version_label.strip()

        return self.raw_service.upload_document(
            document_id=document_id,
            original_filename=original_filename,
            original_format=original_format,
            raw_content=raw_content,
            uploaded_by=uploaded_by,
            version_label=version_label
        )

    # ============================================================
    # 2️⃣ FETCH RAW DRAFT
    # ============================================================

    def fetch_raw_draft(self, document_id: str) -> Dict[str, Any]:

        document_id = document_id.strip()

        draft = self.raw_service.fetch_active_draft(document_id)

        if not draft:
            raise Exception("No active draft found")

        return draft

    # ============================================================
    # 3️⃣ PUBLISH COMPILED POLICY VERSION
    # ============================================================

    def publish_version(
        self,
        version_label: str,
        published_by: str
    ) -> Dict[str, Any]:

        version_label = version_label.strip()

        with self.engine.begin() as conn:

            # ----------------------------------------------------
            # Verify compiled version exists
            # ----------------------------------------------------

            version_exists = conn.execute(text("""
                SELECT 1
                FROM kirana_kart.policy_versions
                WHERE policy_version = :version_label
            """), {
                "version_label": version_label
            }).scalar()

            if not version_exists:
                raise Exception(
                    f"Policy version '{version_label}' not compiled"
                )

            # ----------------------------------------------------
            # Verify vectorization completed
            # ----------------------------------------------------

            vector_status = conn.execute(text("""
                SELECT vector_status
                FROM kirana_kart.policy_versions
                WHERE policy_version = :version_label
            """), {
                "version_label": version_label
            }).scalar()

            if vector_status != "completed":
                raise Exception(
                    f"Policy version '{version_label}' cannot be published until vectorization completes"
                )

            # ----------------------------------------------------
            # Prevent duplicate publishing
            # ----------------------------------------------------

            already_published = conn.execute(text("""
                SELECT 1
                FROM kirana_kart.knowledge_base_versions
                WHERE version_label = :version_label
            """), {
                "version_label": version_label
            }).scalar()

            if already_published:
                raise Exception(
                    f"Version '{version_label}' already published"
                )

            # ----------------------------------------------------
            # Snapshot rule registry
            # ----------------------------------------------------

            rules = conn.execute(text("""
                SELECT *
                FROM kirana_kart.rule_registry
                WHERE policy_version = :version_label
            """), {
                "version_label": version_label
            }).mappings().all()

            if not rules:
                raise Exception(
                    "No compiled rules found for this version"
                )

            snapshot = [dict(r) for r in rules]

            conn.execute(text("""
                INSERT INTO kirana_kart.knowledge_base_versions (
                    version_label,
                    status,
                    created_by,
                    snapshot_data
                )
                VALUES (
                    :version_label,
                    'published',
                    :created_by,
                    :snapshot
                )
            """), {
                "version_label": version_label,
                "created_by": published_by,
                "snapshot": json.dumps(snapshot, default=str)
            })

            # ----------------------------------------------------
            # Activate runtime version
            # ----------------------------------------------------

            existing_runtime = conn.execute(text("""
                SELECT 1
                FROM kirana_kart.kb_runtime_config
                LIMIT 1
            """)).scalar()

            if existing_runtime:

                conn.execute(text("""
                    UPDATE kirana_kart.kb_runtime_config
                    SET active_version = :version_label
                """), {
                    "version_label": version_label
                })

            else:

                conn.execute(text("""
                    INSERT INTO kirana_kart.kb_runtime_config (
                        active_version
                    )
                    VALUES (:version_label)
                """), {
                    "version_label": version_label
                })

            # NOTE: No kb_vector_jobs insert here.
            # Vectorization is confirmed complete by the vector_status
            # gate above. Inserting a job here would trigger a redundant
            # second vectorization run, doubling embedding cost and
            # overwriting the Weaviate index unnecessarily on every publish.

        return {
            "status": "published",
            "version_label": version_label,
            "rules_snapshot_count": len(snapshot)
        }

    # ============================================================
    # 4️⃣ ROLLBACK RUNTIME VERSION
    # ============================================================

    def rollback(
        self,
        target_version: str
    ) -> Dict[str, Any]:

        target_version = target_version.strip()

        with self.engine.begin() as conn:

            exists = conn.execute(text("""
                SELECT 1
                FROM kirana_kart.knowledge_base_versions
                WHERE version_label = :version_label
            """), {
                "version_label": target_version
            }).scalar()

            if not exists:
                raise Exception(
                    f"Version '{target_version}' not found"
                )

            runtime_exists = conn.execute(text("""
                SELECT 1
                FROM kirana_kart.kb_runtime_config
                LIMIT 1
            """)).scalar()

            if runtime_exists:

                conn.execute(text("""
                    UPDATE kirana_kart.kb_runtime_config
                    SET active_version = :version_label
                """), {
                    "version_label": target_version
                })

            else:

                conn.execute(text("""
                    INSERT INTO kirana_kart.kb_runtime_config (
                        active_version
                    )
                    VALUES (:version_label)
                """), {
                    "version_label": target_version
                })

        return {
            "status": "rollback_complete",
            "active_version": target_version
        }

    # ============================================================
    # 5️⃣ FETCH ACTIVE VERSION
    # ============================================================

    def get_active_version(self) -> str:

        with self.engine.connect() as conn:

            version = conn.execute(text("""
                SELECT active_version
                FROM kirana_kart.kb_runtime_config
                LIMIT 1
            """)).scalar()

        return version

    # ============================================================
    # 6️⃣ LIST PUBLISHED VERSIONS
    # ============================================================

    def list_versions(self) -> List[Dict[str, Any]]:

        with self.engine.connect() as conn:

            rows = conn.execute(text("""
                SELECT
                    version_label,
                    status,
                    created_by,
                    created_at
                FROM kirana_kart.knowledge_base_versions
                ORDER BY created_at DESC
            """)).mappings().all()

        return [dict(r) for r in rows]