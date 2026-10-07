"""Which errors are the model provider's (a slow answer, a dropped connection, rate limiting, a 5xx) rather than the agent's."""

PROVIDER_ERRORS = ("timeout", "timed out", "connecterror", "connectionerror", "apiconnectionerror", "readerror", "remoteprotocol",
                   "ratelimit", "rate limit", "internalservererror", "serviceunavailable", "badgateway", "overloaded",
                   "status_code=429", "status_code=500", "status_code=502", "status_code=503", "status_code=504")


def is_provider_error(error: str | None) -> bool:
    """True for a timeout, a dropped connection, rate limiting or a 5xx from the model provider."""
    low = (error or "").lower()
    return any(m in low for m in PROVIDER_ERRORS)
