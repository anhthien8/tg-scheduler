"""WEEX deal rules (owner, 2026-11-10).

1. Starting tier follows proven volume on the KOL's current exchange ("likely, once the
   team verifies"), replacing "the first month is always the starting tier".
2. Ask ONCE for the KOL's current deal; save it on the lead card.
3. Never refuse upfront: team reviews their deal, milestone-based staged upfront after a
   good first month; hand over only at the second step.
Rules 2 + 3 also cover WEEX region leads; non-WEEX agents keep UPFRONT_PROOF_RULE.
"""
import copy

import database as db
import kol_offer


# ── rule 1: starting tier ────────────────────────────────────────────────────

def test_default_rules_drop_first_month_and_add_starting_tier():
    rules = kol_offer.DEFAULT_LADDER["rules"]
    assert "first month is always" not in rules["upgrade"]
    assert rules.get("starting_tier")
    text = kol_offer.render_offer_ladder_prompt(kol_offer.DEFAULT_LADDER)
    assert "first month is always" not in text.lower()
    assert "STARTING TIER" in text
    assert "under 10M → Tier 1 (55%)" in text and "100M+ → Tier 5 (80%)" in text


def test_saved_legacy_upgrade_text_is_replaced_by_the_new_default():
    saved = copy.deepcopy(kol_offer.DEFAULT_LADDER)
    saved["rules"]["upgrade"] = kol_offer._LEGACY_RULE_TEXT["upgrade"]
    out = kol_offer._normalize_ladder(saved)
    assert out["rules"]["upgrade"] == kol_offer.DEFAULT_LADDER["rules"]["upgrade"]
    # A custom (non-legacy) text written by the owner is kept as is.
    saved["rules"]["upgrade"] = "Owner's own wording."
    assert kol_offer._normalize_ladder(saved)["rules"]["upgrade"] == "Owner's own wording."


def test_volume_ranges_count_as_their_lower_bound():
    assert kol_offer.parse_volume_m("18-20M") == 18
    assert kol_offer.parse_volume_m("18k-20k") == 0.018
    assert kol_offer.parse_volume_m("18 to 20 million") == 18
    assert kol_offer.parse_volume_m("$1.5B") == 1500
    assert kol_offer.parse_volume_m("about 25M a month") == 25
    assert kol_offer.parse_volume_m("no idea") is None


def test_stated_volume_prefers_admin_value_then_chat_value():
    assert kol_offer.stated_volume({"volume_claimed": "50M", "monthly_volume": "12M"}) == "50M"
    assert kol_offer.stated_volume({"monthly_volume": "12M"}) == "12M"
    assert kol_offer.stated_volume({}) is None
    assert kol_offer.stated_volume(None) is None


def test_chat_stated_volume_predicts_tier_but_not_the_value_estimate():
    ladder = kol_offer.DEFAULT_LADDER
    text = kol_offer.render_offer_ladder_prompt(ladder, volume_stated="18-20M")
    assert "TIER PREDICTION ALLOWED" in text and "Tier 2" in text
    assert "once our team verifies" in text
    assert "PERFORMANCE SYNC" not in text  # value estimate only from the admin-entered volume

    with_admin = kol_offer.render_offer_ladder_prompt(ladder, volume_claimed="60M")
    assert "Tier 4" in with_admin and "PERFORMANCE SYNC" in with_admin


# ── rules 2 + 3: current deal + upfront review ───────────────────────────────

def test_weex_ladder_prompt_carries_current_deal_and_upfront_review():
    text = kol_offer.render_offer_ladder_prompt(kol_offer.DEFAULT_LADDER)
    assert kol_offer.CURRENT_DEAL_RULE in text
    assert kol_offer.UPFRONT_REVIEW_RULE in text
    assert kol_offer.UPFRONT_PROOF_RULE not in text
    assert "upfront is unavailable" not in text.replace("never say upfront is unavailable", "")


def test_upfront_review_rule_never_refuses_and_hands_over_on_the_second_step():
    rule = kol_offer.UPFRONT_REVIEW_RULE
    assert "do NOT refuse" in rule and "do NOT hand over yet" in rule
    assert "not yet activated" in rule and "first-time partnership" in rule
    assert "after the first month" in rule and "community's performance" in rule
    assert "trial-fund" in rule and "paid in stages" not in rule
    assert "Never state an upfront amount" in rule and "CASH upfront" in rule
    # The kept knowledge-base ladder (marketing fee bonus, then trial fund) stays reachable and quotable.
    assert "MARKETING FEE & UPFRONT LADDER" in rule and "step 1 before step 2" in rule
    assert "MARKETING FEE & UPFRONT LADDER" in kol_offer.render_offer_ladder_prompt(kol_offer.DEFAULT_LADDER).splitlines()[-2]
    assert rule.rstrip(".").endswith("[HANDOVER_REQUIRED]")
    deal = kol_offer.CURRENT_DEAL_RULE
    assert "ONCE" in deal and "Never name an exchange" in deal and "never promise" in deal


async def test_region_leads_get_rules_2_and_3_but_no_starting_tier():
    block, _ = await kol_offer.offer_block_for("WEEX", None, region="AE")
    assert kol_offer.CURRENT_DEAL_RULE in block and kol_offer.UPFRONT_REVIEW_RULE in block
    assert "STARTING TIER" not in block
    assert "never on the first ask" in block


async def test_non_weex_agents_keep_the_upfront_proof_rule():
    block, ladder = await kol_offer.offer_block_for("BloFin", None)
    assert ladder is None
    assert kol_offer.UPFRONT_PROOF_RULE in block
    assert kol_offer.UPFRONT_REVIEW_RULE not in block and kol_offer.CURRENT_DEAL_RULE not in block


def test_guard_passes_milestone_upfront_wording():
    reply = ("Totally get it bro. Upfront isn't activated yet for a first-time partnership — after the first month "
             "together our team will review your request again based on your community's performance. "
             "Could you share your current revshare %, any volume bonus or fixed fee, and a screenshot of "
             "your commission page?")
    assert kol_offer.find_off_ladder_figures(reply) == []


# ── lead card: current deal + stated volume ──────────────────────────────────

async def test_kol_state_exposes_current_deal_and_stated_volume():
    await db.create_account({
        "id": 1, "name": "BD Acc", "phone": "+84123456", "api_id": "api", "api_hash": "hash",
        "session_name": "bd_acc",
    })
    await db.get_or_create_followup_chat(account_id=1, user_id=303, username="kol", name="KOL")
    await db.upsert_kol_profile(1, 303, {"monthly_volume": "18-20M", "current_cex": "OtherEx",
                                         "current_deal": "50% revshare, $2k fixed/month, 6-month exclusive"})
    state = await db.get_kol_state(1, 303)
    assert state["have"]["monthly_volume"] == "18-20M"
    assert state["current_deal"] == {"current_cex": "OtherEx",
                                     "current_deal": "50% revshare, $2k fixed/month, 6-month exclusive"}
    pred = kol_offer.predicted_tier(kol_offer.DEFAULT_LADDER, kol_offer.stated_volume(state["have"]))
    assert pred["tier"] == 2


async def test_kol_state_current_deal_empty_without_data():
    await db.create_account({
        "id": 1, "name": "BD Acc", "phone": "+84123456", "api_id": "api", "api_hash": "hash",
        "session_name": "bd_acc",
    })
    await db.get_or_create_followup_chat(account_id=1, user_id=304, username="kol2", name="KOL2")
    state = await db.get_kol_state(1, 304)
    assert state["current_deal"] == {}
