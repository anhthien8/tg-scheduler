import ai_remix


def _env(mp, host="ai.example.com"):
    mp.setenv("CF_ACCESS_HOST", host)
    mp.setenv("CF_ACCESS_CLIENT_ID", "id.access")
    mp.setenv("CF_ACCESS_CLIENT_SECRET", "s3cret")


def test_headers_sent_only_to_configured_host(monkeypatch):
    _env(monkeypatch)
    h = ai_remix._cf_access_headers("https://ai.example.com/v1/chat/completions")
    assert h == {"CF-Access-Client-Id": "id.access", "CF-Access-Client-Secret": "s3cret"}
    assert ai_remix._cf_access_headers("https://api.openai.com/v1/chat/completions") == {}
    assert ai_remix._cf_access_headers("https://ai.example.com.evil.io/v1") == {}


def test_no_headers_when_env_incomplete(monkeypatch):
    _env(monkeypatch)
    monkeypatch.delenv("CF_ACCESS_CLIENT_SECRET")
    assert ai_remix._cf_access_headers("https://ai.example.com/v1") == {}
