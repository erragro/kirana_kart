import os
import json
import time
import requests
import datetime
from pathlib import Path


# ============================================================
# CONFIG
# ============================================================

BASE_URL   = "http://127.0.0.1:8000"
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")   # set in env before running

TEST_FILE   = Path("second_path4.md")        # fixed: error message matched this name
VERSION     = "v_test5"
BASELINE    = "v_test4"                      # must exist in DB — used for simulation diff
                                             # if no prior version exists, simulation will
                                             # still run but report 0 differences

LOG_DIR  = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "api_test_log.json"

MAX_FIELD_LENGTH        = 500
VECTORIZE_POLL_INTERVAL = 3     # seconds between status checks
VECTORIZE_TIMEOUT       = 120   # seconds before giving up

results  = []
failures = []


# ============================================================
# HELPERS
# ============================================================

def truncate(value):
    if isinstance(value, str):
        if len(value) > MAX_FIELD_LENGTH:
            return value[:MAX_FIELD_LENGTH] + " ... [TRUNCATED]"
        return value
    if isinstance(value, dict):
        return {k: truncate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [truncate(v) for v in value]
    return value


def log(entry):
    entry["payload"]  = truncate(entry.get("payload"))
    entry["response"] = truncate(entry.get("response"))
    results.append(entry)


# ============================================================
# API REQUEST
# ============================================================

def api_request(method, endpoint, payload=None, expect_status=200, token=True):
    """
    Makes an HTTP request, logs the result, and prints a pass/fail line.

    token=True  → sends X-Admin-Token header (for taxonomy/RBAC routes)
    token=False → no auth header (public endpoints)
    """

    url = BASE_URL + endpoint

    headers = {}
    if token and ADMIN_TOKEN:
        headers["X-Admin-Token"] = ADMIN_TOKEN

    entry = {
        "timestamp":   datetime.datetime.utcnow().isoformat(),
        "endpoint":    endpoint,
        "method":      method,
        "payload":     payload,
        "status_code": None,
        "response":    None,
        "error":       None
    }

    try:
        if method == "GET":
            response = requests.get(url, headers=headers)
        elif method == "POST":
            response = requests.post(url, json=payload, headers=headers)
        elif method == "PUT":
            response = requests.put(url, json=payload, headers=headers)
        elif method == "PATCH":
            response = requests.patch(url, json=payload, headers=headers)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        entry["status_code"] = response.status_code

        try:
            entry["response"] = response.json()
        except Exception:
            entry["response"] = response.text

        # Pass/fail output
        if response.status_code == expect_status:
            print(f"  ✓  {method:5} {endpoint}  →  {response.status_code}")
        else:
            msg = f"  ✗  {method:5} {endpoint}  →  {response.status_code} (expected {expect_status})"
            print(msg)
            failures.append(msg)

    except Exception as e:
        entry["error"] = str(e)
        msg = f"  ✗  {method:5} {endpoint}  →  ERROR: {e}"
        print(msg)
        failures.append(msg)

    log(entry)
    return entry


# ============================================================
# VECTORIZATION POLL
# ============================================================

def wait_for_vectorization(version_label):
    """
    Polls /vectorize/status/{version_label} until status = 'completed'.
    Raises if it fails or times out.
    Needed because POST /vectorize/version enqueues the job
    asynchronously — publish gates on vector_status = 'completed'
    so we must wait before calling publish.
    """

    print(f"\n  ⏳ Waiting for vectorization of {version_label}...")

    deadline = time.time() + VECTORIZE_TIMEOUT

    while time.time() < deadline:

        resp = requests.get(f"{BASE_URL}/vectorize/status/{version_label}")

        try:
            status = resp.json().get("vector_status")
        except Exception:
            status = None

        print(f"     vector_status = {status}")

        if status == "completed":
            print(f"  ✓  Vectorization complete")
            return

        if status == "failed":
            raise Exception(f"Vectorization failed for {version_label}")

        time.sleep(VECTORIZE_POLL_INTERVAL)

    raise Exception(
        f"Vectorization timed out after {VECTORIZE_TIMEOUT}s for {version_label}"
    )


# ============================================================
# STEP MARKER
# ============================================================

def step(title):
    print(f"\n{'='*55}")
    print(f"  {title}")
    print(f"{'='*55}")


# ============================================================
# TEST SUITE
# ============================================================

def run_tests():

    print("\nRunning API test suite...\n")

    # Guard: test file must exist
    if not TEST_FILE.exists():
        raise FileNotFoundError(
            f"{TEST_FILE} not found in working directory. "
            "Place the policy markdown file next to this script."
        )

    markdown = TEST_FILE.read_text()

    # ----------------------------------------------------------
    # Health
    # ----------------------------------------------------------

    step("Health Checks")

    api_request("GET",  "/health",        token=False)
    api_request("GET",  "/system-status", token=False)

    # ----------------------------------------------------------
    # KB Upload
    # ----------------------------------------------------------

    step("KB Upload")

    api_request("POST", "/kb/upload", {
        "document_id":       "test_policy",
        "original_filename": "second_path4.md",
        "original_format":   "md",
        "raw_content":       markdown,
        "uploaded_by":       "test_script",
        "version_label":     VERSION
    }, token=False)

    api_request("GET", f"/kb/active/test_policy", token=False)

    # ----------------------------------------------------------
    # Compiler
    # ----------------------------------------------------------

    step("Compiler")

    # compile-version takes the label in the path — body must be empty
    api_request("POST", f"/compiler/compile-version/{VERSION}", {}, token=False)

    api_request("GET",  f"/compiler/status/{VERSION}", token=False)

    # ----------------------------------------------------------
    # Simulation
    # ----------------------------------------------------------

    step("Simulation")

    # NOTE: using the same version for baseline and candidate is valid
    # for testing that the endpoint works (expect 0 differences).
    # To test actual divergence, set BASELINE to a prior compiled version.
    api_request("POST", "/simulation/run", {
        "baseline_version":  BASELINE,
        "candidate_version": VERSION
    }, token=False)

    # ----------------------------------------------------------
    # Vectorization — with async poll before publish
    # ----------------------------------------------------------

    step("Vectorization")

    api_request("POST", "/vectorize/version", {
        "version_label": VERSION
    }, token=False)

    # Poll until completed — publish will reject if this is skipped
    try:
        wait_for_vectorization(VERSION)
    except Exception as e:
        print(f"  ✗  Vectorization wait failed: {e}")
        failures.append(str(e))

    api_request("GET", f"/vectorize/status/{VERSION}", token=False)

    # ----------------------------------------------------------
    # Publish
    # ----------------------------------------------------------

    step("Publish")

    api_request("POST", "/kb/publish", {
        "version_label": VERSION,
        "published_by":  "test_script"
    }, token=False)

    api_request("GET", "/kb/active-version", token=False)

    # ----------------------------------------------------------
    # Shadow Policy
    # ----------------------------------------------------------

    step("Shadow Policy")

    # /shadow/enable expects {"shadow_version": "..."}
    api_request("POST", "/shadow/enable", {
        "shadow_version": VERSION
    }, token=False)

    api_request("GET", "/shadow/stats", token=False)

    # /shadow/disable takes no body — POST with empty is fine
    api_request("POST", "/shadow/disable", token=False)

    # ----------------------------------------------------------
    # Save logs
    # ----------------------------------------------------------

    with open(LOG_FILE, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n{'='*55}")
    print(f"  TEST RUN COMPLETE")
    print(f"{'='*55}")
    print(f"  Total calls : {len(results)}")
    print(f"  Failures    : {len(failures)}")

    if failures:
        print("\n  Failed steps:")
        for f in failures:
            print(f"    {f}")

    print(f"\n  Full log → {LOG_FILE}\n")


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    run_tests()