"""
Shadow Policy Routes
====================

FastAPI endpoints for managing shadow policy testing.

Responsibilities:
- Enable shadow policy
- Disable shadow policy
- Inspect shadow results

No runtime evaluation here.
Shadow execution happens in the agent runtime.
"""

import os
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from dotenv import load_dotenv


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

DATABASE_URL = (
    f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}"
    f"@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

engine = create_engine(DATABASE_URL)


router = APIRouter(
    prefix="/shadow",
    tags=["Shadow Policy"]
)

logger = logging.getLogger("shadow_routes")
logger.setLevel(logging.INFO)


# ============================================================
# REQUEST MODEL
# ============================================================

class ShadowEnableRequest(BaseModel):
    shadow_version: str


# ============================================================
# ENABLE SHADOW POLICY
# ============================================================

@router.post("/enable")
def enable_shadow(request: ShadowEnableRequest):

    try:

        with engine.begin() as conn:

            conn.execute(text("""
                UPDATE kirana_kart.kb_runtime_config
                SET shadow_version = :version
            """), {
                "version": request.shadow_version
            })

        logger.info(f"Shadow policy enabled: {request.shadow_version}")

        return {
            "status": "shadow_enabled",
            "shadow_version": request.shadow_version
        }

    except Exception as e:

        logger.error(f"Failed to enable shadow policy: {str(e)}")

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


# ============================================================
# DISABLE SHADOW POLICY
# ============================================================

@router.post("/disable")
def disable_shadow():

    try:

        with engine.begin() as conn:

            conn.execute(text("""
                UPDATE kirana_kart.kb_runtime_config
                SET shadow_version = NULL
            """))

        logger.info("Shadow policy disabled")

        return {
            "status": "shadow_disabled"
        }

    except Exception as e:

        logger.error(f"Failed to disable shadow policy: {str(e)}")

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


# ============================================================
# SHADOW STATS
# ============================================================

@router.get("/stats")
def get_shadow_stats():

    try:

        with engine.connect() as conn:

            result = conn.execute(text("""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN decision_changed THEN 1 ELSE 0 END) AS changed
                FROM kirana_kart.policy_shadow_results
            """)).mappings().first()

        total = result["total"] or 0
        changed = result["changed"] or 0

        change_rate = 0
        if total > 0:
            change_rate = round((changed / total) * 100, 2)

        return {
            "tickets_evaluated": total,
            "decision_changes": changed,
            "change_rate_percent": change_rate
        }

    except Exception as e:

        logger.error(f"Failed to fetch shadow stats: {str(e)}")

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )