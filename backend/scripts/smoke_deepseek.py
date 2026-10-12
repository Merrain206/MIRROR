"""One paid connectivity request through FastAPI and the configured provider."""

from fastapi.testclient import TestClient

from app.main import create_app


def main() -> None:
    client = TestClient(create_app(debug=True, playtest=False))
    response = client.post("/debug/llm/smoke")
    response.raise_for_status()
    result = response.json()
    print(f"DeepSeek smoke: {result['status']} · {result['response']}")


if __name__ == "__main__":
    main()
