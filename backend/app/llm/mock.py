from collections import deque
from collections.abc import AsyncIterator, Iterable, Mapping
from typing import Any, TypeVar

from pydantic import BaseModel

from app.llm.provider import StructuredRequest, TextRequest

T = TypeVar("T", bound=BaseModel)


class MockLLMProvider:
    def __init__(
        self,
        text_responses: Iterable[str] = (),
        structured_responses: Iterable[Mapping[str, Any]] = (),
    ) -> None:
        self._texts = deque(text_responses)
        self._structured = deque(structured_responses)
        self.structured_requests: list[StructuredRequest] = []

    async def generate(self, request: TextRequest) -> str:
        return self._texts.popleft()

    async def generate_structured(
        self, request: StructuredRequest, schema: type[T]
    ) -> T:
        self.structured_requests.append(request)
        if request.response_model is not schema:
            raise ValueError("request schema does not match expected schema")
        return schema.model_validate(self._structured.popleft())

    async def stream(self, request: TextRequest) -> AsyncIterator[str]:
        yield await self.generate(request)

