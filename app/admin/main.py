# app/admin/main.py

import threading
import time
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from dotenv import load_dotenv

# ------------------------------------------------------------
# Load ENV
# ------------------------------------------------------------

load_dotenv()

# ------------------------------------------------------------
# DATABASE ENGINE (GLOBAL - CREATED ONCE)
# ------------------------------------------------------------

from sqlalchemy import create_engine, text

DATABASE_URL = (
    f"postgresql+psycopg2://{os.getenv('DB_USER')}:"
    f"{os.getenv('DB_PASSWORD')}@"
    f"{os.getenv('DB_HOST')}:"
    f"{os.getenv('DB_PORT', '5432')}/"
    f"{os.getenv('DB_NAME')}"
)

engine = create_engine(
    DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True
)

# ------------------------------------------------------------
# App Initialization
# ------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    start_background_worker()
    yield
    # Shutdown
    global _worker_running
    _worker_running = False
    print("Governance service shutting down...")


app = FastAPI(
    title="Kirana Kart Governance Control Plane",
    version="3.3.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ------------------------------------------------------------
# ROUTE REGISTRATION
# ------------------------------------------------------------

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

# NOTE: Cardinal ingest routes live in the separate Cardinal plane.
# Run that service via: uvicorn main:app --port 8000
# This governance plane runs on:  uvicorn app.admin.main:app --port 8001

# ------------------------------------------------------------
# VECTOR BACKGROUND WORKER
# ------------------------------------------------------------

from app.l45_ml_platform.vectorization.vector_service import VectorService

_worker_thread = None
_worker_running = False


def _vector_worker_loop():
    """
    Continuous background job runner.
    Executes pending vectorization jobs.
    """

    global _worker_running
    _worker_running = True

    service = VectorService()

    while _worker_running:

        try:
            service.run_pending_jobs()

        except Exception as e:
            print(f"[Vector Worker Error] {str(e)}")

        # Prevent tight CPU loop
        time.sleep(10)

    _worker_running = False


def start_background_worker():
    """
    Starts the vectorization worker once.
    Prevents duplicate threads.
    """

    global _worker_thread

    if _worker_thread and _worker_thread.is_alive():
        return

    _worker_thread = threading.Thread(
        target=_vector_worker_loop,
        daemon=True
    )

    _worker_thread.start()


# ------------------------------------------------------------
# FASTAPI LIFECYCLE EVENTS
# ------------------------------------------------------------

# ------------------------------------------------------------
# HEALTH CHECK
# ------------------------------------------------------------

@app.get("/health")
def health():

    return {
        "status": "ok",
        "service": "governance"
    }


# ------------------------------------------------------------
# SYSTEM STATUS
# ------------------------------------------------------------

@app.get("/system-status")
def system_status():

    status = {
        "database": "unknown",
        "weaviate": "unknown",
        "active_version": None,
        "shadow_version": None,
        "vector_worker_running": (
            _worker_thread.is_alive() if _worker_thread else False
        )
    }

    # --------------------------------------------------------
    # DATABASE CHECK
    # --------------------------------------------------------

    try:

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

        status["database"] = "connected"

    except Exception:

        status["database"] = "error"

    # --------------------------------------------------------
    # WEAVIATE CHECK
    # --------------------------------------------------------

    try:

        import weaviate

        host = os.getenv("WEAVIATE_HOST", "127.0.0.1")
        port = os.getenv("WEAVIATE_HTTP_PORT", "8080")

        weaviate_url = f"http://{host}:{port}"

        client = weaviate.Client(weaviate_url)

        if client.is_ready():
            status["weaviate"] = "connected"
        else:
            status["weaviate"] = "not_ready"

    except Exception:

        status["weaviate"] = "error"

    # --------------------------------------------------------
    # ACTIVE + SHADOW POLICY VERSION
    # --------------------------------------------------------

    try:

        with engine.connect() as conn:

            result = conn.execute(text("""
                SELECT active_version, shadow_version
                FROM kirana_kart.kb_runtime_config
                LIMIT 1
            """)).mappings().first()

        if result:

            status["active_version"] = result["active_version"]
            status["shadow_version"] = result["shadow_version"]

    except Exception:

        status["active_version"] = None
        status["shadow_version"] = None

    return status