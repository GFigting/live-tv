"""直播源检测：m3u8 头特征 + 首个切片可达性双重验证，并采样比特率。"""

from __future__ import annotations

import re
import socket
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from .constants import (
    CheckStatus,
    DEFAULT_MAX_BYTES,
    DEFAULT_SPEED_SECONDS,
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENT,
    FALLBACK_USER_AGENT,
    HEAD_BYTES,
    IP_FAMILY_IPV4,
    IP_FAMILY_IPV6,
    M3U8_HEADER,
    M3U8_STREAM_COUNT_MAX_DEPTH,
    MIN_CHECK_BYTES,
    MIN_CHECK_SECONDS,
    M3U8_STREAM_INF,
    UA_RETRY_STATUSES,
)
from .models import Channel, CheckResult
from .scorer import bitrate_from_measurement, score_from_bitrate


@dataclass(slots=True)
class CheckSettings:
    """检测参数。"""

    timeout: float = DEFAULT_TIMEOUT
    speed_seconds: float = DEFAULT_SPEED_SECONDS
    max_bytes: int = DEFAULT_MAX_BYTES
    user_agent: str = DEFAULT_USER_AGENT

    def headers(self, url: str | None = None, user_agent: str | None = None) -> dict[str, str]:
        """构造请求头：按目标站点补 Referer/Origin，可临时覆盖 UA 用于回退重试。"""
        headers = {
            "User-Agent": user_agent or self.user_agent,
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if url:
            parts = urlsplit(url)
            if parts.scheme and parts.netloc:
                site = f"{parts.scheme}://{parts.netloc}"
                headers["Referer"] = f"{site}/"
                headers["Origin"] = site
        return headers


@dataclass(slots=True)
class ProbeResult:
    """一次流探测的原始观测值。"""

    status: int | None = None
    bytes_read: int = 0
    elapsed: float = 0.0
    first_byte_delay: float | None = None
    head: bytes = b""
    error: str | None = None
    ip_family: str | None = None


def _ip_family(response: httpx.Response) -> str | None:
    """从已建立的连接读取实际使用的网络协议族。"""
    stream = response.extensions.get("network_stream")
    if stream is None:
        return None
    sock = stream.get_extra_info("socket")
    family = getattr(sock, "family", None)
    if family == socket.AF_INET6:
        return IP_FAMILY_IPV6
    if family == socket.AF_INET:
        return IP_FAMILY_IPV4
    return None


def looks_like_playlist(head: bytes) -> bool:
    """通过响应头部判断是否为 m3u8 清单。"""
    text = head.decode("utf-8", errors="ignore").lstrip("\ufeff \t\r\n")
    return text.startswith(M3U8_HEADER)


def is_http_url(url: str) -> bool:
    """上游清单可能含相对地址或占位符，只有 http/https 绝对地址才可直接请求。"""
    return (url or "").lower().startswith(("http://", "https://"))


def first_playlist_uri(text: str) -> tuple[str, bool]:
    """取清单中首个 URI，并返回该清单是否为 master（多码率索引）。"""
    is_master = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith(M3U8_STREAM_INF):
            is_master = True
            continue
        if not line or line.startswith("#"):
            continue
        return line, is_master
    return "", is_master


_BANDWIDTH = re.compile(r"BANDWIDTH=(\d+)")


def best_variant_uri(text: str) -> str:
    """master 清单按 BANDWIDTH 取最高码率子流；没有 BANDWIDTH 时回退首个 URI。"""
    best_bandwidth = -1
    best_uri = ""
    pending: int | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith(M3U8_STREAM_INF):
            match = _BANDWIDTH.search(line)
            pending = int(match.group(1)) if match else 0
            continue
        if not line or line.startswith("#"):
            continue
        bandwidth = pending if pending is not None else 0
        if bandwidth > best_bandwidth:
            best_bandwidth, best_uri = bandwidth, line
        pending = None
    return best_uri


async def resolve_segment_url(
    client: httpx.AsyncClient,
    playlist_url: str,
    text: str,
    settings: CheckSettings,
    depth: int = 0,
) -> str | None:
    """把清单中的首个 URI 解析为可下载的切片地址，master 清单选最高码率并向下钻一层。"""
    uri, is_master = first_playlist_uri(text)
    if not uri:
        return None
    if is_master and depth < M3U8_STREAM_COUNT_MAX_DEPTH:
        uri = best_variant_uri(text) or uri
    absolute = urljoin(playlist_url, uri)
    if not is_http_url(absolute):
        return None
    if not is_master or depth >= M3U8_STREAM_COUNT_MAX_DEPTH:
        return absolute
    try:
        response = await client.get(
            absolute, headers=settings.headers(absolute), timeout=settings.timeout
        )
        if response.status_code in UA_RETRY_STATUSES:
            response = await client.get(
                absolute,
                headers=settings.headers(absolute, FALLBACK_USER_AGENT),
                timeout=settings.timeout,
            )
    except (httpx.HTTPError, ValueError):
        return None
    if response.status_code >= 400:
        return None
    return await resolve_segment_url(client, absolute, response.text, settings, depth + 1)


async def probe_stream(
    client: httpx.AsyncClient,
    url: str,
    settings: CheckSettings,
    seconds: float,
    max_bytes: int,
    user_agent: str | None = None,
) -> ProbeResult:
    """流式读取一小段时间，记录状态码、首字节延迟与读取量。"""
    if not is_http_url(url):
        return ProbeResult(error=f"非法 URL：{url}")

    started = time.perf_counter()
    first_byte_at: float | None = None
    total = 0
    head = b""
    status: int | None = None

    try:
        async with client.stream(
            "GET", url, headers=settings.headers(url, user_agent), timeout=settings.timeout
        ) as response:
            status = response.status_code
            family = _ip_family(response)
            if status >= 400:
                return ProbeResult(
                    status=status,
                    elapsed=time.perf_counter() - started,
                    ip_family=family,
                )
            async for chunk in response.aiter_bytes():
                if first_byte_at is None:
                    first_byte_at = time.perf_counter()
                total += len(chunk)
                if len(head) < HEAD_BYTES:
                    head += chunk[: HEAD_BYTES - len(head)]
                if total >= max_bytes or time.perf_counter() - started >= seconds:
                    break
    except (httpx.HTTPError, ValueError) as exc:
        return ProbeResult(status=status, elapsed=time.perf_counter() - started, error=str(exc))

    delay = None if first_byte_at is None else first_byte_at - started
    return ProbeResult(
        status=status,
        bytes_read=total,
        elapsed=max(time.perf_counter() - started, 1e-3),
        first_byte_delay=delay,
        head=head,
        ip_family=family,
    )


async def probe_stream_with_fallback(
    client: httpx.AsyncClient,
    url: str,
    settings: CheckSettings,
    seconds: float,
    max_bytes: int,
) -> ProbeResult:
    """探针入口：鉴权类失败（401/403/406/451）换备用 UA 重试一次。"""
    probe = await probe_stream(client, url, settings, seconds, max_bytes)
    if probe.status in UA_RETRY_STATUSES:
        return await probe_stream(
            client, url, settings, seconds, max_bytes, FALLBACK_USER_AGENT
        )
    return probe


async def check_channel(
    client: httpx.AsyncClient,
    channel: Channel,
    settings: CheckSettings,
    speed_test: bool = True,
) -> CheckResult:
    """检测单条直播源：先探清单，再验证首个切片或直链。"""
    seconds = settings.speed_seconds if speed_test else MIN_CHECK_SECONDS
    max_bytes = settings.max_bytes if speed_test else MIN_CHECK_BYTES

    probe = await probe_stream_with_fallback(client, channel.url, settings, seconds, max_bytes)
    if probe.error:
        return CheckResult(url=channel.url, status=CheckStatus.FAILED, reason=probe.error)
    if probe.status is None or probe.status >= 400:
        return CheckResult(
            url=channel.url,
            status=CheckStatus.FAILED,
            http_status=probe.status,
            # status 为空说明没拿到响应（连接失败、超时等），不要显示成 HTTP None
            reason=f"HTTP {probe.status}" if probe.status is not None else "连接失败",
            ip_family=probe.ip_family,
        )

    latency_ms = _latency_ms(probe)
    if not looks_like_playlist(probe.head):
        bitrate = bitrate_from_measurement(probe.bytes_read, probe.elapsed)
        if not bitrate:
            return CheckResult(
                url=channel.url,
                status=CheckStatus.FAILED,
                http_status=probe.status,
                reason="响应无可读数据",
                latency_ms=latency_ms,
                playlist_latency_ms=latency_ms,
                ip_family=probe.ip_family,
            )
        return CheckResult(
            url=channel.url,
            status=CheckStatus.OK,
            http_status=probe.status,
            latency_ms=latency_ms,
            playlist_latency_ms=latency_ms,
            bitrate=bitrate,
            fluency=score_from_bitrate(bitrate),
            ip_family=probe.ip_family,
        )

    text = probe.head.decode("utf-8", errors="ignore")
    segment_url = await resolve_segment_url(client, channel.url, text, settings)
    if segment_url is None:
        return CheckResult(
            url=channel.url,
            status=CheckStatus.FAILED,
            http_status=probe.status,
            reason="清单中未找到可用切片",
            latency_ms=latency_ms,
            playlist_latency_ms=latency_ms,
            ip_family=probe.ip_family,
        )

    segment = await probe_stream_with_fallback(client, segment_url, settings, seconds, max_bytes)
    segment_latency_ms = _latency_ms(segment)
    if segment.error or segment.status is None or segment.status >= 400 or segment.bytes_read <= 0:
        reason = segment.error or f"切片 HTTP {segment.status}"
        return CheckResult(
            url=channel.url,
            status=CheckStatus.FAILED,
            http_status=segment.status,
            reason=reason,
            latency_ms=round(latency_ms + segment_latency_ms, 1),
            playlist_latency_ms=latency_ms,
            segment_latency_ms=segment_latency_ms,
            segment_url=segment_url,
            ip_family=segment.ip_family or probe.ip_family,
        )

    bitrate = bitrate_from_measurement(segment.bytes_read, segment.elapsed)
    return CheckResult(
        url=channel.url,
        status=CheckStatus.OK,
        http_status=probe.status,
        latency_ms=round(latency_ms + segment_latency_ms, 1),
        playlist_latency_ms=latency_ms,
        segment_latency_ms=segment_latency_ms,
        bitrate=bitrate,
        fluency=score_from_bitrate(bitrate),
        segment_url=segment_url,
        ip_family=segment.ip_family or probe.ip_family,
    )


def _latency_ms(probe: ProbeResult) -> float:
    return round((probe.first_byte_delay or 0.0) * 1000, 1)
