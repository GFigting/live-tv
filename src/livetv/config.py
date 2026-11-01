"""配置加载：把 sources.yaml 解析为强类型配置，默认值统一取自 constants。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .constants import (
    DEFAULT_CONCURRENCY,
    DEFAULT_EPG_MAP,
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_PER_CHANNEL,
    DEFAULT_MIN_FLUENCY,
    DEFAULT_OUTPUT_M3U,
    DEFAULT_OUTPUT_REPORT,
    DEFAULT_SPEED_SECONDS,
    DEFAULT_TIMEOUT,
)
from .errors import ConfigError


@dataclass(slots=True)
class SourceSpec:
    """一条源清单：远程 URL 或本地文件。"""

    name: str
    location: str
    is_local: bool = False
    group: str | None = None
    enabled: bool = True


@dataclass(slots=True)
class AppConfig:
    """一次运行所需的全部配置。"""

    sources: list[SourceSpec] = field(default_factory=list)
    output_m3u: str = DEFAULT_OUTPUT_M3U
    output_report: str = DEFAULT_OUTPUT_REPORT
    concurrency: int = DEFAULT_CONCURRENCY
    timeout: float = DEFAULT_TIMEOUT
    speed_seconds: float = DEFAULT_SPEED_SECONDS
    max_bytes: int = DEFAULT_MAX_BYTES
    min_fluency: float = DEFAULT_MIN_FLUENCY
    max_per_channel: int = DEFAULT_MAX_PER_CHANNEL
    epg_urls: list[str] = field(default_factory=list)
    epg_map: bool = DEFAULT_EPG_MAP
    split_by_family: bool = False
    blacklist_domains: list[str] = field(default_factory=list)
    blacklist_keywords: list[str] = field(default_factory=list)
    base_dir: Path = Path(".")
    config_path: str = ""


def load_config(config_path: str | Path) -> AppConfig:
    """读取 sources.yaml，缺失或结构错误时给出明确异常。"""
    path = Path(config_path)
    if not path.is_file():
        raise ConfigError(f"配置文件不存在：{path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ConfigError("配置文件根节点必须是映射")

    check = data.get("check") or {}
    output = data.get("output") or {}
    epg = data.get("epg") or {}
    blacklist = data.get("blacklist") or {}
    raw_sources = data.get("sources") or []
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ConfigError("配置中缺少 sources 列表")

    sources: list[SourceSpec] = []
    for index, item in enumerate(raw_sources):
        if not isinstance(item, dict):
            raise ConfigError(f"sources[{index}] 必须是映射")
        url = item.get("url")
        local = item.get("path")
        if not url and not local:
            raise ConfigError(f"sources[{index}] 必须提供 url 或 path")
        sources.append(
            SourceSpec(
                name=str(item.get("name") or f"source-{index + 1}"),
                location=str(url or local),
                is_local=bool(local) and not url,
                group=item.get("group"),
                enabled=bool(item.get("enabled", True)),
            )
        )

    return AppConfig(
        sources=sources,
        output_m3u=str(output.get("m3u", DEFAULT_OUTPUT_M3U)),
        output_report=str(output.get("report", DEFAULT_OUTPUT_REPORT)),
        concurrency=_positive_int(check.get("concurrency"), DEFAULT_CONCURRENCY),
        timeout=_positive_float(check.get("timeout"), DEFAULT_TIMEOUT),
        speed_seconds=_positive_float(check.get("speed_seconds"), DEFAULT_SPEED_SECONDS),
        max_bytes=_positive_int(check.get("max_bytes"), DEFAULT_MAX_BYTES),
        min_fluency=_non_negative_float(check.get("min_fluency"), DEFAULT_MIN_FLUENCY),
        max_per_channel=_positive_int(check.get("max_per_channel"), DEFAULT_MAX_PER_CHANNEL),
        epg_urls=[str(item) for item in (epg.get("urls") or [])],
        epg_map=bool(epg.get("map", DEFAULT_EPG_MAP)),
        split_by_family=bool(output.get("split_by_family", False)),
        blacklist_domains=[str(item) for item in (blacklist.get("domains") or [])],
        blacklist_keywords=[str(item) for item in (blacklist.get("keywords") or [])],
        base_dir=path.resolve().parent,
        config_path=str(path.resolve()),
    )


def _positive_int(value: object, fallback: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _positive_float(value: object, fallback: float) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _non_negative_float(value: object, fallback: float) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed >= 0 else fallback
