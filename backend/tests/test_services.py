import asyncio
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.domain.models import ActionProposal
from app.llm.mock import MockLLMProvider
from app.llm.provider import StructuredRequest, TextRequest
from app.main import create_app


def test_health_checks() -> None:
    client = TestClient(create_app(create_engine("sqlite+pysqlite:///:memory:")))
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/health/database").json() == {"status": "ok"}


def test_mock_llm_returns_fixed_text_and_structured_action() -> None:
    actor_id, target_id, claim_id = uuid4(), uuid4(), uuid4()
    mock = MockLLMProvider(
        text_responses=["我会考虑告诉 Bob。"],
        structured_responses=[
            {
                "type": "SHARE_INFORMATION",
                "actor_id": actor_id,
                "target_id": target_id,
                "claim_id": claim_id,
                "reason": "Bob 应该知道",
                "confidence": 0.8,
            }
        ],
    )

    async def exercise() -> None:
        assert await mock.generate(TextRequest(system="role", prompt="hello")) == "我会考虑告诉 Bob。"
        action = await mock.generate_structured(
            StructuredRequest(
                system="role", prompt="next action", response_model=ActionProposal
            ),
            ActionProposal,
        )
        assert action.actor_id == actor_id
        assert action.target_id == target_id

    asyncio.run(exercise())

