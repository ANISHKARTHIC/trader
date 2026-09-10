"""Formats a completed companion scan (agent.companion.build_today output)
into a Telegram-friendly daily report, and sends it.

Kept separate from companion.py: that module produces data, this module
produces prose for a specific channel. A future second channel (email, a
Slack webhook) would get its own formatter here, not a fork of companion.py.
"""

from __future__ import annotations

from agent.companion import ActionLabel, CompanionAction
from agent.notify.telegram import send_message


def _fmt_money(n: float) -> str:
    return f"₹{n:,.0f}"


def _fmt_price(n: float) -> str:
    return f"₹{n:,.2f}"


def _action_line(a: CompanionAction) -> str:
    plan = a.result.trade_plan
    if a.holding_eval:
        he = a.holding_eval
        pnl_sign = "+" if he.unrealized_pnl >= 0 else ""
        return (
            f"*{a.symbol}* — {a.label.value}\n"
            f"  P&L {pnl_sign}{_fmt_money(he.unrealized_pnl)} ({he.unrealized_pnl_pct:.1f}%) · "
            f"last {_fmt_price(he.last_price)} · stop {_fmt_price(he.hard_stop)}"
        )
    if plan.side.value == "FLAT":
        return f"*{a.symbol}* — {a.label.value}"
    return (
        f"*{a.symbol}* — {a.label.value}\n"
        f"  entry {_fmt_price(plan.entry)} · stop {_fmt_price(plan.stop)} · "
        f"target {_fmt_price(plan.target)} · qty {plan.suggested_qty}"
    )


def build_report_text(actions: list[CompanionAction], analysis_date: str) -> str:
    needs_attention = [
        a for a in actions if a.label in (ActionLabel.EXIT, ActionLabel.TRIM)
    ]
    new_ideas = [
        a for a in actions
        if not a.is_existing_holding and a.label == ActionLabel.BUY_NEW
    ]
    other_holdings = [
        a for a in actions
        if a.is_existing_holding and a.label not in (ActionLabel.EXIT, ActionLabel.TRIM)
    ]

    lines = [f"*Verdict — {analysis_date}*", ""]

    if needs_attention:
        lines.append("⚠️ *Needs attention*")
        lines.extend(_action_line(a) for a in needs_attention)
        lines.append("")

    if new_ideas:
        lines.append("🟢 *New opportunities*")
        lines.extend(_action_line(a) for a in new_ideas)
        lines.append("")

    if other_holdings:
        lines.append("📋 *Other holdings*")
        lines.extend(_action_line(a) for a in other_holdings)
        lines.append("")

    if not needs_attention and not new_ideas and not other_holdings:
        lines.append("Nothing actionable today.")

    return "\n".join(lines).strip()


def send_daily_report(actions: list[CompanionAction], analysis_date: str) -> bool:
    """Format and send the report. Returns whether the send succeeded
    (False if Telegram isn't configured or the request failed) — never
    raises, per agent.notify.telegram's contract.
    """
    text = build_report_text(actions, analysis_date)
    return send_message(text)
