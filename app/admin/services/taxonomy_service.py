# app/admin/services/taxonomy_service.py
#
# All DB access uses get_db_session() — connections come from the shared
# SQLAlchemy pool (pool_size=10, max_overflow=20) defined in app.admin.db.
# No raw psycopg2.connect() calls; every connection is properly pooled.

from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import text

from app.admin.db import get_db_session


# ============================================================
# RBAC
# ============================================================

def get_user_role(api_token: str):
    with get_db_session() as session:
        row = session.execute(
            text("""
                SELECT role
                FROM kirana_kart.admin_users
                WHERE api_token = :token
            """),
            {"token": api_token},
        ).mappings().first()

        if not row:
            raise HTTPException(status_code=401, detail="Invalid API token")

        return row["role"]


def require_role(api_token: str, allowed_roles: list):
    role = get_user_role(api_token)
    if role not in allowed_roles:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return role


# ============================================================
# AUTO SNAPSHOT (BEFORE MUTATION)
# ============================================================

def _auto_snapshot(session) -> str:
    """
    Take an automatic pre-change snapshot within the caller's session.
    The caller is responsible for committing the outer transaction.
    """
    label = f"pre_change_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    session.execute(
        text("SELECT kirana_kart.create_taxonomy_snapshot(:label)"),
        {"label": label},
    )
    return label


# ============================================================
# CRUD (LIVE TABLE)
# ============================================================

def fetch_all_issues(include_inactive=False):
    with get_db_session() as session:
        sql = """
            SELECT id, issue_code, label, description,
                   parent_id, level, is_active
            FROM kirana_kart.issue_taxonomy
            {where}
            ORDER BY level, issue_code
        """.format(
            where="" if include_inactive else "WHERE is_active = TRUE"
        )
        return session.execute(text(sql)).fetchall()


def add_issue(issue_code, label, description, parent_id, level):
    with get_db_session() as session:
        snapshot_label = _auto_snapshot(session)

        session.execute(
            text("""
                INSERT INTO kirana_kart.issue_taxonomy
                (issue_code, label, description, parent_id, level)
                VALUES (:code, :label, :desc, :parent_id, :level)
            """),
            {
                "code": issue_code, "label": label, "desc": description,
                "parent_id": parent_id, "level": level,
            },
        )
        return snapshot_label


def update_issue(issue_code, label, description):
    with get_db_session() as session:
        snapshot_label = _auto_snapshot(session)

        session.execute(
            text("""
                UPDATE kirana_kart.issue_taxonomy
                SET label = :label,
                    description = :desc,
                    updated_at = CURRENT_TIMESTAMP
                WHERE issue_code = :code
            """),
            {"label": label, "desc": description, "code": issue_code},
        )
        return snapshot_label


def deactivate_issue(issue_code):
    with get_db_session() as session:
        snapshot_label = _auto_snapshot(session)
        session.execute(
            text("""
                UPDATE kirana_kart.issue_taxonomy
                SET is_active = FALSE
                WHERE issue_code = :code
            """),
            {"code": issue_code},
        )
        return snapshot_label


def reactivate_issue(issue_code):
    with get_db_session() as session:
        snapshot_label = _auto_snapshot(session)
        session.execute(
            text("""
                UPDATE kirana_kart.issue_taxonomy
                SET is_active = TRUE
                WHERE issue_code = :code
            """),
            {"code": issue_code},
        )
        return snapshot_label


# ============================================================
# ROLLBACK
# ============================================================

def rollback_taxonomy(version_label):
    with get_db_session() as session:
        session.execute(
            text("SELECT kirana_kart.rollback_taxonomy(:label)"),
            {"label": version_label},
        )


# ============================================================
# VERSION MANAGEMENT
# ============================================================

def list_versions():
    with get_db_session() as session:
        return session.execute(
            text("""
                SELECT version_label, created_at, created_by, status
                FROM kirana_kart.issue_taxonomy_versions
                ORDER BY created_at DESC
            """)
        ).fetchall()


def get_version_snapshot(version_label):
    with get_db_session() as session:
        row = session.execute(
            text("""
                SELECT snapshot_data
                FROM kirana_kart.issue_taxonomy_versions
                WHERE version_label = :label
            """),
            {"label": version_label},
        ).mappings().first()

        if not row:
            raise ValueError("Version not found")

        return row["snapshot_data"]


def diff_versions(from_version, to_version):
    old = get_version_snapshot(from_version)
    new = get_version_snapshot(to_version)

    old_map = {x["issue_code"]: x for x in old}
    new_map = {x["issue_code"]: x for x in new}

    return {
        "added":   [k for k in new_map if k not in old_map],
        "removed": [k for k in old_map if k not in new_map],
        "updated": [k for k in new_map if k in old_map and old_map[k] != new_map[k]],
    }


# ============================================================
# DRAFT MANAGEMENT
# ============================================================

def get_draft_issues():
    with get_db_session() as session:
        return session.execute(
            text("""
                SELECT id, issue_code, label, description,
                       parent_id, level, is_active
                FROM kirana_kart.taxonomy_drafts
                ORDER BY level, issue_code
            """)
        ).fetchall()


def save_draft(issue_code, label, description, parent_id, level):
    with get_db_session() as session:
        session.execute(
            text("""
                INSERT INTO kirana_kart.taxonomy_drafts
                (issue_code, label, description, parent_id, level)
                VALUES (:code, :label, :desc, :parent_id, :level)
                ON CONFLICT (issue_code)
                DO UPDATE SET
                    label = EXCLUDED.label,
                    description = EXCLUDED.description,
                    parent_id = EXCLUDED.parent_id,
                    level = EXCLUDED.level,
                    updated_at = CURRENT_TIMESTAMP
            """),
            {
                "code": issue_code, "label": label, "desc": description,
                "parent_id": parent_id, "level": level,
            },
        )


# ============================================================
# PUBLISH (IMMUTABLE + LOCKED + QUEUED)
# ============================================================

def publish_version_atomic(version_label):
    """
    Idempotent publish.
    Prevents duplicate vector jobs.
    Immutable once published.
    Concurrency safe — row-level lock via SELECT ... FOR UPDATE.

    Queues into kirana_kart.kb_vector_jobs — the table the
    background worker (VectorService.run_pending_jobs) polls.
    """
    with get_db_session() as session:
        # Row-level lock on the specific version
        row = session.execute(
            text("""
                SELECT status
                FROM kirana_kart.issue_taxonomy_versions
                WHERE version_label = :label
                FOR UPDATE
            """),
            {"label": version_label},
        ).mappings().first()

        if not row:
            raise ValueError("Version not found")

        current_status = row["status"]

        if current_status == "published":
            # Check for an active vector job — idempotent exit if one exists
            existing = session.execute(
                text("""
                    SELECT status
                    FROM kirana_kart.kb_vector_jobs
                    WHERE version_label = :label
                    AND status IN ('pending', 'running')
                """),
                {"label": version_label},
            ).first()

            if existing:
                return  # already queued — idempotent
        else:
            session.execute(
                text("""
                    UPDATE kirana_kart.issue_taxonomy_versions
                    SET status = 'published'
                    WHERE version_label = :label
                """),
                {"label": version_label},
            )

        # Set as active version (single-row config table)
        session.execute(text("DELETE FROM kirana_kart.taxonomy_runtime_config"))
        session.execute(
            text("""
                INSERT INTO kirana_kart.taxonomy_runtime_config(active_version)
                VALUES (:label)
            """),
            {"label": version_label},
        )

        # Queue vector job (double safety check)
        existing_job = session.execute(
            text("""
                SELECT id FROM kirana_kart.kb_vector_jobs
                WHERE version_label = :label
                AND status IN ('pending', 'running')
            """),
            {"label": version_label},
        ).first()

        if not existing_job:
            session.execute(
                text("""
                    INSERT INTO kirana_kart.kb_vector_jobs(version_label, status)
                    VALUES (:label, 'pending')
                """),
                {"label": version_label},
            )


# ============================================================
# VECTOR JOB QUEUE
# ============================================================

def get_pending_vector_job():
    with get_db_session() as session:
        return session.execute(
            text("""
                SELECT id, version_label
                FROM kirana_kart.kb_vector_jobs
                WHERE status = 'pending'
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            """)
        ).first()


def mark_vector_job_started(job_id):
    with get_db_session() as session:
        session.execute(
            text("""
                UPDATE kirana_kart.kb_vector_jobs
                SET status = 'running',
                    started_at = CURRENT_TIMESTAMP
                WHERE id = :id
            """),
            {"id": job_id},
        )


def mark_vector_job_completed(job_id):
    with get_db_session() as session:
        session.execute(
            text("""
                UPDATE kirana_kart.kb_vector_jobs
                SET status = 'completed',
                    completed_at = CURRENT_TIMESTAMP
                WHERE id = :id
            """),
            {"id": job_id},
        )


# ============================================================
# ACTIVE VERSION
# ============================================================

def get_active_version():
    with get_db_session() as session:
        row = session.execute(
            text("""
                SELECT active_version
                FROM kirana_kart.taxonomy_runtime_config
                ORDER BY id DESC
                LIMIT 1
            """)
        ).mappings().first()

        return row["active_version"] if row else None


# ============================================================
# VALIDATION
# ============================================================

def validate_taxonomy():
    rows = fetch_all_issues(include_inactive=True)
    seen = set()
    errors = []

    for r in rows:
        code  = r[1]
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
    with get_db_session() as session:
        return session.execute(
            text("""
                SELECT action_type, issue_code, changed_by, changed_at
                FROM kirana_kart.issue_taxonomy_audit
                ORDER BY changed_at DESC
                LIMIT :limit
            """),
            {"limit": limit},
        ).fetchall()
