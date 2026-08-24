import re


def sanitize_error(exc: Exception) -> str:
    """Strip query strings out of an exception's text before it's ever
    stored, broadcast, or returned to a client.

    Found live: a Gemini 429 propagated its request URL — including
    ?key=<the actual API key> — straight into state.errors, which
    GET /agent/{id} returns verbatim. Every provider that puts credentials
    in the URL (Gemini does; OpenAI/Anthropic use headers, which exceptions
    don't normally include) leaks them through any unhandled error this way.
    """
    text = f"{type(exc).__name__}: {exc}"
    return re.sub(r"\?[^\s'\"]+", "", text)
