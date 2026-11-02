"""Accumulates real, measured token/latency usage from the local Ollama
classifier calls made during a pipeline run, for usage_report.md. Every
number here comes directly from Ollama's own response fields
(prompt_eval_count, eval_count, total_duration) or wall-clock
measurements taken by this process -- nothing is estimated or invented.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class UsageTracker:
    model: str
    live_calls: int = 0
    cache_hits: int = 0
    fallback_calls: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_duration_ns: int = 0
    wall_clock_start: float = field(default_factory=time.time)
    wall_clock_end: float = 0.0

    def record(self, result) -> None:
        if result.cache_hit:
            self.cache_hits += 1
            return
        if result.source == "fallback_heuristic":
            self.fallback_calls += 1
            return
        self.live_calls += 1
        self.total_prompt_tokens += result.prompt_eval_count or 0
        self.total_completion_tokens += result.eval_count or 0
        self.total_duration_ns += result.total_duration_ns or 0

    def finish(self) -> None:
        self.wall_clock_end = time.time()

    @property
    def total_calls(self) -> int:
        return self.live_calls + self.cache_hits + self.fallback_calls

    @property
    def total_tokens(self) -> int:
        return self.total_prompt_tokens + self.total_completion_tokens

    @property
    def wall_clock_seconds(self) -> float:
        return self.wall_clock_end - self.wall_clock_start
