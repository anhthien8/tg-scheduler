import kol_offer as k


def _ladder_with(est):
    r = {**k.DEFAULT_LADDER["rules"], "extra_allowed_dollars": sorted(k.estimate_dollars(est))}
    return {**k.DEFAULT_LADDER, "rules": r}


def test_estimate_tiers_and_math():
    e = k.value_estimate(None, "15M")
    # 15M at the 0.08% futures taker fee = 12,000; SABC tier keeps its own table rate.
    assert e["sabc"] == "B" and e["fees"] == 12000
    # Tier 2: 60% of 12,000 = 7,200 commission + $100 per 1M on ALL 15M futures volume = 1,500 bonus.
    assert e["steady"]["tier"] == 2 and e["steady"]["commission"] == 7200 and e["steady"]["bonus"] == 1500
    assert e["steady"]["total"] == 8700
    assert "RETENTION CARD" in k.render_value_estimate(e) and "$200" not in k.render_value_estimate(e) and "8,700" in k.render_value_estimate(e)
    assert k.value_estimate(None, None) is None
    assert k.sabc_tier(99) is None and k.sabc_tier(100_000) == "S"


def test_guard_allows_only_estimate_figures():
    e = k.value_estimate(None, "60M")
    L = _ladder_with(e)
    assert k.find_off_ladder_figures(f"about ${e['steady']['total']:,}/month", L) == []
    assert k.find_off_ladder_figures("about $99,999/month", L) == ["$99,999"]


def test_prompt_includes_estimate_only_with_volume():
    assert "VALUE ESTIMATE" in k.render_offer_ladder_prompt(None, "15M")
    assert "VALUE ESTIMATE" not in k.render_offer_ladder_prompt(None, None)


def test_weex_guard_accepts_kb_figures():
    agent = {"knowledge_base": "WEEX tiers: earn up to 18.5% APR, 5,000 USDT prize pool"}
    assert k.find_off_offer_figures("18.5% APR and 5,000 USDT", "WEEX", agent) == []
    assert k.find_off_offer_figures("18.5% APR", "WEEX", None) == ["18.5%"]


def test_guard_reads_dot_grouped_thousands_like_vietnamese_writers():
    """'7.490 USDT' is 7,490, not 7.49: a Vietnamese campaign reply must not be blocked for it."""
    agent = {"knowledge_base": "WEEX campaign prizes: 1,000 USDT, 7,490 USDT and a 3,000 USDT pool. Fee 0.125 USDT."}
    assert k.find_off_offer_figures("Giải 1.000 USDT, 7.490 USDT, pool 3.000 USDT", "WEEX", agent) == []
    assert k.find_off_offer_figures("pool $1.000.000", "WEEX", agent) == []      # 'per 1M' is always allowed
    assert k.find_off_offer_figures("Giải 9.999 USDT", "WEEX", agent) == ["9.999 USDT"]
    assert k.find_off_offer_figures("phí 0.125 USDT", "WEEX", agent) == []        # a real decimal stays a decimal
    assert k.find_off_offer_figures("phí 12.5 USDT", "WEEX", agent) == ["12.5 USDT"]
