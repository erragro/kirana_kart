"""
Shadow Policy Service
=====================

Executes runtime shadow policy evaluation.

Responsibilities:
- Evaluate active policy
- Evaluate shadow policy (if enabled)
- Store shadow comparison results

Used by:
- Freshchat agent runtime
- Ticket processing pipeline

No FastAPI dependencies.
"""

import logging
from typing import Dict, Any
from sqlalchemy.engine import Engine

from .shadow_repository import ShadowRepository


logger = logging.getLogger("shadow_policy_service")
logger.setLevel(logging.INFO)


class ShadowPolicyService:

    def __init__(self, engine: Engine, policy_engine):

        """
        engine = SQLAlchemy database engine
        policy_engine = deterministic rule executor
        """

        self.repo = ShadowRepository(engine)
        self.policy_engine = policy_engine

    # ============================================================
    # EVALUATE TICKET
    # ============================================================

    def evaluate_ticket(self, ticket: Dict[str, Any]):

        ticket_id = ticket.get("ticket_id", "unknown")

        active_version, shadow_version = self.repo.get_runtime_versions()

        if not active_version:
            raise Exception("No active policy version configured")

        # ------------------------------------------------
        # ACTIVE POLICY DECISION
        # ------------------------------------------------

        active_action = self.policy_engine.evaluate(
            ticket,
            active_version
        )

        # ------------------------------------------------
        # SHADOW POLICY DECISION
        # ------------------------------------------------

        if shadow_version:

            shadow_action = self.policy_engine.evaluate(
                ticket,
                shadow_version
            )

            self.repo.store_shadow_result(
                ticket_id,
                active_version,
                shadow_version,
                active_action,
                shadow_action
            )

            logger.info(
                f"Shadow evaluated ticket={ticket_id} "
                f"active={active_action} shadow={shadow_action}"
            )

        return active_action