"""页面服务用例：运行管理器状态、HTTP 路由与下载白名单。"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from livetv import cli
from livetv.config import AppConfig
from livetv.constants import DEFAULT_WEB_HOST, DEFAULT_WEB_PORT, PipelineStage, RunStatus
from livetv.models import RunReport
from livetv.web.run_manager import RunBusyError, RunManager, RunOptions
from livetv.web.server import create_server


def _write_config(tmp_path: Path) -> str:
    source = tmp_path / "source.m3u"
    source.write_text(
        "#EXTM3U\n#EXTINF:-1,示例频道\nhttp://example.com/a.m3u8\n", encoding="utf-8"
    )
    config = tmp_path / "sources.yaml"
    out = tmp_path / "out"
    config.write_text(
        f"""
output:
  m3u: {(out / 'live.m3u').as_posix()}
  report: {(out / 'report.json').as_posix()}
sources:
  - name: 本地样例
    path: {source.as_posix()}
""",
        encoding="utf-8",
    )
    return str(config)


def _fake_report(m3u_path: Path, report_path: Path) -> RunReport:
    return RunReport(
        generated_at="2026-11-01T16:00:00",
        config_path="config/sources.yaml",
        output_m3u=str(m3u_path),
        output_report=str(report_path),
        speed_test=True,
    )


def _wait(manager: RunManager, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = manager.snapshot()
        if snapshot["status"] != RunStatus.RUNNING.value:
            return snapshot
        time.sleep(0.01)
    raise AssertionError("运行未在超时内结束")


def test_run_manager_reports_progress_and_report(tmp_path: Path) -> None:
    calls: list[tuple[str, int, int]] = []
    m3u_path = tmp_path / "out" / "live.m3u"
    report_path = tmp_path / "out" / "report.json"
    m3u_path.parent.mkdir(parents=True)
    m3u_path.write_text("#EXTM3U\n", encoding="utf-8")

    def runner(config: AppConfig, options: RunOptions, progress) -> RunReport:
        def spy(stage: PipelineStage, done: int, total: int) -> None:
            calls.append((stage.value, done, total))
            progress(stage, done, total)

        spy(PipelineStage.SOURCES, 1, 2)
        spy(PipelineStage.CHECK, 3, 3)
        spy(PipelineStage.DONE, 1, 1)
        return _fake_report(m3u_path, report_path)

    manager = RunManager(_write_config(tmp_path), runner=runner)
    manager.start(RunOptions(limit=10, speed_test=True))
    snapshot = _wait(manager)

    assert snapshot["status"] == RunStatus.DONE.value
    assert snapshot["stage"] == PipelineStage.DONE.value
    assert snapshot["progress"] == {"done": 1, "total": 1}
    assert calls[0][0] == PipelineStage.SOURCES.value
    assert calls[-1][0] == PipelineStage.DONE.value
    assert manager.report_payload() is not None


def test_run_manager_rejects_second_run(tmp_path: Path) -> None:
    gate = threading.Event()
    m3u_path = tmp_path / "out" / "live.m3u"

    def runner(config: AppConfig, options: RunOptions, progress) -> RunReport:
        gate.wait(1.0)
        return _fake_report(m3u_path, tmp_path / "out" / "report.json")

    manager = RunManager(_write_config(tmp_path), runner=runner)
    manager.start(RunOptions())
    with pytest.raises(RunBusyError):
        manager.start(RunOptions())
    gate.set()
    snapshot = _wait(manager)

    assert snapshot["status"] == RunStatus.DONE.value


def test_run_manager_marks_failure_and_recovers(tmp_path: Path) -> None:
    def runner(config: AppConfig, options: RunOptions, progress) -> RunReport:
        raise RuntimeError("boom")

    manager = RunManager(_write_config(tmp_path), runner=runner)
    manager.start(RunOptions())
    snapshot = _wait(manager)

    assert snapshot["status"] == RunStatus.FAILED.value
    assert "boom" in snapshot["last_error"]
    # 失败后允许重新运行
    manager.start(RunOptions())
    assert manager.snapshot()["status"] == RunStatus.RUNNING.value
    _wait(manager)


@pytest.fixture
def server_env(tmp_path: Path) -> Iterator[tuple[httpx.Client, RunManager]]:
    manager = RunManager(_write_config(tmp_path), runner=lambda config, options, progress: None)
    server = create_server("127.0.0.1", 0, manager)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5.0) as client:
        yield client, manager
    server.shutdown()
    server.server_close()
    thread.join(1.0)


def test_health_and_index(server_env: tuple[httpx.Client, RunManager]) -> None:
    client, _ = server_env
    assert client.get("/api/health").json() == {"status": "ok"}
    response = client.get("/")
    assert response.status_code == 200
    assert response.text.startswith("<!DOCTYPE html>")


def test_report_missing_returns_404(server_env: tuple[httpx.Client, RunManager]) -> None:
    client, _ = server_env
    response = client.get("/api/report")
    assert response.status_code == 404
    assert response.json()["error"]


def test_report_and_download_whitelist(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    m3u_path = out / "live.m3u"
    m3u_path.write_text("#EXTM3U\n", encoding="utf-8")
    report_path = out / "report.json"
    report_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-11-01T16:00:00",
                "stats": {},
                "groups": {},
                "channels": [],
                "failures": [],
                "outputs": {"m3u": str(m3u_path), "report": str(report_path)},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    manager = RunManager(config_path)
    server = create_server("127.0.0.1", 0, manager)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5.0) as client:
            report_response = client.get("/api/report")
            assert report_response.status_code == 200
            assert report_response.json()["generated_at"] == "2026-11-01T16:00:00"

            download = client.get("/download", params={"name": "live.m3u"})
            assert download.status_code == 200
            assert download.text.startswith("#EXTM3U")

            assert client.get("/download", params={"name": "report.json"}).status_code == 404
            assert client.get("/download", params={"name": "../x"}).status_code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(1.0)


def test_post_run_rejects_invalid_options(
    server_env: tuple[httpx.Client, RunManager],
) -> None:
    client, manager = server_env
    assert client.post("/api/run", json={"limit": 0}).status_code == 400
    assert client.post("/api/run", json={"speed_test": "yes"}).status_code == 400
    assert manager.snapshot()["status"] == RunStatus.IDLE.value


def test_cli_serve_defaults() -> None:
    args = cli.build_parser().parse_args(["serve"])
    assert args.host == DEFAULT_WEB_HOST
    assert args.port == DEFAULT_WEB_PORT
    assert args.config.endswith("sources.yaml")
