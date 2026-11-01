"""解析层用例：M3U 属性、TXT 分组与格式识别。"""

from livetv.constants import UNKNOWN_GROUP
from livetv.parser import detect_format, parse_m3u, parse_playlist, parse_txt

M3U_SAMPLE = """#EXTM3U
#EXTINF:-1 tvg-id="cctv1" tvg-name="CCTV1" tvg-logo="http://logo/cctv1.png" group-title="央视",CCTV1 高清
http://example.com/cctv1.m3u8
#EXTINF:-1,湖南卫视
#EXTGRP:卫视
http://example.com/hunan.m3u8
"""

TXT_SAMPLE = """央视,#genre#
CCTV1,http://example.com/cctv1.m3u8
CCTV5,http://a.example.com/cctv5.m3u8#http://b.example.com/cctv5.m3u8
卫视,#genre#
湖南卫视,http://example.com/hunan.m3u8
"""


def test_parse_m3u_reads_attributes_and_group():
    channels = parse_m3u(M3U_SAMPLE, "demo")
    first, second = channels
    assert first.name == "CCTV1 高清"
    assert first.url == "http://example.com/cctv1.m3u8"
    assert first.group == "央视"
    assert first.logo == "http://logo/cctv1.png"
    assert first.tvg_id == "cctv1"
    assert first.source == "demo"
    assert second.group == "卫视"


def test_parse_m3u_without_attributes_falls_back():
    text = "#EXTM3U\n#EXTINF:-1,无属性频道\nhttp://example.com/a.m3u8\n"
    channel = parse_m3u(text)[0]
    assert channel.name == "无属性频道"
    assert channel.group == UNKNOWN_GROUP
    assert channel.logo is None


def test_extinf_name_ignores_commas_inside_attribute_values():
    text = (
        "#EXTM3U\n"
        '#EXTINF:-1 tvg-id="NeiMonggolTV.cn@SD" '
        'http-user-agent="Mozilla/5.0 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36" '
        'group-title="Undefined",Nei Monggol TV (960p)\n'
        "http://example.com/nei.m3u8\n"
    )
    channel = parse_m3u(text)[0]
    assert channel.name == "Nei Monggol TV (960p)"
    assert channel.group == "Undefined"
    assert channel.tvg_id == "NeiMonggolTV.cn@SD"


def test_parse_txt_supports_genre_and_multiple_urls():
    channels = parse_txt(TXT_SAMPLE, "demo")
    assert [channel.name for channel in channels] == ["CCTV1", "CCTV5", "CCTV5", "湖南卫视"]
    assert channels[0].group == "央视"
    assert channels[1].group == "央视"
    assert channels[2].url == "http://b.example.com/cctv5.m3u8"
    assert channels[3].group == "卫视"


def test_parse_playlist_detects_format():
    assert detect_format(M3U_SAMPLE).value == "m3u"
    assert detect_format(TXT_SAMPLE).value == "txt"
    assert len(parse_playlist(M3U_SAMPLE)) == 2
    assert len(parse_playlist(TXT_SAMPLE)) == 4
