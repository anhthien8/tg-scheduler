"""Unit tests for forum_link.parse_forum_link — pure, offline."""
import pytest

from forum_link import parse_forum_link


def test_c_link_with_topic():
    r = parse_forum_link("https://t.me/c/4352266125/5")
    assert r.group_id == -1004352266125
    assert r.general_topic_id == 5
    assert r.needs_network is False


def test_c_link_without_topic_defaults_to_general():
    r = parse_forum_link("https://t.me/c/4352266125")
    assert r.group_id == -1004352266125
    assert r.general_topic_id == 1


def test_c_link_bare_host_no_scheme():
    r = parse_forum_link("t.me/c/1234567890/12")
    assert r.group_id == -1001234567890
    assert r.general_topic_id == 12


def test_privatepost_tg_scheme():
    r = parse_forum_link("tg://privatepost?channel=4352266125&post=7")
    assert r.group_id == -1004352266125
    assert r.general_topic_id == 7


def test_bare_negative_supergroup_id():
    r = parse_forum_link("-1004352266125")
    assert r.group_id == -1004352266125


def test_bare_positive_internal_id_gets_prefix():
    r = parse_forum_link("4352266125")
    assert r.group_id == -1004352266125


def test_username_link_needs_network():
    r = parse_forum_link("https://t.me/weex_official")
    assert r.needs_network is True
    assert r.username == "weex_official"


def test_username_link_with_topic():
    r = parse_forum_link("https://t.me/weex_official/15")
    assert r.needs_network is True
    assert r.username == "weex_official"
    assert r.general_topic_id == 15


def test_invite_link_needs_network():
    r = parse_forum_link("https://t.me/+AbCdEf123xyz")
    assert r.needs_network is True
    assert r.invite_hash == "AbCdEf123xyz"


def test_joinchat_link_needs_network():
    r = parse_forum_link("https://t.me/joinchat/AbCdEf123xyz")
    assert r.needs_network is True
    assert r.invite_hash == "AbCdEf123xyz"


def test_non_telegram_host_rejected():
    with pytest.raises(ValueError):
        parse_forum_link("https://example.com/foo")


def test_empty_rejected():
    with pytest.raises(ValueError):
        parse_forum_link("")


def test_garbage_rejected():
    with pytest.raises(ValueError):
        parse_forum_link("not a link at all !!!")
