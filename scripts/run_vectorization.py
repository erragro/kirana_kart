#!/usr/bin/env python3
"""
Run Vectorization Job
=====================

CLI entrypoint for vector job execution.

Intended for:
- Manual execution
- Cron scheduling
- Docker task runner
- CI/CD pipelines

This file contains NO business logic.
It simply invokes VectorService.
"""

import sys
import logging
from pathlib import Path

# ------------------------------------------------------------
# Ensure project root is in path
# ------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

# ------------------------------------------------------------
# Logging setup
# ------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

logger = logging.getLogger("vector_job_runner")

# ------------------------------------------------------------
# Import service
# ------------------------------------------------------------

from app.l45_ml_platform.vectorization.vector_service import VectorService


def main():
    logger.info("Starting vectorization job runner...")

    try:
        service = VectorService()
        service.run_pending_jobs()
        logger.info("Vectorization job runner completed successfully.")

    except Exception as e:
        logger.error(f"Vectorization runner failed: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()