"""流代理的单元测试：白名单、清单改写与抓取分支。"""

from __future__ import annotations

from urllib.parse import quote

import httpx
import pytest

from livetv.constants import PROXY_ROUTE
from livetv.web import proxy


def _report() -> dict:
    return {
        "channels": [
            {
                "best_url": "https://a.example/live.m3u8",
                "urls": [
                    {"url": "https://B.Example/live.m3u8"},
                    {"url": "rtmp://c.example/app"},
                ],
            },
            {"urls": [{"url": "http://d.example/x.ts"}]},
        ]
    }


def test_allowed_hosts_collects_stream_hosts() -> None:
    """只收 http/https 的主机名，并统一转成小写。"""
    hosts = proxy.allowed_hosts(_report())
    assert hosts == {"a.example", "b.example", "d.example"}


def test_allowed_hosts_tolerates_missing_report() -> None:
    """报告缺失或结构异常时返回空集合，不抛异常。"""
    assert proxy.allowed_hosts(None) == set()
    assert proxy.allowed_hosts({"channels": "bad"}) == set()


def test_is_allowed_rejects_unknown_host_and_scheme() -> None:
    """未知主机、非 HTTP 协议与非法地址一律拒绝。"""
    hosts = {"a.example"}
    assert proxy.is_allowed("https://a.example/live.m3u8", hosts) is True
    assert proxy.is_allowed("https://other.example/live.m3u8", hosts) is False
    assert proxy.is_allowed("rtmp://a.example/app", hosts) is False
    assert proxy.is_allowed("file:///tmp/x.m3u8", hosts) is False
    assert proxy.is_allowed("not-a-url", hosts) is False


def test_rewrite_playlist_resolves_relative_and_keeps_comments() -> None:
    """相对地址解析为绝对地址并换成代理地址，注释与空行原样保留。"""
    text = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n\n../seg/1.ts\n#EXT-X-ENDLIST\n"
    rewritten = proxy.rewrite_playlist(text, "https://cdn.example/a/b/live.m3u8", PROXY_ROUTE)
    lines = rewritten.splitlines()
    assert lines[0] == "#EXTM3U"
    assert lines[1] == "#EXT-X-TARGETDURATION:6"
    assert lines[2] == ""
    assert lines[3] == f"{PROXY_ROUTE}?url={quote('https://cdn.example/a/seg/1.ts', safe='')}"
    assert lines[4] == "#EXT-X-ENDLIST"


def test_proxied_targets_extracts_absolute_urls() -> None:
    """改写后的清单可反向解析出目标地址，用于动态扩展白名单。"""
    text = "#EXTM3U\n../seg/1.ts\n#EXT-X-ENDLIST\n"
    rewritten = proxy.rewrite_playlist(text, "https://cdn.example/a/b/live.m3u8", PROXY_ROUTE)
    assert proxy.proxied_targets(rewritten, PROXY_ROUTE) == ["https://cdn.example/a/seg/1.ts"]


def _patch_client(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """把 proxy 内部的 httpx.Client 换成走 MockTransport 的客户端。"""
    original = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(proxy.httpx, "Client", factory)


def test_fetch_stream_rewrites_playlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """清单响应被识别并改写为代理地址。"""

    def handler(request: httpx.Request) -> httpx.Response:
        body = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n1.ts\n#EXT-X-ENDLIST\n"
        return httpx.Response(
            200,
            headers={"content-type": "application/vnd.apple.mpegurl"},
            text=body,
        )

    _patch_client(monkeypatch, handler)
    status, payload = proxy.fetch_stream(
        "https://cdn.example/live.m3u8", timeout=5.0, max_bytes=1024
    )
    assert status == 200
    assert isinstance(payload, str)
    assert f"{PROXY_ROUTE}?url=" in payload
    assert quote("https://cdn.example/1.ts", safe="") in payload


def test_fetch_stream_returns_bytes_for_segments(monkeypatch: pytest.MonkeyPatch) -> None:
    """非清单响应按字节返回。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "video/mp2t"}, content=b"\x47\x40\x00")

    _patch_client(monkeypatch, handler)
    status, payload = proxy.fetch_stream(
        "https://cdn.example/1.ts", timeout=5.0, max_bytes=1024
    )
    assert status == 200
    assert payload == b"\x47\x40\x00"


def test_fetch_stream_rejects_oversized_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """超出字节上限时抛 ValueError，避免无限下载。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "video/mp2t"}, content=b"x" * 64)

    _patch_client(monkeypatch, handler)
    with pytest.raises(ValueError, match="上限"):
        proxy.fetch_stream("https://cdn.example/1.ts", timeout=5.0, max_bytes=32)
