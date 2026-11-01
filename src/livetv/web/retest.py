"""按频道、地址或分组复测：只更新页面结果，不改写 M3U 与报告文件。"""

from __future__ import annotations

import asyncio

import httpx

from ..checker import CheckSettings, check_channel
from ..config import AppConfig
from ..constants import CheckStatus, PipelineStage, UNKNOWN_GROUP
from ..models import Channel, ChannelGroup, ChannelResult, CheckResult
from ..pipeline import ProgressCallback, channel_payload, local_now


async def retest_channels(
    config: AppConfig,
    targets: list[dict],
    url_filter: dict[str, list[str]] | None = None,
    progress: ProgressCallback | None = None,
) -> list[dict]:
    """对目标频道重新检测，按报告原有顺序返回更新后的频道字典。"""
    settings = CheckSettings(
        timeout=config.timeout,
        speed_seconds=config.speed_seconds,
        max_bytes=config.max_bytes,
    )
    semaphore = asyncio.Semaphore(max(1, config.concurrency))
    selected = [(item, _urls_of(item, url_filter)) for item in targets]
    total = sum(len(entries) for _, entries in selected)
    done = 0
    now = local_now()

    async with httpx.AsyncClient(follow_redirects=True) as client:

        async def probe(item: dict, entry: dict) -> CheckResult:
            nonlocal done
            async with semaphore:
                channel = Channel(name=str(item.get("name") or ""), url=str(entry.get("url") or ""))
                result = await check_channel(client, channel, settings)
            done += 1
            if progress is not None:
                progress(PipelineStage.CHECK, done, total)
            return result

        checked = await asyncio.gather(
            *(
                asyncio.gather(*(probe(item, entry) for entry in entries))
                for item, entries in selected
            )
        )

    updated: list[dict] = []
    for (item, entries), results in zip(selected, checked, strict=True):
        refreshed = {
            str(entry.get("url") or ""): result
            for entry, result in zip(entries, results, strict=True)
        }
        originals = [entry for entry in item.get("urls") or []]
        # 未复测的地址回退到报告旧条目，保持原有结果
        merged = [
            refreshed.get(str(entry.get("url") or "")) or entry for entry in originals
        ]
        payload = channel_payload(
            ChannelResult(group=_group_of(item), results=[_check_result(entry) for entry in merged])
        )
        payload["retested_at"] = now
        for entry in payload["urls"]:
            if entry["url"] in refreshed:
                entry["retested_at"] = now
        updated.append(payload)
    return updated


def _urls_of(item: dict, url_filter: dict[str, list[str]] | None) -> list[dict]:
    """按过滤条件取出要复测的地址；没有过滤时返回该频道全部地址。"""
    entries = [entry for entry in item.get("urls") or [] if entry.get("url")]
    if url_filter is None:
        return entries
    wanted = set(url_filter.get(str(item.get("key")), []))
    return [entry for entry in entries if entry.get("url") in wanted]


def _group_of(item: dict) -> ChannelGroup:
    return ChannelGroup(
        key=str(item.get("key") or ""),
        name=str(item.get("name") or ""),
        group=str(item.get("group") or UNKNOWN_GROUP),
        group_tags=list(item.get("group_tags") or []),
        logo=item.get("logo"),
        tvg_id=item.get("tvg_id"),
    )


def _check_result(entry: CheckResult | dict) -> CheckResult:
    """复测得到的结果直接用；未复测的地址按报告旧条目还原，保持原值。"""
    if isinstance(entry, CheckResult):
        return entry
    try:
        status = CheckStatus(entry.get("status"))
    except (TypeError, ValueError):
        status = CheckStatus.FAILED
    return CheckResult(
        url=str(entry.get("url") or ""),
        status=status,
        latency_ms=entry.get("latency_ms"),
        playlist_latency_ms=entry.get("playlist_latency_ms"),
        segment_latency_ms=entry.get("segment_latency_ms"),
        fluency=float(entry.get("fluency") or 0.0),
        bitrate=entry.get("bitrate"),
        ip_family=entry.get("ip_family"),
        reason=str(entry.get("reason") or ""),
    )
