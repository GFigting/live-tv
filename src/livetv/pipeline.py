"""流水线编排：加载源 → 解析 → 合并去重 → 检测 → 过滤 → 输出 M3U 与报告。"""

from __future__ import annotations

import asyncio
import json
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import httpx

from .checker import CheckSettings, check_channel, is_http_url
from .config import AppConfig, SourceSpec
from .constants import (
    ATTR_GROUP_TAGS,
    ATTR_GROUP_TITLE,
    ATTR_TVG_ID,
    ATTR_TVG_LOGO,
    ATTR_TVG_NAME,
    ATTR_XTVG_URL,
    CheckStatus,
    GROUP_CATEGORY_ORDER,
    GROUP_TAG_SEPARATOR,
    IP_FAMILY_IPV4,
    IP_FAMILY_IPV6,
    M3U_EPG_URL_SEPARATOR,
    M3U_EXTM3U,
    M3U8_EXTINF,
    PipelineStage,
    UNKNOWN_GROUP,
)
from .epg import EpgChannel, apply_tvg_ids, build_index, parse_xmltv
from .errors import SourceError
from .models import (
    Channel,
    ChannelResult,
    CheckResult,
    EpgSourceReport,
    RunReport,
    SourceReport,
)
from .normalize import is_blacklisted, merge_channels
from .parser import parse_playlist

TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"

# 进度回调：阶段、已完成数量、总数量；不传时流水线行为保持不变
ProgressCallback = Callable[[PipelineStage, int, int], None]


def local_now() -> str:
    """本地墙钟时间（不带时区），与项目其它产物的时间口径一致。"""
    return datetime.now().strftime(TIME_FORMAT)


def _emit(progress: ProgressCallback | None, stage: PipelineStage, done: int, total: int) -> None:
    if progress is not None:
        progress(stage, done, total)


async def run_pipeline(
    config: AppConfig,
    *,
    limit: int | None = None,
    check: bool = True,
    progress: ProgressCallback | None = None,
) -> RunReport:
    """执行一次完整流水线并写出产物。"""
    report = RunReport(
        generated_at=local_now(),
        config_path=config.config_path,
        output_m3u=config.output_m3u,
        output_report=config.output_report,
        speed_test=check,
        epg_urls=list(config.epg_urls),
    )
    settings = CheckSettings(
        timeout=config.timeout,
        speed_seconds=config.speed_seconds,
        max_bytes=config.max_bytes,
    )

    async with httpx.AsyncClient(follow_redirects=True) as client:
        channels, source_reports = await _load_sources(client, config, progress)
        report.sources = source_reports
        report.parsed_channels = len(channels)

        groups = merge_channels(channels)
        report.unique_channels = len(groups)

        if limit:
            groups = groups[:limit]

        groups = _apply_blacklist(groups, config)

        if config.epg_map and config.epg_urls:
            epg_sources, epg_index = await _load_epg(client, config)
            report.epg_mapping = True
            report.epg_sources = epg_sources
            report.epg_matched = apply_tvg_ids(groups, epg_index)

        results: list[ChannelResult] = []
        if check:
            semaphore = asyncio.Semaphore(config.concurrency)
            results = await _check_groups(client, groups, semaphore, settings, progress)
        else:
            results = [
                ChannelResult(
                    group=group,
                    results=[
                        CheckResult(url=channel.url, status=CheckStatus.SKIPPED, reason="未检测")
                        for channel in group.channels
                    ],
                )
                for group in groups
            ]
        report.channel_results = results

    selected = _select_output(report.channel_results, config, check)
    report.output_channels = len(selected)

    _emit(progress, PipelineStage.OUTPUT, 0, 1)
    m3u_path, report_path = _write_outputs(config, selected, report)
    report.output_m3u = str(m3u_path)
    report.output_report = str(report_path)
    _emit(progress, PipelineStage.DONE, 1, 1)
    return report


async def _load_sources(
    client: httpx.AsyncClient,
    config: AppConfig,
    progress: ProgressCallback | None = None,
) -> tuple[list[Channel], list[SourceReport]]:
    channels: list[Channel] = []
    reports: list[SourceReport] = []
    total = sum(1 for spec in config.sources if spec.enabled)

    for spec in config.sources:
        if not spec.enabled:
            continue
        item = SourceReport(name=spec.name, location=spec.location)
        try:
            text = await _read_source(client, spec, config)
            parsed = parse_playlist(text, spec.name)
            # 上游清单存在 "/[NO PUBLIC STREAM]" 之类的占位地址，请求前先剔除
            parsed = [channel for channel in parsed if is_http_url(channel.url)]
            for channel in parsed:
                if spec.group and (not channel.group or channel.group == UNKNOWN_GROUP):
                    channel.group = spec.group
            item.parsed = len(parsed)
            channels.extend(parsed)
        except SourceError as exc:
            item.error = str(exc)
        finally:
            reports.append(item)
            _emit(progress, PipelineStage.SOURCES, len(reports), total)

    return channels, reports


async def _read_source(client: httpx.AsyncClient, spec: SourceSpec, config: AppConfig) -> str:
    if spec.is_local:
        path = Path(spec.location)
        if not path.is_absolute():
            path = config.base_dir / path
        if not path.is_file():
            raise SourceError(f"本地源不存在：{path}")
        return path.read_text(encoding="utf-8")
    try:
        response = await client.get(spec.location, timeout=config.timeout)
    except httpx.HTTPError as exc:
        raise SourceError(f"下载失败：{exc}") from exc
    if response.status_code >= 400:
        raise SourceError(f"下载失败：HTTP {response.status_code}")
    return response.text


async def _load_epg(
    client: httpx.AsyncClient, config: AppConfig
) -> tuple[list[EpgSourceReport], dict[str, str]]:
    """读取 EPG 源并建立台名索引；单个源失败只记录错误，不影响其余流程。"""
    reports: list[EpgSourceReport] = []
    channels: list[EpgChannel] = []
    for location in config.epg_urls:
        item = EpgSourceReport(location=location)
        try:
            parsed = parse_xmltv(await _read_epg(client, location, config))
        except (SourceError, httpx.HTTPError, OSError, ET.ParseError) as exc:
            item.error = str(exc) or exc.__class__.__name__
            reports.append(item)
            continue
        item.channels = len(parsed)
        channels.extend(parsed)
        reports.append(item)
    return reports, build_index(channels)


async def _read_epg(client: httpx.AsyncClient, location: str, config: AppConfig) -> bytes:
    """远程地址下载，其余按本地文件读取（相对配置文件目录解析）。"""
    if location.lower().startswith(("http://", "https://")):
        try:
            response = await client.get(location, timeout=config.timeout)
        except httpx.HTTPError as exc:
            raise SourceError(f"EPG 下载失败：{exc}") from exc
        if response.status_code >= 400:
            raise SourceError(f"EPG 下载失败：HTTP {response.status_code}")
        return response.content
    path = Path(location)
    if not path.is_absolute():
        path = config.base_dir / path
    return path.read_bytes()


async def _check_groups(
    client: httpx.AsyncClient,
    groups: list,
    semaphore: asyncio.Semaphore,
    settings: CheckSettings,
    progress: ProgressCallback | None = None,
) -> list[ChannelResult]:
    total = sum(len(group.channels) for group in groups)
    done = 0

    async def check_one(channel: Channel) -> CheckResult:
        nonlocal done
        async with semaphore:
            result = await check_channel(client, channel, settings)
        done += 1
        _emit(progress, PipelineStage.CHECK, done, total)
        return result

    tasks = [
        asyncio.gather(*(check_one(channel) for channel in group.channels))
        for group in groups
    ]
    checked = await asyncio.gather(*tasks)
    return [
        ChannelResult(group=group, results=list(results))
        for group, results in zip(groups, checked, strict=True)
    ]


def _apply_blacklist(groups: list, config: AppConfig) -> list:
    filtered = []
    for group in groups:
        kept = [
            channel
            for channel in group.channels
            if not is_blacklisted(
                channel.url, config.blacklist_domains, config.blacklist_keywords
            )
        ]
        if kept:
            group.channels = kept
            filtered.append(group)
    return filtered


def _select_output(
    results: list[ChannelResult], config: AppConfig, check: bool
) -> list[ChannelResult]:
    if not check:
        return [result for result in results if result.group.channels]
    selected = []
    for result in results:
        best = result.best()
        if best is None:
            continue
        if best.fluency < config.min_fluency:
            continue
        selected.append(result)
    return selected


def _write_outputs(
    config: AppConfig, selected: list[ChannelResult], report: RunReport
) -> tuple[Path, Path]:
    m3u_path = resolve_output_path(config.output_m3u)
    report_path = resolve_output_path(config.output_report)
    m3u_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    report.output_m3u = str(m3u_path)
    report.output_report = str(report_path)
    header_epg_urls = _header_epg_urls(report.epg_urls)
    m3u_path.write_text(
        render_m3u(selected, config.max_per_channel, header_epg_urls), encoding="utf-8"
    )
    if config.split_by_family and report.speed_test:
        for family in (IP_FAMILY_IPV4, IP_FAMILY_IPV6):
            family_path = _family_path(m3u_path, family)
            family_path.write_text(
                render_m3u(
                    _select_family(selected, family),
                    config.max_per_channel,
                    header_epg_urls,
                ),
                encoding="utf-8",
            )
            if family == IP_FAMILY_IPV4:
                report.output_ipv4 = str(family_path)
            else:
                report.output_ipv6 = str(family_path)
    report_path.write_text(
        json.dumps(build_report_dict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return m3u_path, report_path


def _header_epg_urls(urls: list[str]) -> list[str]:
    """只有远程地址能写进 x-tvg-url；本地 EPG 文件仅用于台名映射。"""
    return [url for url in urls if url.lower().startswith(("http://", "https://"))]


def _family_path(m3u_path: Path, family: str) -> Path:
    """按协议族派生输出路径，如 output/live-ipv4.m3u。"""
    return m3u_path.with_name(f"{m3u_path.stem}-{family}.m3u")


def _select_family(results: list[ChannelResult], family: str) -> list[ChannelResult]:
    """保留最优结果实际走该协议族的频道。"""
    return [
        item
        for item in results
        if item.best() is not None and item.best().ip_family == family
    ]


def resolve_output_path(value: str) -> Path:
    """输出路径相对当前工作目录解析，与运行位置保持一致。"""
    path = Path(value)
    return path if path.is_absolute() else Path.cwd() / path


def render_m3u(
    results: list[ChannelResult], max_per_channel: int, epg_urls: list[str] | None = None
) -> str:
    """渲染 M3U：按固定大类顺序排序，每频道最多输出 max_per_channel 条 URL。"""
    ordered = sorted(results, key=_sort_key)
    header = M3U_EXTM3U
    if epg_urls:
        joined = M3U_EPG_URL_SEPARATOR.join(epg_urls)
        header = f'{header} {ATTR_XTVG_URL}="{_escape(joined)}"'
    lines = [header]
    for item in ordered:
        best = item.best()
        if best is not None:
            candidates = [best, *item.alternatives(max_per_channel)]
        else:
            # 未检测模式没有「最优」概念，按解析顺序输出前若干条
            candidates = item.results[:max_per_channel]
        for candidate in candidates:
            lines.append(_extinf_line(item.group))
            lines.append(candidate.url)
    return "\n".join(lines) + "\n"


_CATEGORY_RANK = {category: index for index, category in enumerate(GROUP_CATEGORY_ORDER)}


def _sort_key(item: ChannelResult) -> tuple[int, str]:
    """固定大类按声明顺序排列，未知取值排在最后。"""
    return (_CATEGORY_RANK.get(item.group.group, len(GROUP_CATEGORY_ORDER)), item.group.name)


def _extinf_line(group) -> str:
    attributes = {
        ATTR_TVG_ID: group.tvg_id or "",
        ATTR_TVG_NAME: group.name,
        ATTR_TVG_LOGO: group.logo or "",
        ATTR_GROUP_TITLE: group.group or UNKNOWN_GROUP,
    }
    if group.group_tags:
        attributes[ATTR_GROUP_TAGS] = GROUP_TAG_SEPARATOR.join(group.group_tags)
    rendered = " ".join(f'{key}="{_escape(value)}"' for key, value in attributes.items())
    return f'{M3U8_EXTINF}:-1 {rendered},{group.name}'


def _escape(value: str) -> str:
    return value.replace('"', "'")


def _epg_dict(report: RunReport) -> dict:
    return {
        "mapping": report.epg_mapping,
        "channels": report.epg_channels,
        "matched": report.epg_matched,
        "sources": [
            {
                "location": source.location,
                "channels": source.channels,
                "error": source.error,
            }
            for source in report.epg_sources
        ],
    }


def _outputs_dict(report: RunReport) -> dict:
    outputs = {"m3u": report.output_m3u, "report": report.output_report}
    if report.output_ipv4:
        outputs[IP_FAMILY_IPV4] = report.output_ipv4
    if report.output_ipv6:
        outputs[IP_FAMILY_IPV6] = report.output_ipv6
    return outputs


def channel_payload(item: ChannelResult) -> dict:
    """单个频道的报告字段；首次报告与页面复测共用同一口径。"""
    best = item.best()
    return {
        "name": item.group.name,
        "key": item.group.key,
        "group": item.group.group,
        "group_tags": item.group.group_tags,
        "logo": item.group.logo,
        "tvg_id": item.group.tvg_id,
        "best_url": best.url if best else None,
        "fluency": best.fluency if best else 0.0,
        "latency_ms": best.latency_ms if best else None,
        "playlist_latency_ms": best.playlist_latency_ms if best else None,
        "segment_latency_ms": best.segment_latency_ms if best else None,
        "bitrate": round(best.bitrate) if best and best.bitrate else None,
        "ip_family": best.ip_family if best else None,
        "urls": [
            {
                "url": result.url,
                "status": result.status.value,
                "fluency": result.fluency,
                "latency_ms": result.latency_ms,
                "playlist_latency_ms": result.playlist_latency_ms,
                "segment_latency_ms": result.segment_latency_ms,
                "ip_family": result.ip_family,
                "bitrate": round(result.bitrate) if result.bitrate else None,
                "reason": result.reason,
            }
            for result in item.results
        ],
    }


def build_report_dict(report: RunReport) -> dict:
    """把运行报告转为可序列化字典。"""
    channels = []
    failures = []
    groups: dict[str, int] = {}

    for item in report.channel_results:
        best = item.best()
        payload = channel_payload(item)
        channels.append(payload)
        # 分组计数与频道表口径一致：包含未测出可用地址的频道
        groups[item.group.group] = groups.get(item.group.group, 0) + 1
        if best is None:
            if report.speed_test:
                failures.append(
                    {"name": item.group.name, "urls": [result.url for result in item.results]}
                )
            continue

    return {
        "generated_at": report.generated_at,
        "speed_test": report.speed_test,
        "config_path": report.config_path,
        "epg_urls": report.epg_urls,
        "epg": _epg_dict(report),
        "outputs": _outputs_dict(report),
        "stats": {
            "parsed_channels": report.parsed_channels,
            "unique_channels": report.unique_channels,
            "checked_urls": report.checked_urls,
            "ok_urls": report.ok_urls,
            "output_channels": report.output_channels,
        },
        "groups": groups,
        "sources": [
            {
                "name": source.name,
                "location": source.location,
                "parsed": source.parsed,
                "error": source.error,
            }
            for source in report.sources
        ],
        "channels": channels,
        "failures": failures,
    }
