"""Generate architecture.png from a hand-authored layout.

Run once after architecture changes:

    python scripts/gen_architecture.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


WIDTH, HEIGHT = 1600, 1100
BG = (250, 250, 252)
BORDER = (60, 70, 90)
ACCENT = (32, 99, 155)
ACCENT_SOFT = (210, 230, 245)
WARN = (200, 80, 60)
MUTED = (95, 105, 120)
TEXT = (35, 40, 55)


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/SFNSMono.ttf",
        "/System/Library/Fonts/Supplemental/Menlo.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _box(d: ImageDraw.ImageDraw, xy, fill, outline=BORDER, radius=14, width=2):
    d.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def _text(d: ImageDraw.ImageDraw, xy, text, font, fill=TEXT, anchor=None):
    # PIL forbids anchor with multiline strings; fall back to the default when so.
    if anchor and "\n" not in text:
        d.text(xy, text, font=font, fill=fill, anchor=anchor)
    else:
        d.text(xy, text, font=font, fill=fill)


def _center_text(d: ImageDraw.ImageDraw, box, text, font, fill=TEXT):
    x = (box[0] + box[2]) / 2
    y = (box[1] + box[3]) / 2
    d.text((x, y), text, font=font, fill=fill, anchor="mm")


def _arrow(d: ImageDraw.ImageDraw, start, end, color=ACCENT, width=3, head=12):
    d.line([start, end], fill=color, width=width)
    import math

    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    left = (end[0] - head * math.cos(angle - math.pi / 7),
            end[1] - head * math.sin(angle - math.pi / 7))
    right = (end[0] - head * math.cos(angle + math.pi / 7),
             end[1] - head * math.sin(angle + math.pi / 7))
    d.polygon([end, left, right], fill=color)


def main() -> None:
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    d = ImageDraw.Draw(img)

    title_font = _load_font(36)
    h_font = _load_font(22)
    m_font = _load_font(18)
    s_font = _load_font(15)

    _text(d, (40, 28),
          "ShopWave Autonomous Support Resolution Agent — Architecture",
          title_font)
    _text(d, (40, 76),
          "20 tickets in parallel · CLASSIFY → PLAN → ACT → VERIFY → EVALUATE → RESOLVE/ESCALATE → LOG",
          m_font, fill=MUTED)

    # ---- Runner band ---------------------------------------------------------
    runner = (40, 120, WIDTH - 40, 200)
    _box(d, runner, fill=ACCENT_SOFT, outline=ACCENT, radius=16, width=2)
    _center_text(d, runner,
                 "run.py  —  asyncio.gather + Semaphore(5)   [ process_ticket × 20 ]",
                 h_font, fill=ACCENT)

    # ---- Agent loop boxes ---------------------------------------------------
    steps = [
        ("1. CLASSIFY",  "llm.py", "Groq / Ollama / rules\nstructured JSON"),
        ("2. PLAN",      "policies.py", "chain template\ncategory routing"),
        ("3. ACT",       "tools.py via registry", "11 async tools\nretry + validate"),
        ("4. VERIFY",    "policies.py", "refund_guard\nnon_returnable\nconflict check"),
        ("5. EVALUATE",  "policies.py", "confidence adjust\ndecision_basis"),
        ("6. RESOLVE /\nESCALATE", "agent.py", "write action\nor escalate"),
        ("7. LOG",       "audit_log.json", "reasoning_trace\nper-ticket entry"),
    ]

    step_top = 240
    step_bot = 430
    gap = 20
    step_w = (WIDTH - 80 - gap * (len(steps) - 1)) / len(steps)

    centers: list[tuple[float, float]] = []
    for i, (label, sub, detail) in enumerate(steps):
        x0 = 40 + i * (step_w + gap)
        x1 = x0 + step_w
        box = (x0, step_top, x1, step_bot)
        _box(d, box, fill="white", outline=BORDER, radius=12, width=2)
        multiline_label = "\n" in label
        label_font = m_font if multiline_label else h_font
        _text(d, (x0 + 14, step_top + 14), label, label_font, fill=ACCENT)
        sub_y = step_top + (78 if multiline_label else 54)
        detail_y = sub_y + 36
        _text(d, (x0 + 14, sub_y), sub, s_font, fill=MUTED)
        _text(d, (x0 + 14, detail_y), detail, s_font, fill=TEXT)
        centers.append(((x0 + x1) / 2, (step_top + step_bot) / 2))

        if i < len(steps) - 1:
            _arrow(d,
                   (x1 + 2, (step_top + step_bot) / 2),
                   (x0 + step_w + gap - 2, (step_top + step_bot) / 2),
                   color=ACCENT, width=3, head=10)

    # ---- Registry band ------------------------------------------------------
    reg_top = 480
    reg = (40, reg_top, WIDTH - 40, reg_top + 230)
    _box(d, reg, fill="white", outline=ACCENT, radius=14, width=2)
    _text(d, (60, reg_top + 16),
          "registry.py  —  unified retry / Pydantic validation / DLQ / LLM repair",
          h_font, fill=ACCENT)

    bullets = [
        "• Wraps every tool call AND every LLM call",
        "• Exponential backoff + jitter (max_retries=2)",
        "• Pydantic validates Customer / Order / Product responses",
        "• Classifies errors: timeout · malformed_json · partial_fields · stale_data · schema_violation",
        "• stale_data is NOT retryable — prevents masking real inconsistencies",
        "• Exhaustion → dead_letter_queue.json + RegistryError",
        "• LLM path: 429 retry-with-jitter + one schema-repair attempt",
    ]
    for i, b in enumerate(bullets):
        _text(d, (80, reg_top + 58 + i * 24), b, s_font, fill=TEXT)

    # ---- Side panels: policies + thresholds --------------------------------
    side_top = 740
    pol = (40, side_top, 800, side_top + 320)
    _box(d, pol, fill="white", outline=BORDER, radius=14, width=2)
    _text(d, (60, side_top + 16),
          "policies.py  +  TicketState  —  state + decisions",
          h_font, fill=ACCENT)
    pol_items = [
        "TicketState.cache                 → customer / order / product / KB hits",
        "TicketState.tools_used            → executed chain for audit + UI",
        "TicketState.failures              → recovered / unrecovered evidence",
        "TicketState.reasoning_trace       → explainable per-ticket log",
        "classifier / evidence / action    → three-way confidence split",
        "reply_sent / escalation_brief     → final operator-visible outputs",
        "chain_template(category)          → per-category tool list",
        "refund_guard(state)               → eligibility / VIP exception",
        "compute_decision_basis(state)     → 7-value enum",
        "compute_escalation_brief(state)   → human-readable handoff",
    ]
    for i, it in enumerate(pol_items):
        _text(d, (80, side_top + 58 + i * 24), it, s_font, fill=TEXT)

    # Thresholds / tools panel
    th = (830, side_top, WIDTH - 40, side_top + 320)
    _box(d, th, fill="white", outline=BORDER, radius=14, width=2)
    _text(d, (850, side_top + 16),
          "Thresholds  ·  Tools  ·  Outcomes",
          h_font, fill=ACCENT)
    th_items = [
        "Dual confidence:  reversible ≥ 0.70   |   irreversible ≥ 0.85",
        "DOA uses the same refund guardrail order; KB §1.5 changes eligibility",
        "",
        "Tools: get_order · get_customer · get_customer_orders · get_product",
        "       search_knowledge_base · check_refund_eligibility",
        "       issue_refund (idempotent lock) · cancel_order",
        "       initiate_exchange · send_reply · escalate",
        "",
        "decision_basis ∈ {",
        "   successful_resolution · recovered_and_resolved · policy_guard",
        "   low_confidence · tool_failure · unresolvable_ticket · fraud_detected",
        "}",
    ]
    for i, it in enumerate(th_items):
        _text(d, (850, side_top + 58 + i * 22), it, s_font, fill=TEXT)

    # ---- Wire registry to ACT and to LLM ------------------------------------
    # Arrow from ACT (steps[2]) down to registry
    act_cx, act_cy = centers[2]
    _arrow(d, (act_cx, step_bot + 2), (act_cx, reg_top - 2), color=ACCENT, width=3)
    # Arrow from CLASSIFY to registry (LLM path)
    cls_cx, cls_cy = centers[0]
    _arrow(d, (cls_cx, step_bot + 2), (cls_cx + 40, reg_top - 2),
           color=ACCENT, width=2)

    # Footer
    _text(d, (40, HEIGHT - 30),
          "Failures: timeout (read tools) · malformed JSON (get_product) · partial (get_customer) · "
          "stale (get_order) · 429 (LLM) · RuntimeError (check_refund_eligibility)",
          s_font, fill=MUTED)

    out = Path(__file__).resolve().parent.parent / "architecture.png"
    img.save(out, format="PNG")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
