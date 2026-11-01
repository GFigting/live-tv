"""固定顶层分组：把上游分类、国家名与台别收敛为固定大类，原始分类另存为标签。"""

from __future__ import annotations

import re
import unicodedata

from .constants import (
    GROUP_CCTV,
    GROUP_CCTV_HINTS,
    GROUP_FOREIGN_HINTS,
    GROUP_HKMO_TW,
    GROUP_HKMO_TW_HINTS,
    GROUP_INTERNATIONAL,
    GROUP_LABELS,
    GROUP_LOCAL,
    GROUP_LOCAL_HINTS,
    GROUP_OTHER,
    GROUP_SATELLITE,
    GROUP_SATELLITE_HINT,
    GROUP_TYPE_HINTS,
    UNKNOWN_GROUP,
)

_CJK = re.compile(r"[\u4e00-\u9fff]")
_TAG_SEPARATORS = re.compile(r"[;；]")
# 上游分组常带 emoji 或装饰字符，剥离前缀只保留文字
_TAG_PREFIX_NOISE = re.compile(r"^[^\w\u4e00-\u9fff]+")


def split_group_tags(group: str | None) -> list[str]:
    """把 group-title 拆成独立标签并中文化，复合分类不再拼成一个字符串。"""
    text = unicodedata.normalize("NFKC", group or "").strip()
    if not text:
        return []
    tags: list[str] = []
    for part in _TAG_SEPARATORS.split(text):
        label = _localize(part)
        if not label or label == UNKNOWN_GROUP or label in tags:
            continue
        tags.append(label)
    return tags


def classify_group(tags: list[str], name: str) -> str:
    """按台名特征与原始分类归类，顺序即优先级。"""
    channel = unicodedata.normalize("NFKC", name or "").casefold()
    labels = " ".join(tags).casefold()

    if _hit(channel, GROUP_CCTV_HINTS) or _hit(labels, GROUP_CCTV_HINTS):
        return GROUP_CCTV
    if _hit(channel, GROUP_HKMO_TW_HINTS) or _hit(labels, GROUP_HKMO_TW_HINTS):
        return GROUP_HKMO_TW
    if GROUP_SATELLITE_HINT in channel or GROUP_SATELLITE_HINT in labels:
        return GROUP_SATELLITE
    if _hit(channel, GROUP_LOCAL_HINTS) or _hit(labels, GROUP_LOCAL_HINTS):
        return GROUP_LOCAL
    for category, hints in GROUP_TYPE_HINTS:
        if _hit(labels, hints) or _hit(channel, hints):
            return category
    if _is_foreign(tags):
        return GROUP_INTERNATIONAL
    return GROUP_OTHER


def _localize(part: str) -> str:
    text = _TAG_PREFIX_NOISE.sub("", part.strip()).strip()
    if not text:
        return ""
    return GROUP_LABELS.get(text.casefold(), text)


def _hit(text: str, hints: tuple[str, ...]) -> bool:
    return any(hint in text for hint in hints)


def _is_foreign(tags: list[str]) -> bool:
    """原始分类既不含中文也无已知内容类型时，视为地区/国家分组。"""
    if not tags:
        return False
    if any(_hit(tag.casefold(), GROUP_FOREIGN_HINTS) for tag in tags):
        return True
    return all(_CJK.search(tag) is None for tag in tags)
