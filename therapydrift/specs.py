from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from typing import Any


FENCE_INFO = "therapydrift"

_FENCE_RE = re.compile(
    r"```(?P<info>therapydrift)\s*\n(?P<body>.*?)\n```",
    re.DOTALL,
)


def extract_therapydrift_spec(description: str) -> str | None:
    m = _FENCE_RE.search(description or "")
    if not m:
        return None
    return m.group("body").strip()


def parse_therapydrift_spec(text: str) -> dict[str, Any]:
    data = tomllib.loads(text)
    if not isinstance(data, dict):
        raise ValueError("therapydrift block must parse to a TOML table/object.")
    return data


@dataclass(frozen=True)
class TherapydriftSpec:
    schema: int
    min_signal_count: int
    followup_prefixes: list[str]
    require_recovery_plan: bool
    ignore_signal_prefixes: list[str]
    cooldown_seconds: int
    max_auto_actions_per_hour: int
    min_new_signals: int
    circuit_breaker_after: int

    @staticmethod
    def from_raw(raw: dict[str, Any]) -> "TherapydriftSpec":
        schema = int(raw.get("schema", 1))
        min_signal_count = int(raw.get("min_signal_count", 2))
        if min_signal_count < 1:
            min_signal_count = 1
        followup_prefixes = [str(x) for x in (raw.get("followup_prefixes") or ["drift-", "speedrift-pit-"])]
        require_recovery_plan = bool(raw.get("require_recovery_plan", True))
        ignore_signal_prefixes = [str(x) for x in (raw.get("ignore_signal_prefixes") or ["Therapydrift:"])]
        cooldown_seconds = int(raw.get("cooldown_seconds", 1800))
        if cooldown_seconds < 0:
            cooldown_seconds = 0
        max_auto_actions_per_hour = int(raw.get("max_auto_actions_per_hour", 2))
        if max_auto_actions_per_hour < 0:
            max_auto_actions_per_hour = 0
        min_new_signals = int(raw.get("min_new_signals", 1))
        if min_new_signals < 0:
            min_new_signals = 0
        circuit_breaker_after = int(raw.get("circuit_breaker_after", 6))
        if circuit_breaker_after < 1:
            circuit_breaker_after = 1
        return TherapydriftSpec(
            schema=schema,
            min_signal_count=min_signal_count,
            followup_prefixes=followup_prefixes,
            require_recovery_plan=require_recovery_plan,
            ignore_signal_prefixes=ignore_signal_prefixes,
            cooldown_seconds=cooldown_seconds,
            max_auto_actions_per_hour=max_auto_actions_per_hour,
            min_new_signals=min_new_signals,
            circuit_breaker_after=circuit_breaker_after,
        )
