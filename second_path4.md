# Kirana Kart — Knowledge Base Business Rules Specification

**Version:** 1.0.0  
**Status:** Draft - Awaiting PM/Finance Approval  
**Last Updated:** February 26, 2026  
**Owner:** Product Management + Finance Team

---

## Document Purpose

This document defines the **exact business rules** that drive automated ticket resolution in the Kirana Kart system. These rules are executed by the L4 Agent Pipeline and must be:

- ✅ **Validated by Product Management** - Customer experience alignment
- ✅ **Approved by Finance** - P&L impact acceptable
- ✅ **Reviewed by Legal/Compliance** - Regulatory requirements met
- ✅ **Signed off by Operations** - Operationally feasible

**Critical Note:** Changes to these rules directly impact:
- Customer satisfaction scores
- Refund leakage (₹ impact)
- Compliance risk
- Fraud exposure
- Operational workload

---

## Table of Contents

1. [Module 1: Resolution Policy (Financial Decision Engine)](#module-1-resolution-policy)
2. [Module 2: Evidence & Validation Rules](#module-2-evidence--validation-rules)
3. [Module 3: Escalation & Risk Override](#module-3-escalation--risk-override)
4. [Module 4: Compliance & Regulatory](#module-4-compliance--regulatory)
5. [Module 5: Fraud & Abuse Intelligence](#module-5-fraud--abuse-intelligence)
6. [Module 6: Food Delivery Intelligence](#module-6-food-delivery-intelligence)
7. [Module 7: FMCG/Quick Commerce Intelligence](#module-7-fmcg-quick-commerce-intelligence)
8. [Module 8: Operational Workflow](#module-8-operational-workflow)
9. [Financial Impact Analysis](#financial-impact-analysis)
10. [Approval Sign-offs](#approval-sign-offs)

---

## Module 1: Resolution Policy (Financial Decision Engine)

**Purpose:** Determines refund amount and action code for valid complaints.

**Finance Owner:** CFO Office  
**Risk Level:** 🔴 High - Direct P&L Impact

### 1.1 Refund Percentage Matrix

**Rule ID:** RP-001  
**Business Logic:** Different issue severities warrant different refund percentages

| Issue Type | Issue Code Pattern | Refund % | Rationale | Annual ₹ Impact Est. |
|------------|-------------------|----------|-----------|---------------------|
| **Critical Issues** |
| Food Safety | FOOD_SAFETY_* | 100% + ₹100 | Legal liability, customer health | ₹2.5 Cr |
| Foreign Object | FOREIGN_OBJECT_* | 100% + ₹100 | Health hazard, brand risk | ₹1.8 Cr |
| Wrong Order | WRONG_ORDER_FULL | 100% | Complete failure of service | ₹12 Cr |
| Veg/Non-Veg Mix | VEG_NONVEG_MIX | 100% + ₹50 | Religious/dietary violation | ₹80 L |
| Spoiled/Rotten | QUALITY_SPOILED | 100% | Food safety, health risk | ₹3.5 Cr |
| **High Severity** |
| Missing Items | MISSING_ITEMS_FULL | 100% | Zero value delivered | ₹15 Cr |
| Burnt/Inedible | QUALITY_BURNT | 80% | Partial utility loss | ₹4 Cr |
| Severely Late | DELAY_SEVERE | 50% + ₹50 | Significant inconvenience | ₹8 Cr |
| Packaging Leak | PACKAGING_LEAK_SEVERE | 70% | Product compromised | ₹2 Cr |
| **Medium Severity** |
| Portion Size | QUALITY_PORTION_SMALL | 30% | Partial value delivered | ₹5 Cr |
| Moderate Delay | DELAY_MODERATE | 25% | Minor inconvenience | ₹6 Cr |
| Quality Issue | QUALITY_TEMPERATURE_COLD | 40% | Reduced enjoyment | ₹7 Cr |
| Missing Item (Partial) | MISSING_ITEMS_PARTIAL | 50% | Partial fulfillment | ₹9 Cr |
| **Low Severity** |
| Packaging Dent | PACKAGING_DENT_MINOR | 10% | Cosmetic only | ₹1 Cr |
| Slight Delay | DELAY_MINOR | 10% | Minimal impact | ₹2 Cr |
| Substitution (Approved) | SUBSTITUTION_APPROVED | 5% | Goodwill gesture | ₹1.5 Cr |

**Total Estimated Annual Impact:** ₹82.8 Crores

**PM Approval Required:** ✅  
**Finance Approval Required:** ✅  
**Last Reviewed:** [Date]

---

### 1.2 Fixed Compensation Rules

**Rule ID:** RP-002  
**Business Logic:** Some issues warrant fixed compensation regardless of order value

| Issue Type | Fixed Amount | Cap | Conditions | Rationale |
|------------|--------------|-----|------------|-----------|
| Data Breach | ₹500 | ₹500 | Personal info exposed | Regulatory requirement |
| Repeated Issue | ₹100 | ₹200 | Same issue 3+ times in 30 days | Customer retention |
| SLA Miss (Gold) | ₹150 | ₹150 | Premium customer, late delivery | Membership promise |
| Allergen Mislabel | ₹200 | ₹200 | Allergic reaction risk | Legal liability |
| Driver Misconduct | ₹100-300 | ₹300 | Based on severity | Safety protocol |

**Finance Note:** Fixed compensation not subject to order value variations. Budget: ₹50L/month.

---

### 1.3 Partial Refund Logic

**Rule ID:** RP-003  
**Business Logic:** When only specific items in order are affected

```
IF issue_scope = "PARTIAL":
    affected_items_value = SUM(item_price WHERE item_id IN affected_items)
    base_refund = affected_items_value × refund_percentage
    
    # Apply minimum refund floor
    IF base_refund < ₹20:
        base_refund = ₹20
    
    # Cap at affected items value
    final_refund = MIN(base_refund, affected_items_value)
```

**Example:**
- Order: ₹500 (5 items, each ₹100)
- Issue: 2 items missing (₹200 value)
- Refund: ₹200 (100% of affected items)

**PM Validation:** Does minimum ₹20 refund make sense for low-value items?  
**Finance Validation:** Is floor of ₹20 acceptable for P&L?

---

### 1.4 Compensation Caps

**Rule ID:** RP-004  
**Business Logic:** Maximum compensation limits to prevent abuse

| Order Value Range | Max Refund Cap | Max Fixed Comp | Total Cap | Override Authority |
|-------------------|----------------|----------------|-----------|-------------------|
| ₹0 - ₹200 | Order value | ₹100 | ₹300 | Auto-approved |
| ₹201 - ₹500 | Order value | ₹150 | ₹650 | Auto-approved |
| ₹501 - ₹1000 | Order value | ₹200 | ₹1200 | Auto-approved |
| ₹1001 - ₹2000 | Order value | ₹250 | ₹2250 | Team Lead |
| ₹2001 - ₹5000 | Order value | ₹300 | ₹5300 | Manager |
| ₹5000+ | Order value | ₹500 | Case-by-case | Senior Manager |

**Finance Control:** Prevents unlimited refund exposure on high-value orders.

---

### 1.5 SLA-Based Compensation

**Rule ID:** RP-005  
**Business Logic:** Compensation for delivery delays

| Customer Tier | Promised SLA | Delay Window | Compensation | Auto-Apply |
|---------------|--------------|--------------|--------------|------------|
| Gold/Premium | 30 min | 31-45 min | ₹50 | Yes |
| Gold/Premium | 30 min | 46-60 min | ₹100 | Yes |
| Gold/Premium | 30 min | 60+ min | ₹150 + 30% refund | Yes |
| Standard | 45 min | 46-60 min | ₹25 | Yes |
| Standard | 45 min | 61-90 min | ₹50 | Yes |
| Standard | 45 min | 90+ min | ₹75 + 20% refund | Yes |

**Conditions:**
- Only applies if customer didn't cancel
- Weather/force majeure: No auto-compensation
- Restaurant delay (not delivery partner): Different matrix

**Finance Impact:** Estimated ₹3.5 Cr/year based on 2% order delay rate.

---

### 1.6 Multiplier Rules

**Rule ID:** RP-006  
**Business Logic:** Increase/decrease compensation based on customer history

```python
def calculate_multiplier(customer_data):
    base_multiplier = 1.0
    
    # Loyalty multiplier
    if customer_data['total_orders'] > 100:
        base_multiplier += 0.1  # +10% for loyal customers
    
    if customer_data['membership_tier'] == 'GOLD':
        base_multiplier += 0.15  # +15% for premium
    
    # Abuse penalty
    if customer_data['complaints_last_30_days'] > 5:
        base_multiplier -= 0.2  # -20% for frequent complainers
    
    if customer_data['fraud_risk_score'] > 0.7:
        base_multiplier -= 0.3  # -30% for high fraud risk
    
    # Cap multiplier
    final_multiplier = MAX(0.5, MIN(1.3, base_multiplier))
    
    return final_multiplier
```

**Example:**
- Base refund: ₹200
- Customer: 150 orders, Gold member, 2 complaints in 30 days
- Multiplier: 1.0 + 0.1 + 0.15 = 1.25
- Final refund: ₹200 × 1.25 = ₹250

**PM Review:** Is loyalty bonus too generous? Does fraud penalty need adjustment?

---

### 1.7 Action Code Mapping

**Rule ID:** RP-007  
**Business Logic:** Determines operational action alongside refund

| Action Code | Description | Financial Impact | Operational Impact |
|-------------|-------------|------------------|-------------------|
| REFUND_FULL | 100% refund to payment method | Order value | Payment processing |
| REFUND_PARTIAL | Percentage refund | Calculated amount | Payment processing |
| CREDIT_WALLET | Add to Kirana wallet | Order value | No payment processing |
| CREDIT_BONUS | Wallet credit + bonus | Order value + 10% | Retention incentive |
| REPLACEMENT | Send replacement order | COGS + delivery | Logistics cost |
| REDELIVERY | Redeliver same order | Delivery cost only | Logistics cost |
| COUPON | Issue discount coupon | Future order impact | Marketing cost |
| ESCALATE | Manual review required | Variable | Agent time |
| REJECT | No compensation | ₹0 | None |

**Finance Preference Order (by cost):**
1. REJECT (₹0)
2. COUPON (deferred cost)
3. CREDIT_WALLET (cash flow neutral)
4. REFUND_PARTIAL (actual cost)
5. REFUND_FULL (full cost)
6. REPLACEMENT (COGS + delivery)

**PM Note:** CREDIT_BONUS has highest retention impact but increases cost by 10%.

---

### 1.8 Auto-Approval Thresholds

**Rule ID:** RP-008  
**Business Logic:** When to auto-approve without human review

```yaml
auto_approval_criteria:
  max_refund_amount: ₹500
  max_total_compensation: ₹750
  min_order_value: ₹50
  max_customer_complaints_30d: 3
  min_customer_orders: 2
  max_fraud_risk_score: 0.5
  
  exclusions:
    - issue_type: FOOD_SAFETY_*
    - issue_type: FOREIGN_OBJECT_*
    - customer_segment: BLOCKED
    - customer_segment: HIGH_RISK
```

**If ALL criteria met:** Auto-approve  
**If ANY exclusion triggered:** Escalate to human

**Finance Control:** Auto-approval limited to ₹500 to cap exposure.  
**Operations Impact:** ~78% of tickets can be auto-resolved.

---

## Module 2: Evidence & Validation Rules

**Purpose:** Determines what evidence is required to validate a complaint.

**Owner:** Operations + Product  
**Risk Level:** 🟡 Medium - Fraud Prevention

### 2.1 Evidence Requirements by Issue Type

**Rule ID:** EV-001

| Issue Type | Image Required | Order Data Required | Additional Validation | Rationale |
|------------|----------------|---------------------|----------------------|-----------|
| **Always Require Image** |
| Food Safety | ✅ Mandatory | ✅ Order ID, Item ID | Health dept. escalation | Legal evidence |
| Foreign Object | ✅ Mandatory | ✅ Order ID, Item ID | Object identification | Fraud prevention |
| Wrong Item | ✅ Mandatory | ✅ Order ID, Expected vs Received | Visual confirmation | Prevent false claims |
| Damaged Packaging | ✅ Mandatory | ✅ Order ID, Item ID | Damage assessment | Fraud prevention |
| Quality Issues (Visual) | ✅ Mandatory | ✅ Order ID | Visual defect | Prevent abuse |
| **Conditional Image** |
| Missing Items | ⚠️ If >₹200 | ✅ Order ID, Items list | Receipt/package photo | High-value protection |
| Portion Size | ⚠️ If >₹300 | ✅ Order ID | Comparison expected | Subjective claims |
| Temperature Issues | ⚠️ If dispute | ✅ Order ID, Delivery time | Timestamp verification | Hard to prove |
| **No Image Required** |
| Delivery Delay | ❌ Not required | ✅ Order ID, SLA time | System timestamp | System validation |
| App/Payment Issues | ❌ Not required | ✅ Transaction ID | System logs | Technical issue |
| Driver Behavior | ❌ Not required | ✅ Order ID, Driver ID | Complaint text | Behavioral issue |

**PM Review:** Are image requirements too strict for some categories?  
**Operations Review:** Can we validate without images for certain issues?

---

### 2.2 Image Validation Standards

**Rule ID:** EV-002  
**Business Logic:** What constitutes valid image evidence

```yaml
valid_image_criteria:
  format: ['jpg', 'jpeg', 'png', 'heic']
  min_size: 50KB
  max_size: 10MB
  min_resolution: 640x480
  max_age: 48_hours  # From order delivery time
  
  content_requirements:
    food_safety:
      - must_show: product
      - must_show: issue (contamination/foreign object)
      - blur_check: false
      
    damaged_packaging:
      - must_show: full_package
      - must_show: damage
      - blur_check: true
      
    wrong_item:
      - must_show: received_item
      - must_show: packaging_label
      - blur_check: false
```

**Auto-Rejection Triggers:**
- Image older than 48 hours from delivery
- Blurry/unclear images (AI confidence < 70%)
- Stock images detected (reverse image search)
- Manipulated images (metadata analysis)

**PM Note:** 48-hour window may be too restrictive for some customers.

---

### 2.3 Order Data Verification Logic

**Rule ID:** EV-003  
**Business Logic:** Validate complaint against order database

```sql
-- Validation checks performed
SELECT 
    o.order_id,
    o.order_value,
    o.order_status,
    o.delivery_time,
    oi.item_id,
    oi.item_name,
    oi.quantity,
    oi.price,
    c.issue_code,
    c.complaint_time,
    
    -- Validation flags
    CASE 
        WHEN o.order_status != 'DELIVERED' THEN 'INVALID_ORDER_STATUS'
        WHEN c.complaint_time > o.delivery_time + INTERVAL '7 days' THEN 'LATE_COMPLAINT'
        WHEN NOT EXISTS (SELECT 1 FROM order_items WHERE item_id = c.claimed_item_id) THEN 'ITEM_NOT_IN_ORDER'
        WHEN c.claimed_quantity > oi.quantity THEN 'QUANTITY_MISMATCH'
        ELSE 'VALID'
    END as validation_status

FROM orders o
JOIN order_items oi ON o.order_id = oi.order_id
JOIN complaints c ON o.order_id = c.order_id
WHERE c.complaint_id = ?
```

**Auto-Reject Conditions:**
- Order not delivered yet
- Complaint > 7 days after delivery
- Claimed item not in order
- Claimed quantity > ordered quantity

---

### 2.4 SLA Threshold Logic

**Rule ID:** EV-004  
**Business Logic:** When delay compensation triggers

```python
def calculate_sla_breach(order_data):
    customer_tier = order_data['customer_tier']
    order_time = order_data['order_placed_at']
    delivery_time = order_data['delivered_at']
    
    # Promised SLA
    sla_minutes = {
        'GOLD': 30,
        'PREMIUM': 35,
        'STANDARD': 45,
        'FIRST_TIME': 50
    }[customer_tier]
    
    actual_minutes = (delivery_time - order_time).total_seconds() / 60
    delay_minutes = max(0, actual_minutes - sla_minutes)
    
    # Check for exceptions
    if order_data['weather_alert'] == True:
        return {'breach': False, 'reason': 'FORCE_MAJEURE'}
    
    if order_data['customer_requested_delay'] == True:
        return {'breach': False, 'reason': 'CUSTOMER_REQUEST'}
    
    if delay_minutes > 0:
        return {
            'breach': True,
            'delay_minutes': delay_minutes,
            'compensation_tier': calculate_compensation_tier(delay_minutes)
        }
    
    return {'breach': False}
```

**PM Review:** Should first-time customers get extra SLA buffer?

---

### 2.5 Missing Item Verification Conditions

**Rule ID:** EV-005  
**Business Logic:** Special validation for missing item claims

```yaml
missing_item_validation:
  # Auto-approve conditions
  auto_approve_if:
    - item_value < ₹100 AND customer_orders > 10
    - item_value < ₹50 AND customer_fraud_score < 0.3
    - delivery_partner_confirmed_missing: true
    - restaurant_acknowledged_mistake: true
  
  # Require proof conditions
  require_proof_if:
    - item_value >= ₹200
    - customer_complaints_30d >= 3
    - customer_fraud_score >= 0.5
    - same_item_claimed_missing_before: true
  
  # Auto-reject conditions
  auto_reject_if:
    - item_not_in_order_list: true
    - customer_marked_order_complete_in_app: true
    - delivery_photo_shows_item: true
    - customer_complaints_30d >= 8
```

**Fraud Prevention:** Multiple missing item claims trigger manual review.

---

### 2.6 Auto-Reject Conditions

**Rule ID:** EV-006  
**Business Logic:** When to automatically reject without review

```python
auto_reject_reasons = {
    'ORDER_NOT_DELIVERED': 'Cannot complain about undelivered order',
    'COMPLAINT_TOO_LATE': 'Complaint filed >7 days after delivery',
    'ITEM_NOT_IN_ORDER': 'Claimed item was never ordered',
    'CUSTOMER_CANCELLED': 'Customer cancelled the order',
    'ALREADY_REFUNDED': 'Order already fully refunded',
    'DUPLICATE_COMPLAINT': 'Same complaint already processed',
    'INVALID_EVIDENCE': 'Image/proof does not match issue',
    'FRAUD_DETECTED': 'High confidence fraud pattern detected',
    'ACCOUNT_BLOCKED': 'Customer account blocked for abuse',
    'PAYMENT_FAILED': 'Original payment failed, no refund possible'
}
```

**Communication Template:**
```
"We're unable to process your complaint because: {reason}.
If you believe this is an error, please contact support at support@kiranakart.com"
```

**PM Note:** Auto-reject saves significant manual review time but may frustrate customers.

---

## Module 3: Escalation & Risk Override

**Purpose:** Determine when automation must stop and humans must review.

**Owner:** Operations + Risk Management  
**Risk Level:** 🔴 High - Customer Satisfaction Impact

### 3.1 Escalation Matrix

**Rule ID:** ESC-001  
**Business Logic:** When to escalate to human review

| Condition | Escalation Level | Rationale | SLA |
|-----------|------------------|-----------|-----|
| **Immediate Escalation (L3)** |
| Food safety with health impact | Senior Manager | Legal liability | 15 min |
| Foreign object causing injury | Senior Manager | Safety incident | 15 min |
| Allergen reaction | Manager | Health emergency | 30 min |
| Data breach | Security Team | Privacy incident | 30 min |
| Death threat/violence | Security + Legal | Safety concern | Immediate |
| **Standard Escalation (L2)** |
| Refund amount > ₹2000 | Team Lead | Financial control | 2 hours |
| Repeated issue (5+ times) | Team Lead | Chronic problem | 4 hours |
| VIP customer complaint | Team Lead | Relationship management | 1 hour |
| Restaurant dispute | Operations Manager | Partner relations | 4 hours |
| Media/social escalation | PR Team | Brand protection | 1 hour |
| **Review Queue (L1)** |
| Refund ₹500-₹2000 | Agent review | Cost control | 24 hours |
| Fraud score 0.5-0.7 | Fraud analyst | Risk assessment | 24 hours |
| Missing evidence | Operations agent | Info gathering | 48 hours |
| Policy edge case | Policy team | Clarification needed | 48 hours |

**Operations Impact:** ~22% of tickets require human review.

---

### 3.2 Human Review Triggers

**Rule ID:** ESC-002  
**Business Logic:** Specific conditions that force manual review

```python
def requires_human_review(ticket_data, customer_data, resolution_data):
    triggers = []
    
    # Financial triggers
    if resolution_data['refund_amount'] > 2000:
        triggers.append('HIGH_VALUE_REFUND')
    
    if resolution_data['total_compensation'] > 3000:
        triggers.append('HIGH_TOTAL_COMPENSATION')
    
    # Risk triggers
    if customer_data['fraud_score'] > 0.7:
        triggers.append('HIGH_FRAUD_RISK')
    
    if customer_data['complaints_30d'] > 5:
        triggers.append('REPEAT_COMPLAINER')
    
    # Issue triggers
    if ticket_data['issue_category'] == 'FOOD_SAFETY':
        triggers.append('FOOD_SAFETY_ISSUE')
    
    if ticket_data['issue_category'] == 'FOREIGN_OBJECT':
        triggers.append('CONTAMINATION')
    
    # Evidence triggers
    if ticket_data['evidence_quality_score'] < 0.5:
        triggers.append('WEAK_EVIDENCE')
    
    # Customer triggers
    if customer_data['segment'] == 'VIP':
        triggers.append('VIP_CUSTOMER')
    
    if customer_data['lifetime_value'] > 50000:
        triggers.append('HIGH_LTV_CUSTOMER')
    
    return len(triggers) > 0, triggers
```

**Example Output:**
```json
{
  "requires_review": true,
  "triggers": ["HIGH_VALUE_REFUND", "HIGH_LTV_CUSTOMER"],
  "recommended_reviewer": "TEAM_LEAD",
  "priority": "MEDIUM"
}
```

---

### 3.3 High-Value Order Override

**Rule ID:** ESC-003  
**Business Logic:** Special handling for expensive orders

| Order Value Range | Auto-Approval Limit | Required Approver | Max Auto-Refund |
|-------------------|---------------------|-------------------|-----------------|
| ₹0 - ₹500 | Full automation | None | ₹500 |
| ₹501 - ₹1000 | ₹500 auto | Team Lead for >₹500 | ₹500 |
| ₹1001 - ₹2000 | ₹500 auto | Manager for >₹500 | ₹500 |
| ₹2001 - ₹5000 | ₹500 auto | Senior Manager | ₹500 |
| ₹5000+ | No automation | Senior Manager + Finance | Case-by-case |

**Rationale:** Limits per-ticket exposure while enabling automation for majority of orders.

**Finance Note:** 94% of orders are <₹1000, so most remain automated.

---

### 3.4 Risk-Level Mapping

**Rule ID:** ESC-004  
**Business Logic:** Classify ticket risk level

```python
def calculate_ticket_risk_level(ticket_data, customer_data, resolution_data):
    risk_score = 0
    
    # Issue severity (0-40 points)
    severity_scores = {
        'FOOD_SAFETY': 40,
        'FOREIGN_OBJECT': 40,
        'HEALTH_IMPACT': 40,
        'WRONG_ORDER': 20,
        'QUALITY_MAJOR': 20,
        'QUALITY_MINOR': 10,
        'DELAY': 10
    }
    risk_score += severity_scores.get(ticket_data['issue_severity'], 0)
    
    # Financial impact (0-30 points)
    if resolution_data['refund_amount'] > 2000:
        risk_score += 30
    elif resolution_data['refund_amount'] > 1000:
        risk_score += 20
    elif resolution_data['refund_amount'] > 500:
        risk_score += 10
    
    # Customer fraud risk (0-30 points)
    risk_score += customer_data['fraud_score'] * 30
    
    # Map to risk level
    if risk_score >= 70:
        return 'CRITICAL'
    elif risk_score >= 50:
        return 'HIGH'
    elif risk_score >= 30:
        return 'MEDIUM'
    else:
        return 'LOW'
```

**Risk Actions:**
- **CRITICAL:** Immediate escalation + Senior Manager review
- **HIGH:** Escalation + Manager review within 2 hours
- **MEDIUM:** Queue for agent review within 24 hours
- **LOW:** Full automation allowed

---

### 3.5 Critical Issue Handling

**Rule ID:** ESC-005  
**Business Logic:** Zero-tolerance escalation for critical issues

```yaml
critical_issues:
  food_poisoning:
    action: IMMEDIATE_ESCALATE
    notify: [SENIOR_MANAGER, LEGAL, OPERATIONS_HEAD]
    refund: ORDER_VALUE_FULL
    compensation: ₹1000
    follow_up: HEALTH_DEPARTMENT_REPORT
    
  choking_hazard:
    action: IMMEDIATE_ESCALATE
    notify: [SENIOR_MANAGER, SAFETY_TEAM, LEGAL]
    refund: ORDER_VALUE_FULL
    compensation: ₹500
    follow_up: RESTAURANT_SUSPENSION_REVIEW
    
  allergic_reaction:
    action: IMMEDIATE_ESCALATE
    notify: [MANAGER, HEALTH_TEAM]
    refund: ORDER_VALUE_FULL
    compensation: ₹300
    follow_up: INGREDIENT_VERIFICATION
    
  contamination:
    action: IMMEDIATE_ESCALATE
    notify: [MANAGER, QA_TEAM]
    refund: ORDER_VALUE_FULL
    compensation: ₹200
    follow_up: RESTAURANT_AUDIT
```

**SLA for Critical Issues:**
- First response: 15 minutes
- Manager review: 30 minutes
- Resolution/Escalation: 1 hour
- Customer follow-up call: 2 hours

---

## Module 4: Compliance & Regulatory

**Purpose:** Enforce legal and regulatory requirements.

**Owner:** Legal + Compliance Team  
**Risk Level:** 🔴 Critical - Legal Exposure

### 4.1 FSSAI Contamination Handling

**Rule ID:** COMP-001  
**Business Logic:** Mandatory protocol for food safety violations

```yaml
fssai_protocol:
  detection:
    issue_codes:
      - FOOD_SAFETY_CONTAMINATION
      - FOREIGN_OBJECT_INSECT
      - FOREIGN_OBJECT_METAL
      - FOREIGN_OBJECT_PLASTIC
      - QUALITY_SPOILED
      - QUALITY_ROTTEN
  
  immediate_actions:
    - action: FULL_REFUND
    - action: COMPENSATION_FIXED_₹500
    - action: ESCALATE_LEGAL_TEAM
    - action: PRESERVE_EVIDENCE
    - action: RESTAURANT_ALERT
  
  investigation:
    timeline: 24_hours
    requirements:
      - photo_evidence
      - customer_statement
      - restaurant_response
      - batch_number_if_available
      - delivery_partner_statement
  
  reporting:
    internal: OPERATIONS_HEAD, QA_MANAGER
    external: FSSAI_IF_REQUIRED
    restaurant: IMMEDIATE_SUSPENSION_PENDING_REVIEW
  
  customer_communication:
    initial: "We take food safety extremely seriously. We've escalated your case to our safety team."
    follow_up: "Within 24 hours with investigation update"
    resolution: "Within 72 hours with final action"
```

**Legal Note:** FSSAI violations must be reported within 24 hours if severity threshold met.

---

### 4.2 Product Recall Protocol

**Rule ID:** COMP-002  
**Business Logic:** Handle batch recalls

```python
def handle_product_recall(order_data, recall_data):
    """
    Triggered when supplier/manufacturer issues recall
    """
    
    # Check if order contains recalled products
    recalled_items = []
    for item in order_data['items']:
        if item['batch_number'] in recall_data['affected_batches']:
            recalled_items.append(item)
    
    if len(recalled_items) == 0:
        return None
    
    # Automatic actions
    actions = {
        'refund': 'FULL_REFUND',
        'compensation': calculate_recall_compensation(recalled_items),
        'notification': 'IMMEDIATE_SMS_EMAIL',
        'collection': 'ARRANGE_PICKUP' if recall_data['severity'] == 'HIGH' else 'CUSTOMER_DISPOSAL',
        'replacement': 'OFFER_ALTERNATIVE' if available else None
    }
    
    # Legal compliance
    log_recall_action(order_id, recall_data, actions)
    notify_regulatory_body(recall_data)
    
    return actions
```

**Communication Template:**
```
"URGENT: Product Recall Notice

We've been notified of a safety recall affecting items in your order #{order_id}:
- {recalled_items}

For your safety:
1. DO NOT consume these items
2. We've issued a full refund of ₹{amount}
3. {collection_instructions}

We sincerely apologize for this inconvenience. Your safety is our top priority.

Ref: {recall_reference_number}"
```

---

### 4.3 Allergen Escalation

**Rule ID:** COMP-003  
**Business Logic:** Allergen mislabeling protocol

| Allergen Type | Risk Level | Action | Compensation | Reporting |
|---------------|------------|--------|--------------|-----------|
| Peanuts | 🔴 Critical | Immediate escalation | ₹500 + full refund | FSSAI report |
| Tree nuts | 🔴 Critical | Immediate escalation | ₹500 + full refund | FSSAI report |
| Shellfish | 🔴 Critical | Immediate escalation | ₹500 + full refund | FSSAI report |
| Dairy | 🟡 High | Escalation within 1hr | ₹200 + full refund | Internal review |
| Gluten | 🟡 High | Escalation within 1hr | ₹200 + full refund | Internal review |
| Soy | 🟢 Medium | Standard process | ₹100 + full refund | Restaurant warning |
| Eggs | 🟢 Medium | Standard process | ₹100 + full refund | Restaurant warning |

**Special Handling:**
- If customer reports reaction: Immediate medical guidance + manager call
- Restaurant flagged for review after 2 allergen incidents
- Restaurant suspended after 3 allergen incidents

---

### 4.4 Infant Product Restrictions

**Rule ID:** COMP-004  
**Business Logic:** Enhanced protection for baby food/products

```yaml
infant_product_rules:
  categories:
    - BABY_FOOD
    - INFANT_FORMULA
    - BABY_CARE
  
  enhanced_checks:
    expiry_date:
      reject_if_expires_within: 30_days
      display_warning_if_expires_within: 60_days
    
    temperature:
      required_range: 2°C - 8°C for dairy-based
      breach_action: AUTO_REJECT_DELIVERY
    
    packaging:
      seal_break: AUTO_REJECT_DELIVERY
      damage: AUTO_REJECT_DELIVERY
      tampering_suspected: IMMEDIATE_ESCALATE
  
  complaint_handling:
    auto_escalate: TRUE
    refund: FULL_AUTOMATIC
    compensation: ₹300_MINIMUM
    quality_check: MANDATORY_RESTAURANT_AUDIT
    
  regulatory:
    fssai_notification: MANDATORY
    batch_tracking: REQUIRED
    supplier_audit: QUARTERLY
```

**Zero Tolerance:** Any infant product issue triggers immediate escalation.

---

### 4.5 Legal Metrology Pricing Logic

**Rule ID:** COMP-005  
**Business Logic:** MRP compliance for packaged goods

```python
def validate_mrp_compliance(item_data, order_data):
    """
    Ensure price charged doesn't exceed MRP for packaged goods
    """
    
    if item_data['category'] not in PACKAGED_GOODS_CATEGORIES:
        return {'compliant': True}
    
    charged_price = item_data['charged_price']
    mrp = item_data['mrp']
    
    # Check if overcharged
    if charged_price > mrp:
        overcharge = charged_price - mrp
        
        # Automatic correction
        actions = {
            'compliant': False,
            'violation_type': 'OVERCHARGE',
            'overcharge_amount': overcharge,
            'action': 'AUTO_REFUND_DIFFERENCE',
            'refund_amount': overcharge,
            'notify_legal': True if overcharge > 50 else False,
            'restaurant_penalty': calculate_penalty(overcharge)
        }
        
        log_mrp_violation(item_data, order_data, actions)
        return actions
    
    return {'compliant': True}
```

**Penalties for Restaurant:**
- First violation: Warning
- Second violation: ₹1000 fine
- Third violation: 7-day suspension
- Fourth violation: Permanent removal

---

### 4.6 Data Privacy Rules for Refunds

**Rule ID:** COMP-006  
**Business Logic:** GDPR/data protection compliance

```yaml
data_privacy:
  customer_data_access:
    allowed_for_resolution:
      - order_id
      - order_value
      - order_items
      - delivery_address (masked)
      - phone_number (last 4 digits only)
      - complaint_history (aggregated)
    
    restricted:
      - full_phone_number
      - email_address (except for communication)
      - payment_card_details
      - complete_address
      - personal_identification
  
  data_retention:
    complaint_records: 7_years  # Legal requirement
    evidence_images: 90_days    # Storage optimization
    resolved_tickets: 3_years   # Audit requirement
    customer_pii: RETAIN_WHILE_ACTIVE_PLUS_1_YEAR
  
  refund_processing:
    payment_method: ORIGINAL_PAYMENT_METHOD_ONLY
    third_party_refunds: PROHIBITED
    manual_bank_transfer: MANAGER_APPROVAL_REQUIRED
    
  breach_protocol:
    detection: IMMEDIATE_ALERT
    containment: LOCK_AFFECTED_ACCOUNTS
    notification: CUSTOMER_WITHIN_72_HOURS
    reporting: DATA_PROTECTION_OFFICER
```

**Critical:** Never share customer PII with restaurants or delivery partners.

---

## Module 5: Fraud & Abuse Intelligence

**Purpose:** Detect and prevent fraud/abuse patterns.

**Owner:** Fraud & Risk Team  
**Risk Level:** 🔴 High - Direct Financial Impact

### 5.1 Repeat Refund Thresholds

**Rule ID:** FRAUD-001  
**Business Logic:** Flag customers with excessive refund requests

```python
def calculate_fraud_risk_repeat_refunds(customer_data):
    """
    Analyze refund patterns over multiple time windows
    """
    
    # Time windows
    windows = {
        '7_days': customer_data['refunds_last_7_days'],
        '30_days': customer_data['refunds_last_30_days'],
        '90_days': customer_data['refunds_last_90_days'],
        'lifetime': customer_data['total_refunds']
    }
    
    orders = {
        '7_days': customer_data['orders_last_7_days'],
        '30_days': customer_data['orders_last_30_days'],
        '90_days': customer_data['orders_last_90_days'],
        'lifetime': customer_data['total_orders']
    }
    
    # Calculate refund rates
    refund_rates = {}
    for window in windows:
        if orders[window] > 0:
            refund_rates[window] = windows[window] / orders[window]
        else:
            refund_rates[window] = 0
    
    # Risk scoring
    risk_score = 0
    
    # Short-term abuse (high intensity)
    if refund_rates['7_days'] > 0.5 and windows['7_days'] >= 3:
        risk_score += 0.4  # 40% risk
    
    # Medium-term pattern
    if refund_rates['30_days'] > 0.3 and windows['30_days'] >= 5:
        risk_score += 0.3  # 30% risk
    
    # Long-term abuse
    if refund_rates['90_days'] > 0.25 and windows['90_days'] >= 10:
        risk_score += 0.2  # 20% risk
    
    # Lifetime abuse
    if refund_rates['lifetime'] > 0.2 and windows['lifetime'] >= 20:
        risk_score += 0.1  # 10% risk
    
    # Classify
    if risk_score >= 0.7:
        classification = 'HIGH_RISK'
        action = 'BLOCK_AUTO_REFUNDS'
    elif risk_score >= 0.5:
        classification = 'MEDIUM_RISK'
        action = 'REQUIRE_EVIDENCE_ALWAYS'
    elif risk_score >= 0.3:
        classification = 'LOW_RISK'
        action = 'REDUCE_AUTO_APPROVAL_LIMIT'
    else:
        classification = 'NORMAL'
        action = 'STANDARD_PROCESS'
    
    return {
        'risk_score': risk_score,
        'classification': classification,
        'action': action,
        'refund_rates': refund_rates
    }
```

**Action by Risk Level:**

| Risk Level | Refund Rate | Action | Review Required |
|------------|-------------|--------|-----------------|
| NORMAL | <30% | Standard automation | No |
| LOW_RISK | 30-50% | Reduce auto-limit to ₹200 | Quarterly review |
| MEDIUM_RISK | 50-70% | Require evidence always | Monthly review |
| HIGH_RISK | >70% | Block auto-refunds | Manager review per ticket |
| BLOCKED | Banned | No refunds | Permanent ban |

---

### 5.2 High-Value Abuse Triggers

**Rule ID:** FRAUD-002  
**Business Logic:** Detect sophisticated fraud on expensive orders

```yaml
high_value_fraud_patterns:
  pattern_1_serial_high_value:
    description: "Multiple high-value orders with refund requests"
    detection:
      - orders_above_₹1000_in_30_days >= 5
      - refund_requests_on_those_orders >= 3
      - refund_rate > 60%
    action: BLOCK_HIGH_VALUE_ORDERS
    
  pattern_2_order_spike:
    description: "Sudden increase in order value"
    detection:
      - average_order_value_last_7_days > 3× average_order_value_previous_90_days
      - new_delivery_addresses_added >= 2
      - payment_method_changed = TRUE
    action: HOLD_ORDERS_FOR_VERIFICATION
    
  pattern_3_missing_items_premium:
    description: "Claims most expensive items missing"
    detection:
      - missing_item_claims_in_30_days >= 3
      - average_claimed_item_value > 70th_percentile_of_order
      - image_evidence_provided = FALSE or WEAK
    action: REQUIRE_SIGNATURE_CONFIRMATION
    
  pattern_4_coordinated_fraud:
    description: "Multiple accounts with similar patterns"
    detection:
      - shared_delivery_address
      - shared_payment_method
      - similar_refund_patterns
      - account_creation_within_7_days_of_each_other
    action: BLOCK_ALL_LINKED_ACCOUNTS
```

**Financial Impact:** Prevents estimated ₹5-8 Cr annual leakage.

---

### 5.3 Duplicate Complaint Detection Logic

**Rule ID:** FRAUD-003  
**Business Logic:** Prevent same issue being claimed multiple times

```sql
-- Detect duplicate complaints
WITH complaint_fingerprint AS (
    SELECT 
        customer_id,
        order_id,
        issue_type,
        claimed_items,
        complaint_time,
        
        -- Create fingerprint
        MD5(CONCAT(
            order_id,
            issue_type,
            ARRAY_TO_STRING(claimed_items, ',')
        )) as fingerprint
    FROM complaints
    WHERE complaint_time > NOW() - INTERVAL '30 days'
)

SELECT 
    cf1.complaint_id as original_complaint,
    cf2.complaint_id as duplicate_complaint,
    cf1.customer_id,
    cf1.order_id,
    cf1.issue_type
FROM complaint_fingerprint cf1
JOIN complaint_fingerprint cf2 
    ON cf1.fingerprint = cf2.fingerprint
    AND cf1.complaint_id < cf2.complaint_id
WHERE cf2.complaint_time > cf1.complaint_time + INTERVAL '1 hour'
```

**Action on Duplicate:**
- First complaint: Process normally
- Duplicate detected: Auto-reject with message "This issue has already been reported"
- Customer can appeal if genuinely different issue

---

### 5.4 Marked-Delivered Fraud SOP

**Rule ID:** FRAUD-004  
**Business Logic:** Handle "marked delivered but not received" claims

```python
def validate_marked_delivered_claim(order_data, customer_data):
    """
    High fraud risk: Customer claims order marked delivered but never received
    """
    
    fraud_indicators = []
    evidence_points = []
    
    # Check delivery evidence
    if order_data['delivery_photo_exists']:
        evidence_points.append('DELIVERY_PHOTO_AVAILABLE')
    
    if order_data['gps_location_match'] > 0.9:
        evidence_points.append('GPS_CONFIRMS_LOCATION')
    
    if order_data['customer_marked_received_in_app']:
        fraud_indicators.append('CUSTOMER_CONFIRMED_RECEIPT')
    
    # Check customer history
    if customer_data['marked_delivered_claims_last_90_days'] >= 2:
        fraud_indicators.append('REPEAT_MD_CLAIMER')
    
    if customer_data['refund_rate_last_30_days'] > 0.5:
        fraud_indicators.append('HIGH_REFUND_RATE')
    
    # Check delivery partner
    if order_data['delivery_partner_rating'] > 4.5:
        evidence_points.append('TRUSTED_DELIVERY_PARTNER')
    
    # Decision logic
    fraud_score = len(fraud_indicators) * 0.25
    evidence_score = len(evidence_points) * 0.2
    
    if fraud_score > 0.5 and len(evidence_points) >= 2:
        return {
            'decision': 'REJECT',
            'reason': 'Strong evidence of delivery + fraud indicators',
            'allow_appeal': True,
            'appeal_requires': ['POLICE_REPORT', 'BUILDING_SECURITY_FOOTAGE']
        }
    
    elif fraud_score > 0.5:
        return {
            'decision': 'MANUAL_REVIEW',
            'reason': 'High fraud indicators, needs investigation',
            'reviewer': 'FRAUD_ANALYST'
        }
    
    elif evidence_score >= 0.4:
        return {
            'decision': 'PARTIAL_REFUND_50%',
            'reason': 'Evidence suggests delivery but giving benefit of doubt',
            'action': 'INVESTIGATE_DELIVERY_PARTNER'
        }
    
    else:
        return {
            'decision': 'FULL_REFUND',
            'reason': 'Insufficient evidence to deny claim',
            'action': 'FLAG_FOR_MONITORING'
        }
```

---

### 5.5 Risk Multipliers

**Rule ID:** FRAUD-005  
**Business Logic:** Adjust refund based on fraud risk

```python
def apply_fraud_risk_multiplier(base_refund, customer_data, ticket_data):
    """
    Reduce refund for high-risk customers
    """
    
    multiplier = 1.0
    adjustments = []
    
    # Fraud risk reduction
    if customer_data['fraud_score'] > 0.8:
        multiplier *= 0.5  # 50% reduction
        adjustments.append('HIGH_FRAUD_RISK: -50%')
    
    elif customer_data['fraud_score'] > 0.6:
        multiplier *= 0.7  # 30% reduction
        adjustments.append('MEDIUM_FRAUD_RISK: -30%')
    
    # Repeat offender penalty
    if customer_data['refunds_last_30_days'] > 5:
        multiplier *= 0.8  # Additional 20% reduction
        adjustments.append('EXCESSIVE_REFUNDS: -20%')
    
    # Weak evidence penalty
    if ticket_data['evidence_quality'] < 0.5:
        multiplier *= 0.75  # 25% reduction
        adjustments.append('WEAK_EVIDENCE: -25%')
    
    # Floor: Never reduce below 50% unless rejecting
    multiplier = max(0.5, multiplier)
    
    # Calculate final amount
    adjusted_refund = base_refund * multiplier
    
    return {
        'original_refund': base_refund,
        'multiplier': multiplier,
        'adjusted_refund': adjusted_refund,
        'adjustments': adjustments,
        'customer_notification': f"Refund adjusted based on account history"
    }
```

**Example:**
- Base refund: ₹400
- Customer: Fraud score 0.75, 6 refunds in 30 days, weak evidence
- Multiplier: 0.7 × 0.8 × 0.75 = 0.42 → capped at 0.5
- Final refund: ₹400 × 0.5 = ₹200

---

### 5.6 Auto-Block Conditions

**Rule ID:** FRAUD-006  
**Business Logic:** When to permanently block a customer

```yaml
permanent_block_triggers:
  proven_fraud:
    - police_report_filed: TRUE
    - fraud_confirmed_by_investigation: TRUE
    - action: IMMEDIATE_PERMANENT_BLOCK
    
  serial_abuse:
    - refund_rate_lifetime > 80%
    - total_orders >= 20
    - total_refund_amount > ₹50000
    - action: PERMANENT_BLOCK
    
  coordinated_fraud_ring:
    - linked_to_known_fraud_accounts >= 3
    - shared_payment_instruments: TRUE
    - action: BLOCK_ALL_LINKED_ACCOUNTS
    
  threatening_behavior:
    - threats_to_staff: TRUE
    - blackmail_attempts: TRUE
    - action: IMMEDIATE_BLOCK_PLUS_LEGAL_ACTION
    
  payment_fraud:
    - chargebacks >= 3
    - stolen_card_used: CONFIRMED
    - action: BLOCK_PLUS_REPORT_TO_AUTHORITIES

temporary_suspension_triggers:
  investigation_pending:
    duration: 30_days
    conditions:
      - fraud_score > 0.9
      - manual_review_required: TRUE
    
  cooling_off_period:
    duration: 14_days
    conditions:
      - refunds_last_7_days >= 5
      - appears_automated: TRUE
```

**Appeals Process:**
- Permanent blocks: Can appeal to senior manager with evidence
- Temporary suspensions: Auto-expire after duration
- Proven innocent: Remove block + ₹500 goodwill credit

---

## Module 6: Food Delivery Intelligence

**Purpose:** Domain-specific rules for cooked food orders.

**Owner:** Product + Food Safety Team  
**Risk Level:** 🟡 Medium - Customer Health & Satisfaction

### 6.1 Food Safety Handling

**Rule ID:** FOOD-001  
**Business Logic:** Contamination and health hazard response

```yaml
food_safety_categories:
  category_1_critical:
    issues:
      - INSECT_IN_FOOD
      - METAL_OBJECT
      - GLASS_SHARD
      - PLASTIC_PIECE
      - HAIR_IN_FOOD (multiple)
      - MOLD_VISIBLE
      - FOUL_ODOR
    
    immediate_actions:
      refund: 100% + ₹500
      escalation: IMMEDIATE_TO_SENIOR_MANAGER
      restaurant_action: SUSPEND_IMMEDIATELY
      investigation: MANDATORY_WITHIN_24H
      customer_follow_up: CALL_WITHIN_1H
      legal_notification: IF_CUSTOMER_REQUESTS
    
  category_2_high:
    issues:
      - UNDERCOOKED_MEAT
      - CROSS_CONTAMINATION
      - WRONG_TEMPERATURE
      - PACKAGING_BREACH
      - SINGLE_HAIR
    
    immediate_actions:
      refund: 100% + ₹200
      escalation: MANAGER_REVIEW_2H
      restaurant_action: WARNING_PLUS_AUDIT
      investigation: 48_HOURS
      customer_follow_up: EMAIL_UPDATE
    
  category_3_medium:
    issues:
      - SLIGHTLY_OVERCOOKED
      - MINOR_PRESENTATION
      - SAUCE_SPILLED
    
    immediate_actions:
      refund: 40-60%
      escalation: STANDARD_PROCESS
      restaurant_action: FEEDBACK_ONLY
```

---

### 6.2 Foreign Object Handling

**Rule ID:** FOOD-002  
**Business Logic:** Response based on object type and risk

| Object Type | Health Risk | Refund | Compensation | Restaurant Action |
|-------------|-------------|--------|--------------|-------------------|
| **Critical** |
| Metal piece | 🔴 Extreme | 100% | ₹500 | Immediate suspension |
| Glass shard | 🔴 Extreme | 100% | ₹500 | Immediate suspension |
| Insect (live) | 🔴 High | 100% | ₹300 | 7-day suspension |
| Plastic (sharp) | 🔴 High | 100% | ₹300 | 3-day suspension |
| **High** |
| Dead insect | 🟡 Medium | 100% | ₹200 | Warning + audit |
| Hair (multiple) | 🟡 Medium | 100% | ₹150 | Warning + training |
| Paper/cardboard | 🟡 Low | 100% | ₹100 | Warning |
| **Low** |
| Single hair | 🟢 Low | 50% | ₹50 | Feedback |
| Food in wrong section | 🟢 Low | 40% | ₹30 | Feedback |

**Image Evidence Required:** Mandatory for all foreign object claims >₹100 refund.

---

### 6.3 Veg/Non-Veg Misclassification Logic

**Rule ID:** FOOD-003  
**Business Logic:** Cultural/religious sensitivity compliance

```python
def handle_veg_nonveg_violation(order_data, customer_data):
    """
    Extremely sensitive issue - handle with care
    """
    
    violation_type = order_data['violation_type']
    
    # Severity assessment
    if violation_type == 'NONVEG_SENT_TO_VEG_CUSTOMER':
        severity = 'CRITICAL'
        refund = order_data['order_value']  # 100%
        compensation = 200
        apology_level = 'SENIOR_MANAGER_CALL'
        restaurant_action = 'SUSPEND_UNTIL_AUDIT'
        
    elif violation_type == 'VEG_CLAIMED_HAD_NONVEG':
        severity = 'CRITICAL'
        refund = order_data['order_value']  # 100%
        compensation = 200
        apology_level = 'MANAGER_CALL'
        restaurant_action = 'IMMEDIATE_INVESTIGATION'
        
    elif violation_type == 'EGG_IN_VEG_ORDER':
        severity = 'HIGH'
        refund = order_data['order_value']  # 100%
        compensation = 100
        apology_level = 'MANAGER_EMAIL'
        restaurant_action = 'WARNING_PLUS_TRAINING'
        
    else:  # MISLABELED_ON_MENU
        severity = 'MEDIUM'
        refund = order_data['order_value'] * 0.8  # 80%
        compensation = 50
        apology_level = 'STANDARD_APOLOGY'
        restaurant_action = 'MENU_CORRECTION_24H'
    
    # Customer preferences consideration
    if customer_data['dietary_preference'] == 'STRICT_VEG':
        compensation += 50  # Extra compensation for strict veg
    
    if customer_data['religion'] in ['HINDU', 'JAIN']:
        apology_level = 'SENIOR_MANAGER_CALL'  # Extra sensitivity
    
    return {
        'severity': severity,
        'refund': refund,
        'compensation': compensation,
        'total': refund + compensation,
        'apology_level': apology_level,
        'restaurant_action': restaurant_action,
        'customer_notification': generate_sensitive_apology(violation_type)
    }
```

**Customer Communication Template:**
```
"We sincerely apologize for this unacceptable mistake. We understand how important dietary preferences are, especially for religious and cultural reasons.

We have:
✓ Issued a full refund of ₹{refund}
✓ Added ₹{compensation} compensation
✓ {restaurant_action}

A senior manager will personally call you within 1 hour to discuss how we can regain your trust.

We are deeply sorry for this incident."
```

---

### 6.4 Portion Dispute Handling

**Rule ID:** FOOD-004  
**Business Logic:** Subjective claims about quantity

```yaml
portion_dispute_protocol:
  auto_approve_conditions:
    - image_evidence: TRUE
    - portion_significantly_less: TRUE  # AI visual analysis
    - customer_orders >= 10
    - first_portion_complaint: TRUE
    - refund: 30-40%
  
  require_review_conditions:
    - image_evidence: FALSE OR unclear
    - portion_complaint_history >= 2
    - high_value_item: TRUE
    - refund: MANAGER_DISCRETION
  
  auto_reject_conditions:
    - no_image_provided: TRUE
    - portion_complaint_3rd_time: TRUE
    - image_shows_normal_portion: TRUE
    - refund: 0%
  
  restaurant_feedback:
    first_complaint: NOTIFY_RESTAURANT
    second_complaint: WARNING_PLUS_PHOTO_EVIDENCE_REQUIREMENT
    third_complaint: AUDIT_KITCHEN_PRACTICES
```

**Example Outcomes:**
- **First complaint, image shows half portion:** 40% refund approved
- **Second complaint, no image:** Require image, no refund
- **Third complaint:** Auto-escalate for fraud review

---

### 6.5 Burnt/Undercooked Rules

**Rule ID:** FOOD-005  
**Business Logic:** Cooking quality issues

| Issue | Evidence Required | Refund | Restaurant Action | Customer History Impact |
|-------|------------------|--------|-------------------|------------------------|
| **Severely burnt** | Image mandatory | 80% | Warning | None if first time |
| **Visibly burnt** | Image recommended | 60% | Feedback | Monitor if repeat |
| **Slightly overcooked** | No image needed | 30% | Feedback | None |
| **Undercooked (safety risk)** | Image mandatory | 100% + ₹100 | Suspension review | None |
| **Raw/uncooked** | Image mandatory | 100% + ₹200 | Immediate suspension | None |

**Special Case - Undercooked Meat:**
```python
if issue == 'UNDERCOOKED_MEAT':
    actions = {
        'refund': 100%,
        'compensation': 200,
        'escalation': 'IMMEDIATE_FOOD_SAFETY_TEAM',
        'restaurant': 'SUSPEND_UNTIL_INVESTIGATION',
        'customer': 'HEALTH_CHECK_OFFER',
        'legal': 'PRESERVE_EVIDENCE_FOR_7_DAYS'
    }
```

---

### 6.6 Cold/Melted SLA Rules

**Rule ID:** FOOD-006  
**Business Logic:** Temperature-sensitive food handling

```python
def handle_temperature_complaint(order_data, delivery_data):
    """
    Cold/melted food depends on delivery time and food type
    """
    
    food_type = order_data['food_type']
    delivery_time_minutes = delivery_data['total_delivery_time']
    
    # Expected delivery SLA
    sla_map = {
        'ICE_CREAM': 20,
        'FROZEN': 30,
        'HOT_FOOD': 45,
        'COLD_FOOD': 45,
        'BEVERAGES': 45
    }
    
    expected_sla = sla_map.get(food_type, 45)
    sla_breach_minutes = max(0, delivery_time_minutes - expected_sla)
    
    # Temperature complaint validity
    if food_type in ['ICE_CREAM', 'FROZEN']:
        if sla_breach_minutes > 10:
            # Delivery took too long, our fault
            refund_percent = 100
            compensation = 100
            fault = 'DELIVERY_DELAY'
        else:
            # Delivered on time, possible restaurant issue
            refund_percent = 80
            compensation = 50
            fault = 'RESTAURANT_PACKAGING'
    
    elif food_type == 'HOT_FOOD':
        if sla_breach_minutes > 15:
            # Significant delay, food expected to be cold
            refund_percent = 60
            compensation = 50
            fault = 'DELIVERY_DELAY'
        elif sla_breach_minutes < 5:
            # Delivered on time, restaurant sent cold food
            refund_percent = 80
            compensation = 100
            fault = 'RESTAURANT_QUALITY'
        else:
            # Borderline case
            refund_percent = 40
            compensation = 30
            fault = 'SHARED'
    
    return {
        'refund_percent': refund_percent,
        'compensation': compensation,
        'fault_attribution': fault,
        'restaurant_action': get_restaurant_action(fault),
        'delivery_partner_action': get_dp_action(fault)
    }
```

---

### 6.7 Combo Composition Logic

**Rule ID:** FOOD-007  
**Business Logic:** Meal/combo order issues

```yaml
combo_rules:
  missing_item_in_combo:
    calculation: (item_value / combo_value) × combo_price
    minimum_refund: ₹30
    maximum_refund: 100% if main_item_missing
    
    examples:
      - combo: Burger + Fries + Drink = ₹200
      - missing: Fries (₹50 standalone value, ₹70 of combo)
      - refund: (70/200) × 200 = ₹70 OR 35%
  
  wrong_item_in_combo:
    if substitution_value_higher:
      action: NO_REFUND_NOTIFY_UPGRADE
    if substitution_value_lower:
      refund: VALUE_DIFFERENCE + 20%
    if substitution_value_equal:
      compensation: ₹30_GOODWILL
  
  incomplete_combo:
    if >50%_items_missing:
      refund: 100%_OF_COMBO
    if 1-2_items_missing:
      refund: PROPORTIONAL_AS_ABOVE
```

---

## Module 7: FMCG / Quick Commerce Intelligence

**Purpose:** Packaged goods specific rules.

**Owner:** Product + Category Management  
**Risk Level:** 🟡 Medium - Quality & Compliance

### 7.1 Expiry & Shelf-Life Rules

**Rule ID:** FMCG-001  
**Business Logic:** Freshness guarantees

```python
def validate_expiry_complaint(product_data, delivery_date):
    """
    Handle expiry-related complaints
    """
    
    expiry_date = product_data['expiry_date']
    category = product_data['category']
    
    days_until_expiry = (expiry_date - delivery_date).days
    
    # Minimum shelf life requirements
    min_shelf_life = {
        'DAIRY': 3,
        'BAKERY': 2,
        'FRESH_PRODUCE': 2,
        'MEAT_SEAFOOD': 2,
        'PACKAGED_FOOD': 30,
        'BEVERAGES': 30,
        'BABY_FOOD': 60
    }
    
    required_days = min_shelf_life.get(category, 7)
    
    # Evaluate complaint
    if days_until_expiry <= 0:
        # Expired product delivered
        return {
            'valid': True,
            'severity': 'CRITICAL',
            'refund': 100,
            'compensation': 200,
            'restaurant_action': 'SUSPEND_PENDING_INVESTIGATION',
            'fssai_report': True
        }
    
    elif days_until_expiry == 1:
        # Expires tomorrow
        return {
            'valid': True,
            'severity': 'HIGH',
            'refund': 100,
            'compensation': 100,
            'restaurant_action': 'WARNING_INVENTORY_AUDIT',
            'fssai_report': False
        }
    
    elif days_until_expiry < required_days:
        # Below minimum shelf life
        return {
            'valid': True,
            'severity': 'MEDIUM',
            'refund': 100,
            'compensation': 50,
            'restaurant_action': 'FEEDBACK_INVENTORY_TRAINING',
            'fssai_report': False
        }
    
    else:
        # Acceptable shelf life
        return {
            'valid': False,
            'severity': 'NONE',
            'message': f'Product has {days_until_expiry} days until expiry, which meets our {required_days}-day standard.'
        }
```

**Customer Communication for Invalid Claims:**
```
"We've reviewed your complaint about product expiry.

The product has {days} days remaining shelf life, which meets our quality standards of {required_days} days for {category} items.

If you'd prefer products with longer shelf life, you can filter by 'Freshness' when shopping."
```

---

### 7.2 Substitution Rules

**Rule ID:** FMCG-002  
**Business Logic:** When substitute product sent

```yaml
substitution_matrix:
  auto_approve_substitutions:
    same_brand_different_variant:
      refund: 0%
      action: INFORM_ONLY
      example: "Nestle Maggi 200g → Nestle Maggi 250g"
    
    same_category_equal_or_higher_value:
      refund: 0%
      action: INFORM_ONLY
      example: "Amul Butter → Britannia Butter (same price)"
  
  require_consent_substitutions:
    different_brand_same_category:
      if_no_prior_consent: REFUND_DIFFERENCE
      if_consented: NO_REFUND
      example: "Nestle → ITC product"
    
    premium_to_regular:
      refund: PRICE_DIFFERENCE + 20%
      compensation: ₹30
      example: "Premium rice → Regular rice"
    
    different_variant_lower_value:
      refund: PRICE_DIFFERENCE
      compensation: 0
      example: "500ml → 250ml"
  
  never_allow_substitutions:
    categories:
      - BABY_FOOD
      - MEDICINES
      - PERSONAL_CARE
      - RELIGIOUS_ITEMS
    action: CANCEL_ITEM_FULL_REFUND
```

**Substitution Consent Collection:**
- At order time: "Allow substitutions for out-of-stock items?"
- If YES: Store preference, allow category-similar substitutions
- If NO: Cancel items if unavailable

---

### 7.3 Packaging Damage Assessment

**Rule ID:** FMCG-003  
**Business Logic:** Determine if damage affects product usability

| Damage Type | Image Required | Product Affected? | Refund | Example |
|-------------|----------------|-------------------|--------|---------|
| **Critical** |
| Seal broken (food) | ✅ Yes | Yes | 100% | Packet opened, chips exposed |
| Bottle leaked | ✅ Yes | Yes | 100% | Oil spilled, bottle cracked |
| Can dented (bulging) | ✅ Yes | Yes | 100% + ₹100 | Potential botulism risk |
| Glass jar broken | ✅ Yes | Yes | 100% + ₹50 | Pickle jar shattered |
| **High** |
| Seal broken (non-food) | ✅ Yes | Maybe | 80% | Detergent packet opened |
| Box crushed (contents visible) | ✅ Yes | Maybe | 70% | Cereal box damaged, bag intact |
| Can heavily dented | ✅ Yes | Maybe | 50% | Cosmetic dent, no bulge |
| **Medium** |
| Box torn/crushed | ⚠️ Recommended | No | 30% | Box damaged, inner packaging fine |
| Label damaged | ⚠️ Recommended | No | 20% | Label torn, product fine |
| **Low** |
| Minor dent | ❌ No | No | 10% | Tiny can dent, purely cosmetic |
| Dust/dirt on package | ❌ No | No | 5% | Exterior dirty, product sealed |

**Special Rule - Safety-Critical Damage:**
```python
if damage_type in ['SEAL_BROKEN', 'LEAK', 'BULGING_CAN']:
    return {
        'refund': 100,
        'compensation': 100,
        'disposal_instruction': 'DO NOT CONSUME - Please dispose safely',
        'replacement': 'OFFER_IMMEDIATE_REPLACEMENT',
        'investigation': 'TRACE_BATCH_AND_SUPPLIER'
    }
```

---

### 7.4 Category-Specific Rules

#### 7.4.1 Dairy Cold Chain Rules

**Rule ID:** FMCG-004

```yaml
dairy_rules:
  temperature_breach:
    detection:
      - delivery_time > 60_minutes
      - ambient_temp > 25°C
      - no_insulated_bag_used
    
    action:
      refund: 100%
      compensation: ₹50
      delivery_partner: WARNING
  
  packaging_requirements:
    milk_packets:
      must_be_sealed: TRUE
      breach_action: FULL_REFUND_NO_QUESTIONS
    
    yogurt_cups:
      seal_intact: MANDATORY
      bulging_lid: REJECT_AT_DELIVERY
    
    cheese_blocks:
      vacuum_sealed: REQUIRED
      mold_visible: FULL_REFUND_PLUS_₹200
```

#### 7.4.2 Frozen Food Melt Rules

**Rule ID:** FMCG-005

```python
def assess_frozen_food_complaint(product_data, delivery_data):
    """
    Frozen food must arrive frozen
    """
    
    delivery_time = delivery_data['delivery_time_minutes']
    temp_at_delivery = delivery_data['estimated_temp']
    
    # Frozen food tolerance
    if delivery_time > 45:
        # Excessive delivery time
        refund = 100
        compensation = 100
        fault = 'DELIVERY_DELAY'
    
    elif temp_at_delivery > 0:  # Above freezing
        # Melted/thawed
        if delivery_time < 30:
            # Quick delivery but still melted = restaurant fault
            refund = 100
            compensation = 150
            fault = 'RESTAURANT_PACKAGING'
        else:
            # Borderline delivery time
            refund = 100
            compensation = 75
            fault = 'SHARED'
    
    elif 'PARTIAL_THAW' in product_data['complaint_description']:
        # Started to thaw but still cold
        refund = 50
        compensation = 50
        fault = 'DELIVERY_TIME'
    
    else:
        # Complaint not valid
        refund = 0
        compensation = 0
        fault = 'NONE'
    
    return {
        'refund_percent': refund,
        'compensation': compensation,
        'fault': fault,
        'health_warning': 'Do not refreeze thawed products' if temp_at_delivery > 0 else None
    }
```

#### 7.4.3 Baby Food Escalation

**Rule ID:** FMCG-006

```yaml
baby_food_protocol:
  zero_tolerance_policy: TRUE
  
  any_issue_triggers:
    - AUTO_ESCALATE: SENIOR_MANAGER
    - REFUND: 100% + ₹300
    - INVESTIGATION: MANDATORY
    - SUPPLIER_AUDIT: IF_QUALITY_ISSUE
    - FSSAI_REPORT: IF_SAFETY_CONCERN
  
  issues:
    packaging_damage:
      action: FULL_REFUND_NO_QUESTIONS
      replacement: IMMEDIATE_FROM_DIFFERENT_BATCH
      
    expiry_concern:
      if_expires_within_60_days: AUTO_REJECT_AT_WAREHOUSE
      if_expired: FULL_REFUND_PLUS_₹500_PLUS_SUPPLIER_PENALTY
      
    quality_doubt:
      action: ACCEPT_COMPLAINT_WITHOUT_EVIDENCE
      reason: "Cannot risk infant health for proof requirements"
      
  customer_communication:
    tone: EXTREMELY_APOLOGETIC
    follow_up: MANDATORY_MANAGER_CALL
    future_orders: DOUBLE_CHECK_INFANT_PRODUCTS
```

#### 7.4.4 OTC Medicine Protocol

**Rule ID:** FMCG-007

```yaml
otc_medicine_rules:
  licensed_sellers_only: TRUE
  
  expiry_requirements:
    minimum_shelf_life: 180_days
    reject_if_less: TRUE
    compensation_if_violation: ₹200
  
  packaging_requirements:
    seal_intact: MANDATORY
    blister_pack_damaged: FULL_REFUND
    bottle_seal_broken: FULL_REFUND_PLUS_₹100
  
  substitution:
    allowed: FALSE
    if_unavailable: CANCEL_AND_REFUND
    generic_for_branded: NEVER_WITHOUT_CONSENT
  
  complaints:
    quality_concern: ESCALATE_TO_PHARMACIST
    adverse_reaction: IMMEDIATE_MEDICAL_TEAM_ALERT
    counterfeit_suspected: LEGAL_TEAM_PLUS_AUTHORITIES
```

---

## Module 8: Operational Workflow

**Purpose:** Route tickets and trigger operational actions.

**Owner:** Operations Team  
**Risk Level:** 🟢 Low - Process Efficiency

### 8.1 Delivery Partner Escalation SOP

**Rule ID:** OPS-001  
**Business Logic:** When and how to involve delivery partners

```yaml
delivery_partner_involvement:
  auto_notify_DP:
    issues:
      - DELIVERY_DELAY
      - MARKED_DELIVERED_NOT_RECEIVED
      - WRONG_ADDRESS_DELIVERED
      - DAMAGED_IN_TRANSIT
      - RUDE_BEHAVIOR
      - SAFETY_CONCERN
    
    action:
      - SEND_TICKET_TO_DP_APP
      - REQUEST_DP_STATEMENT
      - WAIT_FOR_RESPONSE_4H
      - PROCEED_WITHOUT_IF_NO_RESPONSE
  
  dp_dispute_resolution:
    if_dp_disagrees_with_customer:
      evidence_weight:
        - GPS_LOGS: HIGH
        - DELIVERY_PHOTO: HIGH
        - CUSTOMER_HISTORY: MEDIUM
        - DP_RATING: MEDIUM
      
      decision_logic: EVIDENCE_BASED_ARBITRATION
      
    if_dp_admits_fault:
      customer_refund: PROCESS_IMMEDIATELY
      dp_penalty: AS_PER_DP_CONTRACT
  
  dp_performance_tracking:
    complaint_threshold: 5_PER_100_ORDERS
    if_exceeded:
      - WARNING_TO_DP
      - RETRAINING_REQUIRED
      - SUSPENSION_IF_NO_IMPROVEMENT
```

---

### 8.2 Restaurant Dispute Resolution

**Rule ID:** OPS-002  
**Business Logic:** Handle restaurant pushback on complaints

```python
def handle_restaurant_dispute(complaint_data, restaurant_response):
    """
    Restaurant disputes customer complaint
    """
    
    dispute_type = restaurant_response['dispute_type']
    
    if dispute_type == 'CUSTOMER_FRAUD_SUSPECTED':
        # Restaurant claims customer is lying
        
        if complaint_data['customer_fraud_score'] > 0.7:
            # High risk customer + restaurant dispute = investigate
            return {
                'action': 'INVESTIGATE',
                'hold_refund': True,
                'request_evidence_from': 'BOTH_PARTIES',
                'resolution_sla': '48_HOURS'
            }
        else:
            # Trusted customer + restaurant dispute = side with customer
            return {
                'action': 'SIDE_WITH_CUSTOMER',
                'refund': 'PROCESS_AS_PLANNED',
                'restaurant_note': 'Multiple disputes may affect restaurant rating'
            }
    
    elif dispute_type == 'ORDER_WAS_CORRECT':
        # Restaurant claims they sent correct items
        
        if complaint_data['image_evidence_exists']:
            # Customer has photo proof
            return {
                'action': 'SIDE_WITH_CUSTOMER',
                'refund': 'PROCESS_FULL',
                'restaurant_penalty': 'DEDUCT_FROM_PAYOUT'
            }
        else:
            # No photo, he-said-she-said
            return {
                'action': 'SPLIT_COST',
                'refund_to_customer': 'PROCESS_FULL',
                'restaurant_charge': '50%_OF_REFUND',
                'reason': 'Benefit of doubt to customer, partial cost to restaurant'
            }
    
    elif dispute_type == 'QUANTITY_AS_PER_RECIPE':
        # Restaurant claims portion size is standard
        
        return {
            'action': 'CHECK_MENU_PHOTO',
            'if_menu_shows_larger': 'SIDE_WITH_CUSTOMER',
            'if_menu_matches_actual': 'REJECT_COMPLAINT',
            'if_ambiguous': 'PARTIAL_REFUND_30%'
        }
    
    else:
        # Default: Customer benefit of doubt
        return {
            'action': 'SIDE_WITH_CUSTOMER',
            'restaurant_note': 'Excessive disputes hurt restaurant performance score'
        }
```

---

### 8.3 Refund Timeline Communication

**Rule ID:** OPS-003  
**Business Logic:** Set customer expectations

```yaml
refund_timelines:
  payment_method_timelines:
    CREDIT_CARD:
      processing: "2-4 business days"
      customer_message: "Refund will appear in your credit card statement in 2-4 business days"
    
    DEBIT_CARD:
      processing: "3-5 business days"
      customer_message: "Refund will be credited to your account in 3-5 business days"
    
    UPI:
      processing: "Instant to 24 hours"
      customer_message: "Refund will be credited instantly or within 24 hours"
    
    WALLET:
      processing: "Instant"
      customer_message: "₹{amount} has been added to your Kirana Wallet instantly"
    
    COD:
      processing: "Wallet credit only"
      customer_message: "₹{amount} added to your Wallet for future orders"
    
    NETBANKING:
      processing: "3-5 business days"
      customer_message: "Refund will appear in your bank account in 3-5 business days"
  
  communication_schedule:
    immediate: "Refund approved. ₹{amount} will be credited via {method}"
    day_3: "Your refund is being processed" (if not yet reflected)
    day_7: "Please contact bank if refund not received"
```

**Proactive Updates:**
- SMS immediately upon approval
- Email with timeline details
- In-app notification
- Follow-up if bank delays

---

### 8.4 Internal Queue Mapping

**Rule ID:** OPS-004  
**Business Logic:** Route tickets to appropriate team

```python
def route_ticket_to_queue(ticket_data, resolution_data):
    """
    Determine which internal team should handle ticket
    """
    
    # Auto-resolved tickets
    if resolution_data['auto_approved'] == True:
        return {
            'queue': 'AUTO_RESOLVED',
            'sla': 'IMMEDIATE',
            'assignee': 'SYSTEM'
        }
    
    # Escalation routing
    if ticket_data['escalation_required']:
        severity = ticket_data['severity']
        
        queue_map = {
            'CRITICAL': 'L3_SENIOR_MANAGER',
            'HIGH': 'L2_TEAM_LEAD',
            'MEDIUM': 'L1_AGENT_REVIEW'
        }
        
        sla_map = {
            'CRITICAL': '15_MINUTES',
            'HIGH': '2_HOURS',
            'MEDIUM': '24_HOURS'
        }
        
        return {
            'queue': queue_map[severity],
            'sla': sla_map[severity],
            'priority': severity
        }
    
    # Special team routing
    if ticket_data['issue_category'] == 'FOOD_SAFETY':
        return {'queue': 'FOOD_SAFETY_TEAM', 'sla': '30_MINUTES'}
    
    if ticket_data['issue_category'] == 'FRAUD_SUSPECTED':
        return {'queue': 'FRAUD_TEAM', 'sla': '24_HOURS'}
    
    if ticket_data['issue_category'] == 'PAYMENT_ISSUE':
        return {'queue': 'FINANCE_TEAM', 'sla': '24_HOURS'}
    
    if ticket_data['customer_segment'] == 'VIP':
        return {'queue': 'VIP_CONCIERGE', 'sla': '1_HOUR'}
    
    # Default agent review
    return {
        'queue': 'STANDARD_REVIEW',
        'sla': '48_HOURS',
        'priority': 'NORMAL'
    }
```

---

### 8.5 Customer Abuse Handling Workflow

**Rule ID:** OPS-005  
**Business Logic:** Protect staff from abusive customers

```yaml
abuse_handling:
  verbal_abuse:
    first_incident:
      action: WARNING_EMAIL
      content: "Please maintain respectful communication with our team"
      
    second_incident:
      action: MANAGER_REVIEW
      possible_outcome: TEMPORARY_SUSPENSION_7_DAYS
      
    third_incident:
      action: PERMANENT_BLOCK
      reason: "Repeated abusive behavior"
  
  threats_violence:
    any_incident:
      action: IMMEDIATE_BLOCK
      escalate_to: LEGAL_TEAM_PLUS_SECURITY
      police_report: IF_CREDIBLE_THREAT
      staff_protection: ALERT_ALL_TEAMS
  
  blackmail_extortion:
    detection:
      - "I'll post bad review unless..."
      - "Give me refund or I'll complain to media"
      - "I know people in [authority]"
    
    action:
      - DOCUMENT_THOROUGHLY
      - ESCALATE_TO_LEGAL
      - REJECT_COMPLAINT
      - BLOCK_ACCOUNT
      - LEGAL_ACTION_IF_APPROPRIATE
  
  discriminatory_behavior:
    any_incident:
      action: WARNING_FIRST_BLOCK_SECOND
      reason: "We don't tolerate discrimination"
      support_staff: PROVIDE_COUNSELING_IF_NEEDED
```

**Staff Protection Priority:** Staff safety and dignity > customer satisfaction.

---

## Financial Impact Analysis

### Expected Annual Costs by Module

| Module | Estimated Annual Impact | Risk if Overgenerous | Risk if Too Strict |
|--------|------------------------|---------------------|-------------------|
| Resolution Policy | ₹82.8 Cr | +₹20-30 Cr leakage | Customer churn |
| Evidence Requirements | -₹5 Cr (fraud prevented) | +₹10 Cr fraud | Friction, dissatisfaction |
| Escalation Rules | ₹2 Cr (agent costs) | +₹3 Cr (inefficiency) | Auto-approve risks |
| Compliance | ₹3.5 Cr | Legal exposure | Non-compliance fines |
| Fraud Prevention | -₹8 Cr (prevented) | +₹15 Cr losses | False positives alienate |
| Food Intelligence | ₹45 Cr | +₹10 Cr abuse | Safety risks |
| FMCG Intelligence | ₹25 Cr | +₹5 Cr abuse | Quality reputation |
| Operations | ₹1 Cr | Inefficiency | Delayed resolutions |

**Total Net Cost (with rules):** ₹146.3 Cr  
**Estimated Cost Without Rules:** ₹190+ Cr  
**Net Savings:** ₹43.7+ Cr per year

**Key Metrics to Monitor:**
- Refund rate (target: <3% of GMV)
- Auto-resolution rate (target: 78%)
- Fraud detection rate (target: 92%+)
- Customer satisfaction post-resolution (target: 4.2+/5)
- Repeat complaint rate (target: <1.5%)

---

## Approval Sign-offs

This document requires approval from multiple stakeholders before implementation.

### Required Approvals

**Product Management**
- [ ] VP Product: _________________________ Date: _______
- [ ] Senior PM (CX): _________________________ Date: _______

**Finance**
- [ ] CFO: _________________________ Date: _______
- [ ] Finance Manager: _________________________ Date: _______

**Legal & Compliance**
- [ ] Head of Legal: _________________________ Date: _______
- [ ] Compliance Officer: _________________________ Date: _______

**Operations**
- [ ] VP Operations: _________________________ Date: _______
- [ ] Operations Manager: _________________________ Date: _______

**Technology**
- [ ] CTO: _________________________ Date: _______
- [ ] Engineering Lead: _________________________ Date: _______

### Review Schedule

**First Review:** [Date] - 30 days post-launch  
**Quarterly Reviews:** Every 90 days  
**Emergency Review:** Triggered by >10% deviation in key metrics

### Version Control

| Version | Date | Changes | Approved By |
|---------|------|---------|-------------|
| 1.0.0 | 2026-02-26 | Initial draft | Pending |

---

## Next Steps

1. **PM Review:** Validate customer experience impact (1 week)
2. **Finance Review:** Approve P&L impact and caps (1 week)
3. **Legal Review:** Ensure regulatory compliance (1 week)
4. **Operations Review:** Confirm operational feasibility (1 week)
5. **Technical Review:** Validate system can implement rules (1 week)
6. **Pilot Program:** Test on 5% of traffic (2 weeks)
7. **Full Rollout:** Deploy to 100% traffic
8. **Monitor & Iterate:** Weekly reviews for first month

---

**Document Status:** DRAFT - Awaiting Approvals  
**Next Review:** [30 days post-approval]  
**Owner:** Product Management Team  
**Last Modified:** February 26, 2026