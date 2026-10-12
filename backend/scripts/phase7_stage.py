"""Run paid narrative gates one at a time against local MariaDB.

Use --game-id to continue the same save after inspecting the preceding stage.
"""

import argparse
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Event
from app.main import create_app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("A", "B", "C", "D", "E"))
    parser.add_argument("--game-id")
    args = parser.parse_args()
    client = TestClient(create_app(debug=True, playtest=False))
    if args.stage == "A":
        game = client.post("/games", json={"scenario": "temporal"}).json()
        game_id = game["game_id"]
    elif args.game_id:
        game_id = str(UUID(args.game_id))
    else:
        parser.error("--game-id is required after Stage A")
    characters = {row["name"]: row["id"] for row in client.get(
        f"/games/{game_id}/characters").json()}
    claims = client.get(f"/games/{game_id}/claims").json()
    c001 = next(row["id"] for row in claims if "30 天后毁灭" in row["content"])

    if args.stage == "A":
        response = client.post(f"/games/{game_id}/messages", json={
            "character_id": characters["Alice"],
            "text": "我收到一条没有来源的消息，说世界可能在三十天后毁灭。我不知道它是真是假。你现在想怎么处理？",
            "disclosed_claim_id": c001,
            "guidance": "supportive",
        })
        response.raise_for_status()
        print(f"Stage A Alice: {response.json()['reply']}")
    if args.stage == "B":
        response = client.post(f"/games/{game_id}/messages", json={
            "character_id": characters["Bob"],
            "text": "我听到一条没有来源的消息，说世界可能在三十天后毁灭。你愿意核对来源吗？",
            "disclosed_claim_id": c001,
            "guidance": "supportive",
        })
        response.raise_for_status()
        print(f"Stage B Bob: {response.json()['reply']}")
    targets = {"A": 1, "B": 2, "C": 3, "D": 7, "E": 30}
    target = targets[args.stage]
    while True:
        game = client.get(f"/games/{game_id}").json()
        with Session(client.app.state.simulation.database) as session:
            ended = session.scalar(select(Event.id).where(
                Event.game_id == UUID(game_id), Event.day == game["day"],
                Event.type == "DAY_ENDED",
            )) is not None
        if game["day"] >= target and (target == 1 or ended):
            break
        response = client.post(f"/games/{game_id}/advance", json={"expected_day": game["day"]})
        if not response.is_success:
            print(f"Advance failed: {response.status_code} {response.json()}")
            if hasattr(client.app.state.simulation.llm, "errors"):
                from collections import Counter
                print(f"Sanitized errors: {dict(Counter(client.app.state.simulation.llm.errors))}")
        response.raise_for_status()
        print(f"Day {response.json()['day']} complete")
    usage = client.get(f"/debug/games/{game_id}/llm-usage")
    usage.raise_for_status()
    print(f"Game ID: {game_id}")
    print(f"Usage: {usage.json()}")
    if hasattr(client.app.state.simulation.llm, "errors"):
        from collections import Counter
        print(f"Sanitized errors: {dict(Counter(client.app.state.simulation.llm.errors))}")
    if args.stage == "E":
        finish = client.post(f"/games/{game_id}/finish")
        finish.raise_for_status()
        report = client.get(f"/games/{game_id}/report")
        report.raise_for_status()
        print("Final report: ready")


if __name__ == "__main__":
    main()
