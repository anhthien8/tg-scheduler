"""BD potential is for futures traders only; airdrop / bonus hunters never qualify."""
from telegram_client import score_community_trading as score


def test_airdrop_channel_not_bd():
    r = score("Airdrop Finder", "Get free Cryptocurrency Airdrop everyday, legitimate airdrops. Contact @x",
              "airdropfind", ["@x"], 516311)
    assert r["category"] == "airdrop_bonus" and r["trading_score"] < 45


def test_generic_crypto_without_futures_not_bd():
    r = score("Crypto Rich Indonesia", "Spot Market/Crypto Updates. Degen, Shitcoin @a chat",
              "cryptorichindonesia", ["@a"], 4389)
    assert r["trading_score"] < 45 and r["category"] != "potential_bd"


def test_futures_channel_still_high():
    r = score("VIP Futures Signals", "Daily futures signals, leverage x20, TP/SL. VIP chat t.me/+abc @admin",
              "vipfutures", ["@admin"], 20000)
    assert r["trading_score"] >= 80


def test_futures_channel_mentioning_one_giveaway_kept():
    r = score("BTC Futures Calls", "Futures signals with leverage, weekly giveaway @admin",
              "btcfut", ["@admin"], 5000)
    assert r["category"] != "airdrop_bonus" and r["trading_score"] >= 45
