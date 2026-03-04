"""
Shadow Repository
=================

Handles persistence for shadow policy evaluation.

Responsibilities:
- Fetch active + shadow runtime versions
- Store shadow comparison results

No business logic.
Pure database access layer.
"""

import logging
from sqlalchemy import text
from sqlalchemy.engine import Engine


logger = logging.getLogger("shadow_repository")
logger.setLevel(logging.INFO)


class ShadowRepository:

    def __init__(self, engine: Engine):
        self.engine = engine

    # ============================================================
    # FETCH ACTIVE + SHADOW POLICY VERSION
    # ============================================================

    def get_runtime_versions(self):

        with self.engine.connect() as conn:

            row = conn.execute(text("""
                SELECT
                    active_version,
                    shadow_version
                FROM kirana_kart.kb_runtime_config
                LIMIT 1
            """)).mappings().first()

        if not row:
            logger.warning("No runtime configuration found.")
            return None, None

        return row["active_version"], row["shadow_version"]

    # ============================================================
    # STORE SHADOW RESULT
    # ============================================================

    def store_shadow_result(
        self,
        ticket_id: str,
        active_version: str,
        shadow_version: str,
        active_action: str,
        shadow_action: str
    ):

        decision_changed = active_action != shadow_action

        try:

            with self.engine.begin() as conn:

                conn.execute(text("""
                    INSERT INTO kirana_kart.policy_shadow_results
                    (
                        ticket_id,
                        active_policy_version,
                        candidate_policy_version,
                        active_action_code,
                        shadow_action_code,
                        decision_changed
                    )
                    VALUES
                    (
                        :ticket,
                        :active,
                        :shadow,
                        :active_action,
                        :shadow_action,
                        :changed
                    )
                """), {
                    "ticket": ticket_id,
                    "active": active_version,
                    "shadow": shadow_version,
                    "active_action": active_action,
                    "shadow_action": shadow_action,
                    "changed": decision_changed
                })

            logger.info(
                f"Shadow logged for ticket {ticket_id} "
                f"(changed={decision_changed})"
            )

            return {
                "shadow_logged": True,
                "decision_changed": decision_changed
            }

        except Exception as e:

            logger.error(f"Failed to store shadow result: {str(e)}")

            raise