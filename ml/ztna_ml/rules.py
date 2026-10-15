"""Deterministic detections. Evaluated on every request against the peer's open window,
so obvious attacks are flagged immediately instead of at window close.
Each rule fires at most once per peer per window."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field

from .features import Window


@dataclass(frozen=True)
class Detection:
    source: str  # "rule" | "model"
    kind: str
    penalty: float  # trust points to subtract (before decay)
    details: dict = field(default_factory=dict)


def _env(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


@dataclass(frozen=True)
class RuleConfig:
    brute_force_failures: int = int(_env("RULE_BRUTE_FORCE_FAILURES", 8))
    path_enum_404: int = int(_env("RULE_PATH_ENUM_404", 15))
    path_enum_uniq_ratio: float = _env("RULE_PATH_ENUM_UNIQ_RATIO", 0.7)
    exfil_bytes: int = int(_env("RULE_EXFIL_BYTES", 64 * 1024 * 1024))
    rate_spike_requests: int = int(_env("RULE_RATE_SPIKE_REQUESTS", 300))
    missing_ua_requests: int = int(_env("RULE_MISSING_UA_REQUESTS", 5))
    denied_requests: int = int(_env("RULE_DENIED_REQUESTS", 20))


Rule = Callable[[Window, RuleConfig], Detection | None]


def brute_force(w: Window, c: RuleConfig) -> Detection | None:
    if w.n_auth_fail >= c.brute_force_failures:
        return Detection("rule", "brute_force", 65, {"auth_failures": w.n_auth_fail})
    return None


def scanner_ua(w: Window, c: RuleConfig) -> Detection | None:
    if w.n_scanner_ua:
        tools = sorted(ua for ua in w.uas if ua)[:3]
        return Detection("rule", "scanner_user_agent", 45, {"user_agents": tools})
    return None


def path_enumeration(w: Window, c: RuleConfig) -> Detection | None:
    if w.n_404 >= c.path_enum_404 and len(w.paths) / w.count >= c.path_enum_uniq_ratio:
        return Detection(
            "rule", "path_enumeration", 45, {"not_found": w.n_404, "unique_paths": len(w.paths)}
        )
    return None


def exfiltration(w: Window, c: RuleConfig) -> Detection | None:
    if w.bytes_sum >= c.exfil_bytes:
        return Detection("rule", "data_exfiltration", 65, {"bytes_out": w.bytes_sum})
    return None


def rate_spike(w: Window, c: RuleConfig) -> Detection | None:
    if w.count >= c.rate_spike_requests:
        return Detection("rule", "request_flood", 30, {"requests": w.count})
    return None


def missing_ua(w: Window, c: RuleConfig) -> Detection | None:
    if w.n_empty_ua >= c.missing_ua_requests:
        return Detection("rule", "missing_user_agent", 10, {"requests": w.n_empty_ua})
    return None


def denied_burst(w: Window, c: RuleConfig) -> Detection | None:
    """Hammering resources the access policy has closed for this peer."""
    if w.n_denied >= c.denied_requests:
        return Detection("rule", "access_denied_burst", 20, {"denied": w.n_denied})
    return None


RULES: dict[str, Rule] = {
    "brute_force": brute_force,
    "scanner_user_agent": scanner_ua,
    "path_enumeration": path_enumeration,
    "data_exfiltration": exfiltration,
    "request_flood": rate_spike,
    "missing_user_agent": missing_ua,
    "access_denied_burst": denied_burst,
}


def evaluate(w: Window, config: RuleConfig) -> list[Detection]:
    out = []
    for name, rule in RULES.items():
        if name in w.fired:
            continue
        if (d := rule(w, config)) is not None:
            w.fired.add(name)
            out.append(d)
    return out
