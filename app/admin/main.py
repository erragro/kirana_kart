# app/admin/main.py
#
# Kirana Kart Governance Control Plane
# Runs on port 8001. Cardinal ingest plane is separate (main.py, port 8000).

import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from app.config import settings
from app.admin.db import engine
from app.middleware.logging_middleware import (
    CorrelationIdMiddleware,
    configure_logging,
)
from app.metrics import (
    configure_otel,
    metrics_endpoint,
    update_pool_metrics,
    vector_worker_running,
    vector_worker_last_heartbeat,
)

import logging

logger = logging.getLogger("kirana_kart.governance")


# ============================================================
# VECTOR BACKGROUND WORKER
# ============================================================

from app.l45_ml_platform.vectorization.vector_service import VectorService

_worker_thread: threading.Thread | None = None
_worker_running: bool = False
_worker_last_heartbeat: float = 0.0      # unix timestamp
_worker_jobs_processed: int = 0


def _vector_worker_loop() -> None:
    """
    Continuous background job runner — polls kb_vector_jobs every
    VECTOR_WORKER_POLL_INTERVAL seconds for pending vectorization work.

    Production note: For a multi-instance deployment, migrate this to
    a Celery Beat scheduled task to avoid multiple instances competing
    on the same jobs (the FOR UPDATE SKIP LOCKED handles it safely, but
    Celery Beat is more observable and manageable).
    """
    global _worker_running, _worker_last_heartbeat, _worker_jobs_processed

    _worker_running = True
    service = VectorService()

    while _worker_running:
        try:
            service.run_pending_jobs()
            _worker_jobs_processed += 1
        except Exception as exc:
            logger.error("Vector worker error: %s", exc, exc_info=True)

        # Update observability metrics
        _worker_last_heartbeat = time.time()
        vector_worker_running.set(1)
        vector_worker_last_heartbeat.set(_worker_last_heartbeat)
        update_pool_metrics()

        time.sleep(settings.vector_worker_poll_interval)

    _worker_running = False
    vector_worker_running.set(0)


def start_background_worker() -> None:
    """Start the vectorization worker thread (idempotent)."""
    global _worker_thread

    if _worker_thread and _worker_thread.is_alive():
        return

    _worker_thread = threading.Thread(
        target=_vector_worker_loop,
        daemon=True,
        name="kirana-vector-worker",
    )
    _worker_thread.start()
    logger.info(
        "Vector background worker started | poll_interval=%ds",
        settings.vector_worker_poll_interval,
    )


# ============================================================
# FASTAPI LIFESPAN
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    configure_logging()
    configure_otel(app)
    start_background_worker()
    logger.info(
        "Governance control plane starting | version=%s", settings.service_version
    )
    yield
    # --- Shutdown ---
    global _worker_running
    _worker_running = False
    logger.info("Governance control plane shutting down")


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="Kirana Kart Governance Control Plane",
    version=settings.service_version,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Correlation ID injection + structured request logging
app.add_middleware(CorrelationIdMiddleware)

# Prometheus scrape endpoint
app.add_route("/metrics", metrics_endpoint)

# ============================================================
# ROUTE REGISTRATION
# ============================================================

from app.admin.routes.taxonomy import router as taxonomy_router
from app.l1_ingestion.kb_registry.routes import router as kb_router
from app.l45_ml_platform.compiler.routes import router as compiler_router
from app.l45_ml_platform.vectorization.routes import router as vector_router
from app.l45_ml_platform.simulation.routes import router as simulation_router
from app.l5_intelligence.policy_shadow.routes import router as shadow_router

app.include_router(taxonomy_router)
app.include_router(kb_router)
app.include_router(compiler_router)
app.include_router(vector_router)
app.include_router(simulation_router)
app.include_router(shadow_router)


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health", tags=["ops"])
def health():
    """
    Liveness probe — returns 200 if the process is running.
    Does NOT check downstream dependencies (use /system-status for that).
    """
    return {"status": "ok", "service": "governance"}


# ============================================================
# WORKER HEALTH
# ============================================================

@app.get("/health/worker", tags=["ops"])
def worker_health():
    """
    Background vector worker health check.

    Returns:
        alive        — thread is running
        last_heartbeat_s — seconds since the worker last completed a poll cycle
        jobs_processed   — total job poll iterations since startup
        poll_interval_s  — configured poll interval (seconds)

    A last_heartbeat_s value significantly larger than poll_interval_s
    indicates the worker thread is stuck and may need a restart.
    """
    alive = bool(_worker_thread and _worker_thread.is_alive())
    last_heartbeat_ago = (
        round(time.time() - _worker_last_heartbeat, 1)
        if _worker_last_heartbeat else None
    )
    return {
        "alive": alive,
        "last_heartbeat_s": last_heartbeat_ago,
        "jobs_processed": _worker_jobs_processed,
        "poll_interval_s": settings.vector_worker_poll_interval,
    }


# ============================================================
# SYSTEM STATUS
# ============================================================

@app.get("/system-status", tags=["ops"])
def system_status():
    """
    Readiness probe — checks all downstream dependencies.
    Use this for load-balancer health checks and alerting.
    """
    from app.admin.redis_client import ping as redis_ping

    status: dict = {
        "database":            "unknown",
        "redis":               "unknown",
        "weaviate":            "unknown",
        "active_version":      None,
        "shadow_version":      None,
        "vector_worker": {
            "alive":            bool(_worker_thread and _worker_thread.is_alive()),
            "last_heartbeat_s": (
                round(time.time() - _worker_last_heartbeat, 1)
                if _worker_last_heartbeat else None
            ),
        },
    }

    # Database
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        status["database"] = "connected"
    except Exception:
        status["database"] = "error"

    # Redis
    status["redis"] = "connected" if redis_ping() else "error"

    # Weaviate
    try:
        import weaviate
        client = weaviate.Client(
            f"http://{settings.weaviate_host}:{settings.weaviate_http_port}"
        )
        status["weaviate"] = "connected" if client.is_ready() else "not_ready"
    except Exception:
        status["weaviate"] = "error"

    # Active + shadow policy version
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT active_version, shadow_version
                    FROM kirana_kart.kb_runtime_config
                    LIMIT 1
                """)
            ).mappings().first()

        if row:
            status["active_version"] = row["active_version"]
            status["shadow_version"] = row["shadow_version"]
    except Exception:
        pass

    return status
