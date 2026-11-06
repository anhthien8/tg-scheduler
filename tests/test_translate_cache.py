"""Chat/description translation: detected language is returned, failures are not cached."""
import io
import json
import urllib.request

import pytest

from routes import members


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def gtx(monkeypatch):
    monkeypatch.setattr(members, "_translation_cache", {})
    monkeypatch.setattr(members, "_translation_src_lang", {})
    calls = []
    state = {"fail": False}

    def fake_urlopen(req, timeout=5):
        calls.append(req.full_url)
        if state["fail"]:
            raise OSError("network down")
        body = [[["Hello friend", "Привет друг", None, None]], None, "ru"]
        return _Resp(json.dumps(body).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls, state


async def test_returns_translation_and_detected_language(gtx):
    calls, _ = gtx
    res = await members.translate_descriptions(
        members.TranslateDescriptionsRequest(texts=["Привет друг", " Привет друг "]))
    assert res["translations"] == {"Привет друг": "Hello friend"}
    assert res["langs"] == {"Привет друг": "ru"}
    assert len(calls) == 1  # duplicate (after trimming) translated once

    await members.translate_descriptions(members.TranslateDescriptionsRequest(texts=["Привет друг"]))
    assert len(calls) == 1  # served from cache


async def test_failure_is_not_cached(gtx):
    calls, state = gtx
    state["fail"] = True
    res = await members.translate_descriptions(members.TranslateDescriptionsRequest(texts=["Привет друг"]))
    assert res["translations"] == {"Привет друг": "Привет друг"}  # original back
    assert res["langs"] == {"Привет друг": ""}  # "" tells the UI it failed

    state["fail"] = False
    res = await members.translate_descriptions(members.TranslateDescriptionsRequest(texts=["Привет друг"]))
    assert res["translations"] == {"Привет друг": "Hello friend"}  # retried, not stuck on the failure
    assert len(calls) == 2


def test_target_lang_is_validated():
    with pytest.raises(ValueError):
        members.TranslateDescriptionsRequest(texts=["x"], target_lang="en&q=evil")
    assert members.TranslateDescriptionsRequest(texts=["x"], target_lang="zh-CN").target_lang == "zh-CN"
