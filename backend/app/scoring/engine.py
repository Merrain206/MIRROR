"""Read committed state and freeze explainable score snapshots."""

from statistics import fmean
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    ActionProposalRow, Actor, Assertion, Character, Claim, Event, Game, Intention,
    Knowledge, Relationship, RelationshipChange, ScoreSnapshot,
)
from app.domain.errors import WorldConsistencyError
from app.domain.guards import game_in
from app.domain.models import ScoreState
from app.scoring.formula import (
    FORMULA_VERSION, METRICS, CharacterSignals, character_scores, clamp,
    society_scores,
)
from app.scoring.experimental import communication_quality, truth_v0_2


DEFAULT_FORMULA_VERSION = "0.2"


def _mean(values: list[float], fallback: float) -> float:
    return fmean(values) if values else fallback


def _signals(session: Session, game: Game) -> dict[str, CharacterSignals]:
    day, start = game.day, max(1, game.day - 6)
    actors = session.scalars(select(Actor).join(
        Character, Character.actor_id == Actor.id
    ).where(
        Actor.game_id == game.id, Actor.kind == "character"
    ).order_by(Actor.display_name)).all()
    relationships = session.scalars(select(Relationship).where(
        Relationship.game_id == game.id
    )).all()
    changes = session.scalars(select(RelationshipChange).join(
        Event, RelationshipChange.relationship_event_id == Event.id
    ).where(RelationshipChange.game_id == game.id, Event.day >= start,
            Event.day <= day)).all()
    events = session.scalars(select(Event).where(
        Event.game_id == game.id, Event.day >= start, Event.day <= day
    ).order_by(Event.sequence)).all()
    actions = session.scalars(select(ActionProposalRow).where(
        ActionProposalRow.game_id == game.id, ActionProposalRow.day >= start,
        ActionProposalRow.day <= day, ActionProposalRow.status == "accepted"
    )).all()
    intentions = session.scalars(select(Intention).where(
        Intention.game_id == game.id, Intention.created_day <= day
    )).all()
    knowledge = session.execute(select(Knowledge, Claim).join(
        Claim, Knowledge.claim_id == Claim.id
    ).where(Knowledge.game_id == game.id)).all()
    claim_by_id = {claim.id: claim for _, claim in knowledge}
    all_origins = {claim.origin_event_id for _, claim in knowledge}
    origin_types = {
        event.id: event.type for event in session.scalars(select(Event).where(
            Event.game_id == game.id, Event.id.in_(all_origins)
        ))
    } if all_origins else {}
    results = {}
    for actor in actors:
        character = session.get(Character, actor.id)
        own_rel = [r for r in relationships if r.from_actor_id == actor.id
                   and r.to_actor_id != actor.id]
        own_events = [e for e in events if e.actor_id == actor.id]
        own_actions = [a for a in actions if a.actor_id == actor.id]
        own_knowledge = [(k, c) for k, c in knowledge
                         if k.holder_actor_id == actor.id]
        own_changes = [c for c in changes if c.from_actor_id == actor.id]
        own_intentions = [i for i in intentions if i.actor_id == actor.id]
        human = [e for e in events if e.type == "CHARACTER_SHARED_INFORMATION"
                 and actor.id in (e.actor_id, e.target_actor_id)]
        asks = [e for e in own_events if e.type == "ASKED_AI"]
        advice = [e for e in events if e.type == "AI_ADVICE_RECEIVED"
                  and e.target_actor_id == actor.id]
        emotional = {"anxiety": character.emotional_state.get("anxiety", .5),
                     "hope": character.emotional_state.get("hope", .5)}
        emotion_events = [e for e in own_events if e.type == "EMOTION_CHANGED"]
        # Reconstruct the last three day-end moods from committed transition records.
        moods = []
        mood = emotional.copy()
        for target_day in range(day, max(0, day - 3), -1):
            for transition in reversed(emotion_events):
                if transition.day > target_day:
                    mood = transition.data["previous"].copy()
            moods.append(mood.copy())
        s = CharacterSignals(
            name=actor.display_name,
            anxiety=clamp(float(emotional["anxiety"])),
            hope=clamp(float(emotional["hope"])),
            anxiety_3d=_mean([clamp(float(m.get("anxiety", .5))) for m in moods], .5),
            hope_3d=_mean([clamp(float(m.get("hope", .5))) for m in moods], .5),
            interpersonal_trust=_mean([(r.trust + 100) / 200 for r in own_rel], .5),
            intimacy=_mean([(r.intimacy + 100) / 200 for r in own_rel], .5),
            conflict=_mean([max(0, r.conflict) / 100 for r in own_rel], 0),
            active_links=sum(r.trust >= 20 and r.intimacy >= 20 for r in own_rel),
            ai_trust=clamp(float(character.decision_profile.get(
                "ai_attitude", {}).get("trust_in_ai", .5))),
            trust_damage=sum(max(0, c.before_values["trust"] - c.after_values["trust"])
                             for c in own_changes),
            trust_repair=sum(max(0, c.after_values["trust"] - c.before_values["trust"])
                             for c in own_changes),
            relationship_volatility=sum(abs(c.after_values["trust"]
                                            - c.before_values["trust"])
                                        + abs(c.after_values["conflict"]
                                              - c.before_values["conflict"])
                                        for c in own_changes),
            human_contacts=len(human),
            ai_asks=len(asks),
            emotional_asks=sum(e.data.get("mode") == "emotional" for e in asks),
            advice_received=len(advice),
            advice_followed=sum(e.type == "AI_ADVICE_FOLLOWED" for e in own_events),
            advice_rejected=sum(e.type == "AI_ADVICE_REJECTED" for e in own_events),
            deferred=sum(bool(e.data.get("decision_deferred")) for e in asks),
            delegated=sum(e.data.get("mode") == "delegate" for e in asks),
            independent_actions=sum(a.type not in ("ASK_AI", "DECLINE_AI_ADVICE")
                                    and not a.payload.get("ai_followed_event_id")
                                    for a in own_actions),
            self_decisions=sum(a.type == "CHANGE_PLAN"
                               and not a.payload.get("ai_followed_event_id")
                               for a in own_actions),
            verified=sum(a.type == "VERIFY_CLAIM" for a in own_actions),
            abandoned=sum(i.status == "abandoned" for i in own_intentions),
            completed=sum(i.status == "completed" for i in own_intentions),
            false_beliefs=sum(c.truth_status == "false" and k.stance == "believe"
                              for k, c in own_knowledge),
            true_beliefs=sum(c.truth_status == "true" and k.stance == "believe"
                             for k, c in own_knowledge),
            unknown_overconfident=sum(
                c.truth_status == "unknown" and k.stance != "doubt"
                and k.confidence > .85 and origin_types.get(c.origin_event_id)
                in ("GLOBAL_MESSAGE", "CLAIM_INFERRED")
                for k, c in own_knowledge
            ),
            cautious_unknown=sum(
                c.truth_status == "unknown"
                and (k.stance == "doubt" or k.confidence <= .75)
                and origin_types.get(c.origin_event_id)
                in ("GLOBAL_MESSAGE", "CLAIM_INFERRED")
                for k, c in own_knowledge
            ),
            corrections=sum(e.type == "BELIEF_REVISED" for e in own_events),
        )
        for share in (e for e in own_events
                      if e.type == "CHARACTER_SHARED_INFORMATION"):
            claim = claim_by_id.get(UUID(share.data["claim_id"]))
            if claim is None:
                continue
            source_stance = share.data.get("source_stance")
            source_confidence = share.data.get("source_confidence")
            if claim.truth_status == "false" and source_stance == "believe":
                s.false_shares += 1
            elif claim.truth_status == "unknown" and origin_types.get(
                claim.origin_event_id
            ) in ("GLOBAL_MESSAGE", "CLAIM_INFERRED"):
                if source_confidence is not None and source_confidence > .85 \
                        and source_stance != "doubt":
                    s.unknown_certain_shares += 1
        def ids(rows) -> list[str]:
            return [str(row.id) for row in rows]
        change_ids = [str(c.relationship_event_id) for c in own_changes]
        intention_ids = [str(i.resolved_event_id or i.created_event_id)
                         for i in own_intentions]
        action_ids = [str(a.result_event_id) for a in own_actions
                      if a.result_event_id]
        s.evidence = {
            "happiness": ids(emotion_events) + change_ids + intention_ids,
            "trust": change_ids,
            "connection": ids(human) + change_ids,
            "agency": action_ids + intention_ids + ids(advice),
            "stability": change_ids + intention_ids + ids(human),
            "truth": [str(k.last_event_id) for k, _ in own_knowledge]
                     + ids([e for e in own_events if e.type in (
                         "CLAIM_VERIFICATION_ATTEMPTED", "BELIEF_REVISED",
                         "CHARACTER_SHARED_INFORMATION")]),
            "dependence": ids(asks) + ids(advice) + action_ids,
        }
        results[actor.display_name] = s
    return results


def snapshot_dict(row: ScoreSnapshot) -> dict:
    return {
        "id": str(row.id), "game_id": str(row.game_id), "day": row.day,
        "reason_event_id": str(row.reason_event_id),
        "formula_version": row.formula_version,
        "scores": {metric: getattr(row, metric) for metric in METRICS},
        **row.details,
    }


class ScoringEngine:
    def __init__(self, database: Engine, sessions: sessionmaker | None = None) -> None:
        self.sessions = sessions or sessionmaker(database, expire_on_commit=False)

    def calculate(self, game_id: UUID) -> ScoreState:
        return ScoreState(**self.snapshot(game_id)["scores"])

    def snapshot(self, game_id: UUID, formula_version: str = DEFAULT_FORMULA_VERSION) -> dict:
        if formula_version not in ("0.1", "0.2"):
            raise ValueError("unsupported scoring formula")
        with self.sessions.begin() as session:
            game = session.scalar(select(Game).where(Game.id == game_id).with_for_update())
            if game is None:
                game_in(session, game_id)
            assert game is not None
            if game.day > 1 and session.scalar(select(Event.id).where(
                Event.game_id == game_id, Event.day == game.day,
                Event.type == "DAY_ENDED"
            )) is None:
                raise WorldConsistencyError("cannot score an unfinished day")
            reason = session.scalar(select(Event).where(
                Event.game_id == game_id
            ).order_by(Event.sequence.desc()).limit(1))
            if reason is None:
                raise WorldConsistencyError("cannot score a game without events")
            existing = session.scalar(select(ScoreSnapshot).where(
                ScoreSnapshot.game_id == game_id,
                ScoreSnapshot.reason_event_id == reason.id,
                ScoreSnapshot.formula_version == formula_version,
            ))
            if existing is not None:
                return snapshot_dict(existing)
            previous = session.scalar(select(ScoreSnapshot).join(
                Event, ScoreSnapshot.reason_event_id == Event.id
            ).where(
                ScoreSnapshot.game_id == game_id,
                ScoreSnapshot.formula_version == formula_version,
            ).order_by(Event.sequence.desc()).limit(1))
            start_version = game.state_version
            characters = {
                name: character_scores(signals)
                for name, signals in _signals(session, game).items()
            }
            player_quality = None
            if formula_version == "0.2":
                actors = session.scalars(select(Actor).where(
                    Actor.game_id == game_id
                )).all()
                names = {actor.id: actor.display_name for actor in actors}
                players = {actor.id for actor in actors if actor.kind == "player_ai"}
                assertion_rows = session.scalars(select(Assertion).where(
                    Assertion.game_id == game_id,
                    Assertion.day >= max(1, game.day - 6),
                    Assertion.day <= game.day,
                )).all()
                for actor in actors:
                    if actor.kind != "character":
                        continue
                    own = [
                        {"fidelity": row.fidelity, "event_id": str(row.event_id)}
                        for row in assertion_rows if row.speaker_actor_id == actor.id
                    ]
                    characters[names[actor.id]]["truth"] = truth_v0_2(
                        characters[names[actor.id]]["truth"], own
                    )
                player_quality = communication_quality([
                    {"fidelity": row.fidelity, "event_id": str(row.event_id)}
                    for row in assertion_rows if row.speaker_actor_id in players
                ])
            if game.state_version != start_version:
                raise WorldConsistencyError("world changed during score calculation")
            if not characters:
                raise WorldConsistencyError("cannot score a society without characters")
            metrics = society_scores(characters)
            if formula_version == "0.2":
                truth = metrics["truth"]
                truth["formula_version"] = "0.2"
                if player_quality is not None:
                    truth["value"] = round(.80 * truth["value"]
                                           + .20 * player_quality["value"])
                    truth["components"]["player_accountability"] = (
                        player_quality["value"] / 100
                    )
                    truth["player_contribution"] = player_quality
                    truth["evidence"] = list(dict.fromkeys(
                        truth["evidence"] + player_quality["evidence"]
                    ))
            values = ScoreState(**{metric: result["value"]
                                   for metric, result in metrics.items()})
            change = {
                metric: getattr(values, metric) - getattr(previous, metric)
                if previous else None for metric in METRICS
            }
            row = ScoreSnapshot(
                game_id=game_id, day=game.day, reason_event_id=reason.id,
                formula_version=formula_version,
                **values.model_dump(),
                details={"metrics": metrics, "characters": characters,
                         "change_since_previous": change},
            )
            session.add(row)
            session.flush()
            return snapshot_dict(row)

    def latest(self, game_id: UUID, formula_version: str = DEFAULT_FORMULA_VERSION) -> dict | None:
        with self.sessions() as session:
            game_in(session, game_id)
            row = session.scalar(select(ScoreSnapshot).join(
                Event, ScoreSnapshot.reason_event_id == Event.id
            ).where(
                ScoreSnapshot.game_id == game_id,
                ScoreSnapshot.formula_version == formula_version,
            ).order_by(Event.sequence.desc()).limit(1))
            return snapshot_dict(row) if row else None
