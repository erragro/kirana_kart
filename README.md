# Kirana Kart — Governance Control Plane + Cardinal Pipeline

**Version:** 3.4.0  
**Stack:** FastAPI · PostgreSQL · Redis · Weaviate · OpenAI · Celery  
**Python:** 3.13+

---

## What Is This?

Kirana Kart is a **policy governance and automated resolution engine** for e-commerce/quick-commerce operations. It manages the full lifecycle of business rules — from human-authored markdown documents all the way to vectorized, published policy versions that power automated ticket resolution.

The system operates as a **control plane**, not an agent runtime. It governs:

- **What rules exist** (KB Registry + Taxonomy)
- **How rules are compiled** (LLM-driven structured extraction)
- **How rules are tested before going live** (Simulation + Shadow Mode)
- **How rules are searched at resolution time** (Weaviate vector store)
- **How inbound tickets are ingested, deduplicated, enriched, and dispatched** (Cardinal Pipeline)

Business rules have direct ₹ P&L impact (the included spec covers ~₹82.8 Crore in annual refund exposure), so every mutation is versioned, snapshotted, and auditable.

---

## Architecture Overview

### Governance Control Plane

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

### Cardinal Pipeline

```
POST /cardinal/entry
        │
        ▼
┌───────────────────┐
│ Phase 1           │  Structural validation, customer block check
│ Validator         │
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│ Normaliser        │  Parse source payload → write to fdraw → CanonicalPayload
│ (fdraw write)     │  sl == ticket_id via nextval/currval atomic INSERT
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│ Phase 2           │  SHA-256 hash of request.payload → Redis dedup check
│ Deduplicator      │  24h window · audit log to postgres
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│ Phase 3           │  Source verification · thread grouping · connector_id
│ Source Handler    │
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│ Phase 4           │  Customer profile · risk · order context · policy version
│ Enricher          │
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│ Phase 5           │  execution_id · execution plan · Redis stream dispatch
│ Dispatcher        │
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│ Celery Workers    │  L4 agents consume from Redis streams
│ (L4 agents)       │  LLM stages: classify → evaluate → validate → respond
└───────────────────┘

HTTP Responses:
  202  Accepted          → ticket queued, execution_id returned
  200  DuplicateResponse → same payload within 24h window
  422  ValidationError   → structural failure
  403  CustomerBlocked   → blocked customer
  401  SourceVerificationFailed
  503  PolicyUnavailable / DispatchFailed
  500  SystemError
```

### Layer Naming Convention

| Directory | Layer | Role |
|---|---|---|
| `app/admin` | L0 | Admin: taxonomy CRUD, versioning, RBAC |
| `app/l1_ingestion` | L1 | Raw document upload, KB registry, Cardinal normaliser |
| `app/l15_preprocessing` | L1.5 | KB Bridge: 6-layer validation + compilation |
| `app/l2_cardinal` | L2 | Cardinal pipeline: phases 1–5, routing, dedup |
| `app/l3_sequencer` | L3 | (Reserved) |
| `app/l4_agents` | L4 | Celery workers, LLM agent stubs |
| `app/l45_ml_platform` | L4.5 | Compiler, vectorization, simulation |
| `app/l5_intelligence` | L5 | Shadow policy testing |
| `app/l25_outcome_join` | L2.5 | (Reserved) |

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.10+ (tested on 3.13) |
| PostgreSQL | 14+ |
| Redis | 6+ (required for Cardinal dedup + streams) |
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
pip install fastapi uvicorn sqlalchemy psycopg2-binary python-dotenv \
            openai weaviate-client sentence-transformers pydantic \
            redis celery
```

Or if a `requirements.txt` is present:

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

# Redis (Cardinal dedup + streams + Celery broker)
REDIS_URL="redis://localhost:6379/0"

# Weaviate
WEAVIATE_HOST=127.0.0.1
WEAVIATE_HTTP_PORT=8080
WEAVIATE_GRPC_PORT=50051

# Embedding (local, no cost)
EMBEDDING_MODEL=all-MiniLM-L6-v2

# Admin
ADMIN_TOKEN=your_secure_token_here

# Models
MODEL1="gpt-4o-mini"
MODEL2="gpt-4.1"
MODEL3="o3-mini"
MODEL4="gpt-4o"

# Batch
PROCESS_BATCH_SIZE="10"
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
    policy_version TEXT UNIQUE NOT NULL,
    description TEXT,
    activated_at TIMESTAMP WITH TIME ZONE,
    is_active BOOLEAN DEFAULT FALSE,
    artifact_hash TEXT,
    vector_collection TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    vector_status TEXT
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
    shadow_version TEXT,
    activated_at TIMESTAMP
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

-- Cardinal: raw ticket ingestion table
CREATE TABLE kirana_kart.fdraw (
    sl INTEGER NOT NULL DEFAULT nextval('kirana_kart.fdraw_sl_seq'),
    ticket_id INTEGER NOT NULL,
    group_id VARCHAR(25) NOT NULL,
    group_name VARCHAR,
    cx_email VARCHAR,
    status INTEGER DEFAULT 0,
    subject TEXT,
    description TEXT,
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    tags TEXT,
    code VARCHAR,
    img_flg INTEGER DEFAULT 0,
    attachment BIGINT DEFAULT 0,
    processed INTEGER DEFAULT 0,
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    pipeline_stage VARCHAR DEFAULT 'NEW',
    source VARCHAR DEFAULT 'api',
    connector_id INTEGER,
    thread_id VARCHAR,
    message_count INTEGER DEFAULT 1,
    module VARCHAR,
    canonical_payload JSONB,
    detected_language VARCHAR,
    preprocessing_version VARCHAR,
    preprocessed_text TEXT,
    preprocessing_hash VARCHAR,
    payload_hash VARCHAR,
    queue_name VARCHAR,
    priority_weight INTEGER DEFAULT 5,
    enrichment_payload JSONB,
    semantic_cache_hit BOOLEAN DEFAULT FALSE
);

-- Cardinal: execution plans
CREATE TABLE kirana_kart.cardinal_execution_plans (
    execution_id TEXT PRIMARY KEY,
    execution_mode TEXT,
    org TEXT,
    status TEXT DEFAULT 'queued',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);

-- Cardinal: per-ticket processing state
CREATE TABLE kirana_kart.ticket_processing_state (
    id SERIAL PRIMARY KEY,
    ticket_id INTEGER NOT NULL,
    execution_id TEXT NOT NULL,
    stage_classify TEXT DEFAULT 'pending',
    stage_evaluate TEXT DEFAULT 'pending',
    stage_validate TEXT DEFAULT 'pending',
    stage_respond TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Cardinal: deduplication audit log
CREATE TABLE kirana_kart.deduplication_log (
    id SERIAL PRIMARY KEY,
    payload_hash TEXT NOT NULL,
    original_ticket_id INTEGER,
    duplicate_received_at TIMESTAMP WITH TIME ZONE,
    source TEXT,
    customer_id TEXT,
    channel TEXT,
    action_taken TEXT DEFAULT 'rejected'
);
```

> **Note on `fdraw` sequence:** Create the sequence before the table:
> ```sql
> CREATE SEQUENCE IF NOT EXISTS kirana_kart.fdraw_sl_seq;
> ```

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

### Seed Data

```sql
-- Admin token
INSERT INTO kirana_kart.admin_users (api_token, role)
VALUES ('your_publisher_token', 'publisher');

-- KB runtime config
INSERT INTO kirana_kart.kb_runtime_config (active_version)
VALUES (NULL);

-- Activate a policy version (required for Cardinal pipeline)
UPDATE kirana_kart.policy_versions
SET is_active = true, activated_at = NOW()
WHERE policy_version = 'your_version_label';
```

---

## Cardinal Pipeline

The Cardinal Pipeline is the inbound ticket processing spine. Every ticket — regardless of source (email, chat, voice, webhook) — enters through a single entry point and is routed deterministically.

### Entry Point

```
POST /cardinal/entry
```

**Request body:**
```json
{
  "channel": "email",
  "org": "AcmeCorp",
  "business_line": "ecommerce",
  "module": "delivery",
  "source": "api",
  "payload": {
    "cx_email": "customer@example.com",
    "subject": "Order not received",
    "description": "I placed an order yesterday and it has not arrived.",
    "order_id": "ORD-12345",
    "customer_id": "CX-001"
  },
  "metadata": {
    "environment": "production",
    "called_by": "agent",
    "agent_id": "AGT-123"
  }
}
```

**Responses:**

| Status | Meaning |
|--------|---------|
| `202` | Ticket accepted and queued. Returns `execution_id` + `ticket_id`. |
| `200` | Duplicate — same payload already submitted within 24h. Returns original `ticket_id`. |
| `422` | Validation failure (missing description, invalid order_id format, etc.) |
| `403` | Customer is blocked. |
| `401` | Source verification failed (Freshdesk HMAC / API token). |
| `503` | Policy unavailable or Redis dispatch failed. |
| `500` | Unexpected system error. |

### Execution ID Format

Every processing attempt gets a unique `execution_id`:

```
{mode}_{org}_{timestamp}_{uuid8}

Examples:
  single_AcmeCorp_1771953892_a7b3c9d2   → production call
  single_Sandbox_1771954000_b8c4d3e1    → sandbox/test call
  batch_AcmeCorp_1771954100_c9d5e2f3    → batch processing
```

### Sandbox vs Production

Use `org = "Sandbox"` (or any org prefixed with `Sandbox`) for test calls. Sandbox mode:
- Skips source verification (no HMAC / token check)
- Skips DB order existence check
- Dispatches to the `P4_LOW` priority stream
- Execution ID contains `Sandbox` for easy filtering

### Celery Workers

The pipeline dispatches tickets to Redis Streams. Celery workers consume from these streams and run the LLM stages:

```bash
# Start a worker
celery -A app.l4_agents.worker.celery_app worker --queues=cardinal

# Start beat scheduler (for periodic tasks)
celery -A app.l4_agents.worker.celery_app beat
```

Registered tasks (9 total):
- LLM Stage 0: Classification
- LLM Stage 1: Evaluation
- LLM Stage 2: Validation
- LLM Stage 3: Response generation
- Periodic: stream polling, idle reclaim, risk profile refresh, timeout cleanup, dedup key purge

---

## API Reference

All endpoints are documented interactively at `/docs`. Key routes:

### Health & Status

| Method | Endpoint | Description |
|---|---|---|
| GET | `/health` | Service liveness check |
| GET | `/system-status` | DB + Weaviate + worker status |

### Cardinal (`/cardinal`)

| Method | Endpoint | Description |
|---|---|---|
| POST | `/cardinal/entry` | Ingest a ticket through the full 5-phase pipeline |

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
1. Author writes business rules/uploads in docx, pdf or markdown
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
9. Active policy live — Cardinal pipeline queries Weaviate at resolution time
```

---

## Background Vector Worker

The application starts a daemon thread on startup (`app/admin/main.py`) that polls for pending vector jobs every 10 seconds:

```
startup → start_background_worker()
        → threads.Thread(_vector_worker_loop, daemon=True)
        → every 10s: VectorService.run_pending_jobs()
            → SELECT ... FOR UPDATE SKIP LOCKED  (concurrency safe)
            → fetch rules from rule_registry
            → build semantic text per rule
            → OpenAI text-embedding-3-large (3072 dims, batched)
            → Weaviate KBRule class upsert (delete-then-insert by version)
```

Embedding model: `text-embedding-3-large` (3072 dimensions).  
Local embedding fallback: `all-MiniLM-L6-v2` (configured via `EMBEDDING_MODEL`).

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
├── second_path3.md               # Business rules specification document
├── test_weaviate.py              # Weaviate connection test
│
├── docker/
│   └── weaviate/
│       └── docker-compose.yml    # Weaviate v1.29.4
│
├── app/
│   ├── admin/
│   │   ├── main.py               # FastAPI app, lifecycle, background worker
│   │   ├── db.py                 # psycopg2 connection factory
│   │   ├── redis_client.py       # Redis connection + dedup key helper
│   │   ├── routes/
│   │   │   └── taxonomy.py       # Taxonomy CRUD, versioning, RBAC
│   │   └── services/
│   │       ├── taxonomy_service.py
│   │       └── vector_service.py
│   │
│   ├── l1_ingestion/
│   │   ├── normaliser.py         # Phase 0: fdraw write, CanonicalPayload
│   │   ├── schemas.py            # Pydantic schemas: CardinalIngestRequest etc.
│   │   └── kb_registry/
│   │       ├── routes.py
│   │       ├── kb_registry_service.py
│   │       ├── raw_storage_service.py
│   │       └── markdown_converter.py
│   │
│   ├── l2_cardinal/
│   │   ├── pipeline.py           # Orchestrator: chains phases 1–5
│   │   ├── phase1_validator.py   # Structural + customer block checks
│   │   ├── phase2_deduplicator.py# SHA-256 hash · Redis dedup · audit log
│   │   ├── phase3_handler.py     # Source verify · thread grouping · connector
│   │   ├── phase4_enricher.py    # Customer · risk · order · policy resolution
│   │   └── phase5_dispatcher.py  # execution_id · execution plan · Redis stream
│   │
│   ├── l4_agents/
│   │   ├── worker.py             # Celery app + Redis stream consumer
│   │   └── tasks.py              # 9 registered Celery tasks
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
├── scripts/
│   ├── test_cardinal.py          # Cardinal pipeline test suite (39 tests)
│   ├── test_endpoints.py         # Governance API integration test runner
│   ├── test_kb_upload.py
│   ├── kb_compiler.py
│   ├── analyze_kb_markdown.py
│   └── run_vectorization.py
│
├── data/
│   ├── kb_docs/                  # Source markdown KB documents
│   ├── simulated/                # Sample tickets for simulation
│   └── raw/
│
├── logs/
│   ├── cardinal_test_latest.json # Last Cardinal test run output
│   └── api_test_log.json         # Last governance API test run output
│
├── config/
└── tests/
```

---

## Running Tests

### Cardinal Pipeline Test Suite

```bash
# Redis and PostgreSQL must be running
python scripts/test_cardinal.py
```

Runs 39 tests across: prerequisites → normaliser → phase 1–5 → full pipeline → Celery registration. Results logged to `logs/cardinal_test_latest.json`.

Expected result: **38/39 PASS**, 1 WARN (Celery worker ping — only fires when worker is running).

### Integration Test (Governance API Walkthrough)

```bash
# Make sure the server is running first
python scripts/test_endpoints.py
```

Output is logged to `logs/api_test_log.json`. Tests: health → system-status → upload → compile → publish → vectorize → shadow → simulation.

### Weaviate Connection Test

```bash
python test_weaviate.py
```

### KB Bridge Unit Test

```bash
python app/l15_preprocessing/kb_test.py
```

---

## Common Issues

**`LLM_API_KEY not found`** — `.env` is not in the project root or not loaded. Run from the `kirana_kart/` directory (same level as `.env`).

**`Weaviate instance is not ready`** — Docker container hasn't finished starting. Wait 10–15 seconds after `docker compose up -d` and retry.

**`relation kirana_kart.xxx does not exist`** — DB schema not initialized. Run the DDL from the Database Setup section above.

**`Invalid API token` on taxonomy endpoints** — `X-Admin-Token` header missing or token not in `admin_users` table. Insert a row with `role = 'publisher'` for full access.

**`Embedding count mismatch`** — OpenAI returned fewer embeddings than rules submitted. Usually a rate-limit issue. Reduce `PROCESS_BATCH_SIZE` in `.env`.

**Port 8080 conflict** — Something else is using Weaviate's port. Change `WEAVIATE_HTTP_PORT` in `.env` and the `docker-compose.yml` `ports` mapping to match.

**`Policy version exists but is_active=False`** — The active policy version exists in `policy_versions` but was never activated. Run:
```sql
UPDATE kirana_kart.policy_versions
SET is_active = true, activated_at = NOW()
WHERE policy_version = 'your_version_label';
```

**`null value in column "ticket_id"`** — Old version of `normaliser.py`. The sequence was being advanced twice, leaving `ticket_id` null. Update to the current version which uses `nextval`/`currval` in a single atomic INSERT.

**`Duplicate payload → got 202 instead of 200`** — Old version of `pipeline.py` was passing `canonical.model_dump()` (which includes a freshly assigned `ticket_id`) to the deduplicator instead of `request.payload`. Update to the current version.

---

## Production Considerations

- The background vector worker uses `SKIP LOCKED` — safe to run multiple replicas without job duplication.
- All taxonomy mutations auto-snapshot before the change; rollback is a full restore from snapshot JSON.
- Taxonomy version publish is idempotent — calling `/taxonomy/publish` twice with the same label will not create duplicate vector jobs.
- The `KBRule` Weaviate class uses delete-then-insert per `policy_version` — safe for re-vectorization.
- Shadow results accumulate in `policy_shadow_results` — prune this table periodically in production.
- Cardinal dedup keys have a 24h TTL in Redis — resubmitting the same ticket after 24h will create a new execution.
- `fdraw.sl` and `fdraw.ticket_id` are always equal for non-Freshdesk sources — enforced atomically via `nextval`/`currval` in a single INSERT.
- Always query LLM output tables by both `ticket_id` AND `execution_id` — the same ticket can be reprocessed multiple times with different execution IDs.  │ /vector    │ │ /simulation  │    │ /shadow          │
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
pip install fastapi uvicorn sqlalchemy psycopg2-binary python-dotenv \
            openai weaviate-client sentence-transformers pydantic
```

Or if a `requirements.txt` is present:

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

# Weaviate
WEAVIATE_HOST=127.0.0.1
WEAVIATE_HTTP_PORT=8080
WEAVIATE_GRPC_PORT=50051

# Embedding (local, no cost)
EMBEDDING_MODEL=all-MiniLM-L6-v2

# Admin
ADMIN_TOKEN=your_secure_token_here

# Models
MODEL1="gpt-4o-mini"
MODEL2="gpt-4.1"
MODEL3="o3-mini"
MODEL4="gpt-4o"

# Batch
PROCESS_BATCH_SIZE="10"
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
| GET | `/system-status` | DB + Weaviate + worker status |

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

The application starts a daemon thread on startup (`app/admin/main.py`) that polls for pending vector jobs every 10 seconds:

```
startup → start_background_worker()
        → threads.Thread(_vector_worker_loop, daemon=True)
        → every 10s: VectorService.run_pending_jobs()
            → SELECT ... FOR UPDATE SKIP LOCKED  (concurrency safe)
            → fetch rules from rule_registry
            → build semantic text per rule
            → OpenAI text-embedding-3-large (3072 dims, batched)
            → Weaviate KBRule class upsert (delete-then-insert by version)
```

Embedding model: `text-embedding-3-large` (3072 dimensions).  
Local embedding fallback: `all-MiniLM-L6-v2` (configured via `EMBEDDING_MODEL`).

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
├── second_path3.md               # Business rules specification document
├── test_weaviate.py              # Weaviate connection test
│
├── docker/
│   └── weaviate/
│       └── docker-compose.yml    # Weaviate v1.29.4
│
├── app/
│   ├── admin/
│   │   ├── main.py               # FastAPI app, lifecycle, background worker
│   │   ├── db.py                 # psycopg2 connection factory
│   │   ├── routes/
│   │   │   └── taxonomy.py       # Taxonomy CRUD, versioning, RBAC
│   │   └── services/
│   │       ├── taxonomy_service.py
│   │       └── vector_service.py (admin-layer vector wrapper)
│   │
│   ├── l1_ingestion/
│   │   └── kb_registry/
│   │       ├── routes.py         # Upload, publish, rollback endpoints
│   │       ├── kb_registry_service.py
│   │       ├── raw_storage_service.py
│   │       └── markdown_converter.py
│   │
│   │
│   ├── l45_ml_platform/
│   │   ├── compiler/
│   │   │   ├── routes.py
│   │   │   └── compiler_service.py   # LLM-driven rule extraction
│   │   ├── vectorization/
│   │   │   ├── routes.py
│   │   │   ├── vector_service.py     # Orchestrates embedding + Weaviate
│   │   │   ├── embedding_service.py  # OpenAI embeddings with retry
│   │   │   └── weaviate_client.py    # Weaviate schema + upsert
│   │   └── simulation/
│   │       ├── routes.py
│   │       └── policy_simulation_service.py
│   │
│   └── l5_intelligence/
│       └── policy_shadow/
│           ├── routes.py             # Enable/disable/stats
│           ├── shadow_service.py
│           └── shadow_repository.py
│
├── scripts/
│   ├── test_endpoints.py         # Full API integration test runner
│   ├── test_kb_upload.py
│   ├── kb_compiler.py
│   ├── analyze_kb_markdown.py
│   └── run_vectorization.py
│
├── data/
│   ├── kb_docs/                  # Source markdown KB documents
│   ├── simulated/                # Sample tickets for simulation
│   └── raw/
│
├── logs/
│   └── api_test_log.json         # Last integration test run output
│
├── config/
└── tests/
```

---

## Running Tests

### Integration Test (Full API Walkthrough)

```bash
# Make sure the server is running first
python scripts/test_endpoints.py
```

Output is logged to `logs/api_test_log.json`. The script tests: health → system-status → upload → compile → publish → vectorize → shadow → simulation.

### Weaviate Connection Test

```bash
python test_weaviate.py
```

### KB Bridge Unit Test

```bash
python app/l15_preprocessing/kb_test.py
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

- The background vector worker uses `SKIP LOCKED` — safe to run multiple replicas without job duplication.
- All taxonomy mutations auto-snapshot before the change; rollback is a full restore from snapshot JSON.
- Taxonomy version publish is idempotent — calling `/taxonomy/publish` twice with the same label will not create duplicate vector jobs.
- The `KBRule` Weaviate class uses delete-then-insert per `policy_version` — safe for re-vectorization.
- Shadow results accumulate in `policy_shadow_results` — prune this table periodically in production.
