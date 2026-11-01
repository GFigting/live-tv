"""归一化与合并用例：多种写法的同名频道应收敛，且不误合并差异频道。"""

from livetv.models import Channel
from livetv.normalize import is_blacklisted, localize_group, merge_channels, normalize_name


def test_normalize_merges_spelling_variants():
    assert normalize_name("CCTV-1") == "cctv1"
    assert normalize_name("CCTV1 高清") == "cctv1"
    assert normalize_name("【央视】CCTV1") == "cctv1"
    assert normalize_name("ＣＣＴＶ１") == "cctv1"


def test_normalize_keeps_distinct_channels():
    assert normalize_name("CCTV5+") != normalize_name("CCTV5")
    assert normalize_name("湖南卫视") != normalize_name("湖南都市")


def test_merge_channels_dedupes_urls_and_keeps_multi_candidates():
    channels = [
        Channel(name="湖南卫视", url="http://a/hunan.m3u8", group="卫视"),
        Channel(name="湖南卫视 高清", url="http://b/hunan.m3u8", group="卫视"),
        Channel(name="湖南卫视", url="http://a/hunan.m3u8", group="卫视"),
    ]
    groups = merge_channels(channels)
    assert len(groups) == 1
    assert groups[0].name == "湖南卫视"
    assert len(groups[0].urls()) == 2


def test_merge_channels_keeps_first_group_and_backfills_logo():
    channels = [
        Channel(name="CCTV1", url="http://a/c1.m3u8"),
        Channel(name="CCTV1", url="http://b/c1.m3u8", group="央视", logo="http://logo/c1.png"),
    ]
    group = merge_channels(channels)[0]
    assert group.group == "央视"
    assert group.logo == "http://logo/c1.png"


def test_localize_group_maps_english_categories():
    assert localize_group("News") == "新闻"
    assert localize_group(" news ") == "新闻"
    assert localize_group("Sports") == "体育"
    assert localize_group("Undefined") == "未分组"


def test_localize_group_keeps_chinese_and_unmapped_values():
    assert localize_group("央视") == "央视"
    assert localize_group("Unknown-Cat") == "Unknown-Cat"
    assert localize_group("") == "未分组"
    assert localize_group(None) == "未分组"


def test_merge_channels_classifies_fixed_group_and_keeps_tags():
    channels = [
        Channel(name="CGTN", url="http://a/cgtn.m3u8", group="News"),
        Channel(name="CGTN", url="http://b/cgtn.m3u8", group="新闻"),
    ]
    group = merge_channels(channels)[0]
    assert group.group == "央视"
    assert group.group_tags == ["新闻"]


def test_is_blacklisted_matches_domain_and_keyword():
    assert is_blacklisted("http://ad.example.com/x.m3u8", ["ad.example.com"], [])
    assert is_blacklisted("http://cdn.example.com/adult.m3u8", [], ["adult"])
    assert not is_blacklisted("http://ok.example.com/x.m3u8", ["ad.example.com"], ["adult"])
