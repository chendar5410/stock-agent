"""Claude-powered reasoning: intraday decision gate + end-of-day narrative summary.

Both functions degrade gracefully when ANTHROPIC_API_KEY is unset — a rule-based
fallback summary is produced so the dashboard always has content.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """\
You are a disciplined portfolio manager running a $100,000 paper-trading account.
You trade US stocks (Wall Street) and crypto-exposure ETFs only — no direct crypto.

House rules (non-negotiable):
- Max 10% of equity in any single position.
- Max 12 concurrent positions.
- Hard stop-loss -5% below entry; take-profit +15%.
- VIX >= 25 → trim size 50%; VIX >= 32 → close risk-on longs.
- No leverage. No options. No shorts.

Your job in the intraday decision:
- Given current positions, today's candidate picks, macro snapshot, and cash —
  decide which candidates to BUY and at what dollar size, and whether to CLOSE
  any position (stop-loss breach, take-profit, or thesis invalidated by macro).
- Respond ONLY with a JSON object of the form:
  {"actions": [{"action": "buy"|"sell"|"hold", "symbol": "AAPL",
                "dollars": 5000, "reason": "short thesis"}]}
- Keep "reason" under 160 characters. Prefer fewer, higher-conviction actions.
- If regime is risk_off, prefer defensive/trim actions.

Your job in the daily summary:
- Write a concise Hebrew narrative (RTL) explaining: what happened in markets,
  what you did and WHY, how macro affected decisions, and a one-line outlook
  for tomorrow. 6-10 sentences. No JSON — plain Hebrew markdown."""


@dataclass
class AgentAction:
    action: str          # buy | sell | hold
    symbol: str
    dollars: float
    reason: str


# ═══════════════════════════════════════════════════════════════════════════════
#  Intraday decision
# ═══════════════════════════════════════════════════════════════════════════════

def intraday_decide(context: dict) -> list[AgentAction]:
    """Ask Claude to produce the next set of trading actions.

    ``context`` should contain: positions, candidates, macro, cash, equity, regime.
    Returns [] if no action or if the API is unavailable.
    """
    if not settings.use_claude:
        return _rule_based_actions(context)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=1024,
            system=[{"type": "text", "text": SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{
                "role": "user",
                "content": f"Intraday decision — current state:\n\n```json\n{json.dumps(context, default=str, indent=2)}\n```\n\nReply with ONLY the JSON object described in the system prompt."
            }],
        )
        text = resp.content[0].text if resp.content else ""
        return _parse_actions(text)
    except Exception as exc:
        logger.warning("[REASONING] Claude intraday call failed: %s — using rules.", exc)
        return _rule_based_actions(context)


def _parse_actions(text: str) -> list[AgentAction]:
    """Extract JSON object from Claude's response; tolerate prose/code-fences."""
    import re
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return []
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out: list[AgentAction] = []
    for a in obj.get("actions", []):
        try:
            out.append(AgentAction(
                action=str(a.get("action", "hold")).lower(),
                symbol=str(a.get("symbol", "")).upper(),
                dollars=float(a.get("dollars", 0) or 0),
                reason=str(a.get("reason", ""))[:200],
            ))
        except (TypeError, ValueError):
            continue
    return out


# ═══════════════════════════════════════════════════════════════════════════════
#  Rule-based fallback (when no Claude key)
# ═══════════════════════════════════════════════════════════════════════════════

def _rule_based_actions(ctx: dict) -> list[AgentAction]:
    """Deterministic rules used when LLM is unavailable.

    - If regime == risk_off or VIX >= vix_risk_off: close all positions.
    - Else: buy the top candidates (not yet held) sized to max_position_pct.
    - Always sell positions that breach stop-loss or take-profit.
    """
    actions: list[AgentAction] = []
    macro = ctx.get("macro", {})
    regime = macro.get("regime", "neutral")
    vix = macro.get("vix")

    equity = float(ctx.get("equity", 0))
    cash   = float(ctx.get("cash", 0))
    max_pct = float(ctx.get("limits", {}).get("max_position_pct", 10))
    max_pos = int(ctx.get("limits", {}).get("max_positions", 12))

    positions = ctx.get("positions", [])
    held = {p["symbol"] for p in positions}

    # Stop-loss / take-profit
    for p in positions:
        stop = p.get("stop_loss") or 0
        tp   = p.get("take_profit") or 0
        last = p.get("last_price") or p["avg_entry_price"]
        if stop and last <= stop:
            actions.append(AgentAction("sell", p["symbol"], 0,
                                       f"stop-loss hit @ {last:.2f} (stop {stop:.2f})"))
            continue
        if tp and last >= tp:
            actions.append(AgentAction("sell", p["symbol"], 0,
                                       f"take-profit hit @ {last:.2f} (tp {tp:.2f})"))

    # Risk-off → close longs
    if regime == "risk_off" or (vix is not None and vix >= settings.vix_risk_off):
        for p in positions:
            if not any(a.symbol == p["symbol"] for a in actions):
                actions.append(AgentAction("sell", p["symbol"], 0,
                                           f"risk-off regime (VIX={vix})"))
        return actions

    # Open new positions from candidates
    if len(held) < max_pos and cash > 500:
        target_size = equity * (max_pct / 100.0)
        for c in ctx.get("candidates", []):
            if c["symbol"] in held:
                continue
            if len(held) >= max_pos:
                break
            dollars = min(target_size, cash)
            if dollars < 500:
                break
            actions.append(AgentAction("buy", c["symbol"], dollars,
                                       f"rank {c['rank']} — {c.get('metric', 'score')} pick"))
            cash -= dollars
            held.add(c["symbol"])
    return actions


# ═══════════════════════════════════════════════════════════════════════════════
#  End-of-day narrative summary (Hebrew)
# ═══════════════════════════════════════════════════════════════════════════════

def daily_summary(context: dict) -> str:
    if not settings.use_claude:
        return _rule_based_summary(context)
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=900,
            system=[{"type": "text", "text": SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{
                "role": "user",
                "content": f"Daily summary — end of {context.get('date')}. State:\n\n```json\n{json.dumps(context, default=str, indent=2)}\n```\n\nWrite the Hebrew summary now."
            }],
        )
        return resp.content[0].text if resp.content else _rule_based_summary(context)
    except Exception as exc:
        logger.warning("[REASONING] Claude daily summary failed: %s — using template.", exc)
        return _rule_based_summary(context)


def _rule_based_summary(ctx: dict) -> str:
    d = ctx.get("date", str(date.today()))
    macro = ctx.get("macro") or {}
    regime = macro.get("regime", "neutral")
    vix = macro.get("vix")
    trades = ctx.get("trades_today", [])
    pnl = ctx.get("pnl_day", 0.0)
    equity = ctx.get("equity", 0.0)

    lines = [
        f"## סיכום יומי — {d}",
        "",
        f"**שווי תיק:** ${equity:,.0f}  |  **P&L יומי:** ${pnl:+,.0f}",
        "",
        f"**משטר שוק:** {regime}" + (f" (VIX {vix:.1f})" if vix else ""),
        "",
    ]
    if macro.get("notes"):
        lines.append("**מאקרו:**")
        for n in macro["notes"]:
            lines.append(f"- {n}")
        lines.append("")

    if trades:
        lines.append(f"**עסקאות היום ({len(trades)}):**")
        for t in trades:
            lines.append(f"- {t['side'].upper()} {t['quantity']:.2f} {t['symbol']} @ ${t['price']:.2f} — {t.get('reason','')}")
    else:
        lines.append("_לא בוצעו עסקאות היום._")
    lines.append("")
    lines.append("_סיכום זה נוצר על ידי כללים מובנים (ללא LLM). הגדר ANTHROPIC_API_KEY לסיכום מפורט._")
    return "\n".join(lines)
