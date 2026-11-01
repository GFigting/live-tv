"""源清单解析：兼容 M3U(#EXTINF/#EXTGRP) 与常见 TXT(名称,地址) 两种格式。"""

from __future__ import annotations

import re

from .constants import (
    ATTR_GROUP_TITLE,
    ATTR_TVG_ID,
    ATTR_TVG_LOGO,
    ATTR_TVG_NAME,
    M3U8_EXTGRP,
    M3U8_EXTINF,
    M3U_EXTM3U,
    PlaylistFormat,
    TXT_GENRE,
    UNKNOWN_GROUP,
)
from .models import Channel

# EXTINF 行内形如 key="value" 的属性
_ATTRIBUTE_PATTERN = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')
# 按位置逐个消费属性，允许属性之间有空白，属性值内部逗号不作为分隔符
_ATTRIBUTE_PREFIX = re.compile(r'\s*([A-Za-z0-9_-]+)="([^"]*)"')
# EXTINF 的类型与时长前缀（如 ":-1"）
_EXTINF_PREFIX = re.compile(r"\s*:[-+]?[0-9.]+\s*")
# TXT 行内多个地址用 # 分隔
_URL_SEPARATOR = "#"


def detect_format(text: str) -> PlaylistFormat:
    """根据标记行判断清单格式。"""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(M3U_EXTM3U) or stripped.startswith(M3U8_EXTINF):
            return PlaylistFormat.M3U
    return PlaylistFormat.TXT


def parse_playlist(text: str, source: str | None = None) -> list[Channel]:
    """按检测到的格式解析清单。"""
    if detect_format(text) is PlaylistFormat.M3U:
        return parse_m3u(text, source)
    return parse_txt(text, source)


def parse_m3u(text: str, source: str | None = None) -> list[Channel]:
    """解析 M3U/M3U8 清单。"""
    channels: list[Channel] = []
    pending: dict[str, str] | None = None
    pending_name: str = ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(M3U8_EXTINF):
            pending = dict(_ATTRIBUTE_PATTERN.findall(line))
            pending_name = _name_from_extinf(line, pending)
            continue
        if line.startswith(M3U8_EXTGRP):
            if pending is not None:
                pending[ATTR_GROUP_TITLE] = line.split(":", 1)[-1].strip()
            continue
        if line.startswith("#"):
            continue
        if pending is None:
            continue
        channels.append(
            Channel(
                name=pending_name or pending.get(ATTR_TVG_NAME, "") or line,
                url=line,
                group=pending.get(ATTR_GROUP_TITLE) or UNKNOWN_GROUP,
                logo=pending.get(ATTR_TVG_LOGO) or None,
                tvg_id=pending.get(ATTR_TVG_ID) or None,
                source=source,
            )
        )
        pending = None
        pending_name = ""

    return channels


def parse_txt(text: str, source: str | None = None) -> list[Channel]:
    """解析 TXT 清单：`#genre#` 行切换分组，`名称,地址` 行为频道。"""
    channels: list[Channel] = []
    group = UNKNOWN_GROUP

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue
        if "," not in line:
            continue
        name, _, urls = line.partition(",")
        name = name.strip()
        urls = urls.strip()
        if not name:
            continue
        if urls == TXT_GENRE:
            group = name
            continue
        candidates = [item.strip() for item in urls.split(_URL_SEPARATOR) if item.strip()]
        for url in candidates:
            if not url.lower().startswith(("http://", "https://", "rtmp://", "rtsp://")):
                continue
            channels.append(Channel(name=name, url=url, group=group, source=source))

    return channels


def _name_from_extinf(line: str, attributes: dict[str, str]) -> str:
    """EXTINF 行逗号后的显示名，缺失时回退到 tvg-name。"""
    rest = _EXTINF_PREFIX.sub("", line[len(M3U8_EXTINF) :], count=1)
    position = 0
    while True:
        matched = _ATTRIBUTE_PREFIX.match(rest, position)
        if matched is None:
            break
        position = matched.end()
    tail = rest[position:].lstrip()
    if tail.startswith(","):
        tail = tail[1:]
    tail = tail.strip()
    if tail:
        return tail
    return attributes.get(ATTR_TVG_NAME, "").strip()
