import hashlib
from typing import Optional, Dict, Any
from sqlalchemy import text
from sqlalchemy.engine import Engine

from .markdown_converter import MarkdownConverter


class KBRawStorageService:
    """
    Production-Grade Raw KB Storage Service

    Responsibilities:
    - Store raw uploads
    - Convert to deterministic markdown
    - Enforce single active draft per document
    - Enforce unique version per document
    - Soft archive previous drafts
    - Support lifecycle (update / deactivate / reactivate)

    Does NOT:
    - Compile policies
    - Touch draft tables
    - Publish versions
    """

    def __init__(self, db_engine: Engine):
        self.engine = db_engine
        self.converter = MarkdownConverter()

    # ============================================================
    # 1️⃣ Upload Raw Document
    # ============================================================

    def upload_document(
        self,
        document_id: str,
        original_filename: str,
        original_format: str,
        raw_content: str,
        uploaded_by: str,
        version_label: str = "draft",
        supersedes_document_id: Optional[str] = None
    ) -> Dict[str, Any]:

        document_id = document_id.strip()
        version_label = version_label.strip()

        markdown_content = self.converter.convert(
            raw_content,
            original_format
        )

        content_hash = self._generate_hash(markdown_content)

        with self.engine.begin() as conn:

            # ----------------------------------------------------
            # Enforce unique (document_id, version_label)
            # ----------------------------------------------------

            existing_version = conn.execute(text("""
                SELECT 1
                FROM kirana_kart.knowledge_base_raw_uploads
                WHERE document_id = :doc_id
                AND version_label = :version_label
            """), {
                "doc_id": document_id,
                "version_label": version_label
            }).scalar()

            if existing_version:
                raise Exception(
                    f"Version '{version_label}' already exists for document '{document_id}'"
                )

            # ----------------------------------------------------
            # Archive existing active draft
            # ----------------------------------------------------

            conn.execute(text("""
                UPDATE kirana_kart.knowledge_base_raw_uploads
                SET is_active = FALSE,
                    registry_status = 'archived',
                    deactivated_at = CURRENT_TIMESTAMP
                WHERE document_id = :doc_id
                AND is_active = TRUE
            """), {"doc_id": document_id})

            # ----------------------------------------------------
            # Insert new draft
            # ----------------------------------------------------

            result = conn.execute(text("""
                INSERT INTO kirana_kart.knowledge_base_raw_uploads (
                    document_id,
                    original_filename,
                    original_format,
                    raw_content,
                    markdown_content,
                    version_label,
                    registry_status,
                    compiled_hash,
                    uploaded_by,
                    supersedes_document_id,
                    is_active
                )
                VALUES (
                    :document_id,
                    :original_filename,
                    :original_format,
                    :raw_content,
                    :markdown_content,
                    :version_label,
                    'draft',
                    :compiled_hash,
                    :uploaded_by,
                    :supersedes_document_id,
                    TRUE
                )
                RETURNING id
            """), {
                "document_id": document_id,
                "original_filename": original_filename,
                "original_format": original_format,
                "raw_content": raw_content,
                "markdown_content": markdown_content,
                "version_label": version_label,
                "compiled_hash": content_hash,
                "uploaded_by": uploaded_by,
                "supersedes_document_id": supersedes_document_id
            })

            new_id = result.scalar()

        return {
            "status": "uploaded",
            "raw_upload_id": new_id,
            "document_id": document_id,
            "version_label": version_label
        }

    # ============================================================
    # 2️⃣ Update Existing Active Draft
    # ============================================================

    def update_document(
        self,
        raw_upload_id: int,
        new_raw_content: str,
        original_format: str
    ) -> Dict[str, Any]:

        markdown_content = self.converter.convert(
            new_raw_content,
            original_format
        )

        content_hash = self._generate_hash(markdown_content)

        with self.engine.begin() as conn:

            existing = conn.execute(text("""
                SELECT registry_status, is_active
                FROM kirana_kart.knowledge_base_raw_uploads
                WHERE id = :id
            """), {"id": raw_upload_id}).mappings().first()

            if not existing:
                raise Exception("Raw document not found")

            if existing["registry_status"] != "draft" or not existing["is_active"]:
                raise Exception("Only active draft documents can be updated")

            conn.execute(text("""
                UPDATE kirana_kart.knowledge_base_raw_uploads
                SET raw_content = :raw_content,
                    markdown_content = :markdown_content,
                    compiled_hash = :compiled_hash,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :id
            """), {
                "id": raw_upload_id,
                "raw_content": new_raw_content,
                "markdown_content": markdown_content,
                "compiled_hash": content_hash
            })

        return {
            "status": "updated",
            "raw_upload_id": raw_upload_id
        }

    # ============================================================
    # 3️⃣ Deactivate Draft
    # ============================================================

    def deactivate_document(self, raw_upload_id: int):

        with self.engine.begin() as conn:

            conn.execute(text("""
                UPDATE kirana_kart.knowledge_base_raw_uploads
                SET is_active = FALSE,
                    registry_status = 'archived',
                    deactivated_at = CURRENT_TIMESTAMP
                WHERE id = :id
                AND registry_status = 'draft'
            """), {"id": raw_upload_id})

    # ============================================================
    # 4️⃣ Reactivate Archived Draft
    # ============================================================

    def reactivate_document(self, raw_upload_id: int):

        with self.engine.begin() as conn:

            row = conn.execute(text("""
                SELECT document_id, registry_status
                FROM kirana_kart.knowledge_base_raw_uploads
                WHERE id = :id
            """), {"id": raw_upload_id}).mappings().first()

            if not row:
                raise Exception("Raw document not found")

            if row["registry_status"] != "archived":
                raise Exception("Only archived documents can be reactivated")

            # Archive currently active draft
            conn.execute(text("""
                UPDATE kirana_kart.knowledge_base_raw_uploads
                SET is_active = FALSE,
                    registry_status = 'archived',
                    deactivated_at = CURRENT_TIMESTAMP
                WHERE document_id = :doc_id
                AND is_active = TRUE
            """), {"doc_id": row["document_id"]})

            # Reactivate selected draft
            conn.execute(text("""
                UPDATE kirana_kart.knowledge_base_raw_uploads
                SET is_active = TRUE,
                    registry_status = 'draft',
                    deactivated_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :id
            """), {"id": raw_upload_id})

    # ============================================================
    # 5️⃣ Fetch Active Draft
    # ============================================================

    def fetch_active_draft(self, document_id: str) -> Optional[Dict[str, Any]]:

        query = text("""
            SELECT *
            FROM kirana_kart.knowledge_base_raw_uploads
            WHERE document_id = :document_id
            AND is_active = TRUE
            ORDER BY uploaded_at DESC
            LIMIT 1
        """)

        with self.engine.connect() as conn:

            result = conn.execute(query, {
                "document_id": document_id
            }).mappings().first()

        return dict(result) if result else None

    # ============================================================
    # 6️⃣ Fetch By ID
    # ============================================================

    def fetch_by_id(self, raw_upload_id: int) -> Optional[Dict[str, Any]]:

        with self.engine.connect() as conn:

            result = conn.execute(text("""
                SELECT *
                FROM kirana_kart.knowledge_base_raw_uploads
                WHERE id = :id
            """), {"id": raw_upload_id}).mappings().first()

        return dict(result) if result else None

    # ============================================================
    # 🔐 Hash Generator
    # ============================================================

    def _generate_hash(self, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()