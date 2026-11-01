"""源开关的读写测试：按行改写保留注释，以及接口行为。"""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer

import httpx
import pytest

from livetv.web.run_manager import RunManager, RunOptions
from livetv.web.server import create_server
from livetv.web.sources import (
    SourceNameError,
    SourceUrlError,
    add_source,
    list_sources,
    set_source_enabled,
)

CONFIG_TEXT = """# 源清单示例，注释需要被保留

sources:
  # 国内：iptv-org 中国
  - name: iptv-org 中国
    url: https://example.com/cn.m3u
    enabled: true

  # 海外：Free-TV
  - name: Free-TV
    url: https://example.com/free.m3u
    enabled: false
"""


def _write_config(tmp_path) -> str:
    path = tmp_path / "sources.yaml"
    path.write_text(CONFIG_TEXT, encoding="utf-8")
    return str(path)


def test_list_sources_reads_flags(tmp_path) -> None:
    """读取每条源的名称、地址与启用状态。"""
    config = _write_config(tmp_path)
    assert list_sources(config) == [
        {"name": "iptv-org 中国", "url": "https://example.com/cn.m3u", "group": "", "enabled": True},
        {"name": "Free-TV", "url": "https://example.com/free.m3u", "group": "", "enabled": False},
    ]


def test_set_source_enabled_flips_only_target_line(tmp_path) -> None:
    """只改目标源的 enabled 行，注释与其他源保持原样。"""
    config = _write_config(tmp_path)
    result = set_source_enabled(config, "Free-TV", True)
    assert result[1]["enabled"] is True
    text = (tmp_path / "sources.yaml").read_text(encoding="utf-8")
    assert "# 源清单示例，注释需要被保留" in text
    assert "# 海外：Free-TV" in text
    assert "enabled: false" not in text
    assert text.count("enabled: true") == 2


def test_add_source_appends_and_keeps_comments(tmp_path) -> None:
    """新增源追加到 sources 段末尾，原有注释与条目保持原样。"""
    config = _write_config(tmp_path)
    result = add_source(config, "手动源", "https://example.com/manual.m3u")
    assert [item["name"] for item in result] == ["iptv-org 中国", "Free-TV", "手动源"]
    assert result[-1] == {
        "name": "手动源", "url": "https://example.com/manual.m3u", "group": "", "enabled": True,
    }
    text = (tmp_path / "sources.yaml").read_text(encoding="utf-8")
    assert "# 源清单示例，注释需要被保留" in text
    assert "# 海外：Free-TV" in text
    assert text.index("url: https://example.com/manual.m3u") > text.index("url: https://example.com/free.m3u")


def test_add_source_rejects_duplicate_and_bad_url(tmp_path) -> None:
    """重名与非法协议拒绝写入，原文件不被改动。"""
    config = _write_config(tmp_path)
    before = (tmp_path / "sources.yaml").read_text(encoding="utf-8")
    with pytest.raises(SourceNameError):
        add_source(config, "Free-TV", "https://example.com/dup.m3u")
    with pytest.raises(SourceUrlError):
        add_source(config, "新源", "ftp://example.com/a.m3u")
    assert (tmp_path / "sources.yaml").read_text(encoding="utf-8") == before


def test_set_source_enabled_unknown_name(tmp_path) -> None:
    """不存在的源名称拒绝修改。"""
    config = _write_config(tmp_path)
    with pytest.raises(SourceNameError):
        set_source_enabled(config, "不存在的源", True)


def _start(config: str) -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    manager = RunManager(config)
    server = create_server("127.0.0.1", 0, manager)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, thread, f"http://{host}:{port}"


def test_sources_api_lists_and_toggles(tmp_path) -> None:
    """接口可读取源清单，并能切换启用状态且立即生效。"""
    config = _write_config(tmp_path)
    server, thread, base = _start(config)
    try:
        with httpx.Client(base_url=base, timeout=5.0) as client:
            listing = client.get("/api/sources").json()
            assert listing["running"] is False
            assert [item["name"] for item in listing["sources"]] == ["iptv-org 中国", "Free-TV"]

            response = client.post("/api/sources", json={"name": "Free-TV", "enabled": True})
            assert response.status_code == 200
            assert response.json()["sources"][1]["enabled"] is True
            assert list_sources(config)[1]["enabled"] is True
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_sources_api_rejects_bad_payload(tmp_path) -> None:
    """缺少字段或源名不存在时返回 400。"""
    config = _write_config(tmp_path)
    server, thread, base = _start(config)
    try:
        with httpx.Client(base_url=base, timeout=5.0) as client:
            assert client.post("/api/sources", json={"name": "Free-TV"}).status_code == 400
            unknown = client.post("/api/sources", json={"name": "不存在", "enabled": True})
            assert unknown.status_code == 400
            assert "不存在" in unknown.json()["error"]
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_sources_add_api_appends_source(tmp_path) -> None:
    """新增接口写入配置，重名或缺字段返回 400。"""
    config = _write_config(tmp_path)
    server, thread, base = _start(config)
    try:
        with httpx.Client(base_url=base, timeout=5.0) as client:
            response = client.post(
                "/api/sources/add",
                json={"name": "手动源", "url": "https://example.com/manual.m3u"},
            )
            assert response.status_code == 200
            assert [item["name"] for item in response.json()["sources"]][-1] == "手动源"
            assert [item["name"] for item in list_sources(config)][-1] == "手动源"
            duplicate = client.post(
                "/api/sources/add",
                json={"name": "手动源", "url": "https://example.com/dup.m3u"},
            )
            assert duplicate.status_code == 400
            missing = client.post("/api/sources/add", json={"name": "只有名称"})
            assert missing.status_code == 400
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_sources_api_blocks_while_running(tmp_path) -> None:
    """检测运行中拒绝修改源开关，返回 409。"""
    config = _write_config(tmp_path)
    release = threading.Event()

    def runner(app_config, options, progress):
        release.wait(timeout=5)
        raise AssertionError("测试不应真正运行流水线")

    manager = RunManager(config, runner=runner)
    server = create_server("127.0.0.1", 0, manager)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    base = f"http://{host}:{port}"
    try:
        manager.start(RunOptions())
        with httpx.Client(base_url=base, timeout=5.0) as client:
            response = client.post("/api/sources", json={"name": "Free-TV", "enabled": True})
            assert response.status_code == 409
    finally:
        release.set()
        server.shutdown()
        thread.join(timeout=5)
        set_source_enabled(config, "Free-TV", False)
