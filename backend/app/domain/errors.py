class DomainError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class NotFound(DomainError):
    def __init__(self, entity: str) -> None:
        super().__init__("not_found", f"{entity} was not found")


class WorldConsistencyError(DomainError):
    def __init__(self, message: str) -> None:
        super().__init__("world_consistency_error", message)


class ActionRejected(DomainError):
    def __init__(self, message: str) -> None:
        super().__init__("action_rejected", message)

