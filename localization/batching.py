"""Adaptive batching and split recovery.

Batches are sized by more than unit count: char weight and protected-token
load shrink a batch below the ceiling; automatic sizing never grows above
`--batch-size`. Recovery splits only the still-invalid subset in half and
shares one bounded root-batch request budget.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Batch, TranslationUnit

DEFAULT_BATCH_SIZE = 40
DEFAULT_MAX_BATCH_CHARS = 24000


@dataclass
class BatchPlanner:
    batch_size: int = DEFAULT_BATCH_SIZE
    max_batch_chars: int = DEFAULT_MAX_BATCH_CHARS
    root_budget: int = 12

    def plan(self, units: list[TranslationUnit]) -> list[Batch]:
        batches: list[Batch] = []
        current: list[TranslationUnit] = []
        chars = 0
        tokens = 0
        for unit in units:
            weight = max(1, len(unit.source))
            tok = len(unit.tokens)
            if (
                current
                and (
                    len(current) >= self.batch_size
                    or chars + weight > self.max_batch_chars
                    or tokens + tok > self.batch_size * 3
                )
            ):
                batches.append(Batch(batch_id=f"b{len(batches)}", units=current))
                current = []
                chars = 0
                tokens = 0
            current.append(unit)
            chars += weight
            tokens += tok
        if current:
            batches.append(Batch(batch_id=f"b{len(batches)}", units=current))
        return batches

    def split_invalid(self, units: list[TranslationUnit], parent: Batch) -> list[Batch]:
        """Split the still-invalid subset approximately in half."""
        midpoint = max(1, len(units) // 2)
        left = units[:midpoint]
        right = units[midpoint:]
        left_batch = Batch(
            batch_id=f"{parent.batch_id}s", units=left, reason="split"
        )
        right_batch = Batch(
            batch_id=f"{parent.batch_id}t", units=right, reason="split"
        )
        return [left_batch, right_batch]