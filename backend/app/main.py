import logging
import os
import time
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import Actor, Character, Claim, Event, Knowledge, LLMCall
from app.db.session import make_engine
from app.domain.models import AssertionDraft
from app.domain.errors import ActionRejected, DomainError, NotFound
from app.domain.guards import game_in
from app.llm.temporal_demo import TemporalSocietyDemoLLMProvider
from app.llm.provider import LLMProvider
from app.llm.provider import TextRequest
from app.projection.player import DebugWorldView, PlayerProjectionService, player_actor
from app.scoring.engine import ScoringEngine
from app.simulation.engine import SimulationEngine
from app.simulation.ending import final_report, finish_game
from app.simulation.narrative import narrative_context


class PlayerMessageIn(BaseModel):
    character_id: UUID
    text: str = Field(min_length=1)
    disclosed_claim_id: UUID | None = None
    confidential: bool = False
    assertion: AssertionDraft | None = None
    guidance: Literal["neutral", "directive", "supportive"] = "neutral"


class GameCreateIn(BaseModel):
    scenario: Literal["temporal", "society", "phase2"] = "temporal"


class AdvanceIn(BaseModel):
    expected_day: int = Field(ge=1, le=30)


def create_app(
    engine: Engine | None = None,
    llm: LLMProvider | None = None,
    debug: bool | None = None,
    playtest: bool | None = None,
) -> FastAPI:
    if engine is None and llm is None:
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    database = engine or make_engine()
    if llm is None and engine is None and os.getenv("MIRROR_LLM_PROVIDER", "demo") == "deepseek":
        from app.llm.deepseek import DeepSeekConfig, DeepSeekLLMProvider

        def save_usage(row: dict) -> None:
            with Session(database) as session, session.begin():
                session.add(LLMCall(
                    game_id=UUID(row["game_id"]) if row["game_id"] else None,
                    character_id=UUID(row["character_id"]) if row["character_id"] else None,
                    provider=row["provider"], model=row["model"], task=row["task"],
                    prompt_tokens=row["prompt_tokens"], completion_tokens=row["completion_tokens"],
                    context_chars=row["context_chars"], latency_ms=row["latency_ms"],
                    retries=row["retries"], success=row["success"], fallback=row["fallback"],
                ))

        llm = DeepSeekLLMProvider(DeepSeekConfig.from_env(), usage_sink=save_usage)
    elif llm is None and engine is None and os.getenv("MIRROR_LLM_PROVIDER") == "mock":
        from app.llm.mock import MockLLMProvider
        llm = MockLLMProvider()
    simulation = SimulationEngine(database, llm or TemporalSocietyDemoLLMProvider())
    scoring = ScoringEngine(database)
    playtest_enabled = playtest if playtest is not None else os.getenv("MIRROR_PLAYTEST") == "1"
    debug_enabled = (not playtest_enabled and
                     (debug if debug is not None else os.getenv("MIRROR_DEBUG") == "1"))
    api = FastAPI(title="Project MIRROR API", version="0.1.0")
    api.state.simulation = simulation
    api.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @api.exception_handler(DomainError)
    async def domain_error(_request, exc: DomainError) -> JSONResponse:
        status = 404 if isinstance(exc, NotFound) else 422 if isinstance(exc, ActionRejected) else 409
        return JSONResponse(status_code=status, content={"code": exc.code, "detail": exc.message})

    @api.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.get("/health/database")
    def database_health() -> dict[str, str]:
        try:
            with database.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            raise HTTPException(status_code=503, detail="database unavailable") from exc
        return {"status": "ok"}

    @api.post("/games")
    def initialize_game(body: GameCreateIn | None = None) -> dict:
        return simulation.initialize_game(body.scenario if body else "temporal")

    @api.get("/games/{game_id}")
    def get_game(game_id: UUID) -> dict:
        with Session(database) as session:
            game = game_in(session, game_id)
            return {"id": str(game.id), "day": game.day, "phase": game.phase,
                    "narrative_phase": narrative_context(game.day, 0)["phase"]}

    @api.get("/games/{game_id}/characters")
    def list_characters(game_id: UUID) -> list[dict]:
        with Session(database) as session:
            game_in(session, game_id)
            rows = session.scalars(
                select(Actor).where(Actor.game_id == game_id, Actor.kind == "character")
                .order_by(Actor.display_name)
            ).all()
            return [{"id": str(row.id), "name": row.display_name,
                     "age": session.get(Character, row.id).age,
                     "occupation": session.get(Character, row.id).occupation}
                    for row in rows]

    @api.get("/games/{game_id}/claims")
    def player_claims(game_id: UUID) -> list[dict]:
        with Session(database) as session:
            player = player_actor(session, game_id)
            rows = session.execute(
                select(Knowledge, Claim)
                .join(Claim, Knowledge.claim_id == Claim.id)
                .where(Knowledge.game_id == game_id,
                       Knowledge.holder_actor_id == player.id)
            ).all()
            return [
                {"id": str(claim.id), "content": claim.content,
                 "stance": knowledge.stance, "confidence": knowledge.confidence}
                for knowledge, claim in rows
            ]

    @api.post("/games/{game_id}/messages")
    async def send_message(game_id: UUID, body: PlayerMessageIn) -> dict:
        return await simulation.send_player_message(
            game_id, body.character_id, body.text, body.disclosed_claim_id,
            body.confidential,
            body.assertion,
            body.guidance,
        )

    @api.get("/games/{game_id}/conversations/{character_id}")
    def get_conversation(game_id: UUID, character_id: UUID) -> list[dict]:
        with Session(database) as session:
            return PlayerProjectionService(session).conversation(game_id, character_id)

    @api.post("/games/{game_id}/advance")
    async def advance(game_id: UUID, body: AdvanceIn) -> dict:
        calls = getattr(simulation.llm, "calls", None)
        first_call = len(calls) if calls is not None else 0
        started = time.monotonic()
        result = await simulation.advance_day(game_id, body.expected_day)
        if calls is not None:
            new = [row for row in calls[first_call:] if row["game_id"] == str(game_id)]
            latencies = sorted(row["latency_ms"] for row in new)
            parallel = sum(row["task"] == "AdviceReply" for row in new)
            logging.getLogger("uvicorn.error").info(
                "MIRROR advance day=%s calls=%s serial=%s parallel_advice=%s "
                "elapsed_ms=%s average_latency_ms=%s p95_latency_ms=%s",
                result["day"], len(new), len(new) - parallel, parallel,
                round((time.monotonic() - started) * 1000),
                round(sum(latencies) / len(latencies)) if latencies else 0,
                latencies[(95 * len(latencies) + 99) // 100 - 1] if latencies else 0,
            )
        return result

    @api.get("/games/{game_id}/advance-progress")
    def advance_progress(game_id: UUID) -> dict:
        with Session(database) as session:
            game = game_in(session, game_id)
            completed = session.scalars(select(Event.actor_id).where(
                Event.game_id == game_id, Event.day == game.day,
                Event.type == "TURN_EVALUATED",
            )).all()
            return {"day": game.day, "phase": narrative_context(game.day, 0)["phase"],
                    "completed_characters": len(set(completed)),
                    "total_characters": 6 if game.scenario == "temporal" else 0,
                    "stage": ("ending" if game.day == 30 else
                              "characters" if completed else "preparing")}

    @api.post("/games/{game_id}/finish")
    def finish(game_id: UUID) -> dict:
        return finish_game(simulation, game_id)

    @api.get("/games/{game_id}/report")
    def report(game_id: UUID) -> dict:
        with Session(database) as session:
            return final_report(session, game_id)

    @api.get("/games/{game_id}/timeline")
    def get_timeline(game_id: UUID) -> list[dict]:
        with Session(database) as session:
            return PlayerProjectionService(session).timeline(game_id)

    @api.get("/games/{game_id}/relationships")
    def get_relationships(game_id: UUID) -> list[dict]:
        with Session(database) as session:
            return PlayerProjectionService(session).relationships(game_id)

    if debug_enabled:
        @api.post("/debug/llm/smoke")
        async def llm_smoke() -> dict:
            from app.llm.deepseek import DeepSeekLLMProvider
            if not isinstance(simulation.llm, DeepSeekLLMProvider):
                raise HTTPException(status_code=503, detail="DeepSeek provider is not configured")
            answer = await simulation.llm.generate(TextRequest(
                system="用中文简短回复连通性测试。", prompt="只回复：连接成功。",
            ))
            if not simulation.llm.calls[-1]["success"]:
                raise HTTPException(status_code=503, detail="DeepSeek request failed")
            return {"status": "ok", "response": answer}

        @api.get("/debug/games/{game_id}/llm-usage")
        def llm_usage(game_id: UUID) -> dict:
            with Session(database) as session:
                game_in(session, game_id)
                rows = session.scalars(select(LLMCall).where(LLMCall.game_id == game_id)).all()
            per_character: dict[str, int] = {}
            per_task: dict[str, int] = {}
            for row in rows:
                character = str(row.character_id) if row.character_id else "player_ai"
                per_character[character] = per_character.get(character, 0) + 1
                per_task[row.task] = per_task.get(row.task, 0) + 1
            return {"total_calls": len(rows), "prompt_tokens": sum(row.prompt_tokens for row in rows),
                    "completion_tokens": sum(row.completion_tokens for row in rows),
                    "average_latency_ms": round(sum(row.latency_ms for row in rows) / len(rows)) if rows else 0,
                    "retries": sum(row.retries for row in rows),
                    "fallbacks": sum(row.fallback for row in rows),
                    "calls_per_character": per_character, "calls_per_task": per_task}

        @api.get("/debug/games/{game_id}")
        def debug_game(game_id: UUID) -> dict:
            with Session(database) as session:
                return DebugWorldView(session).snapshot(game_id)

        @api.get("/debug/games/{game_id}/scores")
        def debug_scores(game_id: UUID, formula_version: Literal["0.1", "0.2"] = "0.2") -> dict | None:
            return scoring.latest(game_id, formula_version)

        @api.post("/debug/games/{game_id}/scores")
        def calculate_scores(game_id: UUID, formula_version: Literal["0.1", "0.2"] = "0.2") -> dict:
            return scoring.snapshot(game_id, formula_version)

    return api


app = create_app()

