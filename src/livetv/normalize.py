"""频道归一化与去重：把同一频道在不同源的多种写法收敛为一个分组。"""

from __future__ import annotations

import re
import unicodedata

from .constants import BRACKET_PAIRS, GROUP_LABELS, QUALITY_SUFFIXES, UNKNOWN_GROUP
from .grouping import classify_group, split_group_tags
from .models import Channel, ChannelGroup

# 只剔除分隔类字符，保留 + / & 等有语义的符号（如 CCTV5+ 与 CCTV5 不应合并）
_SEPARATORS = re.compile(r"[ \t\u00b7\u30fb\-_\u2014\u2013.,:;、，。]")


def normalize_name(name: str) -> str:
    """把频道名归一化为可比较的 key。"""
    text = unicodedata.normalize("NFKC", name or "").strip().lower()
    for left, right in BRACKET_PAIRS:
        text = re.sub(f"{re.escape(left)}.*?{re.escape(right)}", "", text)
    changed = True
    while changed:
        changed = False
        for suffix in QUALITY_SUFFIXES:
            if text.endswith(suffix):
                text = text[: -len(suffix)]
                changed = True
    text = _SEPARATORS.sub("", text)
    return text.strip()


def localize_group(group: str | None) -> str:
    """把上游英文分类统一为中文分组名，未收录或已是中文时回退原值。"""
    text = unicodedata.normalize("NFKC", group or "").strip()
    if not text:
        return UNKNOWN_GROUP
    return GROUP_LABELS.get(text.casefold(), text)


def merge_channels(channels: list[Channel]) -> list[ChannelGroup]:
    """按归一化名称合并频道，同一条 URL 只保留一次。"""
    groups: dict[str, ChannelGroup] = {}

    for channel in channels:
        key = normalize_name(channel.name)
        if not key:
            key = normalize_name(channel.url)
        tags = split_group_tags(channel.group)
        group = groups.get(key)
        if group is None:
            group = ChannelGroup(
                key=key,
                name=channel.name.strip(),
                logo=channel.logo,
                tvg_id=channel.tvg_id,
                group_tags=list(tags),
            )
            groups[key] = group
        else:
            for tag in tags:
                if tag not in group.group_tags:
                    group.group_tags.append(tag)
            if not group.logo and channel.logo:
                group.logo = channel.logo
            if not group.tvg_id and channel.tvg_id:
                group.tvg_id = channel.tvg_id

        if channel.url not in {item.url for item in group.channels}:
            group.channels.append(channel)

    for group in groups.values():
        group.group = classify_group(group.group_tags, group.name)

    return list(groups.values())


def is_blacklisted(url: str, domains: list[str], keywords: list[str]) -> bool:
    """按域名与关键词黑名单过滤直播源。"""
    lowered = url.lower()
    if any(domain.lower() in lowered for domain in domains):
        return True
    return any(keyword.lower() in lowered for keyword in keywords)
