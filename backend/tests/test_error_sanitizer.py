"""Regression test for a live-found leak: a Gemini 429 propagated its full
request URL, including ?key=<the actual API key>, into state.errors, which
GET /agent/{id} returns verbatim to any client.
"""

from backend.security.error_sanitizer import sanitize_error


def test_strips_query_string_containing_api_key():
    exc = ValueError(
        "Client error '429 Too Many Requests' for url "
        "'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:"
        "generateContent?key=AIzaSyFAKEKEYFORTESTINGONLY1234567'"
    )
    result = sanitize_error(exc)
    assert "AIzaSy" not in result
    assert "key=" not in result
    assert "429 Too Many Requests" in result  # the useful part survives


def test_message_with_no_query_string_is_unchanged_besides_type_prefix():
    exc = ValueError("plain message, no url at all")
    assert sanitize_error(exc) == "ValueError: plain message, no url at all"


def test_includes_exception_type_name():
    exc = RuntimeError("boom")
    assert sanitize_error(exc).startswith("RuntimeError: ")
