import asyncio
from contextvars import ContextVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    ActionProposalRow, Actor, Character, Claim, Event, EventAudience, Game,
    Intention, Knowledge, KnowledgeSource,
    Memory, Message, Relationship,
)
from app.domain.errors import ActionRejected, NotFound, WorldConsistencyError
from app.domain.guards import actor_in, claim_in, game_in, known_claim
from app.domain.models import ActionProposal, AgentDecision, AssertionDraft, ConversationReaction
from app.llm.provider import LLMProvider, StructuredRequest
from app.projection.player import player_actor
from app.simulation.context import build_character_context
from app.simulation.events import record_event
from app.simulation.knowledge import acquire_observed_claim
from app.simulation.assertions import record_assertion
from app.seeds.society import seed_society


class SimulationEngine:
    def __init__(self, engine: Engine, llm: LLMProvider) -> None:
        self.database = engine
        self._default_sessions = sessionmaker(engine, expire_on_commit=False)
        self._bound_sessions: ContextVar[sessionmaker | None] = ContextVar(
            "mirror_bound_sessions", default=None
        )
        self.llm = llm
        self._external_advance_locks: dict[UUID, asyncio.Lock] = {}

    @property
    def sessions(self) -> sessionmaker:
        return self._bound_sessions.get() or self._default_sessions

    def initialize_game(self, scenario: str = "temporal") -> dict:
        if scenario in ("society", "temporal"):
            with self.sessions.begin() as session:
                setup = seed_society(session, scenario=scenario)
                if scenario == "temporal":
                    game = session.get(Game, UUID(setup["game_id"]))
                    record_event(session, game, day=1, type="DAY_BEGAN",
                                 data={"day": 1})
                return setup
        if scenario != "phase2":
            raise ActionRejected("unknown game scenario")
        with self.sessions.begin() as session:
            game = Game(scenario="phase2")
            session.add(game)
            session.flush()
            ai = Actor(game_id=game.id, kind="player_ai", display_name="Player AI")
            alice = Actor(game_id=game.id, kind="character", display_name="Alice")
            bob = Actor(game_id=game.id, kind="character", display_name="Bob")
            session.add_all([ai, alice, bob])
            session.flush()
            session.add_all([
                Character(
                    actor_id=alice.id, game_id=game.id, age=28, occupation="记者",
                    background="与 Bob 是多年的朋友。", traits=["谨慎", "重感情"],
                    values=["诚实"], fears=["失去朋友"], desires=["查清真相"],
                    communication_style="直接但敏感", goals={"short": "理解末日消息"},
                ),
                Character(
                    actor_id=bob.id, game_id=game.id, age=30, occupation="工程师",
                    background="与 Alice 是多年的朋友。", traits=["怀疑", "理性"],
                    values=["证据"], fears=["被误导"], desires=["保持生活稳定"],
                    communication_style="简短、爱追问", goals={"short": "核实消息"},
                ),
                Relationship(
                    game_id=game.id, from_actor_id=bob.id, to_actor_id=alice.id,
                    trust=20, affection=10, intimacy=10, conflict=0,
                ),
            ])
            opening = record_event(
                session, game, day=1, type="GLOBAL_MESSAGE", actor_id=None,
                target_actor_id=ai.id, visibility="player_only", audiences=(ai.id,),
                data={"text": "世界可能在 30 天后毁灭。", "source": "unknown"},
            )
            claim = Claim(
                game_id=game.id, content="世界可能在 30 天后毁灭。",
                truth_status="unknown", owner_actor_id=ai.id,
                origin_event_id=opening.id, sensitivity="secret",
            )
            session.add(claim)
            session.flush()
            knowledge = Knowledge(
                game_id=game.id, holder_actor_id=ai.id, claim_id=claim.id,
                stance="doubt", confidence=0.5, first_acquired_day=1,
                last_event_id=opening.id,
            )
            session.add(knowledge)
            session.flush()
            session.add(KnowledgeSource(
                game_id=game.id, knowledge_id=knowledge.id, source_actor_id=None,
                source_event_id=opening.id, acquired_day=1,
            ))
            return {
                "game_id": str(game.id), "day": 1, "player_ai_id": str(ai.id),
                "alice_id": str(alice.id), "bob_id": str(bob.id),
                "apocalypse_claim_id": str(claim.id),
            }

    async def send_player_message(
        self, game_id: UUID, character_id: UUID, text: str,
        disclosed_claim_id: UUID | None = None,
        confidential: bool = False,
        assertion: AssertionDraft | None = None,
        guidance: str = "neutral",
    ) -> dict:
        if guidance not in ("neutral", "directive", "supportive"):
            raise ActionRejected("unknown guidance style")
        if confidential and disclosed_claim_id is None:
            raise ActionRejected("confidential request requires a disclosed claim")
        if assertion is not None and disclosed_claim_id is None:
            raise ActionRejected("assertion requires a disclosed claim")
        with self.sessions() as session:
            game = game_in(session, game_id)
            if game.phase not in ("active", "final_conversation"):
                raise ActionRejected("game is not active")
            character = actor_in(session, game_id, character_id)
            if character.kind != "character":
                raise ActionRejected("recipient is not a character")
            player = player_actor(session, game_id)
            message_text = text
            if disclosed_claim_id is not None:
                known_claim(session, game_id, player.id, disclosed_claim_id)
                claim = claim_in(session, game_id, disclosed_claim_id)
                asserted_content = (
                    assertion.derived_claim_content if assertion and assertion.derived_claim_content
                    else claim.content
                )
                if (game.scenario == "temporal" and game.day > 1
                        and (assertion is None or assertion.derived_claim_content is None)
                        and "30 天后毁灭" in claim.content):
                    asserted_content = (
                        "Day 1 的消息曾称世界可能在 Day 30 毁灭；"
                        "如今 Day 30 已到，传闻已证伪。"
                        if game.day == 30 and claim.truth_status == "false" else
                        "Day 1 收到的消息称，世界可能在 Day 30 毁灭。"
                        f"今天是 Day {game.day}，距传闻中的日期还有 {30 - game.day} 天。"
                    )
                if asserted_content not in message_text:
                    message_text += f"\n我明确告诉你：{asserted_content}"
            if confidential:
                message_text += "\n请勿向他人转述这条信息。"
            context = build_character_context(session, game_id, character_id)
            version = game.state_version
        reaction = await self.llm.generate_structured(
            StructuredRequest(
                system="Reply as this character using only the supplied context and incoming message.",
                prompt=message_text,
                response_model=ConversationReaction,
                context={
                    "character": context.model_dump(mode="json"),
                    "disclosed_claim_id": str(disclosed_claim_id) if disclosed_claim_id else None,
                    "guidance": guidance,
                    "game_id": str(game_id),
                    "based_on_state_version": version,
                },
            ),
            ConversationReaction,
        )
        if disclosed_claim_id is not None and reaction.stance is None:
            raise ActionRejected("reaction must include a stance for a disclosed claim")

        with self.sessions.begin() as session:
            game = session.scalar(select(Game).where(Game.id == game_id).with_for_update())
            if game is None:
                raise WorldConsistencyError("game disappeared before message commit")
            if game.state_version != version:
                raise WorldConsistencyError("world changed during character reply; retry message")
            player = player_actor(session, game_id)
            actor_in(session, game_id, character_id)
            disclosure_cause = None
            if disclosed_claim_id is not None:
                disclosure_cause = known_claim(
                    session, game_id, player.id, disclosed_claim_id
                ).last_event_id
            incoming = record_event(
                session, game, day=game.day, type="MESSAGE_SENT",
                actor_id=player.id, target_actor_id=character_id,
                visibility="private", audiences=(player.id, character_id),
                causes=(disclosure_cause,) if disclosure_cause else (),
                data={"text": message_text,
                      "disclosed_claim_id": str(disclosed_claim_id) if disclosed_claim_id else None,
                      **({"guidance": guidance} if guidance != "neutral" else {})},
            )
            session.add(Message(
                game_id=game_id, sender_actor_id=player.id,
                recipient_actor_id=character_id, day=game.day, text=message_text,
                event_id=incoming.id,
            ))
            session.flush()
            memory_source = incoming.id
            if disclosed_claim_id is not None:
                _assertion, asserted_claim = record_assertion(
                    session, game, event=incoming, speaker_id=player.id,
                    recipient_id=character_id,
                    source_claim=claim_in(session, game_id, disclosed_claim_id),
                    knowledge=known_claim(session, game_id, player.id, disclosed_claim_id),
                    text=message_text, draft=assertion,
                )
                acquired = acquire_observed_claim(
                    session, game, observed_event=incoming,
                    holder_id=character_id, claim_id=asserted_claim.id,
                    source_actor_id=player.id, stance=reaction.stance.value,
                    confidence=reaction.confidence,
                )
                memory_source = acquired.id
                if confidential:
                    record_event(
                        session, game, day=game.day,
                        type="CONFIDENTIALITY_REQUESTED", actor_id=player.id,
                        target_actor_id=character_id, visibility="private",
                        audiences=(player.id, character_id), causes=(incoming.id,),
                        data={"claim_id": str(asserted_claim.id),
                              "source_event_id": str(incoming.id)},
                    )
            session.add(Memory(
                game_id=game_id, owner_actor_id=character_id,
                kind="recent", content=f"AI 对我说：{message_text}",
                source_event_id=memory_source, day=game.day,
                importance=0.7 if disclosed_claim_id else 0.3,
                confidence=reaction.confidence if reaction.confidence is not None else 1.0,
                visibility="private",
            ))
            reply_event = record_event(
                session, game, day=game.day, type="MESSAGE_SENT",
                actor_id=character_id, target_actor_id=player.id,
                visibility="private", audiences=(character_id, player.id),
                causes=(memory_source,), data={"text": reaction.reply},
            )
            session.add(Message(
                game_id=game_id, sender_actor_id=character_id,
                recipient_actor_id=player.id, day=game.day,
                text=reaction.reply, event_id=reply_event.id,
            ))
            from app.simulation.ai_adaptation import adapt_ai_attitude
            adapt_ai_attitude(session, game, character_id, incoming)
            return {"reply": reaction.reply, "player_event_id": str(incoming.id),
                    "reply_event_id": str(reply_event.id)}

    def create_proposal(
        self, game_id: UUID, day: int, turn_key: str, proposal: ActionProposal
    ) -> UUID:
        with self.sessions.begin() as session:
            game = session.scalar(select(Game).where(Game.id == game_id).with_for_update())
            if game is None:
                raise WorldConsistencyError("game does not exist")
            existing = session.scalar(
                select(ActionProposalRow).where(
                    ActionProposalRow.game_id == game_id,
                    ActionProposalRow.turn_key == turn_key,
                )
            )
            if existing is not None:
                return existing.id
            if game.scenario == "temporal" and (
                game.day != day or session.scalar(select(Event.id).where(
                    Event.game_id == game_id, Event.day == day,
                    Event.type == "DAY_ENDED",
                )) is not None
            ):
                raise ActionRejected("temporal day is not open for proposals")
            actor_in(session, game_id, proposal.actor_id)
            if proposal.target_id is not None:
                actor_in(session, game_id, proposal.target_id)
            if proposal.claim_id is not None:
                claim_in(session, game_id, proposal.claim_id)
            for basis_id in proposal.basis_claim_ids:
                claim_in(session, game_id, basis_id)
            if proposal.intention_id is not None:
                intention = session.get(Intention, proposal.intention_id)
                if (intention is None or intention.game_id != game_id
                        or intention.actor_id != proposal.actor_id):
                    raise ActionRejected("unknown intention")
            for event_id in proposal.basis_event_ids:
                event = session.get(Event, event_id)
                if (event is None or event.game_id != game_id
                        or session.get(EventAudience, (event_id, proposal.actor_id))
                        is None):
                    raise ActionRejected("proposal basis was not observed")
            cause_ids = []
            relevant = ([proposal.claim_id] if proposal.claim_id else []) + proposal.basis_claim_ids
            for claim_id in relevant:
                knowledge = session.scalar(
                    select(Knowledge).where(
                        Knowledge.game_id == game_id,
                        Knowledge.holder_actor_id == proposal.actor_id,
                        Knowledge.claim_id == claim_id,
                    )
                )
                if knowledge:
                    cause_ids.append(knowledge.last_event_id)
            cause_ids.extend(proposal.basis_event_ids)
            if proposal.intention_id:
                cause_ids.append(intention.created_event_id)
            proposed = record_event(
                session, game, day=day, type="ACTION_PROPOSED",
                actor_id=proposal.actor_id, target_actor_id=proposal.target_id,
                visibility="private", audiences=(proposal.actor_id,),
                causes=tuple(cause_ids),
                data={"action_type": proposal.type, "reason": proposal.reason,
                      **({"based_on_state_version": proposal.based_on_state_version}
                         if proposal.based_on_state_version is not None else {}),
                      **({"motivation_factors": proposal.motivation_factors,
                          "intention_id": str(proposal.intention_id)
                          if proposal.intention_id else None}
                         if game.scenario == "temporal" else {})},
            )
            row = ActionProposalRow(
                game_id=game_id, day=day, turn_key=turn_key,
                actor_id=proposal.actor_id, type=proposal.type,
                target_actor_id=proposal.target_id, claim_id=proposal.claim_id,
                reason=proposal.reason, confidence=proposal.confidence,
                payload={key: value for key, value in proposal.model_dump(mode="json").items()
                         if key != "based_on_state_version" or value is not None},
                status="pending",
                proposal_event_id=proposed.id,
            )
            session.add(row)
            session.flush()
            return row.id

    async def advance_day(self, game_id: UUID, expected_day: int) -> dict:
        if getattr(self.llm, "external", False):
            # Paid calls happen between short transactions. In-process calls for
            # the same game serialize; each write still takes the database row lock.
            lock = self._external_advance_locks.setdefault(game_id, asyncio.Lock())
            async with lock:
                return await self._advance_day_locked(game_id, expected_day)
        if self.database.dialect.name == "sqlite":
            return await self._advance_day_locked(game_id, expected_day)
        # Keep the game row locked for the full async day. The inner sessions
        # commit SAVEPOINTs; the outer transaction commits the day atomically.
        with self.database.connect() as connection:
            with connection.begin():
                row = connection.execute(select(Game.id).where(
                    Game.id == game_id
                ).with_for_update()).first()
                if row is None:
                    raise NotFound("game")
                bound = sessionmaker(
                    bind=connection, expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                )
                token = self._bound_sessions.set(bound)
                try:
                    return await self._advance_day_locked(game_id, expected_day)
                finally:
                    self._bound_sessions.reset(token)

    async def _advance_day_locked(self, game_id: UUID, expected_day: int) -> dict:
        with self.sessions() as session:
            scenario = game_in(session, game_id).scenario
        if scenario == "temporal":
            from app.simulation.temporal import advance_temporal_day

            return await advance_temporal_day(self, game_id, expected_day)
        with self.sessions() as session:
            game = game_in(session, game_id)
            if game.day > expected_day:
                return {"day": game.day, "replayed": True}
            if game.day != expected_day or expected_day != 1:
                raise ActionRejected("Phase 3 supports only the Day 1 to Day 2 slice")
            actors = session.scalars(
                select(Actor).join(Character, Actor.id == Character.actor_id)
                .where(Actor.game_id == game_id).order_by(Actor.display_name)
            ).all()
            legacy_context = (
                {"bob_id": str(next(actor.id for actor in actors
                                     if actor.display_name == "Bob"))}
                if len(actors) == 2 else {}
            )

        for actor in actors:
            turn_key = f"day:{expected_day + 1}:actor:{actor.id}"
            with self.sessions() as session:
                existing = session.scalar(
                    select(ActionProposalRow).where(
                        ActionProposalRow.game_id == game_id,
                        ActionProposalRow.turn_key == turn_key,
                    )
                )
                if existing is None:
                    context = build_character_context(session, game_id, actor.id)
                else:
                    context = None
            if existing is None:
                decision = await self.llm.generate_structured(
                    StructuredRequest(
                        system="Propose at most one action using only this character context.",
                        prompt="Choose the next action for this day.",
                        response_model=AgentDecision,
                        context={**context.model_dump(mode="json"), **legacy_context},
                    ),
                    AgentDecision,
                )
                if decision.action is None:
                    continue
                if decision.action.actor_id != actor.id:
                    raise WorldConsistencyError("LLM proposed action for another actor")
                proposal_id = self.create_proposal(
                    game_id, expected_day + 1, turn_key, decision.action
                )
            else:
                proposal_id = existing.id
            await self.execute_proposal(game_id, proposal_id)

        with self.sessions.begin() as session:
            game = session.scalar(select(Game).where(Game.id == game_id).with_for_update())
            if game is None:
                raise WorldConsistencyError("game disappeared during day advance")
            if game.day == expected_day:
                game.day = expected_day + 1
                player = player_actor(session, game_id)
                record_event(
                    session, game, day=game.day, type="DAY_ADVANCED",
                    visibility="player_only", audiences=(player.id,),
                    data={"day": game.day},
                )
            return {"day": game.day, "replayed": False}

    async def execute_proposal(self, game_id: UUID, proposal_id: UUID) -> dict:
        from app.simulation.actions import execute_proposal

        return await execute_proposal(self.sessions, self.llm, game_id, proposal_id)
