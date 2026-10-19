"""Common signal type returned by every analytic."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal, Optional

Bias = Literal["bullish", "bearish", "neutral"]


@dataclass
class Signal:
    name: str
    bias: Bias
    strength: float  # 0..1, how strongly this signal leans
    value: Optional[float]
    detail: str
    category: str = "options"

    def score(self) -> float:
        """Signed score: +strength for bullish, -strength for bearish, 0 for neutral."""
        return {"bullish": 1, "bearish": -1, "neutral": 0}[self.bias] * self.strength

    def to_dict(self) -> dict:
        d = asdict(self)
        d["score"] = self.score()
        return d
