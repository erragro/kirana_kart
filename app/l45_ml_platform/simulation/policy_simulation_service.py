"""
Policy Simulation Service
=========================

Runs offline simulations comparing two policy versions.

Used to estimate impact of policy changes before publishing.

This service:
- Loads historical/sample tickets
- Executes baseline policy
- Executes candidate policy
- Reports decision differences

No API logic.
No FastAPI dependencies.
"""

import logging
from sqlalchemy import text


logger = logging.getLogger("policy_simulation")
logger.setLevel(logging.INFO)


class PolicySimulationService:

    def __init__(self, engine):
        self.engine = engine


    # ============================================================
    # PUBLIC ENTRYPOINT
    # ============================================================

    def run_simulation(self, candidate_version: str, baseline_version: str):

        logger.info(
            f"Running simulation baseline={baseline_version} candidate={candidate_version}"
        )

        tickets = self._load_sample_tickets()

        if not tickets:
            raise Exception("No sample tickets found")

        baseline_rules = self._load_rules(baseline_version)
        candidate_rules = self._load_rules(candidate_version)

        differences = []

        for ticket in tickets:

            baseline_action = self._evaluate(ticket, baseline_rules)
            candidate_action = self._evaluate(ticket, candidate_rules)

            if baseline_action != candidate_action:

                differences.append({
                    "ticket_id": ticket["ticket_id"],
                    "baseline": baseline_action,
                    "candidate": candidate_action
                })

        result = {
            "tickets_tested": len(tickets),
            "differences": len(differences),
            "examples": differences[:20]
        }

        logger.info(f"Simulation completed: {result}")

        return result


    # ============================================================
    # LOAD SAMPLE TICKETS
    # ============================================================

    def _load_sample_tickets(self):

        with self.engine.connect() as conn:

            rows = conn.execute(text("""
                SELECT
                    ticket_id,
                    issue_type,
                    order_value,
                    fraud_score,
                    customer_tier,
                    business_line
                FROM kirana_kart.simulation_tickets
                LIMIT 1000
            """)).mappings().all()

        return [dict(r) for r in rows]


    # ============================================================
    # LOAD RULES
    # ============================================================

    def _load_rules(self, version):

        with self.engine.connect() as conn:

            rows = conn.execute(text("""
                SELECT
                    rule_id,
                    module_name,
                    rule_type,
                    priority,
                    rule_scope,

                    issue_type_l1,
                    issue_type_l2,
                    business_line,
                    customer_segment,
                    fraud_segment,

                    min_order_value,
                    max_order_value,
                    min_repeat_count,
                    max_repeat_count,

                    sla_breach_required,
                    evidence_required,

                    conditions,

                    action_id,
                    action_payload,
                    overrideable

                FROM kirana_kart.rule_registry
                WHERE policy_version = :v
                ORDER BY priority DESC
            """), {"v": version}).mappings().all()

        if not rows:
            raise Exception(f"No rules found for version {version}")

        return [dict(r) for r in rows]


    # ============================================================
    # RULE EVALUATION
    # ============================================================

    def _evaluate(self, ticket, rules):

        """
        Deterministic rule evaluation.

        Rules are processed by descending priority.
        First matching rule determines the action.
        """

        for rule in rules:

            if self._rule_matches(ticket, rule):

                return rule["action_id"]

        return "NO_ACTION"


    # ============================================================
    # RULE MATCHING
    # ============================================================

    def _rule_matches(self, ticket, rule):

        # --------------------------------------------------------
        # ISSUE TYPE MATCHING
        # --------------------------------------------------------

        if rule.get("issue_type_l1"):

            if ticket.get("issue_type") != rule["issue_type_l1"]:
                return False

        # --------------------------------------------------------
        # BUSINESS LINE
        # --------------------------------------------------------

        if rule.get("business_line"):

            if ticket.get("business_line") != rule["business_line"]:
                return False

        # --------------------------------------------------------
        # ORDER VALUE
        # --------------------------------------------------------

        if rule.get("min_order_value") is not None:

            if ticket.get("order_value", 0) < rule["min_order_value"]:
                return False

        if rule.get("max_order_value") is not None:

            if ticket.get("order_value", 0) > rule["max_order_value"]:
                return False

        # --------------------------------------------------------
        # FRAUD CONDITIONS
        # --------------------------------------------------------

        conditions = rule.get("conditions") or {}

        fraud_limit = conditions.get("max_fraud_score")

        if fraud_limit is not None:

            if ticket.get("fraud_score", 0) > fraud_limit:
                return False

        # --------------------------------------------------------
        # CUSTOMER TIER
        # --------------------------------------------------------

        required_tier = conditions.get("customer_tier")

        if required_tier:

            if ticket.get("customer_tier") != required_tier:
                return False

        return True