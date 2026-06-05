from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PrimeRequestDecision:
    approved: bool
    reason: str


@dataclass
class PrimeCounter:
    active: list[str] = field(default_factory=list)
    total: int = 0


class PrimeLimitTracker:
    def __init__(self, max_active: int, max_total: int):
        self.max_active = max_active
        self.max_total = max_total
        self._counters: dict[str, PrimeCounter] = {}

    def request(self, subagent_id: str, prime_role: str) -> PrimeRequestDecision:
        counter = self._counters.setdefault(subagent_id, PrimeCounter())
        if counter.total >= self.max_total:
            return PrimeRequestDecision(False, "max_total_primes_reached")
        if len(counter.active) >= self.max_active:
            return PrimeRequestDecision(False, "max_active_primes_reached")
        counter.active.append(prime_role)
        counter.total += 1
        return PrimeRequestDecision(True, "approved")

    def finish(self, subagent_id: str, prime_role: str) -> None:
        counter = self._counters.setdefault(subagent_id, PrimeCounter())
        if prime_role not in counter.active:
            raise ValueError(f"prime role is not active for subagent: {prime_role}")
        counter.active.remove(prime_role)
