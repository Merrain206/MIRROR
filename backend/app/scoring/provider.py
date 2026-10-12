from typing import Protocol
from uuid import UUID

from app.domain.models import ScoreState


class ScoringProvider(Protocol):
    def calculate(self, game_id: UUID) -> ScoreState: ...

