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
