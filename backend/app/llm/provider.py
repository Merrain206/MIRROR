from collections.abc import AsyncIterator
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T", bound=BaseModel)


class TextRequest(BaseModel):
    system: str
    prompt: str


class StructuredRequest(BaseModel):
    system: str
    prompt: str
    response_model: type[BaseModel]
    context: dict[str, Any] = Field(default_factory=dict)


class LLMProvider(Protocol):
    async def generate(self, request: TextRequest) -> str: ...

    async def generate_structured(
        self, request: StructuredRequest, schema: type[T]
    ) -> T: ...

    def stream(self, request: TextRequest) -> AsyncIterator[str]: ...

