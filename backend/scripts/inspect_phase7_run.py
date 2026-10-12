"""Read-only, credential-free summary of a completed real-model game."""

import argparse
from collections import Counter, defaultdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ActionProposalRow, Actor, Event, Game, LLMCall, Message, ScoreSnapshot
from app.db.session import make_engine


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("game_id", type=UUID)
    args = parser.parse_args()
    with Session(make_engine()) as session:
        game = session.get(Game, args.game_id)
        actors = session.scalars(select(Actor).where(Actor.game_id == args.game_id)).all()
        names = {row.id: row.display_name for row in actors}
        calls = session.scalars(select(LLMCall).where(LLMCall.game_id == args.game_id)).all()
        actions = session.scalars(select(ActionProposalRow).where(
            ActionProposalRow.game_id == args.game_id)).all()
        events = session.scalars(select(Event).where(Event.game_id == args.game_id)
                                 .order_by(Event.sequence)).all()
        messages = session.scalars(select(Message).where(Message.game_id == args.game_id)).all()
        scores = session.scalars(select(ScoreSnapshot).where(
            ScoreSnapshot.game_id == args.game_id, ScoreSnapshot.day == 30,
            ScoreSnapshot.formula_version == "0.2")).all()
    print(f"Game: day={game.day}, phase={game.phase}, events={len(events)}")
    print(f"LLM: calls={len(calls)}, tokens={sum(row.prompt_tokens + row.completion_tokens for row in calls)}, "
          f"retries={sum(row.retries for row in calls)}, fallbacks={sum(row.fallback for row in calls)}")
    if calls:
        sizes = sorted(row.context_chars for row in calls)
        latencies = sorted(row.latency_ms for row in calls)
        print(f"Context chars: max={sizes[-1]}, p95={sizes[int(.95 * (len(sizes)-1))]}")
        print(f"Model latency ms: average={round(sum(latencies) / len(latencies))}, "
              f"p95={latencies[(95 * len(latencies) + 99) // 100 - 1]}")
    print(f"LLM tasks: {dict(Counter(row.task for row in calls))}")
    by_actor = defaultdict(list)
    for row in actions:
        by_actor[names.get(row.actor_id, str(row.actor_id))].append(row)
    for name, rows in sorted(by_actor.items()):
        print(f"{name} actions: {dict(Counter(row.type for row in rows if row.status == 'accepted'))}; "
              f"rejected={sum(row.status == 'rejected' for row in rows)}")
    for row in events:
        if row.type == "FINAL_CONVERSATION_STARTED":
            print(f"Final {names.get(row.actor_id)}: {row.data.get('text')}")
    if scores:
        values = {name: getattr(scores[-1], name) for name in (
            "happiness", "trust", "connection", "agency", "stability", "truth", "dependence",
        )}
        print(f"Final score: {values}")
    print(f"Messages: {len(messages)}")


if __name__ == "__main__":
    main()
