"""本地流代理：转发远程 m3u8 与切片，并把清单里的地址改写为代理地址。"""

from __future__ import annotations

from urllib.parse import quote, unquote, urljoin, urlparse

import httpx

from ..constants import DEFAULT_USER_AGENT, PROXY_ROUTE

# 只代理明文与加密的 HTTP，其余协议一律拒绝
PROXY_SCHEMES = frozenset({"http", "https"})
# m3u8 清单的判定依据：响应头关键字、路径后缀与正文魔数
PLAYLIST_CONTENT_TYPE_HINT = "mpegurl"
PLAYLIST_SUFFIX = ".m3u8"
PLAYLIST_SIGNATURE = b"#EXTM3U"


def build_headers(url: str) -> dict[str, str]:
    """构造与检测请求一致的请求头：按目标站点补 Referer/Origin。

    检测阶段（checker.CheckSettings.headers）会带上 Referer/Origin 以通过防盗链，
    而早期代理使用固定请求头、不含这两项，导致「检测成功但播放被源站 403」。

    这里复刻同一逻辑：Referer/Origin 均取目标 URL 自身的 origin，
    让代理拉流与检测拉流行为一致。
    """
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        site = f"{parsed.scheme}://{parsed.netloc}"
        headers["Referer"] = f"{site}/"
        headers["Origin"] = site
    return headers


def allowed_hosts(report: dict | None) -> set[str]:
    """从报告里抽取所有流地址的主机名（小写），作为代理白名单。"""
    hosts: set[str] = set()
    if not isinstance(report, dict):
        return hosts
    channels = report.get("channels")
    if not isinstance(channels, list):
        return hosts
    for item in channels:
        if not isinstance(item, dict):
            continue
        urls: list[object] = [item.get("best_url")]
        entries = item.get("urls")
        if isinstance(entries, list):
            urls.extend(
                entry.get("url") if isinstance(entry, dict) else None for entry in entries
            )
        for value in urls:
            if not isinstance(value, str):
                continue
            parsed = urlparse(value)
            if parsed.scheme.lower() not in PROXY_SCHEMES:
                continue
            host = parsed.hostname
            if host:
                hosts.add(host.lower())
    return hosts


def is_allowed(url: str, hosts: set[str]) -> bool:
    """判断地址是否可代理：协议必须是 http/https，主机必须在白名单内。"""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in PROXY_SCHEMES:
        return False
    host = parsed.hostname
    if not host:
        return False
    return host.lower() in hosts


def rewrite_playlist(text: str, base_url: str, proxy_path: str) -> str:
    """把清单中的地址改写为本地代理地址；注释与空行原样保留。"""
    lines: list[str] = []
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        ending = line[len(content) :]
        stripped = content.strip()
        if not stripped or stripped.startswith("#"):
            lines.append(line)
            continue
        absolute = urljoin(base_url, stripped)
        lines.append(f"{proxy_path}?url={quote(absolute, safe='')}{ending}")
    return "".join(lines)


def proxied_targets(text: str, proxy_path: str) -> list[str]:
    """从改写后的清单里抽出被代理的目标地址，供动态扩展白名单使用。"""
    prefix = f"{proxy_path}?url="
    targets: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            targets.append(unquote(stripped[len(prefix) :]))
    return targets


def fetch_stream(url: str, *, timeout: float, max_bytes: int) -> tuple[int, str | bytes]:
    """抓取远程地址：m3u8 清单改写后返回文本，其余按字节返回。"""
    # 每次新建 Client 且不设 Cookie，避免把本地或跨站 Cookie 转发给上游
    with httpx.Client(follow_redirects=True, timeout=timeout, headers=build_headers(url)) as client:
        with client.stream("GET", url) as response:
            status = response.status_code
            if _declared_playlist(response, url):
                text = response.read().decode("utf-8", "replace")
                return status, rewrite_playlist(text, str(response.url), PROXY_ROUTE)

            data = bytearray()
            decided = False
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if not decided:
                    probe = bytes(data).lstrip()
                    if probe[: len(PLAYLIST_SIGNATURE)] == PLAYLIST_SIGNATURE:
                        rest = response.read()
                        text = (bytes(data) + rest).decode("utf-8", "replace")
                        return status, rewrite_playlist(text, str(response.url), PROXY_ROUTE)
                    if len(probe) >= len(PLAYLIST_SIGNATURE):
                        decided = True
                if len(data) > max_bytes:
                    raise ValueError(f"上游响应超过 {max_bytes} 字节上限")
            return status, bytes(data)


def _declared_playlist(response: httpx.Response, url: str) -> bool:
    """按响应头与地址路径判断是否为 m3u8 清单。"""
    if PLAYLIST_CONTENT_TYPE_HINT in response.headers.get("content-type", "").lower():
        return True
    paths = (urlparse(url).path.lower(), urlparse(str(response.url)).path.lower())
    return any(path.endswith(PLAYLIST_SUFFIX) for path in paths)
