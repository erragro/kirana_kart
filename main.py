"""
main.py — Cardinal Ingest Plane
=================================
FastAPI application for the Cardinal ingestion pipeline.

This is a SEPARATE service from the governance/admin plane
(app/admin/main.py). They run as independent processes:

    Governance plane:  uvicorn app.admin.main:app --port 8001
    Cardinal plane:    uvicorn main:app --port 8000

Why separate:
    The governance plane manages KB compilation, vectorisation,
    policy publishing, and shadow testing — long-running admin
    operations that should not share a process with the
    high-throughput ingest path.

    The Cardinal ingest plane handles real-time ticket ingestion.
    It must stay lean, fast, and isolated from admin operations.

Routes registered:
    POST /cardinal/ingest   — main ingest endpoint (phase1→5 pipeline)
    GET  /health            — liveness probe
    GET  /system-status     — DB + Redis connectivity check
"""

import os
import time

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

load_dotenv()

# ----------------------------------------------------------------
# APP
# ----------------------------------------------------------------

app = FastAPI(
    title="OrgIntelligence — Cardinal Ingest Plane",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ----------------------------------------------------------------
# ROUTE REGISTRATION
# ----------------------------------------------------------------

from app.l2_cardinal.routes import router as cardinal_router

app.include_router(cardinal_router)

# ----------------------------------------------------------------
# HEALTH
# ----------------------------------------------------------------

@app.get("/health", tags=["Ops"])
def health():
    return {"status": "ok", "service": "cardinal-ingest"}


# ----------------------------------------------------------------
# SYSTEM STATUS
# ----------------------------------------------------------------

@app.get("/system-status", tags=["Ops"])
def system_status():

    status = {
        "database":       "unknown",
        "redis":          "unknown",
        "active_version": None,
    }

    # ---- Database --------------------------------------------------
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST"),
            port=os.getenv("DB_PORT", "5432"),
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
        )
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.close()
        status["database"] = "connected"
    except Exception as e:
        status["database"] = f"error: {e}"

    # ---- Redis -----------------------------------------------------
    try:
        from app.admin.redis_client import get_redis
        r = get_redis()
        r.ping()
        status["redis"] = "connected"
    except Exception as e:
        status["redis"] = f"error: {e}"

    # ---- Active policy version ------------------------------------
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST"),
            port=os.getenv("DB_PORT", "5432"),
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT active_version FROM kirana_kart.kb_runtime_config "
                "ORDER BY id DESC LIMIT 1"
            )
            row = cur.fetchone()
        conn.close()
        status["active_version"] = row[0] if row else None
    except Exception:
        status["active_version"] = None

    return status
