# app/admin/routes/taxonomy.py

import time
from typing import Optional
from fastapi import APIRouter, HTTPException, Header, Depends, Query
from pydantic import BaseModel, Field

from app.admin.services.taxonomy_service import (
    require_role,
    fetch_all_issues,
    add_issue,
    update_issue,
    deactivate_issue,
    reactivate_issue,
    rollback_taxonomy,
    list_versions,
    get_version_snapshot,
    diff_versions,
    validate_taxonomy,
    fetch_audit_logs,
    publish_version_atomic,
    get_active_version,
    get_draft_issues,
    save_draft,
)

from app.admin.services.vector_service import (
    vectorize_active,
    vectorize_version,
    vector_status,
)

router = APIRouter(prefix="/taxonomy", tags=["taxonomy"])

# ============================================================
# RATE LIMITING
# ============================================================

RATE_LIMIT = 100
WINDOW_SECONDS = 60
_request_log = {}


def rate_limiter(api_token: str):
    now = time.time()
    history = _request_log.get(api_token, [])
    history = [t for t in history if now - t < WINDOW_SECONDS]

    if len(history) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    history.append(now)
    _request_log[api_token] = history


# ============================================================
# AUTH
# ============================================================

def authorize(x_admin_token: str = Header(...)):
    rate_limiter(x_admin_token)
    return x_admin_token


# ============================================================
# RESPONSE FORMATTERS
# ============================================================

def format_issue(row):
    return {
        "id": row[0],
        "issue_code": row[1],
        "label": row[2],
        "description": row[3],
        "parent_id": row[4],
        "level": row[5],
        "is_active": row[6],
    }


def format_version(row):
    return {
        "version_label": row[0],
        "created_at": row[1],
        "created_by": row[2],
        "status": row[3],
    }


def format_audit(row):
    return {
        "action_type": row[0],
        "issue_code": row[1],
        "changed_by": row[2],
        "changed_at": row[3],
    }


# ============================================================
# REQUEST MODELS
# ============================================================

class AddIssueRequest(BaseModel):
    issue_code: str = Field(..., min_length=3)
    label: str
    description: Optional[str] = None
    parent_id: Optional[int] = None
    level: int = Field(..., ge=1)


class UpdateIssueRequest(BaseModel):
    issue_code: str
    label: str
    description: Optional[str] = None


class IssueCodeRequest(BaseModel):
    issue_code: str


class VersionRequest(BaseModel):
    version_label: str


# ============================================================
# READ
# ============================================================

@router.get("/")
def get_all(include_inactive: bool = Query(False), token: str = Depends(authorize)):
    require_role(token, ["viewer", "editor", "publisher"])
    rows = fetch_all_issues(include_inactive)
    return {
        "count": len(rows),
        "issues": [format_issue(r) for r in rows]
    }


@router.get("/drafts")
def drafts(token: str = Depends(authorize)):
    require_role(token, ["viewer", "editor", "publisher"])
    rows = get_draft_issues()
    return {
        "count": len(rows),
        "drafts": [format_issue(r) for r in rows]
    }


@router.get("/versions")
def versions(token: str = Depends(authorize)):
    require_role(token, ["viewer", "editor", "publisher"])
    rows = list_versions()
    return {
        "count": len(rows),
        "versions": [format_version(r) for r in rows]
    }


@router.get("/version/{version_label}")
def version_snapshot(version_label: str, token: str = Depends(authorize)):
    require_role(token, ["viewer", "editor", "publisher"])
    snapshot = get_version_snapshot(version_label)
    return {
        "version": version_label,
        "node_count": len(snapshot),
        "data": snapshot
    }


@router.get("/diff")
def diff(from_version: str, to_version: str, token: str = Depends(authorize)):
    require_role(token, ["viewer", "editor", "publisher"])
    return diff_versions(from_version, to_version)


@router.get("/active-version")
def active_version(token: str = Depends(authorize)):
    require_role(token, ["viewer", "editor", "publisher"])
    return {"active_version": get_active_version()}


@router.get("/validate")
def validate(token: str = Depends(authorize)):
    require_role(token, ["editor", "publisher"])
    errors = validate_taxonomy()
    return {"valid": len(errors) == 0, "errors": errors}


@router.get("/audit")
def audit(limit: int = 100, token: str = Depends(authorize)):
    require_role(token, ["viewer", "editor", "publisher"])
    rows = fetch_audit_logs(limit)
    return {
        "count": len(rows),
        "audit_logs": [format_audit(r) for r in rows]
    }


# ============================================================
# DRAFT SAVE
# ============================================================

@router.post("/draft/save")
def save_draft_endpoint(payload: AddIssueRequest, token: str = Depends(authorize)):
    require_role(token, ["editor", "publisher"])
    save_draft(
        payload.issue_code,
        payload.label,
        payload.description,
        payload.parent_id,
        payload.level
    )
    return {"status": "draft_saved"}


# ============================================================
# LIVE CRUD
# ============================================================

@router.post("/add")
def add(payload: AddIssueRequest, token: str = Depends(authorize)):
    require_role(token, ["editor", "publisher"])
    snapshot = add_issue(
        payload.issue_code,
        payload.label,
        payload.description,
        payload.parent_id,
        payload.level,
    )
    return {"status": "success", "snapshot_created": snapshot}


@router.put("/update")
def update(payload: UpdateIssueRequest, token: str = Depends(authorize)):
    require_role(token, ["editor", "publisher"])
    snapshot = update_issue(
        payload.issue_code,
        payload.label,
        payload.description,
    )
    return {"status": "success", "snapshot_created": snapshot}


@router.patch("/deactivate")
def deactivate(payload: IssueCodeRequest, token: str = Depends(authorize)):
    require_role(token, ["editor", "publisher"])
    snapshot = deactivate_issue(payload.issue_code)
    return {"status": "success", "snapshot_created": snapshot}


@router.patch("/reactivate")
def reactivate(payload: IssueCodeRequest, token: str = Depends(authorize)):
    require_role(token, ["editor", "publisher"])
    snapshot = reactivate_issue(payload.issue_code)
    return {"status": "success", "snapshot_created": snapshot}


# ============================================================
# ROLLBACK
# ============================================================

@router.post("/rollback")
def rollback(payload: VersionRequest, token: str = Depends(authorize)):
    require_role(token, ["publisher"])
    rollback_taxonomy(payload.version_label)
    return {"status": "rolled_back", "version": payload.version_label}


# ============================================================
# PUBLISH
# ============================================================

@router.post("/publish")
def publish(payload: VersionRequest, token: str = Depends(authorize)):
    require_role(token, ["publisher"])
    publish_version_atomic(payload.version_label)
    return {
        "status": "published",
        "version": payload.version_label,
        "vector_job_queued": True
    }


# ============================================================
# VECTOR
# ============================================================

@router.post("/vectorize-active")
def vectorize_current(token: str = Depends(authorize)):
    require_role(token, ["publisher"])
    result = vectorize_active()
    return result


@router.post("/vectorize-version")
def vectorize_specific(payload: VersionRequest, token: str = Depends(authorize)):
    require_role(token, ["publisher"])
    result = vectorize_version(payload.version_label)
    return result


@router.get("/vector-status")
def vector_state(token: str = Depends(authorize)):
    require_role(token, ["viewer", "editor", "publisher"])
    return vector_status()