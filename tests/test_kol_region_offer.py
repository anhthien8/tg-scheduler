"""Region WEEX leads (every market except Vietnam, Korea, China, sanctioned): flat 60% revshare + deposit bonus."""
import kol_offer
import kol_region_offer as region
import dm_reply_tracker as tracker


def _user(*texts):
    return [{"role": "user", "content": t} for t in texts]


def test_detect_region_from_places_language_and_profile():
    assert region.detect_region(_user("Hi, my community is in Jakarta")) == "ID"
    assert region.detect_region(_user("Halo kak, saya punya komunitas trading, bisa kerja sama?")) == "ID"
    assert region.detect_region(_user("Based in Dubai, 30k members")) == "AE"
    assert region.detect_region(_user("We are from the UAE")) == "AE"
    assert region.detect_region([], {"audience_region": "Indonesia"}) == "ID"
    assert region.detect_region([], {"location": "Abu Dhabi"}) == "AE"


def test_detect_region_ignores_others_and_our_own_messages():
    assert region.detect_region(_user("Hello bro, how are you?")) is None
    assert region.detect_region(_user("Vietnam community, 20k members")) is None
    # Malay speaker shares words with Bahasa Indonesia: Malaysia, not Indonesia
    assert region.detect_region(_user("Saya nak tahu, boleh tak kita kerja sama? Saya dari Malaysia")) == "MY"
    assert region.detect_region([{"role": "assistant", "content": "We have KOLs in Jakarta and Dubai"}]) is None
    assert region.detect_region(_user("I flew Emirates airline last week")) is None


def test_region_prompt_leads_with_deposit_campaign_and_no_tiers():
    ladder = kol_offer.DEFAULT_LADDER
    block = region.render_prompt("ID", ladder)
    assert "FOR INDONESIA" in block and "60%" in block and "10%" in block and "20%" in block
    assert block.index("DEPOSIT BONUS CAMPAIGN") < block.index("revshare on their referrals")
    for tier_pct in ("55%", "65%", "70%", "80%"):
        assert tier_pct not in block
    assert "likely qualifies" not in block
    assert "first deposit campaign run" in block.lower() and "30M+" in block
    assert "likely qualifies them for the 20% deposit bonus" in region.render_prompt("AE", ladder, volume_claimed=35)
    assert "likely qualifies" not in region.render_prompt("AE", ladder, volume_claimed=25)


def test_guard_allows_region_figures_and_blocks_ladder_figures():
    ladder = region.guard_ladder(kol_offer.DEFAULT_LADDER)
    ok = "Your members get a 10% deposit bonus (20% with strong volume) and you earn 60% revshare paid daily."
    assert kol_offer.find_off_offer_figures(ok, "WEEX", None, ladder) == []
    assert kol_offer.find_off_offer_figures("Up to 80% revshare for you", "WEEX", None, ladder) == ["80%"]
    assert kol_offer.find_off_offer_figures("Start at 55% revshare", "WEEX", None, ladder) == ["55%"]
    # the normal ladder is unchanged for everyone else
    assert kol_offer.find_off_offer_figures("Start at 55% revshare", "WEEX", None, kol_offer.DEFAULT_LADDER) == []
    assert kol_offer.find_off_offer_figures("10% deposit bonus", "WEEX", None, kol_offer.DEFAULT_LADDER) == ["10%"]


async def test_offer_block_for_swaps_ladder_only_for_weex_region_leads():
    block, ladder = await kol_offer.offer_block_for("WEEX", None, region="AE")
    assert "OFFER FOR UAE" in block and "COMMISSION LADDER" not in block
    assert ladder["rules"]["percents_only"] == [60, 10, 20]
    normal, normal_ladder = await kol_offer.offer_block_for("WEEX", None)
    assert "COMMISSION LADDER" in normal and "percents_only" not in normal_ladder["rules"]
    other, other_ladder = await kol_offer.offer_block_for("BloFin", None, region="ID")
    assert "OFFER FOR" not in other and other_ladder is None


def test_kol_care_highlights_deposit_campaign_for_region():
    text = tracker._kol_care_instruction("WEEX", 30, "events", "ID")
    assert "DEPOSIT BONUS CAMPAIGN" in text and "Indonesia" in text
    assert "DEPOSIT BONUS" not in tracker._kol_care_instruction("WEEX", 30, "events")


def test_kol_own_country_beats_community_country():
    # KOL based in Vietnam serving a Thai community -> normal ladder
    assert region.detect_region(_user("My group is Thai"),
                                {"kol_country": "Vietnam", "community_country": "Thailand"}) is None
    # KOL based in Singapore serving an Indonesian community -> Singapore offer
    assert region.detect_region([], {"kol_country": "Singapore", "community_country": "Indonesia"}) == "SG"
    # "Global" names no country -> the community decides
    assert region.detect_region([], {"kol_country": "Global", "community_country": "Brazil"}) == "BR"
    # KOL in Jakarta serving a global audience -> Indonesia offer
    assert region.detect_region([], {"kol_country": "Indonesia", "community_country": "Global"}) == "ID"
    # own country unknown -> the community decides
    assert region.detect_region([], {"community_country": "UAE, Saudi Arabia"}) == "AE"
    assert region.detect_region([], {"community_country": "Vietnam"}) is None
    assert region.detect_region([], {"kol_country": "Hong Kong"}) is None
    assert region.detect_region([], {"community_country": "Korea, Japan"}) is None


def test_origin_question_only_until_the_kol_answers():
    assert not region.origin_known({})
    assert not region.origin_known({"followers": "20k"})
    assert region.origin_known({"community_country": "Indonesia"})
    assert "where are you based" in region.ORIGIN_QUESTION_RULE


def test_region_prompt_attaches_deposit_banner_and_blocks_outdated_ones():
    block = region.render_prompt("ID", kol_offer.DEFAULT_LADDER)
    assert "[[BANNER:deposit10]]" in block
    assert "Never send [[BANNER:double_gain]]" in block and "[[BANNER:tier_s]]" in block
    # the banner tag itself never trips the figure guard
    reply = "Your members get a 10% deposit bonus and you earn 60% revshare paid daily. [[BANNER:deposit10]]"
    assert kol_offer.find_off_offer_figures(reply, "WEEX", None, region.guard_ladder(kol_offer.DEFAULT_LADDER)) == []
    # no image file -> no instruction to send it
    assert "BANNER" not in region.render_prompt("ID", kol_offer.DEFAULT_LADDER, offer={"banner_key": "missing_x"})


def test_detect_covered_regions_beyond_indonesia_and_uae():
    assert region.detect_region(_user("We are in Mexico City, 20k members")) == "MX"
    assert region.detect_region(_user("I run a group in São Paulo")) == "BR"
    assert region.detect_region(_user("London based, UK community")) == "GB"
    assert region.detect_region(_user("Most of my members are in the USA")) == "US"
    assert region.detect_region(_user("Latin American traders mostly")) == "LATAM"
    assert region.detect_region(_user("Based in Riyadh")) == "SA"
    assert region.detect_region(_user("Berlin and Paris mostly")) == "DE"
    assert region.detect_region(_user("สวัสดีครับ ชุมชนของเรา")) == "TH"
    assert region.detect_region([], {"kol_country": "United States"}) == "US"
    assert "OFFER FOR THE US" in region.render_prompt("US", kol_offer.DEFAULT_LADDER)


def test_excluded_markets_keep_the_ladder():
    # Vietnam / Korea / China market, by place or by writing language
    assert region.detect_region(_user("Mình ở Việt Nam, cộng đồng 20k người")) is None
    assert region.detect_region(_user("我们是中文社区，有两万人")) is None
    assert region.detect_region(_user("안녕하세요, 우리 커뮤니티는 한국입니다")) is None
    # writing in Chinese wins even when the KOL lives in a covered country
    assert region.detect_region(_user("我在新加坡，我们是中文社区"), {"kol_country": "Singapore"}) is None
    # countries outside the covered list, and mixed mentions without an answer yet
    assert region.detect_region(_user("We are in Tehran, Iran")) is None
    assert region.detect_region(_user("My users are in USA and Iran")) is None
    assert region.detect_region(_user("Europe and Vietnam mostly")) is None
    assert region.detect_region(_user("South Korea based")) is None
    assert region.detect_region(_user("Michael Jordan fan here")) is None
    # Romanian diacritics are not Vietnamese
    assert region.detect_region(_user("Bună ziua, sunt din România și comunitatea mea e mare")) == "RO"


def test_region_prompt_never_helps_around_country_restrictions():
    block = region.render_prompt("GB", kol_offer.DEFAULT_LADDER)
    assert "never suggest a VPN" in block and "[HANDOVER_REQUIRED]" in block


def test_detect_south_asia_africa_cis_and_others():
    assert region.detect_region(_user("I am from India")) == "IN"
    assert region.detect_region(_user("Karachi, Pakistan group")) == "PK"
    assert region.detect_region(_user("Lagos, Nigeria crew")) == "NG"
    assert region.detect_region(_user("Istanbul traders")) == "TR"
    assert region.detect_region(_user("Based in Toronto")) == "CA"
    assert region.detect_region(_user("Sydney based")) == "AU"
    assert region.detect_region(_user("My users are in USA and India")) == "US"
    assert region.detect_region(_user("African traders")) == "AFRICA"
    # Japanese uses Han characters but kana marks it as Japanese, not Chinese
    assert region.detect_region(_user("私たちは日本のコミュニティです、東京")) == "JP"
    # look-alike phrases are not places
    assert region.detect_region(_user("canadian dollar hedging")) is None
    assert region.detect_region(_user("Japanese candlesticks strategy")) is None
    # Vietnam-based KOL with an Indian community keeps the ladder (own country first)
    assert region.detect_region([], {"kol_country": "Vietnam", "community_country": "India"}) is None
    assert "OFFER FOR INDIA" in region.render_prompt("IN", kol_offer.DEFAULT_LADDER)


def test_russia_keeps_the_ladder_like_vietnam():
    assert "RU" not in region.REGIONS
    assert region.detect_region(_user("Moscow, Russia")) is None
    assert region.detect_region(_user("Our community is Russian-speaking")) is None
    assert region.detect_region(_user("Мы из Москвы, у нас русскоязычный канал")) is None
    assert region.detect_region(_user("🇷🇺 traders")) is None
    # Russia is a real exclusion, not just an unknown place: it blocks the community fallback
    assert region.detect_region([], {"kol_country": "Russia", "community_country": "Kazakhstan"}) is None
    assert region.detect_region(_user("Kazakhstan community, I live in Moscow")) is None
    # the rest of the CIS keeps the region offer
    assert region.detect_region(_user("Kyiv, Ukraine")) == "UA"
    assert region.detect_region([], {"kol_country": "Kazakhstan"}) == "KZ"
    assert region.detect_region(_user("CIS traders")) == "CIS"
