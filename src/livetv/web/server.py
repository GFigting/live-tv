"""本地 HTTP 服务：提供结果查看页面、报告接口、检测触发与订阅下载。"""

from __future__ import annotations

import json
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..config import load_config
from ..constants import (
    PROXY_MAX_BYTES,
    PROXY_MAX_URL_LENGTH,
    PROXY_ROUTE,
    PROXY_TIMEOUT,
    WEB_REQUEST_MAX_BYTES,
)
from ..errors import LiveTvError
from .proxy import allowed_hosts, fetch_stream, is_allowed, proxied_targets
from .run_manager import RunBusyError, RunManager, RunOptions
from .sources import add_source, list_sources, set_source_enabled

ROUTE_INDEX = "/"
ROUTE_HEALTH = "/api/health"
ROUTE_REPORT = "/api/report"
ROUTE_RUN = "/api/run"
ROUTE_SOURCES = "/api/sources"
ROUTE_SOURCES_ADD = "/api/sources/add"
ROUTE_RETEST = "/api/retest"
ROUTE_DOWNLOAD = "/download"
ROUTE_PROXY = PROXY_ROUTE
# 只放行播放器库这一个固定静态路径，避免任意文件读取
ROUTE_HLS_JS = "/static/lib/hls.min.js"
ROUTE_REFERENCE = "/reference.html"

STATIC_DIR = Path(__file__).parent / "static"
INDEX_FILE = STATIC_DIR / "index.html"
HLS_JS_FILE = STATIC_DIR / "lib" / "hls.min.js"
REFERENCE_FILE = STATIC_DIR / "reference.html"

CONTENT_TYPE_HTML = "text/html; charset=utf-8"
CONTENT_TYPE_JSON = "application/json; charset=utf-8"
CONTENT_TYPE_M3U = "application/vnd.apple.mpegurl"
CONTENT_TYPE_JS = "application/javascript; charset=utf-8"
CONTENT_TYPE_BINARY = "application/octet-stream"

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# 清单里引用到的切片主机：切片常位于与清单不同的 CDN，需在首次拉取后动态放行
_EXTRA_HOSTS: set[str] = set()
_EXTRA_HOSTS_LOCK = threading.Lock()


def _extra_hosts() -> set[str]:
    """返回已通过清单发现并放行的附加主机名。"""
    with _EXTRA_HOSTS_LOCK:
        return set(_EXTRA_HOSTS)


def _remember_hosts(playlist: str) -> None:
    """把改写后清单引用的主机加入白名单。"""
    targets = proxied_targets(playlist, PROXY_ROUTE)
    hosts = {parsed.hostname.lower() for parsed in map(urlparse, targets) if parsed.hostname}
    if hosts:
        with _EXTRA_HOSTS_LOCK:
            _EXTRA_HOSTS.update(hosts)


# 页面提交参数的边界，避免误输入导致长时间运行或异常并发
LIMIT_MIN = 1
LIMIT_MAX = 5000
CONCURRENCY_MIN = 1
CONCURRENCY_MAX = 128
MIN_FLUENCY_MIN = 0.0
MIN_FLUENCY_MAX = 100.0
MAX_PER_CHANNEL_MIN = 1
MAX_PER_CHANNEL_MAX = 10


class _HandlerBase(BaseHTTPRequestHandler):
    """路由基类；具体实例通过 create_handler 绑定运行管理器。"""

    run_manager: RunManager
    server_version = "livetv-web"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == ROUTE_INDEX:
            self._send_index()
        elif parsed.path == ROUTE_HEALTH:
            self._send_json({"status": "ok"})
        elif parsed.path == ROUTE_REPORT:
            payload = self.run_manager.report_payload()
            if payload is None:
                self._send_json({"error": "尚未生成报告"}, HTTPStatus.NOT_FOUND)
            else:
                self._send_json(payload)
        elif parsed.path == ROUTE_RUN:
            self._send_json(self.run_manager.snapshot())
        elif parsed.path == ROUTE_SOURCES:
            self._send_sources()
        elif parsed.path == ROUTE_DOWNLOAD:
            self._send_download(parse_qs(parsed.query))
        elif parsed.path == ROUTE_PROXY:
            self._send_proxy(parse_qs(parsed.query))
        elif parsed.path == ROUTE_HLS_JS:
            self._send_hls_js()
        elif parsed.path == ROUTE_REFERENCE:
            self._send_reference()
        else:
            self._send_json({"error": "未知路径"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == ROUTE_SOURCES:
            self._toggle_source()
            return
        if parsed.path == ROUTE_SOURCES_ADD:
            self._add_source()
            return
        if parsed.path == ROUTE_RETEST:
            self._start_retest()
            return
        if parsed.path != ROUTE_RUN:
            self._send_json({"error": "未知路径"}, HTTPStatus.NOT_FOUND)
            return
        try:
            options = self._read_options()
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        try:
            snapshot = self.run_manager.start(options)
        except RunBusyError:
            self._send_json({"error": "已有检测在运行，请等待当前运行结束"}, HTTPStatus.CONFLICT)
            return
        self._send_json(snapshot, HTTPStatus.ACCEPTED)

    def log_message(self, format: str, *args: Any) -> None:
        """页面会定时轮询，静默默认访问日志避免刷屏。"""
        return

    def _read_options(self) -> RunOptions:
        length = int(self.headers.get("Content-Length") or 0)
        if length > WEB_REQUEST_MAX_BYTES:
            raise ValueError("请求体过大")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return RunOptions()
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("请求体不是合法 JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return RunOptions(
            limit=_optional_int(data, "limit", LIMIT_MIN, LIMIT_MAX),
            speed_test=_optional_bool(data, "speed_test", True),
            concurrency=_optional_int(data, "concurrency", CONCURRENCY_MIN, CONCURRENCY_MAX),
            min_fluency=_optional_number(data, "min_fluency", MIN_FLUENCY_MIN, MIN_FLUENCY_MAX),
            max_per_channel=_optional_int(
                data, "max_per_channel", MAX_PER_CHANNEL_MIN, MAX_PER_CHANNEL_MAX
            ),
        )

    def _send_sources(self) -> None:
        """返回源清单与启停状态，并标注是否有检测在运行。"""
        try:
            sources = list_sources(self.run_manager.config_path)
        except (OSError, ValueError) as exc:
            self._send_json({"error": f"读取配置失败：{exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        running = self.run_manager.snapshot().get("status") == "running"
        self._send_json({"sources": sources, "running": running})

    def _toggle_source(self) -> None:
        """切换单个源的启用状态并写回配置；检测运行中拒绝修改。"""
        if self.run_manager.snapshot().get("status") == "running":
            self._send_json({"error": "检测进行中，暂不能修改源开关"}, HTTPStatus.CONFLICT)
            return
        try:
            data = self._read_json_object()
            name = str(data.get("name") or "").strip()
            enabled = data.get("enabled")
            if not name or not isinstance(enabled, bool):
                raise ValueError("需要提供 name 与布尔类型的 enabled")
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        try:
            sources = set_source_enabled(self.run_manager.config_path, name, enabled)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except OSError as exc:
            self._send_json({"error": f"写入配置失败：{exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self._send_json({"sources": sources, "message": "已保存，下次检测生效"})

    def _add_source(self) -> None:
        """追加一条手动源到配置；检测运行中拒绝修改。"""
        if self.run_manager.snapshot().get("status") == "running":
            self._send_json({"error": "检测进行中，暂不能修改源清单"}, HTTPStatus.CONFLICT)
            return
        try:
            data = self._read_json_object()
            name = str(data.get("name") or "").strip()
            url = str(data.get("url") or "").strip()
            group = str(data.get("group") or "").strip()
            if not name or not url:
                raise ValueError("需要提供 name 与 url")
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        try:
            sources = add_source(self.run_manager.config_path, name, url, group)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except OSError as exc:
            self._send_json({"error": f"写入配置失败：{exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self._send_json({"sources": sources, "message": "已添加，下次检测生效"})

    def _start_retest(self) -> None:
        """按频道、分组或范围发起复测；复测只更新页面结果，不改写产物文件。"""
        try:
            data = self._read_json_object()
            channel = str(data.get("channel") or "").strip()
            group = str(data.get("group") or "").strip()
            scope = str(data.get("scope") or "").strip().lower()
            url = str(data.get("url") or "").strip()
            channels = data.get("channels")
            if channels is not None:
                if not isinstance(channels, list):
                    raise ValueError("channels 必须是数组")
                channels = [str(value).strip() for value in channels if str(value).strip()]
                if not channels:
                    raise ValueError("channels 不能为空")
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        try:
            snapshot = self.run_manager.start_retest(
                channel=channel,
                group=group,
                scope=scope,
                channels=channels,
                url=url,
            )
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except RunBusyError:
            self._send_json({"error": "已有任务在运行，请等待当前运行结束"}, HTTPStatus.CONFLICT)
            return
        self._send_json(snapshot, HTTPStatus.ACCEPTED)

    def _read_json_object(self) -> dict:
        """读取并校验 JSON 请求体，限制体积。"""
        length = int(self.headers.get("Content-Length") or 0)
        if length > WEB_REQUEST_MAX_BYTES:
            raise ValueError("请求体过大")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("请求体不是合法 JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    def _send_download(self, query: dict[str, list[str]]) -> None:
        name = (query.get("name") or [""])[0]
        path = self.run_manager.allowed_downloads().get(name)
        if path is None:
            self._send_json({"error": "文件不可下载"}, HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        filename = path.name.replace('"', "_")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", CONTENT_TYPE_M3U)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_index(self) -> None:
        try:
            body = INDEX_FILE.read_bytes()
        except OSError:
            self._send_json({"error": "页面文件缺失"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self._send_bytes(body, CONTENT_TYPE_HTML)

    def _send_reference(self) -> None:
        """返回静态参考页；与控制台同源部署时可复用 /proxy 代理播放。"""
        try:
            body = REFERENCE_FILE.read_bytes()
        except OSError:
            self._send_json({"error": "参考页文件缺失"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self._send_bytes(body, CONTENT_TYPE_HTML)

    def _send_hls_js(self) -> None:
        """只返回内置的播放器库文件，不接受任何路径拼接。"""
        try:
            body = HLS_JS_FILE.read_bytes()
        except OSError:
            self._send_json({"error": "播放器库缺失"}, HTTPStatus.NOT_FOUND)
            return
        self._send_bytes(body, CONTENT_TYPE_JS)

    def _send_proxy(self, query: dict[str, list[str]]) -> None:
        """代理远程视频流：先校验白名单，再抓取并按清单或二进制返回。"""
        url = (query.get("url") or [""])[0]
        if not url or len(url) > PROXY_MAX_URL_LENGTH:
            self._send_json({"error": "缺少或非法的 url"}, HTTPStatus.BAD_REQUEST)
            return
        hosts = allowed_hosts(self.run_manager.report_payload()) | _extra_hosts()
        if not is_allowed(url, hosts):
            self._send_json({"error": "该地址不在白名单内"}, HTTPStatus.FORBIDDEN)
            return
        try:
            status, body = fetch_stream(url, timeout=PROXY_TIMEOUT, max_bytes=PROXY_MAX_BYTES)
        except Exception as exc:  # 上游异常不应冒泡到服务线程，统一转为 502
            self._send_json({"error": f"上游请求失败：{exc}"}, HTTPStatus.BAD_GATEWAY)
            return
        if isinstance(body, str):
            _remember_hosts(body)
            self._send_bytes(body.encode("utf-8"), CONTENT_TYPE_M3U, status)
        else:
            self._send_bytes(body, CONTENT_TYPE_BINARY, status)

    def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(body, CONTENT_TYPE_JSON, status)

    def _send_bytes(
        self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionError, OSError):
            # 播放器切流或关闭窗口会中断请求，属于正常现象，不必报错
            return


def _optional_int(data: dict, key: str, minimum: int, maximum: int) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} 必须是整数")
    if not minimum <= value <= maximum:
        raise ValueError(f"{key} 必须在 {minimum} 到 {maximum} 之间")
    return value


def _optional_number(data: dict, key: str, minimum: float, maximum: float) -> float | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} 必须是数字")
    number = float(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{key} 必须在 {minimum} 到 {maximum} 之间")
    return number


def _optional_bool(data: dict, key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} 必须是布尔值")
    return value


def create_handler(manager: RunManager) -> type[_HandlerBase]:
    class Handler(_HandlerBase):
        run_manager = manager

    return Handler


class _QuietHTTPServer(ThreadingHTTPServer):
    """忽略客户端主动断连产生的错误，避免播放器切流时刷堆栈。"""

    def handle_error(self, request: Any, client_address: Any) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, ConnectionError):
            return
        super().handle_error(request, client_address)


def create_server(host: str, port: int, manager: RunManager) -> ThreadingHTTPServer:
    return _QuietHTTPServer((host, port), create_handler(manager))


def run_server(config_path: str, host: str, port: int) -> int:
    """启动页面服务并阻塞；返回进程退出码。"""
    try:
        load_config(config_path)
    except LiveTvError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    if not 1 <= port <= 65535:
        print(f"端口无效：{port}", file=sys.stderr)
        return 2
    manager = RunManager(config_path)
    try:
        server = create_server(host, port, manager)
    except OSError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 2
    if host not in LOOPBACK_HOSTS:
        print(f"警告：服务监听 {host} 且无鉴权，请仅在可信网络使用", file=sys.stderr)
    print(f"页面已启动：http://{host}:{port}（Ctrl+C 停止）")
    with server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止")
    return 0
