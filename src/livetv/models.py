"""数据模型：频道、检测结果、频道结果组与运行报告。"""

from __future__ import annotations

from dataclasses import dataclass, field

from .constants import CheckStatus, UNKNOWN_GROUP


@dataclass(slots=True)
class Channel:
    """一条直播源记录。"""

    name: str
    url: str
    group: str = UNKNOWN_GROUP
    logo: str | None = None
    tvg_id: str | None = None
    source: str | None = None


@dataclass(slots=True)
class CheckResult:
    """单条 URL 的检测结果。"""

    url: str
    status: CheckStatus
    http_status: int | None = None
    reason: str = ""
    latency_ms: float | None = None
    playlist_latency_ms: float | None = None
    segment_latency_ms: float | None = None
    fluency: float = 0.0
    bitrate: float | None = None
    segment_url: str | None = None
    ip_family: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is CheckStatus.OK


@dataclass(slots=True)
class ChannelGroup:
    """归一化后的同名频道，保留多条候选 URL。"""

    key: str
    name: str
    group: str = UNKNOWN_GROUP
    group_tags: list[str] = field(default_factory=list)
    logo: str | None = None
    tvg_id: str | None = None
    channels: list[Channel] = field(default_factory=list)

    def urls(self) -> list[str]:
        return [channel.url for channel in self.channels]


@dataclass(slots=True)
class ChannelResult:
    """同名频道在检测后的最终结果。"""

    group: ChannelGroup
    results: list[CheckResult] = field(default_factory=list)

    def best(self) -> CheckResult | None:
        ok_results = [result for result in self.results if result.ok]
        if not ok_results:
            return None
        return max(ok_results, key=lambda result: (result.fluency, -(result.latency_ms or 0)))

    def alternatives(self, limit: int) -> list[CheckResult]:
        best = self.best()
        if best is None:
            return []
        rest = [result for result in self.results if result.ok and result.url != best.url]
        rest.sort(key=lambda result: result.fluency, reverse=True)
        return rest[: max(limit - 1, 0)]


@dataclass(slots=True)
class SourceReport:
    """单个源清单的处理统计。"""

    name: str
    location: str
    parsed: int = 0
    error: str | None = None


@dataclass(slots=True)
class EpgSourceReport:
    """单个 EPG 源的处理统计。"""

    location: str
    channels: int = 0
    error: str | None = None


@dataclass(slots=True)
class RunReport:
    """一次运行的汇总报告。"""

    generated_at: str
    config_path: str
    output_m3u: str
    output_report: str
    speed_test: bool
    epg_urls: list[str] = field(default_factory=list)
    epg_mapping: bool = False
    epg_matched: int = 0
    epg_sources: list[EpgSourceReport] = field(default_factory=list)
    output_ipv4: str | None = None
    output_ipv6: str | None = None
    sources: list[SourceReport] = field(default_factory=list)
    channel_results: list[ChannelResult] = field(default_factory=list)
    parsed_channels: int = 0
    unique_channels: int = 0
    output_channels: int = 0

    @property
    def checked_urls(self) -> int:
        return sum(len(result.results) for result in self.channel_results)

    @property
    def epg_channels(self) -> int:
        return sum(source.channels for source in self.epg_sources)

    @property
    def ok_urls(self) -> int:
        return sum(1 for result in self.channel_results for item in result.results if item.ok)
