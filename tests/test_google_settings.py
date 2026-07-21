from config.settings import Settings


def test_token_paths_and_labels_are_parsed(monkeypatch):
    monkeypatch.setenv("GOOGLE_TOKEN_PATHS", " /one.json, ,/two.json ")
    monkeypatch.setenv("GOOGLE_ACCOUNT_LABELS", "Pribadi,Kerja")

    current = Settings()

    assert current.google_token_paths == ["/one.json", "/two.json"]
    assert current.google_account_labels == ["Pribadi", "Kerja"]


def test_singular_token_path_is_backward_compatible(monkeypatch):
    monkeypatch.delenv("GOOGLE_TOKEN_PATHS", raising=False)
    monkeypatch.setenv("GOOGLE_TOKEN_PATH", "/legacy.json")
    monkeypatch.delenv("GOOGLE_ACCOUNT_LABELS", raising=False)

    current = Settings()

    assert current.google_token_paths == ["/legacy.json"]
    assert current.google_account_labels == ["Akun 1"]


def test_missing_labels_get_account_numbers(monkeypatch):
    monkeypatch.setenv("GOOGLE_TOKEN_PATHS", "/one.json,/two.json")
    monkeypatch.setenv("GOOGLE_ACCOUNT_LABELS", "Pribadi")

    current = Settings()

    assert current.google_account_labels == ["Pribadi", "Akun 2"]
