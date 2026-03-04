"""
Simulation Routes
=================

FastAPI endpoints for policy simulation.

Responsibilities:
- Trigger policy simulation
- Compare candidate vs baseline policy
- Return simulation results

No business logic here.
Delegates to PolicySimulationService.
"""

import os
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine
from dotenv import load_dotenv

from .policy_simulation_service import PolicySimulationService


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


# ============================================================
# ROUTER
# ============================================================

router = APIRouter(
    prefix="/simulation",
    tags=["Policy Simulation"]
)

logger = logging.getLogger("simulation_routes")
logger.setLevel(logging.INFO)


# ============================================================
# REQUEST MODEL
# ============================================================

class SimulationRequest(BaseModel):
    baseline_version: str
    candidate_version: str


# ============================================================
# RUN SIMULATION
# ============================================================

@router.post("/run")
def run_simulation(request: SimulationRequest):
    """
    Run simulation comparing two policy versions.
    """

    try:

        service = PolicySimulationService(engine)

        result = service.run_simulation(
            candidate_version=request.candidate_version,
            baseline_version=request.baseline_version
        )

        return {
            "status": "success",
            "baseline_version": request.baseline_version,
            "candidate_version": request.candidate_version,
            "result": result
        }

    except Exception as e:

        logger.error(f"Simulation failed: {str(e)}")

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


# ============================================================
# QUICK HEALTH CHECK
# ============================================================

@router.get("/health")
def simulation_health():
    """
    Basic health check for simulation module.
    """

    return {
        "status": "ok",
        "module": "policy_simulation"
    }