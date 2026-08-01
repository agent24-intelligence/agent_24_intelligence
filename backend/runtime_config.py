"""Runtime budgets for the live demo pipeline.

The scoring rules live in ``scoring_config.py``. This module only controls how
long each external step may run so a slow provider cannot consume the whole
request budget.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.1, float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RuntimeConfig:
    disable_timeouts: bool = field(default_factory=lambda: _env_bool("DISABLE_TIMEOUTS"))
    total_timeout_s: float = field(default_factory=lambda: _env_float("ANALYZE_TIMEOUT_S", _env_float("PIPELINE_TIMEOUT_S", 55)))
    preflight_timeout_s: float = field(default_factory=lambda: _env_float("PREFLIGHT_TIMEOUT_S", 4))
    scope_timeout_s: float = field(default_factory=lambda: _env_float("SCOPE_TIMEOUT_S", 3))
    query_generation_timeout_s: float = field(default_factory=lambda: _env_float("QUERY_GENERATION_TIMEOUT_S", 2))
    scholar_search_timeout_s: float = field(default_factory=lambda: _env_float("SCHOLAR_SEARCH_TIMEOUT_S", 5))
    academic_vocab_timeout_s: float = field(default_factory=lambda: _env_float("ACADEMIC_VOCAB_TIMEOUT_S", 5))
    adoption_search_timeout_s: float = field(default_factory=lambda: _env_float("ADOPTION_SEARCH_TIMEOUT_S", 5))
    adoption_extraction_timeout_s: float = field(default_factory=lambda: _env_float("ADOPTION_EXTRACTION_TIMEOUT_S", 4))
    academic_extraction_timeout_s: float = field(default_factory=lambda: _env_float("ACADEMIC_EXTRACTION_TIMEOUT_S", 10))
    linkage_timeout_s: float = field(default_factory=lambda: _env_float("LINKAGE_TIMEOUT_S", 4))
    adversarial_timeout_s: float = field(default_factory=lambda: _env_float("ADVERSARIAL_TIMEOUT_S", 4))
    counter_relink_timeout_s: float = field(default_factory=lambda: _env_float("COUNTER_RELINK_TIMEOUT_S", 4))
    deep_research_timeout_s: float = field(default_factory=lambda: _env_float("DEEP_RESEARCH_TIMEOUT_S", 5))
    finalization_timeout_s: float = field(default_factory=lambda: _env_float("FINALIZATION_TIMEOUT_S", 4))
    final_synthesis_timeout_s: float = field(default_factory=lambda: _env_float("FINAL_SYNTHESIS_TIMEOUT_S", 30))
    visualization_timeout_s: float = field(default_factory=lambda: _env_float("VISUALIZATION_TIMEOUT_S", 4))
    final_reserve_s: float = field(default_factory=lambda: _env_float("FINAL_RESERVE_S", 7))
    max_search_results: int = field(default_factory=lambda: max(1, int(os.environ.get("MAX_SEARCH_RESULTS", "5"))))
    max_adoption_queries: int = field(default_factory=lambda: max(1, int(os.environ.get("MAX_ADOPTION_QUERIES", "5"))))
    max_extraction_items: int = field(default_factory=lambda: max(1, int(os.environ.get("MAX_EXTRACTION_ITEMS", "24"))))


@dataclass
class AnalysisDeadline:
    budget_s: float
    started_at: float = field(default_factory=time.monotonic)

    def remaining(self) -> float:
        return max(0.0, self.budget_s - (time.monotonic() - self.started_at))

    def timeout(self, configured_s: float, *, reserve_s: float = 0.0) -> float:
        available = self.remaining() - max(0.0, reserve_s)
        return max(0.05, min(configured_s, available))
