"""检测层用例：使用 httpx.MockTransport 覆盖清单探针、切片验证与失败路径。"""

import asyncio
import socket

import httpx
import pytest

from livetv.checker import CheckSettings, best_variant_uri, check_channel
from livetv.constants import FALLBACK_USER_AGENT, CheckStatus
from livetv.models import Channel

PLAYLIST = "#EXTM3U\n#EXTINF:-1,seg\nseg.ts\n"
MASTER = (
    "#EXTM3U\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=800000\n"
    "low/index.m3u8\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=2500000\n"
    "high/index.m3u8\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=1200000\n"
    "mid/index.m3u8\n"
)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def _run(url: str, handler, settings: CheckSettings | None = None):
    async def scenario():
        async with _client(handler) as client:
            return await check_channel(client, Channel(name="demo", url=url), settings or CheckSettings())

    return asyncio.run(scenario())


def test_check_rejects_non_http_url_without_request():
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, text=PLAYLIST)

    result = _run("/[NO PUBLIC STREAM]", handler)

    assert sent == []
    assert result.status is CheckStatus.FAILED
    assert result.reason.startswith("非法 URL")


def test_check_m3u8_verifies_first_segment():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".m3u8"):
            return httpx.Response(200, text=PLAYLIST)
        return httpx.Response(200, content=b"x" * 100_000)

    result = _run("http://example.com/live.m3u8", handler)
    assert result.status is CheckStatus.OK
    assert result.segment_url == "http://example.com/seg.ts"
    assert result.fluency > 0


def test_check_splits_playlist_and_segment_latency():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".m3u8"):
            return httpx.Response(200, text=PLAYLIST)
        return httpx.Response(200, content=b"x" * 100_000)

    result = _run("http://example.com/live.m3u8", handler)
    assert result.playlist_latency_ms is not None
    assert result.segment_latency_ms is not None
    assert result.latency_ms == pytest.approx(
        result.playlist_latency_ms + result.segment_latency_ms
    )


def test_check_reports_http_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    result = _run("http://example.com/missing.m3u8", handler)
    assert result.status is CheckStatus.FAILED
    assert "404" in result.reason


def test_check_fails_when_segment_unreachable():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".m3u8"):
            return httpx.Response(200, text=PLAYLIST)
        return httpx.Response(500)

    result = _run("http://example.com/live.m3u8", handler)
    assert result.status is CheckStatus.FAILED
    assert result.segment_url == "http://example.com/seg.ts"


def test_relative_segment_is_resolved_against_playlist():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".m3u8"):
            return httpx.Response(200, text=PLAYLIST)
        return httpx.Response(200, content=b"x" * 100_000)

    result = _run("http://cdn.example.com/tv/live.m3u8", handler)
    assert result.segment_url == "http://cdn.example.com/tv/seg.ts"


def test_check_records_ip_family_from_connection():
    class FakeSocket:
        family = socket.AF_INET6

    class FakeStream:
        def get_extra_info(self, name):
            return FakeSocket() if name == "socket" else None

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"x" * 100_000, extensions={"network_stream": FakeStream()}
        )

    result = _run("http://example.com/direct.ts", handler)
    assert result.ip_family == "ipv6"


def test_direct_stream_is_accepted_without_playlist():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 200_000)

    result = _run("http://example.com/direct.ts", handler)
    assert result.status is CheckStatus.OK
    assert result.segment_url is None
    assert result.playlist_latency_ms is not None
    assert result.segment_latency_ms is None


def test_headers_include_referer_and_origin():
    headers = CheckSettings().headers("https://cdn.example.com/tv/live.m3u8")
    assert headers["Referer"] == "https://cdn.example.com/"
    assert headers["Origin"] == "https://cdn.example.com"
    assert headers["User-Agent"]


def test_auth_failure_retries_with_fallback_ua():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        ua = request.headers.get("user-agent", "")
        seen.append(ua)
        if ua != FALLBACK_USER_AGENT:
            return httpx.Response(403)
        if request.url.path.endswith(".m3u8"):
            return httpx.Response(200, text=PLAYLIST)
        return httpx.Response(200, content=b"x" * 100_000)

    result = _run("http://example.com/live.m3u8", handler)
    assert result.status is CheckStatus.OK
    assert seen[0] != FALLBACK_USER_AGENT
    assert seen[1] == FALLBACK_USER_AGENT
    assert len(seen) == 4


def test_non_auth_failure_does_not_retry():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(404)

    result = _run("http://example.com/missing.m3u8", handler)
    assert result.status is CheckStatus.FAILED
    assert calls == ["/missing.m3u8"]


def test_master_playlist_picks_highest_bandwidth():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/master.m3u8":
            return httpx.Response(200, text=MASTER)
        if path == "/high/index.m3u8":
            return httpx.Response(200, text=PLAYLIST)
        if path.endswith(".ts"):
            return httpx.Response(200, content=b"x" * 100_000)
        return httpx.Response(404)

    result = _run("http://example.com/master.m3u8", handler)
    assert result.status is CheckStatus.OK
    assert result.segment_url == "http://example.com/high/seg.ts"


def test_master_without_bandwidth_falls_back_to_first_uri():
    text = '#EXTM3U\n#EXT-X-STREAM-INF:CODECS="avc1.4d401f"\nfirst.m3u8\nsecond.m3u8\n'
    assert best_variant_uri(text) == "first.m3u8"
