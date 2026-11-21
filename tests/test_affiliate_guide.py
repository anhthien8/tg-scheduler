"""Affiliate Portal guide that follows our side's portal-login message (owner request 2026-11-21)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import affiliate_guide as ag
import database as db
import dm_reply_tracker as tracker
import lang_guard
import telegram_forum_inbox as inbox

CREDS = "Account: Chasul\nPassword: 4nA?p?IZ\nhttps://affiliates.weex.com/"


def _h(*user_msgs):
    return [{"role": "user", "content": m} for m in user_msgs]


# ── detection ────────────────────────────────────────────────────────────────

def test_login_kind_detects_portal_login_only():
    assert ag.login_kind(CREDS) == "portal"
    assert ag.login_kind("Tài khoản: abc\nMật khẩu: x1y2") == "generic"
    assert ag.login_kind("账号：abc 密码：123") == "generic"
    assert ag.login_kind("Check the dashboard at https://affiliates.weex.com") == ""   # link alone
    assert ag.login_kind("Did you set my %") == ""
    assert ag.login_kind("I forgot my password lol") == ""


# ── language ─────────────────────────────────────────────────────────────────

def test_guide_lang_edge_cases_from_review():
    assert ag.guide_lang(_h("わかりました、あとでリンクを送ります", "了解")) is None           # kanji-only reply stays Japanese
    assert ag.guide_lang(_h("Oke on bro, I will send the link tonight")) == "en"           # 'oke'+'on' is not Vietnamese
    assert ag.guide_lang(_h("ok ban, minh gui link group sau nhe")) == "en"                # no real Vietnamese letters
    assert ag.guide_lang(_h("Salam bhai, abi main busy hun", "Tamam details bhej dein please")) == "en"  # Roman Urdu
    assert ag.guide_lang(_h("Mulţumesc, şi eu sunt interesat de program")) == "en"         # Romanian
    assert ag.guide_lang(_h("Sige po, pwede ba kita padalhan ng link mamaya")) == "en"     # Tagalog
    quoted = 'Хорошо, понял\n[Quoting your earlier message: "' + "Here is the full offer for you " * 20 + '"]'
    assert ag.guide_lang(_h(quoted)) == "ru"                                               # our quoted text is ignored


def test_guide_lang_follows_what_the_kol_writes():
    assert ag.guide_lang([]) is None                                     # no evidence: BD writes it by hand
    assert ag.guide_lang(_h("I will send you ASAP, did you set my rate")) == "en"
    assert ag.guide_lang(_h("Ok bạn, mình gửi link group liền nhé")) == "vi"
    assert ag.guide_lang(_h("好的，我晚点把群链接发你")) == "zh"
    assert ag.guide_lang(_h("네 알겠습니다 채널 링크 보내드릴게요")) == "ko"
    assert ag.guide_lang(_h("Хорошо, скину ссылку на канал позже")) == "ru"
    assert ag.guide_lang(_h("ได้ครับ เดี๋ยวส่งลิงก์กลุ่มให้นะ")) == "th"
    assert ag.guide_lang(_h("Tamam hocam, kanal linkini şimdi atıyorum")) == "tr"
    assert ag.guide_lang(_h("Oke bang, nanti saya kirim link grup komunitas kita ya")) == "id"
    assert ag.guide_lang(_h("わかりました、あとでリンクを送ります")) is None   # no Japanese template


def test_every_template_is_in_its_own_language_and_names_the_real_menus():
    expected = {"en": "latin", "vi": "vi", "zh": "zh", "id": "latin", "tr": "latin",
                "ko": "ko", "ru": "cyr", "th": "th"}
    assert set(ag.TEMPLATES) == set(expected)
    for lang, fam in expected.items():
        text = ag.render(lang)
        assert lang_guard.text_script(text) == fam, lang
        assert ag.HELP_ARTICLE in text and ag.PORTAL_HOST in text
        for menu in ("Change password", "Referral link", "Campaigns", "Commission stats", "User list",
                     "Event center"):
            assert menu in text, (lang, menu)
        assert not ag.login_kind(text)                      # the guide never re-triggers itself
        assert "<" not in text and "&" not in text          # sent with HTML parse mode


# ── sending ──────────────────────────────────────────────────────────────────

async def _setup(monkeypatch, kol_step=None, history=None, agent_name="A"):
    acc = {"id": 1, "name": "Acc", "phone": "+8411", "api_id": "a", "api_hash": "h", "session_name": "s"}
    agent_id = None
    if agent_name:
        agent_id = await db.create_ai_agent({"name": agent_name, "provider": "gemini", "api_keys_json": ["k"]})
        acc["ai_agent_id"] = agent_id
    await db.create_account(acc)
    if agent_id:
        await db.set_account_ai_agent(1, agent_id)
    await db.get_or_create_followup_chat(account_id=1, user_id=500, username="kol")
    for m in history or []:
        await db.append_followup_chat_message(1, 500, m["role"], m["content"])
    if kol_step:
        await db.set_kol_step(1, 500, kol_step, actor="admin")
    sent = []

    async def fake_send(account_id, user_id, text, *a, **k):
        sent.append(text)
        return True

    monkeypatch.setattr(ag, "SEND_DELAY_SECS", 0)
    monkeypatch.setattr(tracker, "_pending_ai_sends", set())   # our sends lock the chat for 30 s
    monkeypatch.setattr(tracker.tg, "send_text_message", fake_send)
    monkeypatch.setattr(tracker, "is_internal_account_user", AsyncMock(return_value=False))
    import command_bot
    monkeypatch.setattr(command_bot, "relay_outgoing_to_forum", AsyncMock(return_value="relayed"))
    return sent


async def test_portal_login_sends_the_guide_once_and_moves_the_funnel(monkeypatch):
    sent = await _setup(monkeypatch, "uid_received", _h("I will send you ASAP, need to check my rate first"))
    await tracker._after_portal_login(1, 500, CREDS)
    await tracker._after_portal_login(1, 500, CREDS)          # a second login message: no second guide
    assert sent == [ag.render("en")]
    chat = await db.get_followup_chat(1, 500)
    assert chat["dashboard_guide_sent_at"] and chat["kol_step"] == "link_confirmed"
    assert chat["history"][-1] == {**chat["history"][-1], "role": "assistant", "content": ag.render("en")}


async def test_guide_is_written_in_the_kols_language(monkeypatch):
    sent = await _setup(monkeypatch, None, _h("好的，我晚点把群链接发你"))
    await tracker._after_portal_login(1, 500, CREDS)
    assert sent == [ag.render("zh")]


async def test_no_template_the_kol_reads_means_nothing_is_sent(monkeypatch):
    sent = await _setup(monkeypatch, None, _h("わかりました、あとでリンクを送ります"))
    await tracker._after_portal_login(1, 500, CREDS)
    assert sent == []
    assert (await db.get_followup_chat(1, 500))["kol_step"] == "link_confirmed"   # the funnel still moves


async def test_ai_off_account_gets_no_guide_but_the_funnel_moves(monkeypatch):
    sent = await _setup(monkeypatch, "deal_agreed", agent_name=None)
    await tracker._after_portal_login(1, 500, CREDS)
    assert sent == []
    chat = await db.get_followup_chat(1, 500)
    assert chat["kol_step"] == "link_confirmed" and not chat["dashboard_guide_sent_at"]


async def test_blacklisted_or_muted_chats_get_no_guide(monkeypatch):
    sent = await _setup(monkeypatch)
    monkeypatch.setattr(db, "is_user_blacklisted", AsyncMock(return_value=True))
    await tracker._after_portal_login(1, 500, CREDS)
    monkeypatch.setattr(db, "is_user_blacklisted", AsyncMock(return_value=False))
    await db.update_followup_chat_status(1, 500, "paused_admin")
    await tracker._after_portal_login(1, 500, CREDS)
    assert sent == []


async def test_aioff_chat_gets_no_guide(monkeypatch):
    sent = await _setup(monkeypatch, None, _h("I will send you ASAP, need to check my rate first"))
    await db.set_followup_stage(1, 500, "paused")
    await tracker._after_portal_login(1, 500, CREDS)
    assert sent == []


async def test_generic_login_does_not_reopen_a_closed_lead(monkeypatch):
    sent = await _setup(monkeypatch, "media", _h("I will send you ASAP, need to check my rate first"))
    await tracker._after_portal_login(1, 500, "Account: demo1\nPassword: abc123")
    assert sent == [] and (await db.get_followup_chat(1, 500))["kol_step"] == "media"


async def test_guide_does_not_hold_the_bds_next_messages(monkeypatch):
    await _setup(monkeypatch, None, _h("I will send you ASAP, need to check my rate first"))
    await tracker._after_portal_login(1, 500, CREDS)
    assert (1, 500) not in tracker._pending_ai_sends


async def test_login_pasted_right_after_an_ai_reply_still_counts(monkeypatch):
    await _setup(monkeypatch)
    calls = []
    monkeypatch.setattr(tracker, "note_admin_message", lambda a, u, t: calls.append(t))
    monkeypatch.setattr(tracker, "_pending_ai_sends", {(1, 500)})
    ev = MagicMock()
    ev.is_private, ev.out, ev.chat_id, ev.raw_text = True, True, 500, CREDS
    ev.message.message, ev.message.media, ev.message.sticker = CREDS, None, None
    await tracker._make_handler(account_id=1)(ev)
    assert calls == [CREDS]


async def test_a_later_step_is_never_moved_back(monkeypatch):
    await _setup(monkeypatch, "first_post")
    await tracker._after_portal_login(1, 500, CREDS)
    assert (await db.get_followup_chat(1, 500))["kol_step"] == "first_post"


async def test_generic_login_in_a_chat_selling_another_exchange_is_ignored(monkeypatch):
    sent = await _setup(monkeypatch, None, agent_name="BloFin Negotiator")
    await tracker._after_portal_login(1, 500, "Account: kol1\nPassword: abc123")
    assert sent == []
    assert (await db.get_followup_chat(1, 500))["kol_step"] in (None, "prospect")


# ── every way our side sends a message reaches the hook ──────────────────────

async def test_all_three_send_paths_call_the_hook(monkeypatch):
    await _setup(monkeypatch)
    calls = []
    monkeypatch.setattr(tracker, "note_admin_message", lambda a, u, t: calls.append((a, u, t)))
    monkeypatch.setattr(tracker, "_pending_ai_sends", set())   # no AI send in flight for this chat

    # 1. typed in a Telegram app (outgoing event)
    ev = MagicMock()
    ev.is_private, ev.out, ev.chat_id = True, True, 500
    ev.raw_text = CREDS
    ev.message.message, ev.message.media, ev.message.sticker = CREDS, None, None
    with patch("dm_reply_tracker._async_distill_human_rule", new_callable=AsyncMock):
        await tracker._make_handler(account_id=1)(ev)

    # 2. the app's chat box
    import pending_sends
    await pending_sends.record_human_send(1, 500, "Second: " + CREDS)

    # 3. the lead's forum topic
    class FakeDB:
        async def get_telegram_topic_by_thread(self, group_id, topic_id):
            return {"group_id": -1001, "topic_id": 77, "account_id": 1, "user_id": 500, "username": "kol"}

        async def set_human_takeover(self, account_id, user_id):
            pass

        async def append_followup_chat_message(self, account_id, user_id, role, text):
            pass

        async def log_command_action(self, **kwargs):
            pass

    fdb = FakeDB()
    preview = await inbox.prepare_reply(fdb, inbox.ForumConfig(True, -1001, 1, {9}), 9, -1001, 77, "Third: " + CREDS)

    async def sender(account_id, user_id, text):
        return True

    assert await inbox.confirm_reply(fdb, preview.token, 9, -1001, 77, sender) == "sent"
    assert calls == [(1, 500, CREDS), (1, 500, "Second: " + CREDS), (1, 500, "Third: " + CREDS)]


def test_hook_ignores_ordinary_messages(monkeypatch):
    spawned = []
    monkeypatch.setattr(tracker, "_spawn_background", lambda coro: (spawned.append(coro), coro.close()))
    tracker.note_admin_message(1, 500, "could you share your channel link?")
    assert spawned == []
    tracker.note_admin_message(1, 500, CREDS)
    assert len(spawned) == 1
