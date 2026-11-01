"""流水线用例：离线端到端输出、最低流畅度过滤与 CLI 入口。"""

import asyncio
import json

from livetv import cli
from livetv.checker import CheckResult
from livetv.config import AppConfig, SourceSpec
from livetv.constants import CheckStatus, PipelineStage
from livetv.pipeline import run_pipeline

SOURCE_M3U = """#EXTM3U
#EXTINF:-1 group-title="央视",CCTV1
http://a.example.com/cctv1.m3u8
#EXTINF:-1 group-title="央视",CCTV1 高清
http://b.example.com/cctv1.m3u8
#EXTINF:-1 group-title="卫视",湖南卫视
http://a.example.com/hunan.m3u8
"""


def _prepare(tmp_path):
    source = tmp_path / "source.m3u"
    source.write_text(SOURCE_M3U, encoding="utf-8")
    config = tmp_path / "sources.yaml"
    config.write_text(
        f"""
output:
  m3u: {tmp_path.as_posix()}/out/live.m3u
  report: {tmp_path.as_posix()}/out/report.json
check:
  min_fluency: 10
  max_per_channel: 2
sources:
  - name: 本地样例
    path: {source.as_posix()}
""",
        encoding="utf-8",
    )
    return config


def test_pipeline_offline_outputs(tmp_path):
    config_path = _prepare(tmp_path)
    config = AppConfig(
        sources=[SourceSpec(name="本地样例", location=str(tmp_path / "source.m3u"), is_local=True)],
        output_m3u=f"{tmp_path.as_posix()}/out/live.m3u",
        output_report=f"{tmp_path.as_posix()}/out/report.json",
        config_path=str(tmp_path / "sources.yaml"),
    )
    report = asyncio.run(run_pipeline(config, check=False))

    m3u = (tmp_path / "out" / "live.m3u").read_text(encoding="utf-8")
    payload = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))

    assert m3u.startswith("#EXTM3U")
    assert "CCTV1" in m3u and "湖南卫视" in m3u
    # 同名不同写法合并为一个频道
    assert payload["stats"]["parsed_channels"] == 3
    assert payload["stats"]["unique_channels"] == 2
    assert payload["stats"]["output_channels"] == 2
    assert config_path.is_file()


def test_pipeline_filters_by_min_fluency(tmp_path, monkeypatch):
    async def fake_check(client, channel, settings, speed_test=True):
        fluency = 80.0 if "cctv1" in channel.url else 2.0
        return CheckResult(url=channel.url, status=CheckStatus.OK, fluency=fluency)

    monkeypatch.setattr("livetv.pipeline.check_channel", fake_check)

    config = AppConfig(
        sources=[SourceSpec(name="本地样例", location=str(tmp_path / "sources.m3u"), is_local=True)],
        output_m3u=f"{tmp_path.as_posix()}/out/live.m3u",
        output_report=f"{tmp_path.as_posix()}/out/report.json",
        min_fluency=10,
    )
    (tmp_path / "sources.m3u").write_text(SOURCE_M3U, encoding="utf-8")
    report = asyncio.run(run_pipeline(config, check=True))

    m3u = (tmp_path / "out" / "live.m3u").read_text(encoding="utf-8")
    assert "CCTV1" in m3u
    assert "湖南卫视" not in m3u
    assert report.output_channels == 1


def test_pipeline_localizes_groups_and_reports_latency_fields(tmp_path, monkeypatch):
    async def fake_check(client, channel, settings, speed_test=True):
        return CheckResult(
            url=channel.url,
            status=CheckStatus.OK,
            fluency=80.0,
            latency_ms=100.0,
            playlist_latency_ms=40.0,
            segment_latency_ms=60.0,
        )

    monkeypatch.setattr("livetv.pipeline.check_channel", fake_check)

    source = tmp_path / "english.m3u"
    source.write_text(
        "#EXTM3U\n"
        '#EXTINF:-1 group-title="News",CGTN\n'
        "http://a.example.com/cgtn.m3u8\n"
        '#EXTINF:-1 group-title="Undefined",Some Channel\n'
        "http://a.example.com/some.m3u8\n",
        encoding="utf-8",
    )
    config = AppConfig(
        sources=[SourceSpec(name="英文分组样例", location=str(source), is_local=True)],
        output_m3u=f"{tmp_path.as_posix()}/out/live.m3u",
        output_report=f"{tmp_path.as_posix()}/out/report.json",
    )
    asyncio.run(run_pipeline(config, check=True))

    m3u = (tmp_path / "out" / "live.m3u").read_text(encoding="utf-8")
    payload = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))

    assert "央视" in payload["groups"] and "其他" in payload["groups"]
    assert "News" not in m3u
    assert 'group-title="央视"' in m3u
    assert 'group-tags="新闻"' in m3u
    for item in payload["channels"]:
        assert item["playlist_latency_ms"] == 40.0
        assert item["segment_latency_ms"] == 60.0
        assert item["latency_ms"] == 100.0
        for url in item["urls"]:
            assert "playlist_latency_ms" in url and "segment_latency_ms" in url


def test_pipeline_writes_epg_header_and_family_outputs(tmp_path, monkeypatch):
    async def fake_check(client, channel, settings, speed_test=True):
        return CheckResult(
            url=channel.url,
            status=CheckStatus.OK,
            fluency=80.0,
            ip_family="ipv4",
        )

    monkeypatch.setattr("livetv.pipeline.check_channel", fake_check)

    source = tmp_path / "source.m3u"
    source.write_text(SOURCE_M3U, encoding="utf-8")
    config = AppConfig(
        sources=[SourceSpec(name="本地样例", location=str(source), is_local=True)],
        output_m3u=f"{tmp_path.as_posix()}/out/live.m3u",
        output_report=f"{tmp_path.as_posix()}/out/report.json",
        epg_urls=["https://epg.example.com/a.xml"],
        split_by_family=True,
    )
    asyncio.run(run_pipeline(config, check=True))

    m3u = (tmp_path / "out" / "live.m3u").read_text(encoding="utf-8")
    ipv4 = (tmp_path / "out" / "live-ipv4.m3u").read_text(encoding="utf-8")
    ipv6 = (tmp_path / "out" / "live-ipv6.m3u").read_text(encoding="utf-8")
    payload = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))

    assert m3u.startswith('#EXTM3U x-tvg-url="https://epg.example.com/a.xml"')
    assert "CCTV1" in ipv4 and "湖南卫视" in ipv4
    assert "CCTV1" not in ipv6 and "湖南卫视" not in ipv6
    assert payload["epg_urls"] == ["https://epg.example.com/a.xml"]
    assert "ipv4" in payload["outputs"] and "ipv6" in payload["outputs"]
    assert payload["channels"][0]["ip_family"] == "ipv4"


EPG_XML = """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="CCTV1.cn"><display-name>CCTV-1</display-name></channel>
  <channel id="HNTV.cn"><display-name>湖南卫视</display-name></channel>
</tv>
"""


def _fake_ok_check(monkeypatch):
    async def fake_check(client, channel, settings, speed_test=True):
        return CheckResult(url=channel.url, status=CheckStatus.OK, fluency=80.0)

    monkeypatch.setattr("livetv.pipeline.check_channel", fake_check)


def test_pipeline_maps_tvg_ids_from_epg(tmp_path, monkeypatch):
    _fake_ok_check(monkeypatch)
    source = tmp_path / "source.m3u"
    source.write_text(SOURCE_M3U, encoding="utf-8")
    (tmp_path / "epg.xml").write_text(EPG_XML, encoding="utf-8")

    config = AppConfig(
        sources=[SourceSpec(name="本地样例", location=str(source), is_local=True)],
        output_m3u=f"{tmp_path.as_posix()}/out/live.m3u",
        output_report=f"{tmp_path.as_posix()}/out/report.json",
        epg_urls=["epg.xml", "https://epg.example.com/a.xml"],
        base_dir=tmp_path,
    )
    asyncio.run(run_pipeline(config, check=True))

    m3u = (tmp_path / "out" / "live.m3u").read_text(encoding="utf-8")
    payload = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))

    assert 'tvg-id="CCTV1.cn"' in m3u
    assert 'tvg-id="HNTV.cn"' in m3u
    # 本地 EPG 文件只用于映射，不写入 x-tvg-url
    assert m3u.splitlines()[0] == '#EXTM3U x-tvg-url="https://epg.example.com/a.xml"'
    assert payload["epg"]["mapping"] is True
    assert payload["epg"]["channels"] == 2
    assert payload["epg"]["matched"] == 2
    assert payload["epg"]["sources"][0] == {
        "location": "epg.xml",
        "channels": 2,
        "error": None,
    }


def test_pipeline_records_epg_error_without_failing(tmp_path, monkeypatch):
    _fake_ok_check(monkeypatch)
    source = tmp_path / "source.m3u"
    source.write_text(SOURCE_M3U, encoding="utf-8")

    config = AppConfig(
        sources=[SourceSpec(name="本地样例", location=str(source), is_local=True)],
        output_m3u=f"{tmp_path.as_posix()}/out/live.m3u",
        output_report=f"{tmp_path.as_posix()}/out/report.json",
        epg_urls=["missing-epg.xml"],
        base_dir=tmp_path,
    )
    asyncio.run(run_pipeline(config, check=True))

    m3u = (tmp_path / "out" / "live.m3u").read_text(encoding="utf-8")
    payload = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))

    assert 'tvg-id=""' in m3u
    assert payload["epg"]["matched"] == 0
    assert payload["epg"]["sources"][0]["error"]
    assert payload["stats"]["output_channels"] == 2


def test_pipeline_skips_epg_when_mapping_disabled(tmp_path, monkeypatch):
    _fake_ok_check(monkeypatch)
    source = tmp_path / "source.m3u"
    source.write_text(SOURCE_M3U, encoding="utf-8")
    (tmp_path / "epg.xml").write_text(EPG_XML, encoding="utf-8")

    config = AppConfig(
        sources=[SourceSpec(name="本地样例", location=str(source), is_local=True)],
        output_m3u=f"{tmp_path.as_posix()}/out/live.m3u",
        output_report=f"{tmp_path.as_posix()}/out/report.json",
        epg_urls=["epg.xml"],
        epg_map=False,
        base_dir=tmp_path,
    )
    asyncio.run(run_pipeline(config, check=True))

    m3u = (tmp_path / "out" / "live.m3u").read_text(encoding="utf-8")
    payload = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))

    assert 'tvg-id=""' in m3u
    assert payload["epg"]["mapping"] is False
    assert payload["epg"]["matched"] == 0


def test_pipeline_drops_non_http_placeholder_urls(tmp_path, monkeypatch):
    _fake_ok_check(monkeypatch)
    source = tmp_path / "placeholder.m3u"
    source.write_text(
        "#EXTM3U\n"
        '#EXTINF:-1 group-title="央视",CCTV1\n'
        "http://a.example.com/cctv1.m3u8\n"
        '#EXTINF:-1 group-title="未开播",占位频道\n'
        "/[NO PUBLIC STREAM]\n",
        encoding="utf-8",
    )
    config = AppConfig(
        sources=[SourceSpec(name="占位样例", location=str(source), is_local=True)],
        output_m3u=f"{tmp_path.as_posix()}/out/live.m3u",
        output_report=f"{tmp_path.as_posix()}/out/report.json",
    )

    report = asyncio.run(run_pipeline(config, check=True))

    m3u = (tmp_path / "out" / "live.m3u").read_text(encoding="utf-8")
    assert "NO PUBLIC STREAM" not in m3u
    assert report.parsed_channels == 1
    assert report.output_channels == 1


def test_cli_no_test_mode(tmp_path, monkeypatch, capsys):
    config_path = _prepare(tmp_path)
    monkeypatch.chdir(tmp_path)
    exit_code = cli.main(["check", "--config", str(config_path), "--no-test"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "解析模式" in output
    assert (tmp_path / "out" / "live.m3u").is_file()


def test_pipeline_reports_progress(tmp_path, monkeypatch):
    _fake_ok_check(monkeypatch)
    source = tmp_path / "source.m3u"
    source.write_text(SOURCE_M3U, encoding="utf-8")
    config = AppConfig(
        sources=[SourceSpec(name="本地样例", location=str(source), is_local=True)],
        output_m3u=f"{tmp_path.as_posix()}/out/live.m3u",
        output_report=f"{tmp_path.as_posix()}/out/report.json",
    )
    calls: list[tuple[str, int, int]] = []

    asyncio.run(
        run_pipeline(
            config,
            check=True,
            progress=lambda stage, done, total: calls.append((stage.value, done, total)),
        )
    )

    stages = [stage for stage, _, _ in calls]
    assert stages[0] == PipelineStage.SOURCES.value
    assert stages[-1] == PipelineStage.DONE.value
    check_calls = [(done, total) for stage, done, total in calls if stage == PipelineStage.CHECK.value]
    assert check_calls[-1] == (3, 3)
    assert all(done <= total for done, total in check_calls)
