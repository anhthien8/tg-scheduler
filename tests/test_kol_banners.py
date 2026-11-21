import kol_banners as kb


def test_extract_strips_tag_and_echo():
    text, keys = kb.extract("Hi!\n\n[[BANNER: Trading_Comp ]]\n[banner sent: x]")
    assert text == "Hi!" and keys == ["trading_comp"]


def test_banner_files_exist():
    for k in ("trading_comp", "double_gain", "deposit10", "welcome3000", "tradfi_challenge", "ai_wars", "ai_wars_sponsor", "shopping_fest", "hype_airdrop", "uni_airdrop", "aapl_airdrop", "wxt_s9", "p2p_halloween", "hot_token_fest", "starter_pack_3000", "copy_lead_s3", "tier_s", "tier_a", "tier_b", "tier_c"):
        assert kb.banner_path(k), k
    assert kb.banner_path("../etc") is None


def test_already_sent():
    hist = [{"role": "assistant", "content": "x\n" + kb.sent_mark("deposit10")}]
    assert kb.already_sent(hist, "deposit10") and not kb.already_sent(hist, "double_gain")


def test_campaign_block_lists_only_active_unexpired_with_files(tmp_path, monkeypatch):
    import json, kol_banners as kb
    (tmp_path / "live.jpg").write_bytes(b"x")
    (tmp_path / "old.jpg").write_bytes(b"x")
    (tmp_path / "campaigns.json").write_text(json.dumps({"campaigns": [
        {"key": "live", "active": True, "title": "Live", "details": "Deposit 100 USDT -> 10 USDT"},
        {"key": "old", "active": True, "ends": "2026-11-01", "title": "Old", "details": "x"},
        {"key": "off", "active": False, "title": "Off", "details": "x"},
        {"key": "nofile", "active": True, "title": "NoFile", "details": "x"},
    ]}), encoding="utf-8")
    monkeypatch.setattr(kb, "BANNER_DIR", str(tmp_path))
    monkeypatch.setattr(kb, "CATALOG_PATH", str(tmp_path / "campaigns.json"))
    monkeypatch.setattr(kb, "_catalog_cache", (-1.0, []))
    block = kb.campaign_prompt_block(today="2026-11-18")
    assert "[[BANNER:live]]" in block and "100 USDT" in block
    listed = [ln for ln in block.splitlines() if ln.startswith("* ")]   # the campaign lines, not the rule text
    assert listed == [ln for ln in listed if "[[BANNER:live]]" in ln] and len(listed) == 1

    # Owner rule 2026-11-19: a general "what campaigns do you have" gets a question about the kind of
    # campaign first; details and banner only after the KOL picks (overrides "recommend ONE campaign").
    rule = block.split("ONCE THE KOL CONFIRMS")[0]
    assert "ASK FIRST WHEN WE KNOW NOTHING" in rule and "neither their channel" in rule
    assert "do NOT send campaign details or a banner yet" in rule and "ONCE per chat" in rule
    assert "recommend the best-fitting campaign directly" in rule        # known community: no override
    after = block.split("ONCE THE KOL CONFIRMS")[1]
    assert "FULL details" in after and kb.REGISTER_LINK in after          # details + banner + onboarding link


def test_link_confirmed_step_sends_dashboard_and_event_guide():
    import dm_reply_tracker as t, kol_banners as kb
    g = t._STEP_GUIDANCE["link_confirmed"]
    assert kb.AFFILIATE_DASHBOARD_LINK in g and "Event center" in g and "once per chat" in g
    assert "Change password" in g and "Commission stats" in g and "Do not resend the portal guide" in g


async def test_campaign_figures_pass_the_guard(monkeypatch):
    import kol_banners as kb, kol_offer
    monkeypatch.setattr(kb, "campaign_prompt_block", lambda today=None: "* [[BANNER:deposit10]] deposit 1,000 USDT -> 100 USDT, 7,490 USDT")
    block, ladder = await kol_offer.offer_block_for("WEEX", None)
    assert "[[BANNER:deposit10]]" in block
    assert kol_offer.find_off_offer_figures("Trade 200M and get 7,490 USDT trial fund", "WEEX", None, ladder) == []
    assert kol_offer.find_off_offer_figures("get 9,999 USDT", "WEEX", None, ladder) == ["9,999 USDT"]
