"""
Vectorization Routes
====================

FastAPI endpoints for triggering and monitoring
policy vectorization jobs.

No business logic here.
Delegates to VectorService.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import logging

from .vector_service import VectorService


# ============================================================
# ROUTER CONFIG
# ============================================================

router = APIRouter(prefix="/vectorize", tags=["Vectorization"])

logger = logging.getLogger("vector_routes")
logger.setLevel(logging.INFO)

# Single service instance (avoids repeated client creation)
vector_service = VectorService()


# ------------------------------------------------------------
# Request Models
# ------------------------------------------------------------

class VersionRequest(BaseModel):
    version_label: str


# ------------------------------------------------------------
# Trigger: Run Pending Jobs
# ------------------------------------------------------------

@router.post("/run")
def run_vectorization():
    """
    Runs one pending vectorization job (if exists).
    """

    try:

        result = vector_service.run_pending_jobs()

        if result is None:
            return {
                "status": "ok",
                "message": "No pending vectorization jobs."
            }

        return {
            "status": "success",
            "message": "Vectorization job executed.",
            "result": result
        }

    except Exception as e:

        logger.error(f"Vectorization run failed: {str(e)}")

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


# ------------------------------------------------------------
# Trigger: Force Re-Vectorize Specific Version
# ------------------------------------------------------------

@router.post("/version")
def vectorize_specific_version(request: VersionRequest):
    """
    Force vectorization for a specific policy version.
    """

    try:

        version_label = request.version_label.strip()

        vector_service.vectorize_specific_version(version_label)

        return {
            "status": "success",
            "message": f"Vectorization triggered for version {version_label}"
        }

    except Exception as e:

        logger.error(f"Manual vectorization failed: {str(e)}")

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


# ------------------------------------------------------------
# Status Endpoint
# ------------------------------------------------------------

@router.get("/status/{version_label}")
def get_vector_status(version_label: str):
    """
    Returns vectorization status for a given version.
    """

    try:

        version_label = version_label.strip()

        status = vector_service.get_vector_status(version_label)

        return {
            "version_label": version_label,
            "vector_status": status
        }

    except Exception as e:

        logger.error(f"Status check failed: {str(e)}")

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


# ------------------------------------------------------------
# Health Check
# ------------------------------------------------------------

@router.get("/health")
def vector_health():
    """
    Health check for vectorization service.
    """

    return {
        "status": "ok",
        "service": "vectorization"
    }