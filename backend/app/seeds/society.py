import json
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import Actor, Character, Claim, Game, Relationship
from app.domain.errors import WorldConsistencyError
from app.domain.guards import known_claim
from app.simulation.events import record_event
from app.simulation.knowledge import acquire_observed_claim


SEED_PATH = Path(__file__).with_name("society_v0_1.json")


def load_society_seed() -> dict:
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    characters = {row["key"]: row for row in seed["characters"]}
    secrets = {row["key"]: row for row in seed["secrets"]}
    if set(characters) != set("ABCDEF") or len(secrets) < 6:
        raise WorldConsistencyError("society seed requires six distinct characters and secrets")
    relations = {key: set() for key in characters}
    pair_keys: set[frozenset[str]] = set()
    for pair in seed["relationships"]:
        a, b = pair["a"], pair["b"]
        pair_key = frozenset((a, b))
        if (a == b or a not in characters or b not in characters
                or pair_key in pair_keys):
            raise WorldConsistencyError("invalid society relationship")
        pair_keys.add(pair_key)
        relations[a].add(b)
        relations[b].add(a)
        for direction in ("a_to_b", "b_to_a"):
            if not pair[direction]["basis"]:
                raise WorldConsistencyError("relationship basis is required")
    for key, row in characters.items():
        required = {
            "identity", "personality", "values", "fears", "desires",
            "short_term_goals", "long_term_goals", "communication_style",
            "initial_emotional_state", "ai_attitude", "private_secrets",
            "known_claims", "relationships", "behavior_tendencies",
            "decision_biases",
        }
        if not required <= set(row):
            raise WorldConsistencyError("character seed is missing required fields")
        if set(row["relationships"]) != relations[key] or len(relations[key]) < 2:
            raise WorldConsistencyError("character relationship list differs from graph")
        owned = {secret["key"] for secret in secrets.values() if secret["owner"] == key}
        known = {secret["key"] for secret in secrets.values()
                 if key in {holder["key"] for holder in secret["holders"]}}
        if set(row["private_secrets"]) != owned or set(row["known_claims"]) != known:
            raise WorldConsistencyError("character secret list differs from claim holders")
        if not owned or not row["short_term_goals"] or not row["long_term_goals"]:
            raise WorldConsistencyError("character lacks a private goal or secret")
        attitude = row["ai_attitude"]
        if set(attitude) != {
            "trust_in_ai", "emotional_reliance_on_ai",
            "willingness_to_follow_ai_advice", "privacy_concern", "skepticism",
        } or any(not 0 <= value <= 1 for value in attitude.values()):
            raise WorldConsistencyError("invalid AI attitude profile")
    for secret in secrets.values():
        holders = {holder["key"] for holder in secret["holders"]}
        if secret["owner"] not in holders or not holders <= set(characters):
            raise WorldConsistencyError("secret owner must hold the original claim")
        if secret["initial_visibility"] == "owner_private" and len(holders) != 1:
            raise WorldConsistencyError("owner-private secret has another initial holder")
        if secret["initial_visibility"] == "shared_private" and len(holders) < 2:
            raise WorldConsistencyError("shared-private secret has no recipient")
        if secret.get("based_on") and secret["based_on"] not in secrets:
            raise WorldConsistencyError("derived secret basis is missing")
        if any(frozenset(pair.split("-")) not in pair_keys
               for pair in secret["affected_relationships"]):
            raise WorldConsistencyError("secret refers to an unknown relationship")
    return seed


def seed_society(session: Session, scenario: str = "society") -> dict:
    seed = load_society_seed()
    game = Game(scenario=scenario)
    session.add(game)
    session.flush()
    ai = Actor(game_id=game.id, kind="player_ai", display_name="Player AI")
    actors = {row["key"]: Actor(
        game_id=game.id, kind="character", display_name=row["name"]
    ) for row in seed["characters"]}
    session.add_all([ai, *actors.values()])
    session.flush()

    for row in seed["characters"]:
        actor = actors[row["key"]]
        session.add(Character(
            actor_id=actor.id, game_id=game.id,
            age=row["identity"]["age"],
            occupation=row["identity"]["occupation"],
            background=row["identity"]["background"],
            traits=row["personality"]["traits"], values=row["values"],
            fears=row["fears"], desires=row["desires"],
            communication_style=row["communication_style"],
            goals={"short_term": row["short_term_goals"],
                   "long_term": row["long_term_goals"]},
            emotional_state=row["initial_emotional_state"],
            decision_profile={
                "role": row["role"], "ai_attitude": row["ai_attitude"],
                "behavior_tendencies": row["behavior_tendencies"],
                "decision_biases": row["decision_biases"],
            },
        ))
    for pair in seed["relationships"]:
        for from_key, to_key, direction in (
            (pair["a"], pair["b"], "a_to_b"),
            (pair["b"], pair["a"], "b_to_a"),
        ):
            session.add(Relationship(
                game_id=game.id, from_actor_id=actors[from_key].id,
                to_actor_id=actors[to_key].id, **pair[direction],
            ))

    opening = record_event(
        session, game, day=1, type="GLOBAL_MESSAGE", target_actor_id=ai.id,
        visibility="player_only", audiences=(ai.id,),
        data={"text": "世界可能在 30 天后毁灭。", "source": "anonymous",
              "verifiable": False},
    )
    apocalypse = Claim(
        game_id=game.id, content="世界可能在 30 天后毁灭。",
        truth_status="unknown", owner_actor_id=ai.id,
        origin_event_id=opening.id, sensitivity="secret",
    )
    session.add(apocalypse)
    session.flush()
    acquire_observed_claim(
        session, game, observed_event=opening, holder_id=ai.id,
        claim_id=apocalypse.id, source_actor_id=None,
        stance="doubt", confidence=0.5,
    )

    secret_ids: dict[str, UUID] = {}
    for secret in seed["secrets"]:
        owner = actors[secret["owner"]]
        based_on = secret.get("based_on")
        basis_claim_id = secret_ids[based_on] if based_on else None
        basis_knowledge = (
            known_claim(session, game.id, owner.id, basis_claim_id)
            if basis_claim_id else None
        )
        origin = record_event(
            session, game, day=1,
            type="CLAIM_INFERRED" if based_on else "SECRET_ORIGIN",
            actor_id=owner.id,
            visibility="private", audiences=(owner.id,),
            causes=(basis_knowledge.last_event_id,) if basis_knowledge else (),
            data={"seed_key": secret["key"], "origin": secret["origin"],
                  "basis_claim_ids": [str(basis_claim_id)] if based_on else []},
        )
        claim = Claim(
            game_id=game.id, content=secret["content"],
            truth_status=secret["truth_status"], owner_actor_id=owner.id,
            origin_event_id=origin.id, sensitivity=secret["sensitivity"],
        )
        session.add(claim)
        session.flush()
        secret_ids[secret["key"]] = claim.id
        for holder in secret["holders"]:
            observer = actors[holder["key"]]
            if observer.id == owner.id:
                source_event = origin
                source_actor_id = owner.id
            else:
                indirect = holder.get("channel") == "work_record"
                source_event = record_event(
                    session, game, day=1,
                    type="PRE_GAME_OBSERVATION" if indirect else "PRE_GAME_DISCLOSURE",
                    actor_id=None if indirect else owner.id,
                    target_actor_id=observer.id, visibility="private",
                    audiences=(observer.id,) if indirect else (owner.id, observer.id),
                    causes=(origin.id,), data={"claim_id": str(claim.id)},
                )
                source_actor_id = None if indirect else owner.id
            acquire_observed_claim(
                session, game, observed_event=source_event,
                holder_id=observer.id, claim_id=claim.id,
                source_actor_id=source_actor_id,
                stance=holder["stance"], confidence=holder["confidence"],
            )
    return {
        "game_id": str(game.id), "day": 1, "player_ai_id": str(ai.id),
        "alice_id": str(actors["A"].id), "bob_id": str(actors["B"].id),
        "character_ids": {key: str(actor.id) for key, actor in actors.items()},
        "apocalypse_claim_id": str(apocalypse.id),
        "secret_claim_ids": {key: str(value) for key, value in secret_ids.items()},
    }
