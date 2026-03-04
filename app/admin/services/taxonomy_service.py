# app/admin/services/taxonomy_service.py

from datetime import datetime
from fastapi import HTTPException
from app.admin.db import get_connection


# ============================================================
# RBAC
# ============================================================

def get_user_role(api_token: str):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT role
            FROM kirana_kart.admin_users
            WHERE api_token = %s
        """, (api_token,))
        row = cur.fetchone()
        if not row:
            # Raise HTTPException directly so FastAPI returns 401, not 500
            raise HTTPException(status_code=401, detail="Invalid API token")
        return row[0]
    finally:
        cur.close()
        conn.close()


def require_role(api_token: str, allowed_roles: list):
    role = get_user_role(api_token)
    if role not in allowed_roles:
        # Raise HTTPException directly so FastAPI returns 403, not 500
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return role


# ============================================================
# AUTO SNAPSHOT (BEFORE MUTATION)
# ============================================================

def _auto_snapshot(conn):
    cur = conn.cursor()
    label = f"pre_change_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

    try:
        cur.execute(
            "SELECT kirana_kart.create_taxonomy_snapshot(%s)",
            (label,)
        )
    finally:
        cur.close()

    return label


# ============================================================
# CRUD (LIVE TABLE)
# ============================================================

def fetch_all_issues(include_inactive=False):
    conn = get_connection()
    try:
        cur = conn.cursor()

        if include_inactive:
            cur.execute("""
                SELECT id, issue_code, label, description,
                       parent_id, level, is_active
                FROM kirana_kart.issue_taxonomy
                ORDER BY level, issue_code
            """)
        else:
            cur.execute("""
                SELECT id, issue_code, label, description,
                       parent_id, level, is_active
                FROM kirana_kart.issue_taxonomy
                WHERE is_active = TRUE
                ORDER BY level, issue_code
            """)

        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


def add_issue(issue_code, label, description, parent_id, level):
    conn = get_connection()
    try:
        cur = conn.cursor()
        snapshot_label = _auto_snapshot(conn)

        cur.execute("""
            INSERT INTO kirana_kart.issue_taxonomy
            (issue_code, label, description, parent_id, level)
            VALUES (%s, %s, %s, %s, %s)
        """, (issue_code, label, description, parent_id, level))

        conn.commit()
        return snapshot_label
    finally:
        cur.close()
        conn.close()


def update_issue(issue_code, label, description):
    conn = get_connection()
    try:
        cur = conn.cursor()
        snapshot_label = _auto_snapshot(conn)

        cur.execute("""
            UPDATE kirana_kart.issue_taxonomy
            SET label = %s,
                description = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE issue_code = %s
        """, (label, description, issue_code))

        conn.commit()
        return snapshot_label
    finally:
        cur.close()
        conn.close()


def deactivate_issue(issue_code):
    conn = get_connection()
    try:
        cur = conn.cursor()
        snapshot_label = _auto_snapshot(conn)

        cur.execute("""
            UPDATE kirana_kart.issue_taxonomy
            SET is_active = FALSE
            WHERE issue_code = %s
        """, (issue_code,))

        conn.commit()
        return snapshot_label
    finally:
        cur.close()
        conn.close()


def reactivate_issue(issue_code):
    conn = get_connection()
    try:
        cur = conn.cursor()
        snapshot_label = _auto_snapshot(conn)

        cur.execute("""
            UPDATE kirana_kart.issue_taxonomy
            SET is_active = TRUE
            WHERE issue_code = %s
        """, (issue_code,))

        conn.commit()
        return snapshot_label
    finally:
        cur.close()
        conn.close()


# ============================================================
# ROLLBACK
# ============================================================

def rollback_taxonomy(version_label):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT kirana_kart.rollback_taxonomy(%s)",
            (version_label,)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


# ============================================================
# VERSION MANAGEMENT
# ============================================================

def list_versions():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT version_label, created_at, created_by, status
            FROM kirana_kart.issue_taxonomy_versions
            ORDER BY created_at DESC
        """)
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


def get_version_snapshot(version_label):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT snapshot_data
            FROM kirana_kart.issue_taxonomy_versions
            WHERE version_label = %s
        """, (version_label,))
        row = cur.fetchone()
        if not row:
            raise ValueError("Version not found")
        return row[0]
    finally:
        cur.close()
        conn.close()


def diff_versions(from_version, to_version):
    old = get_version_snapshot(from_version)
    new = get_version_snapshot(to_version)

    old_map = {x["issue_code"]: x for x in old}
    new_map = {x["issue_code"]: x for x in new}

    added = [k for k in new_map if k not in old_map]
    removed = [k for k in old_map if k not in new_map]
    updated = [k for k in new_map if k in old_map and old_map[k] != new_map[k]]

    return {
        "added": added,
        "removed": removed,
        "updated": updated
    }


# ============================================================
# DRAFT MANAGEMENT
# ============================================================

def get_draft_issues():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, issue_code, label, description,
                   parent_id, level, is_active
            FROM kirana_kart.taxonomy_drafts
            ORDER BY level, issue_code
        """)
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


def save_draft(issue_code, label, description, parent_id, level):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO kirana_kart.taxonomy_drafts
            (issue_code, label, description, parent_id, level)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (issue_code)
            DO UPDATE SET
                label = EXCLUDED.label,
                description = EXCLUDED.description,
                parent_id = EXCLUDED.parent_id,
                level = EXCLUDED.level,
                updated_at = CURRENT_TIMESTAMP
        """, (issue_code, label, description, parent_id, level))
        conn.commit()
    finally:
        cur.close()
        conn.close()


# ============================================================
# PUBLISH (IMMUTABLE + LOCKED + QUEUED)
# ============================================================

def publish_version_atomic(version_label):
    """
    Idempotent publish.
    Prevents duplicate vector jobs.
    Immutable once published.
    Concurrency safe.

    Queues into kirana_kart.kb_vector_jobs — the table the
    background worker (VectorService.run_pending_jobs) polls.
    """

    conn = get_connection()
    try:
        cur = conn.cursor()

        # Lock specific version row (row-level lock)
        cur.execute("""
            SELECT status
            FROM kirana_kart.issue_taxonomy_versions
            WHERE version_label = %s
            FOR UPDATE
        """, (version_label,))

        row = cur.fetchone()

        if not row:
            raise ValueError("Version not found")

        current_status = row[0]

        # Immutable once published
        if current_status == "published":
            # Check if vector job already exists
            cur.execute("""
                SELECT status
                FROM kirana_kart.kb_vector_jobs
                WHERE version_label = %s
                AND status IN ('pending','running')
            """, (version_label,))
            existing_job = cur.fetchone()

            if existing_job:
                conn.commit()
                return  # idempotent exit

            # If no active job, allow re-queue (manual repair scenario)
        else:
            # Mark as published first time
            cur.execute("""
                UPDATE kirana_kart.issue_taxonomy_versions
                SET status = 'published'
                WHERE version_label = %s
            """, (version_label,))

        # Set active version
        cur.execute("DELETE FROM kirana_kart.taxonomy_runtime_config")

        cur.execute("""
            INSERT INTO kirana_kart.taxonomy_runtime_config(active_version)
            VALUES (%s)
        """, (version_label,))

        # Check if job already exists (double safety)
        cur.execute("""
            SELECT id
            FROM kirana_kart.kb_vector_jobs
            WHERE version_label = %s
            AND status IN ('pending','running')
        """, (version_label,))

        job = cur.fetchone()

        if not job:
            cur.execute("""
                INSERT INTO kirana_kart.kb_vector_jobs(version_label, status)
                VALUES (%s, 'pending')
            """, (version_label,))

        conn.commit()

    finally:
        cur.close()
        conn.close()

# ============================================================
# VECTOR JOB QUEUE
# ============================================================

def get_pending_vector_job():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, version_label
            FROM kirana_kart.kb_vector_jobs
            WHERE status = 'pending'
            ORDER BY created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        """)
        return cur.fetchone()
    finally:
        cur.close()
        conn.close()


def mark_vector_job_started(job_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE kirana_kart.kb_vector_jobs
            SET status = 'running',
                started_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (job_id,))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def mark_vector_job_completed(job_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE kirana_kart.kb_vector_jobs
            SET status = 'completed',
                completed_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (job_id,))
        conn.commit()
    finally:
        cur.close()
        conn.close()


# ============================================================
# ACTIVE VERSION
# ============================================================

def get_active_version():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT active_version
            FROM kirana_kart.taxonomy_runtime_config
            ORDER BY id DESC
            LIMIT 1
        """)
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        cur.close()
        conn.close()


# ============================================================
# VALIDATION
# ============================================================

def validate_taxonomy():
    rows = fetch_all_issues(include_inactive=True)
    seen = set()
    errors = []

    for r in rows:
        code = r[1]
        level = r[5]

        if code in seen:
            errors.append(f"Duplicate issue_code: {code}")
        seen.add(code)

        if level > 4:
            errors.append(f"Level exceeds maximum depth: {code}")

    return errors


# ============================================================
# AUDIT
# ============================================================

def fetch_audit_logs(limit=100):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT action_type, issue_code,
                   changed_by, changed_at
            FROM kirana_kart.issue_taxonomy_audit
            ORDER BY changed_at DESC
            LIMIT %s
        """, (limit,))
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()