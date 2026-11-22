"""How this computer's Memoreei Cloud account stands: what the Connections card
says about billing, and what a chatbot on this computer is told when the account
has lapsed.

The service says it in its answer to ``GET /v1/device``, and memoreei asks only
when it already talks to the service or holds a tunnel to it: on connecting, at
the tunnel's start and once a day while it runs, after renewing the certificate,
and when the relay closes the tunnel for an unpaid account (4402) or lets it back.
Never because a page is open. The answer is kept in ``cloud/billing.json``, so
the dashboard and a stdio server started by an app read it without asking.

memoreei holds no price, card or invoice: billing itself is on the account site,
behind the person's own sign-in, which **Manage billing** links to.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from memoreei.cloud.state import CloudState

DAY = 86400
# Standings in which the relay refuses this computer (when the paywall is enforced).
CUT_OFF = ("disconnected", "cancelled", "refunded", "paused", "none")


@dataclass(frozen=True)
class Line:
    """One line for the card: what to say, a smaller line under it, and how loudly."""
    title: str
    caption: str | None = None
    tone: str = ""              # "", "warn" or "bad"


def manage_url(auth_url: str) -> str:
    """account.<domain>/billing beside auth.<domain>: how the service is laid out."""
    scheme, _, rest = auth_url.partition("://")
    host = rest.split("/", 1)[0]
    if host.startswith("auth."):
        host = "account." + host[len("auth."):]
    return f"{scheme or 'https'}://{host}/billing"


def days_left(until: int, now: float) -> int:
    """Whole days, rounded up: a trial's last day says 1."""
    return max(0, -(-(int(until) - int(now)) // DAY))


def _date(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%-d %B %Y")


def _cut_off(billing: dict[str, Any] | None, tunnel_state: str | None) -> bool:
    if tunnel_state == "unpaid":
        return True
    if tunnel_state == "online" or not billing:
        return False
    return billing.get("standing") in CUT_OFF and billing.get("paywall", True) is not False


def describe(billing: dict[str, Any] | None, tunnel_state: str | None = None,
             now: float | None = None) -> Line | None:
    """What the card says, or None for nothing to say (paid up, or not known yet).

    The relay's word (the tunnel's state) wins over a stored answer that may be a
    day old: a 4402 means disconnected, and a tunnel that's online isn't.
    """
    now = time.time() if now is None else now
    b = billing or {}
    standing, until = b.get("standing"), b.get("until")
    if tunnel_state == "unpaid" and standing not in CUT_OFF:
        standing, until = "disconnected", None
    if standing == "trialing" and until and until > now:
        n = days_left(until, now)
        return Line(f"Trial: {n} day{'' if n == 1 else 's'} left",
                    f"It ends on {_date(until)}, then the subscription you chose at checkout begins.")
    if standing == "past_due":
        return Line("Payment failed: update your card",
                    "Memoreei Cloud keeps working while the payment is retried"
                    + (f", until {_date(until)}." if until else "."), "warn")
    if standing == "ending" and until:
        return Line(f"Cancelled: works until {_date(until)}",
                    "Chatbots can reach this computer until then.")
    if not _cut_off({**b, "standing": standing}, tunnel_state):
        return None
    if standing == "disconnected":
        return Line("Disconnected: payment overdue",
                    "Chatbots can't reach this computer until the card is updated. "
                    "Paying reconnects it within a minute.", "bad")
    return Line("Disconnected: no subscription",
                "Chatbots can't reach this computer through Memoreei Cloud. "
                "Subscribing again reconnects it within a minute.", "bad")


def cut_off(state: CloudState | None = None) -> bool:
    """Whether Memoreei Cloud is refusing this computer for its account's billing."""
    state = state or CloudState()
    if state.device() is None:
        return False
    tunnel = ((state.status() or {}).get("tunnel") or {}).get("state")
    return _cut_off(state.billing(), tunnel)


def mcp_note(state: CloudState | None = None) -> str:
    """Said to chatbots on this computer while the account has lapsed, so one isn't
    left guessing why the person's web chatbots stopped answering. Empty otherwise."""
    state = state or CloudState()
    try:
        if not cut_off(state):
            return ""
        device = state.device()
    except Exception:
        return ""
    url = manage_url(device.auth_url) if device else None
    return ("\nMemoreei Cloud has disconnected this computer because the account's payment is "
            "overdue or the subscription has ended, so web chatbots such as Claude.ai and "
            "ChatGPT can't reach it. Apps on this computer, like this one, still work. If the "
            "person asks why a web chatbot can't reach their messages, tell them that, and "
            "that they can fix it in the memoreei dashboard's Connections page"
            + (f" or at {url}" if url else "") + ".\n")


def remember(state: CloudState, answer: dict[str, Any]) -> dict[str, Any] | None:
    """Keep the billing part of a ``GET /v1/device`` answer. An older service says
    nothing about billing; then nothing is kept."""
    billing = answer.get("billing") if isinstance(answer, dict) else None
    if isinstance(billing, dict) and isinstance(billing.get("standing"), str):
        state.save_billing(billing)
        return billing
    return None
