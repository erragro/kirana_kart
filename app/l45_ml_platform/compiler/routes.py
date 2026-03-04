"""
Compiler Routes
===============

FastAPI endpoints for the KB compiler.

Responsibilities:
- Trigger compilation
- Compile specific version
- Inspect compilation status

No business logic here.
Delegates to CompilerService.
"""

import logging
from fastapi import APIRouter, HTTPException
from typing import Dict

from .compiler_service import CompilerService


# ============================================================
# ROUTER CONFIG
# ============================================================

router = APIRouter(
    prefix="/compiler",
    tags=["KB Compiler"]
)

logger = logging.getLogger("compiler_routes")
logger.setLevel(logging.INFO)

compiler_service = CompilerService()


# ============================================================
# COMPILE LATEST DRAFT
# ============================================================

@router.post("/compile-latest")
def compile_latest() -> Dict:

    """
    Compiles the most recently uploaded KB draft.
    """

    try:

        result = compiler_service.compile_latest_draft()

        return {
            "status": "success",
            "message": "Draft compiled successfully",
            "result": result
        }

    except Exception as e:

        logger.error(f"Compile latest failed: {str(e)}")

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


# ============================================================
# COMPILE SPECIFIC VERSION
# ============================================================

@router.post("/compile-version/{version_label}")
def compile_version(version_label: str) -> Dict:

    """
    Compiles a specific KB version.
    """

    try:

        result = compiler_service.compile_version(version_label)

        return {
            "status": "success",
            "version_label": version_label,
            "result": result
        }

    except Exception as e:

        logger.error(f"Compile version failed: {str(e)}")

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


# ============================================================
# COMPILATION STATUS
# ============================================================

@router.get("/status/{version_label}")
def compilation_status(version_label: str):

    """
    Returns compilation status for a policy version.
    """

    conn = None

    try:

        conn = compiler_service._get_connection()

        with conn.cursor() as cur:

            # --------------------------------------------------
            # Policy Version Status
            # --------------------------------------------------

            cur.execute("""
                SELECT policy_version,
                       artifact_hash,
                       is_active
                FROM kirana_kart.policy_versions
                WHERE policy_version = %s
            """, (version_label,))

            policy_row = cur.fetchone()

            if not policy_row:

                raise HTTPException(
                    status_code=404,
                    detail="Policy version not found"
                )

            # --------------------------------------------------
            # Raw Document Status
            # --------------------------------------------------

            cur.execute("""
                SELECT registry_status
                FROM kirana_kart.knowledge_base_raw_uploads
                WHERE version_label = %s
                ORDER BY uploaded_at DESC
                LIMIT 1
            """, (version_label,))

            raw_row = cur.fetchone()

        return {
            "version_label": policy_row[0],
            "artifact_hash": policy_row[1],
            "is_active": policy_row[2],
            "raw_status": raw_row[0] if raw_row else None
        }

    except HTTPException:
        raise

    except Exception as e:

        logger.error(f"Status fetch failed: {str(e)}")

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

    finally:

        if conn:
            conn.close()