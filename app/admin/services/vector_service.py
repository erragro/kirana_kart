# app/admin/services/vector_service.py

import os
import time
import traceback
from typing import Dict, List, Any
from dotenv import load_dotenv
import weaviate

from app.admin.services.taxonomy_service import (
    fetch_all_issues,
    get_active_version,
    get_version_snapshot,
    mark_vector_job_completed,
)

from app.admin.db import get_connection

load_dotenv()

# WEAVIATE_URL is constructed from WEAVIATE_HOST + WEAVIATE_HTTP_PORT,
# matching the .env variables used everywhere else in the project.
# (The old WEAVIATE_URL key did not exist in .env, so it always fell
# back to the hardcoded default.)
_WEAVIATE_HOST = os.getenv("WEAVIATE_HOST", "127.0.0.1")
_WEAVIATE_PORT = os.getenv("WEAVIATE_HTTP_PORT", "8080")
WEAVIATE_URL = f"http://{_WEAVIATE_HOST}:{_WEAVIATE_PORT}"

CLASS_NAME = "IssueTaxonomy"

MODEL_NAME = os.getenv(
    "EMBEDDING_MODEL",
    "sentence-transformers/all-MiniLM-L6-v2"
)

# ============================================================
# LAZY MODEL SINGLETON  (FATAL-5 FIX)
# ============================================================
# The model is NOT loaded at import time. Loading it at module
# level caused the entire FastAPI app to crash on startup if
# sentence_transformers was absent, and added multi-second cold
# start latency unconditionally. It is now initialised once on
# first use and reused for all subsequent calls.

_MODEL = None

def _get_model():
    global _MODEL
    if _MODEL is None:
        from sentence_transformers import SentenceTransformer
        _MODEL = SentenceTransformer(MODEL_NAME)
    return _MODEL


# ============================================================
# CLIENT MANAGEMENT
# ============================================================

def get_client():

    max_retries = 3
    retry_delay = 2

    for attempt in range(max_retries):

        try:

            client = weaviate.Client(
                url=WEAVIATE_URL
            )

            # Test connection
            client.schema.get()

            return client

        except Exception as e:

            if attempt < max_retries - 1:

                print(
                    f"Connection attempt {attempt+1} failed, retrying..."
                )

                time.sleep(retry_delay)

            else:

                raise ConnectionError(
                    f"Failed to connect to Weaviate: {str(e)}"
                )


# ============================================================
# SCHEMA MANAGEMENT
# ============================================================

def create_schema_if_not_exists(client):

    schema = client.schema.get()

    classes = [c["class"] for c in schema.get("classes", [])]

    if CLASS_NAME in classes:
        return

    print(f"Creating schema {CLASS_NAME}")

    client.schema.create_class({

        "class": CLASS_NAME,
        "vectorizer": "none",

        "properties": [

            {"name": "issue_code", "dataType": ["text"]},
            {"name": "label", "dataType": ["text"]},
            {"name": "description", "dataType": ["text"]},
            {"name": "full_text", "dataType": ["text"]},
            {"name": "level", "dataType": ["int"]},
            {"name": "taxonomy_version", "dataType": ["text"]},

        ]
    })


# ============================================================
# CLEAR VERSION
# ============================================================

def clear_version(client, version):

    print(f"Clearing vectors for version {version}")

    client.batch.delete_objects(

        class_name=CLASS_NAME,

        where={
            "path": ["taxonomy_version"],
            "operator": "Equal",
            "valueText": version
        }
    )


# ============================================================
# INDEX DATASET
# ============================================================

def _index_dataset(version_label, dataset):

    if not dataset:

        return {
            "version": version_label,
            "count": 0,
            "indexed": 0,
            "errors": 0
        }

    client = None

    indexed = 0
    errors = 0

    try:

        client = get_client()

        create_schema_if_not_exists(client)

        clear_version(client, version_label)

        print(f"Indexing {len(dataset)} records")

        # Resolve model once per batch, not per record
        model = _get_model()

        with client.batch as batch:

            batch.batch_size = 50

            for idx, item in enumerate(dataset):

                try:

                    if not item.get("issue_code") or not item.get("label"):
                        print(f"Skipping record {idx}: missing required fields")
                        errors += 1
                        continue

                    full_text = f"{item['label']}. {item.get('description') or ''}"

                    vector = model.encode(full_text).tolist()

                    batch.add_data_object(

                        data_object={

                            "issue_code": str(item["issue_code"]),
                            "label": str(item["label"]),
                            "description": str(item.get("description") or ""),
                            "full_text": full_text,
                            "level": int(item.get("level", 0)),
                            "taxonomy_version": version_label,

                        },

                        class_name=CLASS_NAME,
                        vector=vector
                    )

                    indexed += 1

                    if (idx + 1) % 100 == 0:
                        print(f"Indexed {idx + 1}/{len(dataset)} records")

                except Exception as e:

                    print(f"Index error at record {idx}: {str(e)}")
                    print(traceback.format_exc())
                    errors += 1

        return {

            "version": version_label,
            "count": len(dataset),
            "indexed": indexed,
            "errors": errors

        }

    finally:

        if client:
            try:
                client.close()
            except:
                pass


# ============================================================
# VECTORIZE ACTIVE VERSION
# ============================================================

def vectorize_active():

    active_version = get_active_version()

    if not active_version:
        raise ValueError("No active version set")

    rows = fetch_all_issues(include_inactive=False)

    dataset = [

        {
            "issue_code": r[1],
            "label": r[2],
            "description": r[3],
            "level": r[5],
        }

        for r in rows
    ]

    result = _index_dataset(active_version, dataset)

    result["status"] = "success"

    return result


# ============================================================
# VECTORIZE SPECIFIC VERSION
# ============================================================

def vectorize_version(version_label):

    snapshot = get_version_snapshot(version_label)

    if not snapshot:
        raise ValueError(f"Version {version_label} not found")

    dataset = [

        {
            "issue_code": item.get("issue_code"),
            "label": item.get("label"),
            "description": item.get("description"),
            "level": item.get("level"),
        }

        for item in snapshot
    ]

    result = _index_dataset(version_label, dataset)

    result["status"] = "success"

    return result


# ============================================================
# BACKGROUND WORKER
# ============================================================

def process_single_vector_job():

    conn = None
    cur = None

    try:

        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, version_label
            FROM kirana_kart.kb_vector_jobs
            WHERE status='pending'
            ORDER BY created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        """)

        job = cur.fetchone()

        if not job:
            return False

        job_id, version_label = job

        cur.execute("""
            UPDATE kirana_kart.kb_vector_jobs
            SET status='running',
                started_at=CURRENT_TIMESTAMP
            WHERE id=%s
        """, (job_id,))

        conn.commit()

    finally:

        if cur:
            cur.close()

        if conn:
            conn.close()

    try:

        result = vectorize_version(version_label)

        mark_vector_job_completed(job_id)

        print(f"Vector job completed {result}")

        return True

    except Exception as e:

        _mark_job_failed(job_id, str(e))

        return True


def _mark_job_failed(job_id, error):
    """
    Marks a vector job as failed.
    Uses try/finally to guarantee connection cleanup even if the
    UPDATE or commit raises — previously a failed commit would leave
    the connection open and the job permanently stuck in 'running'.
    """

    conn = None
    cur = None

    try:

        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            UPDATE kirana_kart.kb_vector_jobs
            SET status='failed',
                completed_at=CURRENT_TIMESTAMP,
                error=%s
            WHERE id=%s
        """, (error[:500], job_id))

        conn.commit()

    finally:

        if cur:
            cur.close()

        if conn:
            conn.close()


# ============================================================
# VECTOR STATUS
# ============================================================

def vector_status():

    client = None

    try:

        client = get_client()

        result = client.query.aggregate(CLASS_NAME).with_meta_count().do()

        data = result.get("data", {}).get("Aggregate", {}).get(CLASS_NAME, [])

        total = data[0]["meta"]["count"] if data else 0

        return {
            "total_vectors": total,
            "status": "healthy"
        }

    except Exception as e:

        print("Vector status error:", str(e))

        return {
            "total_vectors": 0,
            "status": "error",
            "error": str(e)
        }

    finally:

        if client:
            try:
                client.close()
            except:
                pass