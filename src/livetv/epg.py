"""XMLTV 节目单解析与 tvg-id 回填：用 EPG 频道表补齐源清单缺失的 tvg-id。"""

from __future__ import annotations

import gzip
import io
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .constants import (
    GZIP_MAGIC,
    XMLTV_CHANNEL_TAG,
    XMLTV_DISPLAY_NAME_TAG,
    XMLTV_ID_ATTR,
)
from .models import ChannelGroup
from .normalize import normalize_name


@dataclass(slots=True)
class EpgChannel:
    """XMLTV 频道表中的一条频道记录。"""

    channel_id: str
    names: list[str] = field(default_factory=list)


def parse_xmltv(data: bytes) -> list[EpgChannel]:
    """解析 XMLTV 的 `<channel>` 节点，自动识别 gzip 压缩内容。"""
    if data.startswith(GZIP_MAGIC):
        data = gzip.decompress(data)

    channels: list[EpgChannel] = []
    for _, element in ET.iterparse(io.BytesIO(data), events=("end",)):
        if element.tag != XMLTV_CHANNEL_TAG:
            continue
        channel_id = (element.get(XMLTV_ID_ATTR) or "").strip()
        names = [
            (child.text or "").strip()
            for child in element
            if child.tag == XMLTV_DISPLAY_NAME_TAG
        ]
        if channel_id and names:
            channels.append(
                EpgChannel(channel_id=channel_id, names=[name for name in names if name])
            )
        element.clear()
    return channels


def build_index(channels: list[EpgChannel]) -> dict[str, str]:
    """归一化台名 → EPG 频道 ID，同一台名只保留最先出现的记录。"""
    index: dict[str, str] = {}
    for channel in channels:
        for name in channel.names:
            key = normalize_name(name)
            if key and key not in index:
                index[key] = channel.channel_id
    return index


def apply_tvg_ids(groups: list[ChannelGroup], index: dict[str, str]) -> int:
    """给缺失 tvg-id 的频道回填 EPG 频道 ID，返回回填数量。"""
    matched = 0
    for group in groups:
        if group.tvg_id:
            continue
        channel_id = index.get(group.key)
        if channel_id:
            group.tvg_id = channel_id
            matched += 1
    return matched
