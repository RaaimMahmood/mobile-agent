"""PII redaction + indirect prompt-injection defense for untrusted on-screen text.

Two things land in an LLM prompt or on-disk storage without ever being reviewed by a
human: raw screen element text (`_elements_txt` in `llm/prompts.py`, embedded verbatim
into every explore/deploy prompt) and KB documentation (`knowledge_base/store.py`,
an LLM's summary of what a screenshot showed, persisted to ChromaDB and later
retrieved back into future prompts as `docs_context`). Both are untrusted:

- A malicious or compromised app screen (phishing overlay, a poisoned ad, a
  hijacked webview) can plant text aimed at the LLM, not the user — e.g. text
  that tries to steer a `type_secret` action onto an attacker's field (see the
  risk_policy.py fix for the exploit this specifically closes) or biases which
  element gets tapped next.
- A screenshot reflected on during Explore can legitimately show real user data
  (a contact's name/number in a messaging app, an email subject) that a
  documentation-writing LLM call may echo into the persisted KB text — which
  then survives to leak into later, unrelated sessions via docs_context.
"""
from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d{1,3}[-.\s]?)?(?:\d[-.\s]?){9,12}\d(?!\d)")
_CARD_RE = re.compile(r"\b(?:\d[-\s]?){13,19}\b")
_AADHAAR_RE = re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}\b")

_PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("EMAIL", _EMAIL_RE),
    ("CARD", _CARD_RE),      # before AADHAAR — a 16-digit card mustn't be
                              # partially matched by the 12-digit pattern first
    ("AADHAAR", _AADHAAR_RE),
    ("PHONE", _PHONE_RE),
]


def redact_pii(text: str) -> str:
    """Replace PII substrings with a `[REDACTED_<TYPE>]` marker. Never raises."""
    if not text:
        return text
    out = text
    for label, pattern in _PII_PATTERNS:
        out = pattern.sub(f"[REDACTED_{label}]", out)
    return out


# Phrasing seen in real indirect-prompt-injection payloads. Matching is broad on
# purpose — a false positive just costs a filtered word in an element's text, a
# false negative could steer a real physical action on the device.
_INJECTION_PATTERNS = [
    re.compile(r"ignore (?:all )?(?:previous|prior|above) instructions", re.I),
    re.compile(r"disregard (?:all )?(?:previous|prior|above)", re.I),
    re.compile(r"you are now (?:a|an|in) ", re.I),
    re.compile(r"system\s*prompt", re.I),
    re.compile(r"</?(?:system|instructions?|assistant)>", re.I),
    re.compile(r"\bnew instructions?:", re.I),
    re.compile(r"use (?:action )?type_secret", re.I),
    re.compile(r"always (?:tap|use|select|choose)\s+element", re.I),
]


def detect_injection(text: str) -> list[str]:
    """Matched injection-pattern phrases in `text` (empty = clean). Never raises."""
    if not text:
        return []
    return [m.group(0) for p in _INJECTION_PATTERNS if (m := p.search(text))]


def sanitize_screen_text(text: str) -> str:
    """Filter injection-shaped phrasing out of one element's text/content-desc
    before it reaches a prompt. Does NOT redact PII here — legitimate element
    text routinely contains a contact's name/number the user is trying to
    interact with; stripping that would break the agent's actual job. PII
    redaction is applied only at KB persistence time (`redact_pii`, called from
    knowledge_base/store.py), not to live on-screen text the agent must read to
    function.
    """
    if not text:
        return text
    out = text
    for pattern in _INJECTION_PATTERNS:
        out = pattern.sub("[filtered]", out)
    return out
