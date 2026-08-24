from backend.security.app_policy import is_app_allowed


def test_no_allowlist_configured_allows_everything(monkeypatch):
    monkeypatch.delenv("ALLOWED_APPS", raising=False)
    assert is_app_allowed("youtube") is True
    assert is_app_allowed("some_random_app") is True


def test_empty_allowlist_env_var_allows_everything(monkeypatch):
    monkeypatch.setenv("ALLOWED_APPS", "")
    assert is_app_allowed("youtube") is True


def test_allowlist_permits_listed_apps(monkeypatch):
    monkeypatch.setenv("ALLOWED_APPS", "youtube, gmail")
    assert is_app_allowed("youtube") is True
    assert is_app_allowed("gmail") is True


def test_allowlist_blocks_unlisted_apps(monkeypatch):
    monkeypatch.setenv("ALLOWED_APPS", "youtube, gmail")
    assert is_app_allowed("whatsapp") is False


def test_allowlist_is_case_and_whitespace_insensitive(monkeypatch):
    monkeypatch.setenv("ALLOWED_APPS", "YouTube")
    assert is_app_allowed("  youtube  ") is True
