"""Pydantic schemas for every I/O boundary: tickets, tools, LLM, audit log."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---- Dataset fixtures -----------------------------------------------------


class Address(BaseModel):
    street: str
    city: str
    state: str
    zip: str


class Customer(BaseModel):
    customer_id: str
    name: str
    email: str
    phone: str
    tier: Literal["standard", "premium", "vip"]
    member_since: str
    total_orders: int
    total_spent: float
    address: Address
    notes: str = ""


class Order(BaseModel):
    order_id: str
    customer_id: str
    product_id: str
    quantity: int
    amount: float
    status: Literal["processing", "shipped", "delivered", "cancelled", "returned"]
    order_date: str
    delivery_date: Optional[str] = None
    return_deadline: Optional[str] = None
    refund_status: Optional[str] = None
    notes: str = ""


class Product(BaseModel):
    product_id: str
    name: str
    category: str
    price: float
    warranty_months: int
    return_window_days: int
    returnable: bool
    notes: str = ""


class Ticket(BaseModel):
    ticket_id: str
    customer_email: str
    subject: str
    body: str
    source: str
    created_at: str
    tier: Optional[int] = None
    expected_action: Optional[str] = None


# ---- Tool I/O -------------------------------------------------------------


class RefundEligibility(BaseModel):
    eligible: bool
    reason: str
    max_refund: float = 0.0
    requires_escalation: bool = False


class KBResult(BaseModel):
    matched: bool
    snippets: list[str] = Field(default_factory=list)


# ---- Agent internal -------------------------------------------------------


class Failure(BaseModel):
    tool: str
    error: str
    retry_count: int = 0
    recovered: bool = False


DecisionBasis = Literal[
    "successful_resolution",
    "recovered_and_resolved",
    "policy_guard",
    "low_confidence",
    "tool_failure",
    "unresolvable_ticket",
    "fraud_detected",
]


class ReasoningStep(BaseModel):
    step: str
    note: str


# ---- LLM structured outputs -----------------------------------------------


Category = Literal[
    "refund_request",
    "return_request",
    "damaged_on_arrival",
    "wrong_item",
    "cancellation",
    "shipping_inquiry",
    "refund_status_check",
    "warranty_claim",
    "policy_question",
    "social_engineering",
    "ambiguous",
]

Urgency = Literal["low", "medium", "high", "urgent"]


class Classification(BaseModel):
    category: Category
    urgency: Urgency
    resolvable: bool
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = ""


class Reply(BaseModel):
    message: str
    tone: Literal["empathetic", "firm", "neutral"] = "empathetic"


# ---- Audit log ------------------------------------------------------------


class AuditEntry(BaseModel):
    run_id: str
    ticket_id: str
    timestamp: str
    mode: str
    category: str
    urgency: str
    outcome: Literal["resolved", "escalated", "declined"]
    confidence: float
    classifier_confidence: Optional[float] = None
    evidence_confidence: Optional[float] = None
    action_confidence: Optional[float] = None
    tools_used: list[str]
    failures: list[Failure]
    recovery_attempted: bool
    decision_basis: DecisionBasis
    duration_ms: int
    reasoning_trace: list[ReasoningStep]
    expected_action: Optional[str] = None
    reply_sent: Optional[str] = None
    escalation_summary: Optional[str] = None
    escalation_brief: Optional[str] = None
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
