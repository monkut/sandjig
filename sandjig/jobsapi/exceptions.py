class QueryParamError(ValueError):
    """Exception raised for errors Query Parameter processing"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
