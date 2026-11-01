"""复测用例：地址过滤、结果合并、上限校验与接口行为。"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from livetv.config import AppConfig
from livetv.constants import CheckStatus, PipelineStage, RunStatus
from livetv.models import CheckResult
from livetv.web import retest as retest_module
from livetv.web.run_manager import RunBusyError, RunManager, RunOptions
from livetv.web.server import create_server


def _channel(key: str, name: str, group: str, urls: list[dict], best: str | None) -> dict:
    return {
        "name": name,
        "key": key,
        "group": group,
        "group_tags": [],
        "logo": None,
        "tvg_id": None,
        "best_url": best,
        "fluency": 60.0 if best else 0.0,
        "latency_ms": 180.0 if best else None,
        "playlist_latency_ms": 100.0 if best else None,
        "segment_latency_ms": 80.0 if best else None,
        "bitrate": 1200 if best else None,
        "ip_family": "ipv4" if best else None,
        "urls": urls,
    }


def _url(url: str, status: str = "failed", fluency: float = 0.0) -> dict:
    return {
        "url": url,
        "status": status,
        "fluency": fluency,
        "latency_ms": 120.0 if status == "ok" else None,
        "playlist_latency_ms": 70.0 if status == "ok" else None,
        "segment_latency_ms": 50.0 if status == "ok" else None,
        "ip_family": "ipv4" if status == "ok" else None,
        "bitrate": 1100 if status == "ok" else None,
        "reason": "" if status == "ok" else "HTTP 404",
    }


def _write_env(tmp_path: Path, channels: list[dict]) -> str:
    """写入含历史报告的配置目录，RunManager 启动时直接加载这份报告。"""
    source = tmp_path / "source.m3u"
    source.write_text("#EXTM3U\n#EXTINF:-1,样例\nhttp://example.com/a.m3u8\n", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    (out / "live.m3u").write_text("#EXTM3U\n", encoding="utf-8")
    report = {
        "generated_at": "2026-11-01T16:00:00",
        "speed_test": True,
        "stats": {"parsed_channels": 2, "unique_channels": 2, "checked_urls": 3, "ok_urls": 1, "output_channels": 1},
        "groups": {"央视": 1, "卫视": 1},
        "channels": channels,
        "failures": [{"name": item["name"], "urls": [entry["url"] for entry in item["urls"]]} for item in channels if not item["best_url"]],
        "outputs": {"m3u": str(out / "live.m3u"), "report": str(out / "report.json")},
        "epg_urls": [],
        "epg": {"mapping": False, "channels": 0, "matched": 0, "sources": []},
        "sources": [],
    }
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    config = tmp_path / "sources.yaml"
    config.write_text(
        f"""
output:
  m3u: {(out / 'live.m3u').as_posix()}
  report: {(out / 'report.json').as_posix()}
check:
  min_fluency: 10
sources:
  - name: 本地样例
    path: {source.as_posix()}
""",
        encoding="utf-8",
    )
    return str(config)


def _sample_channels() -> list[dict]:
    return [
        _channel("a", "央视一", "央视", [_url("http://a.test/1.m3u8", "ok", 60.0), _url("http://a.test/2.m3u8")], "http://a.test/1.m3u8"),
        _channel("b", "卫视二", "卫视", [_url("http://b.test/1.m3u8")], None),
    ]


def _wait_retest(manager: RunManager, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = manager.snapshot()
        if snapshot["retest"]["status"] != RunStatus.RUNNING.value:
            return snapshot
        time.sleep(0.01)
    raise AssertionError("复测未在超时内结束")


def test_retest_channels_only_hits_filtered_url(monkeypatch: pytest.MonkeyPatch) -> None:
    target = _sample_channels()[0]

    async def fake_check(client, channel, settings, speed_test: bool = True) -> CheckResult:
        return CheckResult(url=channel.url, status=CheckStatus.OK, fluency=88.0, latency_ms=90.0)

    monkeypatch.setattr(retest_module, "check_channel", fake_check)
    seen: list[tuple[str, int, int]] = []
    updated = asyncio.run(
        retest_module.retest_channels(
            AppConfig(concurrency=2),
            [target],
            {"a": ["http://a.test/2.m3u8"]},
            lambda stage, done, total: seen.append((stage.value, done, total)),
        )
    )

    entry = updated[0]
    assert [item["status"] for item in entry["urls"]] == ["ok", "ok"]
    # 未命中的地址保留原结果，被复测的地址换成新结果
    assert entry["urls"][0]["fluency"] == 60.0
    assert entry["urls"][1]["fluency"] == 88.0
    assert entry["urls"][1]["retested_at"]
    assert entry["best_url"] == "http://a.test/2.m3u8"
    assert entry["retested_at"]
    assert seen == [(PipelineStage.CHECK.value, 1, 1)]


def test_retest_merges_and_recomputes_stats(tmp_path: Path) -> None:
    channels = _sample_channels()
    # 原来可用的地址复测后失败，备选地址反而可用
    updated = [
        _channel(
            "a",
            "央视一",
            "央视",
            [_url("http://a.test/1.m3u8"), _url("http://a.test/2.m3u8", "ok", 55.0)],
            "http://a.test/2.m3u8",
        )
    ]

    def retester(config: AppConfig, targets: list[dict], url_filter, progress) -> list[dict]:
        keys = {target["key"] for target in targets}
        return [{**item, "retested_at": "2026-11-01T22:10:00"} for item in updated if item["key"] in keys]

    manager = RunManager(_write_env(tmp_path, channels), retester=retester)
    manager.start_retest(group="央视")
    snapshot = _wait_retest(manager)

    assert snapshot["retest"]["status"] == RunStatus.DONE.value
    assert snapshot["retest"]["channels"] == 1
    assert snapshot["busy"] is False
    payload = manager.report_payload()
    assert payload is not None
    assert payload["stats"]["ok_urls"] == 1
    assert payload["stats"]["output_channels"] == 1
    assert [item["name"] for item in payload["failures"]] == ["卫视二"]
    assert payload["channels"][0]["best_url"] == "http://a.test/2.m3u8"
    assert payload["channels"][0]["retested_at"]
    # 未参与复测的频道保持原结果
    assert payload["channels"][1]["best_url"] is None


def test_retest_rejects_invalid_targets(tmp_path: Path) -> None:
    manager = RunManager(_write_env(tmp_path, _sample_channels()))
    with pytest.raises(ValueError):
        manager.start_retest()
    with pytest.raises(ValueError):
        manager.start_retest(channel="nope")
    with pytest.raises(ValueError):
        manager.start_retest(group="体育")
    with pytest.raises(ValueError):
        manager.start_retest(scope="all", channel="a")
    with pytest.raises(ValueError):
        manager.start_retest(url="http://a.test/1.m3u8")
    with pytest.raises(ValueError):
        manager.start_retest(channel="a", url="http://a.test/unknown.m3u8")


def test_retest_failure_marks_status_failed(tmp_path: Path) -> None:
    def retester(config: AppConfig, targets: list[dict], url_filter, progress) -> list[dict]:
        raise RuntimeError("boom")

    manager = RunManager(_write_env(tmp_path, _sample_channels()), retester=retester)
    manager.start_retest(channel="a")
    snapshot = _wait_retest(manager)

    assert snapshot["retest"]["status"] == RunStatus.FAILED.value
    assert "boom" in snapshot["retest"]["last_error"]
    assert snapshot["busy"] is False
    # 失败不影响原有报告
    payload = manager.report_payload()
    assert payload is not None
    assert payload["channels"][0]["best_url"] == "http://a.test/1.m3u8"


def test_retest_without_report(tmp_path: Path) -> None:
    config = _write_env(tmp_path, _sample_channels())
    Path(tmp_path / "out" / "report.json").unlink()
    manager = RunManager(config)
    with pytest.raises(ValueError):
        manager.start_retest(channel="a")


def test_retest_rejects_too_many_channels(tmp_path: Path) -> None:
    channels = [_channel(f"k{index}", f"频道{index}", "其他", [_url(f"http://x.test/{index}.m3u8")], None) for index in range(501)]
    manager = RunManager(_write_env(tmp_path, channels))
    with pytest.raises(ValueError):
        manager.start_retest(scope="all")


def test_retest_busy_while_run_running(tmp_path: Path) -> None:
    gate = threading.Event()

    def runner(config: AppConfig, options: RunOptions, progress) -> None:
        gate.wait(1.0)
        return None

    manager = RunManager(_write_env(tmp_path, _sample_channels()), runner=runner)
    manager.start(RunOptions())
    with pytest.raises(RunBusyError):
        manager.start_retest(channel="a")
    gate.set()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and manager.snapshot()["status"] == RunStatus.RUNNING.value:
        time.sleep(0.01)


@pytest.fixture
def retest_server(tmp_path: Path) -> Iterator[tuple[httpx.Client, RunManager]]:
    # 接口用例不访问真实上游：注入直接返回空结果的复测器
    manager = RunManager(
        _write_env(tmp_path, _sample_channels()),
        retester=lambda config, targets, url_filter, progress: [],
    )
    server = create_server("127.0.0.1", 0, manager)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5.0) as client:
        yield client, manager
    server.shutdown()
    server.server_close()
    thread.join(1.0)


def test_retest_api_validates_and_starts(retest_server: tuple[httpx.Client, RunManager]) -> None:
    client, manager = retest_server
    assert client.post("/api/retest", json={}).status_code == 400
    assert client.post("/api/retest", json={"channel": "nope"}).status_code == 400
    assert client.post("/api/retest", json={"channels": "a"}).status_code == 400
    assert client.post("/api/retest", json={"scope": "all", "group": "央视"}).status_code == 400

    started = client.post("/api/retest", json={"channel": "a"})
    assert started.status_code == 202
    assert started.json()["retest"]["status"] == RunStatus.RUNNING.value
    time.sleep(0.2)
    assert manager.snapshot()["retest"]["status"] != RunStatus.RUNNING.value
