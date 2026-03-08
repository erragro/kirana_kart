# Kirana Kart — Governance Control Plane

**Version:** 3.3.0
**Stack:** FastAPI · PostgreSQL · Weaviate · OpenAI · Redis · Prometheus · OpenTelemetry
**Python:** 3.13+

---

## What Is This?

Kirana Kart is a **policy governance and automated resolution engine** for e-commerce/quick-commerce operations. It manages the full lifecycle of business rules — from human-authored markdown documents all the way to vectorized, published policy versions that power automated ticket resolution.

The system operates as a **control plane**, not an agent runtime. It governs:

- **What rules exist** (KB Registry + Taxonomy)
- **How rules are compiled** (LLM-driven structured extraction)
- **How rules are tested before going live** (Simulation + Shadow Mode)
- **How rules are searched at resolution time** (Weaviate vector store)

Business rules have direct ₹ P&L impact (the included spec covers ~₹82.8 Crore in annual refund exposure), so every mutation is versioned, snapshotted, and auditable.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                 FastAPI Application                      │
│                 app/admin/main.py                        │
│                 http://localhost:8000                    │
└──────────────────────┬──────────────────────────────────┘
                       │ Registers 6 Routers
         ┌─────────────┼──────────────────────┐
         │             │                      │
         ▼             ▼                      ▼
  ┌────────────┐ ┌──────────────┐    ┌──────────────────┐
  │ /taxonomy  │ │ /kb          │    │ /compiler        │
  │ L0 Admin   │ │ L1 Ingestion │    │ L4.5 ML Platform │
  └────────────┘ └──────────────┘    └──────────────────┘
         │             │                      │
         ▼             ▼                      ▼
  ┌────────────┐ ┌──────────────┐    ┌──────────────────┐
  │ /vector    │ │ /simulation  │    │ /shadow          │
  │ L4.5       │ │ L4.5         │    │ L5 Intelligence  │
  └────────────┘ └──────────────┘    └──────────────────┘
         │
         ▼
  ┌──────────────────────────┐
  │  Background Vector Worker│
  │  (daemon thread, 10s poll│
  └──────────────────────────┘

External Services:
  PostgreSQL (schema: kirana_kart)
  Weaviate   (class: KBRule)
  OpenAI API (GPT-4o / embeddings)
```

### Layer Naming Convention

| Directory | Layer | Role |
|---|---|---|
| `app/admin` | L0 | Admin: taxonomy CRUD, versioning, RBAC |
| `app/l1_ingestion` | L1 | Raw document upload and registry |
| `app/l15_preprocessing` | L1.5 | KB Bridge: 6-layer validation + compilation |
| `app/l3_sequencer` | L3 | (Reserved) |
| `app/l4_agents` | L4 | (Reserved) Agent runtime stubs |
| `app/l45_ml_platform` | L4.5 | Compiler, vectorization, simulation |
| `app/l5_intelligence` | L5 | Shadow policy testing |
| `app/l25_outcome_join` | L2.5 | (Reserved) |

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.10+ (tested on 3.13) |
| PostgreSQL | 14+ |
| Docker + Docker Compose | For Weaviate |
| OpenAI API Key | Required for compiler and embeddings |

---

## Quick Setup (5 Steps)

### Step 1 — Clone and Create Virtual Environment

```bash
git clone <your-repo-url> kirana_kart
cd kirana_kart

python3 -m venv venv
source venv/bin/activate       # Linux/macOS
# venv\Scripts\activate        # Windows
```

### Step 2 — Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 3 — Configure Environment

Copy `.env` to project root and fill in your values:

```bash
cp .env .env.local   # keep original as reference
```

Required variables:

```env
# LLM
LLM_API_BASE_URL="https://api.openai.com/v1"
LLM_API_KEY="sk-..."

# PostgreSQL
DB_HOST="localhost"
DB_PORT="5432"
DB_NAME="orgintelligence"
DB_USER="orguser"
DB_PASSWORD="your_password"

# Connection pool (optional — defaults shown)
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=20

# Redis — single node (default)
REDIS_URL="redis://localhost:6379/0"
# Redis — cluster mode (set to enable, overrides REDIS_URL)
# REDIS_CLUSTER_NODES="node1:6379,node2:6379,node3:6379"

# Weaviate
WEAVIATE_HOST=127.0.0.1
WEAVIATE_HTTP_PORT=8080
WEAVIATE_GRPC_PORT=50051

# Embedding
EMBEDDING_MODEL=text-embedding-3-large

# Admin
ADMIN_TOKEN=your_secure_token_here

# Models
MODEL1="gpt-4o-mini"
MODEL2="gpt-4.1"
MODEL3="o3-mini"
MODEL4="gpt-4o"

# Batch
PROCESS_BATCH_SIZE="10"

# Logging — "json" for production, "text" for local dev
LOG_FORMAT="json"
LOG_LEVEL="INFO"

# Observability (optional)
OTLP_ENDPOINT=""          # gRPC collector e.g. "otel-collector:4317"
PROMETHEUS_ENABLED="true" # set "false" to disable /metrics
```

> **Security Note:** Never commit `.env` to source control. The included `.env` contains real credentials — rotate them before deploying.

### Step 4 — Start Weaviate

```bash
cd docker/weaviate
docker compose up -d
cd ../..
```

Verify Weaviate is running:

```bash
curl http://localhost:8080/v1/meta
```

### Step 5 — Start the Application

From the project root (one level above `app/`):

```bash
uvicorn app.admin.main:app --reload --host 0.0.0.0 --port 8000
```

Verify everything is working:

```bash
curl http://localhost:8000/health
# → {"status": "ok", "service": "governance"}

curl http://localhost:8000/system-status
# → {"database": "connected", "weaviate": "connected", ...}
```

Interactive API docs: http://localhost:8000/docs

---

## Database Setup

The application uses a dedicated PostgreSQL schema: `kirana_kart`.

### Create the Database and User

```sql
CREATE DATABASE orgintelligence;
CREATE USER orguser WITH PASSWORD 'your_password';
GRANT ALL PRIVILEGES ON DATABASE orgintelligence TO orguser;
```

### Required Tables

The application expects the following tables under the `kirana_kart` schema. Run this DDL to bootstrap:

```sql
CREATE SCHEMA IF NOT EXISTS kirana_kart;

-- Admin users (RBAC)
CREATE TABLE kirana_kart.admin_users (
    id SERIAL PRIMARY KEY,
    api_token TEXT UNIQUE NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('viewer', 'editor', 'publisher')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Issue taxonomy (live)
CREATE TABLE kirana_kart.issue_taxonomy (
    id SERIAL PRIMARY KEY,
    issue_code TEXT UNIQUE NOT NULL,
    label TEXT NOT NULL,
    description TEXT,
    parent_id INT REFERENCES kirana_kart.issue_taxonomy(id),
    level INT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Taxonomy drafts
CREATE TABLE kirana_kart.taxonomy_drafts (
    id SERIAL PRIMARY KEY,
    issue_code TEXT UNIQUE NOT NULL,
    label TEXT NOT NULL,
    description TEXT,
    parent_id INT,
    level INT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Taxonomy versions (immutable snapshots)
CREATE TABLE kirana_kart.issue_taxonomy_versions (
    id SERIAL PRIMARY KEY,
    version_label TEXT UNIQUE NOT NULL,
    snapshot_data JSONB NOT NULL,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'draft' CHECK (status IN ('draft', 'published'))
);

-- Taxonomy runtime config (active version pointer)
CREATE TABLE kirana_kart.taxonomy_runtime_config (
    id SERIAL PRIMARY KEY,
    active_version TEXT
);

-- Taxonomy audit log
CREATE TABLE kirana_kart.issue_taxonomy_audit (
    id SERIAL PRIMARY KEY,
    action_type TEXT NOT NULL,
    issue_code TEXT,
    changed_by TEXT,
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Vector jobs queue (taxonomy)
CREATE TABLE kirana_kart.vector_jobs (
    id SERIAL PRIMARY KEY,
    version_label TEXT NOT NULL,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    error_message TEXT
);

-- Raw KB uploads
CREATE TABLE kirana_kart.kb_raw_uploads (
    id SERIAL PRIMARY KEY,
    document_id TEXT NOT NULL,
    original_filename TEXT,
    original_format TEXT,
    raw_content TEXT NOT NULL,
    markdown_content TEXT,
    uploaded_by TEXT,
    version_label TEXT,
    status TEXT DEFAULT 'uploaded',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Policy versions
CREATE TABLE kirana_kart.policy_versions (
    id SERIAL PRIMARY KEY,
    policy_version TEXT UNIQUE NOT NULL,
    status TEXT DEFAULT 'draft',
    vector_status TEXT DEFAULT 'pending',
    published_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    published_at TIMESTAMP
);

-- Rule registry (compiled rules)
CREATE TABLE kirana_kart.rule_registry (
    id SERIAL PRIMARY KEY,
    rule_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    module_name TEXT,
    rule_type TEXT,
    conditions JSONB,
    numeric_constraints JSONB,
    filters JSONB,
    flags JSONB,
    action_id INT,
    priority INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Master action codes
CREATE TABLE kirana_kart.master_action_codes (
    id SERIAL PRIMARY KEY,
    action_code_id TEXT UNIQUE NOT NULL,
    action_name TEXT NOT NULL,
    description TEXT
);

-- Knowledge base versions (published snapshots)
CREATE TABLE kirana_kart.knowledge_base_versions (
    id SERIAL PRIMARY KEY,
    version_label TEXT UNIQUE NOT NULL,
    snapshot_data JSONB,
    status TEXT DEFAULT 'draft',
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- KB runtime config (active + shadow version pointers)
CREATE TABLE kirana_kart.kb_runtime_config (
    id SERIAL PRIMARY KEY,
    active_version TEXT,
    shadow_version TEXT
);

-- KB vector jobs queue
CREATE TABLE kirana_kart.kb_vector_jobs (
    id SERIAL PRIMARY KEY,
    version_label TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Shadow policy results
CREATE TABLE kirana_kart.policy_shadow_results (
    id SERIAL PRIMARY KEY,
    ticket_id TEXT,
    active_decision JSONB,
    shadow_decision JSONB,
    decision_changed BOOLEAN DEFAULT FALSE,
    evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Snapshot Helper Function

The taxonomy service calls `kirana_kart.create_taxonomy_snapshot()`. Create it:

```sql
CREATE OR REPLACE FUNCTION kirana_kart.create_taxonomy_snapshot(p_label TEXT)
RETURNS TEXT AS $$
DECLARE
    snapshot_json JSONB;
BEGIN
    SELECT jsonb_agg(row_to_json(t))
    INTO snapshot_json
    FROM kirana_kart.issue_taxonomy t;

    INSERT INTO kirana_kart.issue_taxonomy_versions
        (version_label, snapshot_data, created_by, status)
    VALUES
        (p_label, snapshot_json, 'system', 'draft');

    RETURN p_label;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION kirana_kart.rollback_taxonomy(p_label TEXT)
RETURNS VOID AS $$
DECLARE
    snapshot_json JSONB;
    rec JSONB;
BEGIN
    SELECT snapshot_data INTO snapshot_json
    FROM kirana_kart.issue_taxonomy_versions
    WHERE version_label = p_label;

    IF snapshot_json IS NULL THEN
        RAISE EXCEPTION 'Version % not found', p_label;
    END IF;

    DELETE FROM kirana_kart.issue_taxonomy;

    FOR rec IN SELECT * FROM jsonb_array_elements(snapshot_json)
    LOOP
        INSERT INTO kirana_kart.issue_taxonomy
            (id, issue_code, label, description, parent_id, level, is_active)
        VALUES (
            (rec->>'id')::INT,
            rec->>'issue_code',
            rec->>'label',
            rec->>'description',
            (rec->>'parent_id')::INT,
            (rec->>'level')::INT,
            (rec->>'is_active')::BOOLEAN
        );
    END LOOP;
END;
$$ LANGUAGE plpgsql;
```

### Seed an Admin Token

```sql
INSERT INTO kirana_kart.admin_users (api_token, role)
VALUES ('your_publisher_token', 'publisher');

INSERT INTO kirana_kart.kb_runtime_config (active_version)
VALUES (NULL);
```

---

## API Reference

All endpoints are documented interactively at `/docs`. Key routes:

### Health & Status

| Method | Endpoint | Description |
|---|---|---|
| GET | `/health` | Service liveness check |
| GET | `/health/worker` | Vector worker heartbeat + jobs processed |
| GET | `/system-status` | DB + Redis + Weaviate + worker status |
| GET | `/metrics` | Prometheus metrics (text format) |

### Taxonomy (`/taxonomy`)

Requires `X-Admin-Token` header for all requests.

| Method | Endpoint | Role Required | Description |
|---|---|---|---|
| GET | `/taxonomy/` | viewer+ | List all active issue codes |
| GET | `/taxonomy/drafts` | viewer+ | List draft issues |
| GET | `/taxonomy/versions` | viewer+ | List all taxonomy versions |
| GET | `/taxonomy/version/{label}` | viewer+ | Get a version snapshot |
| GET | `/taxonomy/diff?from_version=&to_version=` | viewer+ | Diff two versions |
| GET | `/taxonomy/active-version` | viewer+ | Get current active version |
| GET | `/taxonomy/validate` | editor+ | Validate live taxonomy |
| GET | `/taxonomy/audit` | viewer+ | Fetch audit log |
| POST | `/taxonomy/draft/save` | editor+ | Save a draft issue |
| POST | `/taxonomy/add` | editor+ | Add issue (auto-snapshots) |
| PUT | `/taxonomy/update` | editor+ | Update issue |
| PATCH | `/taxonomy/deactivate` | editor+ | Deactivate issue |
| PATCH | `/taxonomy/reactivate` | editor+ | Reactivate issue |
| POST | `/taxonomy/publish` | publisher | Publish version atomically |
| POST | `/taxonomy/rollback` | publisher | Rollback to a prior version |
| POST | `/taxonomy/vectorize-active` | publisher | Force vectorize active version |
| GET | `/taxonomy/vector-status` | viewer+ | Check vector job status |

### KB Registry (`/kb`)

| Method | Endpoint | Description |
|---|---|---|
| POST | `/kb/upload` | Upload a raw policy document (markdown/text) |
| PUT | `/kb/update/{raw_id}` | Update an existing draft upload |
| POST | `/kb/publish` | Publish a policy version |
| POST | `/kb/rollback/{version_label}` | Rollback to previous version |
| GET | `/kb/raw/{raw_id}` | Fetch a raw upload by ID |
| GET | `/kb/active/{document_id}` | Get active draft for a document |
| GET | `/kb/active-version` | Get the live active policy version |
| GET | `/kb/version/{version}` | Get a published version snapshot |
| GET | `/kb/versions` | List all published versions |

### Compiler (`/compiler`)

| Method | Endpoint | Description |
|---|---|---|
| POST | `/compiler/compile/{version_label}` | Compile a raw upload into structured rules via LLM |

### Vectorization (`/vectorization`)

| Method | Endpoint | Description |
|---|---|---|
| POST | `/vectorization/run` | Run pending vector jobs |
| POST | `/vectorization/version/{label}` | Force vectorize a specific version |
| GET | `/vectorization/status/{label}` | Get vector status for a version |

### Simulation (`/simulation`)

| Method | Endpoint | Description |
|---|---|---|
| POST | `/simulation/run` | Compare two policy versions against sample tickets |
| GET | `/simulation/health` | Module health check |

**Request body:**
```json
{
  "baseline_version": "v1.0.0",
  "candidate_version": "v1.1.0"
}
```

### Shadow Policy (`/shadow`)

| Method | Endpoint | Description |
|---|---|---|
| POST | `/shadow/enable` | Set a shadow version for live traffic testing |
| POST | `/shadow/disable` | Disable shadow policy |
| GET | `/shadow/stats` | Decision change rate for shadow vs active |

---

## Policy Document Lifecycle

```
1. Author writes business rules/uploads in docx,pdf or markdown
          ↓
2. POST /kb/upload
   (raw_content stored in kb_raw_uploads)
          ↓
3. POST /compiler/compile/{version_label}
   (LLM extracts → rule_registry + knowledge_base_versions)
          ↓
4. POST /simulation/run
   (compare candidate vs baseline on sample tickets)
          ↓
5. POST /shadow/enable {"shadow_version": "v1.1.0"}
   (run shadow in production, capture divergence)
          ↓
6. GET /shadow/stats
   (review change_rate_percent, confirm acceptable)
          ↓
7. POST /kb/publish {"version_label": "v1.1.0", "published_by": "ops"}
   (atomic publish + vector job queued)
          ↓
8. Background worker vectorizes rules into Weaviate
   (10s poll loop, SKIP LOCKED safe for concurrency)
          ↓
9. Active policy live — agents query Weaviate at resolution time
```

---

## Background Vector Worker

The application starts a daemon thread on startup (`app/admin/main.py`) that polls for pending vector jobs every `VECTOR_WORKER_POLL_INTERVAL` seconds (default 10):

```
startup → start_background_worker()
        → threading.Thread(_vector_worker_loop, daemon=True)
        → every N seconds: VectorService.run_pending_jobs()
            → SELECT ... FOR UPDATE SKIP LOCKED  (concurrency safe)
            → fetch rules from rule_registry
            → build semantic text per rule
            → OpenAI text-embedding-3-large (3072 dims, batched)
            → Weaviate KBRule class upsert (delete-then-insert by version)
            → update last_heartbeat timestamp + Prometheus gauges
```

Health monitoring: `GET /health/worker` returns `alive`, `last_heartbeat_s`, and `jobs_processed`. A `last_heartbeat_s` value significantly larger than the poll interval indicates a stuck thread.

Embedding model: `text-embedding-3-large` (3072 dimensions), configured via `EMBEDDING_MODEL`.

> **Production note:** For multi-instance deployments, consider migrating the polling loop to a **Celery Beat** scheduled task for better observability and distributed scheduling.

---

## RBAC Model

Three roles enforced via `X-Admin-Token` header lookup against `kirana_kart.admin_users`:

| Role | Permissions |
|---|---|
| `viewer` | Read-only: list, diff, audit, validate |
| `editor` | viewer + add/update/deactivate/reactivate, save drafts |
| `publisher` | editor + publish, rollback, vectorize |

Rate limit: 100 requests / 60 seconds per token (in-memory, resets on restart).

---

## Project Structure

```
kirana_kart/
├── .env                          # Environment config (do not commit)
├── requirements.txt              # All Python dependencies (pinned)
├── pytest.ini                    # Test discovery configuration
├── test_weaviate.py              # Weaviate connection smoke test
│
├── docker/
│   └── weaviate/
│       └── docker-compose.yml    # Weaviate v1.29.4
│
├── app/
│   ├── config.py                 # Centralised Pydantic BaseSettings (all env vars)
│   ├── metrics.py                # Prometheus metrics + OpenTelemetry setup
│   │
│   ├── middleware/
│   │   └── logging_middleware.py # JSON structured logging + correlation ID
│   │
│   ├── admin/
│   │   ├── main.py               # FastAPI app, lifecycle, middleware, worker
│   │   ├── db.py                 # SQLAlchemy engine + session factory + pool
│   │   ├── redis_client.py       # Redis client (single-node or cluster)
│   │   ├── routes/
│   │   │   └── taxonomy.py       # Taxonomy CRUD, versioning, RBAC
│   │   └── services/
│   │       ├── taxonomy_service.py   # SQLAlchemy session-based service
│   │       └── vector_service.py
│   │
│   ├── l1_ingestion/
│   │   ├── schemas.py
│   │   ├── normaliser.py
│   │   └── kb_registry/
│   │       ├── routes.py
│   │       ├── kb_registry_service.py
│   │       ├── raw_storage_service.py
│   │       └── markdown_converter.py
│   │
│   ├── l2_cardinal/              # 5-phase ingest pipeline
│   │   ├── routes.py
│   │   ├── pipeline.py
│   │   ├── phase1_validator.py
│   │   ├── phase2_deduplicator.py
│   │   ├── phase3_handler.py
│   │   ├── phase4_enricher.py
│   │   └── phase5_dispatcher.py
│   │
│   ├── l4_agents/                # Celery worker — 4-stage LLM pipeline
│   │   ├── worker.py
│   │   └── tasks.py
│   │
│   ├── l45_ml_platform/
│   │   ├── compiler/
│   │   │   ├── routes.py
│   │   │   └── compiler_service.py
│   │   ├── vectorization/
│   │   │   ├── routes.py
│   │   │   ├── vector_service.py
│   │   │   ├── embedding_service.py
│   │   │   └── weaviate_client.py
│   │   └── simulation/
│   │       ├── routes.py
│   │       └── policy_simulation_service.py
│   │
│   └── l5_intelligence/
│       └── policy_shadow/
│           ├── routes.py
│           ├── shadow_service.py
│           └── shadow_repository.py
│
├── tests/                        # pytest unit tests (no DB/Redis required)
│   ├── conftest.py
│   ├── test_config.py
│   ├── test_phase1_validator.py
│   ├── test_redis_client.py
│   └── test_logging_middleware.py
│
├── scripts/
│   ├── test_endpoints.py         # Full API integration test runner
│   ├── test_kb_upload.py
│   ├── kb_compiler.py
│   ├── analyze_kb_markdown.py
│   └── run_vectorization.py
│
└── logs/
    └── api_test_log.json
```

---

## Running Tests

### Unit Tests (no DB or Redis required)

```bash
pytest tests/ -v
```

Covers: config validation, Phase 1 validator (all 8 checks + sandbox mode), Redis key builders, and correlation-ID log filter.

### Integration Test (Full API Walkthrough)

```bash
# Make sure the server is running first
python scripts/test_endpoints.py
```

Output is logged to `logs/api_test_log.json`. Tests: health → system-status → upload → compile → publish → vectorize → shadow → simulation.

### Weaviate Connection Test

```bash
python test_weaviate.py
```

---

## Common Issues

**`LLM_API_KEY not found`** — `.env` is not in the project root or not loaded. Run from the `kirana_kart/` directory (same level as `.env`).

**`Weaviate instance is not ready`** — Docker container hasn't finished starting. Wait 10–15 seconds after `docker compose up -d` and retry.

**`relation kirana_kart.xxx does not exist`** — DB schema not initialized. Run the DDL from the Database Setup section above.

**`Invalid API token` on taxonomy endpoints** — `X-Admin-Token` header missing or token not in `admin_users` table. Insert a row with `role = 'publisher'` for full access.

**`Embedding count mismatch`** — OpenAI returned fewer embeddings than rules submitted. Usually a rate-limit issue. Reduce `PROCESS_BATCH_SIZE` in `.env`.

**Port 8080 conflict** — Something else is using Weaviate's port. Change `WEAVIATE_HTTP_PORT` in `.env` and the `docker-compose.yml` `ports` mapping to match.

---

## Production Considerations

**Connection pooling:** All DB access goes through a single shared SQLAlchemy `QueuePool` (10 connections, 20 overflow). No new TCP connection is opened per request. Tune `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` for your traffic profile.

**Redis resilience:** Set `REDIS_CLUSTER_NODES` to run in cluster mode and eliminate the single Redis instance as a SPOF. Slot-level failures only affect the impacted key range.

**Structured logging:** Set `LOG_FORMAT=json` in production to emit JSON log lines. Every line includes `correlation_id`, which maps to the `X-Correlation-ID` request header — pass this header from your API gateway to trace a request end-to-end across services.

**Observability:** Prometheus metrics are available at `/metrics`. Recommended alerts:
- `kirana_kart_vector_worker_running == 0` — worker thread is dead
- `kirana_kart_vector_worker_last_heartbeat_timestamp` stale by > 2× poll interval
- `kirana_kart_pipeline_phase_errors_total` rate spike

**Worker scaling:** The background vector worker uses `FOR UPDATE SKIP LOCKED` — safe to run multiple replicas without job duplication. For multi-instance deployments consider migrating to Celery Beat.

**Taxonomy mutations** auto-snapshot before the change; rollback is a full restore from snapshot JSON.

**Taxonomy version publish** is idempotent — calling `/taxonomy/publish` twice with the same label will not create duplicate vector jobs.

**Weaviate:** The `KBRule` class uses delete-then-insert per `policy_version` — safe for re-vectorization.

**Shadow results** accumulate in `policy_shadow_results` — prune this table periodically in production.
