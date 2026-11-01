"""EPG 用例：XMLTV 解析、台名索引与 tvg-id 回填。"""

import gzip

from livetv.epg import apply_tvg_ids, build_index, parse_xmltv
from livetv.models import ChannelGroup

XMLTV = """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="CCTV1.cn">
    <display-name>CCTV-1</display-name>
    <display-name>CCTV1 综合</display-name>
  </channel>
  <channel id="HNTV.cn">
    <display-name>湖南卫视</display-name>
  </channel>
  <channel id="EMPTY.cn"></channel>
</tv>
"""


def test_parse_xmltv_reads_channel_names():
    channels = parse_xmltv(XMLTV.encode("utf-8"))

    # 缺少 display-name 的频道不参与映射
    assert [item.channel_id for item in channels] == ["CCTV1.cn", "HNTV.cn"]
    assert channels[0].names == ["CCTV-1", "CCTV1 综合"]


def test_parse_xmltv_accepts_gzip():
    channels = parse_xmltv(gzip.compress(XMLTV.encode("utf-8")))

    assert channels[0].channel_id == "CCTV1.cn"
    assert len(channels) == 2


def test_apply_tvg_ids_only_fills_missing():
    index = build_index(parse_xmltv(XMLTV.encode("utf-8")))
    filled = ChannelGroup(key="cctv1", name="CCTV1", group="央视")
    existing = ChannelGroup(key="hn", name="湖南卫视", group="卫视", tvg_id="source-id")
    missing = ChannelGroup(key="未知频道", name="未知频道")

    matched = apply_tvg_ids([filled, existing, missing], index)

    assert matched == 1
    assert filled.tvg_id == "CCTV1.cn"
    assert existing.tvg_id == "source-id"
    assert missing.tvg_id is None
