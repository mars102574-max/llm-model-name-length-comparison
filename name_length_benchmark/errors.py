"""Language-independent error codes for both humans and automation."""


class BenchmarkError(ValueError):
    def __init__(self, code: str, **context: object) -> None:
        self.code = code
        self.context = context
        super().__init__(code)
