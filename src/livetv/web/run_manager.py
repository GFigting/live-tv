"""后台运行管理：独立线程执行流水线，向页面提供状态、进度与最近一次报告。"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..config import AppConfig, load_config
from ..constants import CheckStatus, M3U_FILE_SUFFIX, RunStatus
from ..models import RunReport
from ..pipeline import ProgressCallback, build_report_dict, local_now, resolve_output_path, run_pipeline
from .retest import retest_channels

# 单次复测的规模上限：防止误点「全部」把数千条地址打满网络
RETEST_MAX_CHANNELS = 500
RETEST_MAX_URLS = 1200


@dataclass(slots=True)
class RunOptions:
    """页面提交的一次运行参数；未填写的项沿用配置文件。"""

    limit: int | None = None
    speed_test: bool = True
    concurrency: int | None = None
    min_fluency: float | None = None
    max_per_channel: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "limit": self.limit,
            "speed_test": self.speed_test,
            "concurrency": self.concurrency,
            "min_fluency": self.min_fluency,
            "max_per_channel": self.max_per_channel,
        }


Runner = Callable[[AppConfig, RunOptions, ProgressCallback], RunReport]
Retester = Callable[[AppConfig, list[dict], dict[str, list[str]] | None, ProgressCallback], list[dict]]


class RunBusyError(RuntimeError):
    """同一时间只允许一个检测或复测运行。"""


def _default_retester(
    config: AppConfig,
    targets: list[dict],
    url_filter: dict[str, list[str]] | None,
    progress: ProgressCallback,
) -> list[dict]:
    return asyncio.run(retest_channels(config, targets, url_filter, progress))


def _default_runner(
    config: AppConfig, options: RunOptions, progress: ProgressCallback
) -> RunReport:
    if options.concurrency is not None:
        config.concurrency = options.concurrency
    if options.min_fluency is not None:
        config.min_fluency = options.min_fluency
    if options.max_per_channel is not None:
        config.max_per_channel = options.max_per_channel
    return asyncio.run(
        run_pipeline(config, limit=options.limit, check=options.speed_test, progress=progress)
    )


class RunManager:
    """维护唯一一次后台运行的状态快照，并保存最新报告供页面读取。"""

    def __init__(
        self,
        config_path: str,
        runner: Runner | None = None,
        retester: Retester | None = None,
    ) -> None:
        self._config_path = config_path
        self._runner = runner or _default_runner
        self._retester = retester or _default_retester
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._status = RunStatus.IDLE
        self._stage = None
        self._done = 0
        self._total = 0
        self._started_at: str | None = None
        self._finished_at: str | None = None
        self._error: str | None = None
        self._options: RunOptions | None = None
        self._report: dict | None = None
        self._outputs: dict[str, str] = {}
        self._min_fluency = 0.0
        self._retest_thread: threading.Thread | None = None
        self._retest_status = RunStatus.IDLE
        self._retest_done = 0
        self._retest_total = 0
        self._retest_channels = 0
        self._retest_urls = 0
        self._retest_started_at: str | None = None
        self._retest_finished_at: str | None = None
        self._retest_error: str | None = None
        self._retested_at: str | None = None
        self._load_report()

    @property
    def config_path(self) -> str:
        """配置文件路径，供源开关读写复用。"""
        return self._config_path

    def start(self, options: RunOptions) -> dict:
        """启动一次后台运行；已有运行或复测在进行时抛出 RunBusyError。"""
        with self._lock:
            if self._status is RunStatus.RUNNING or self._retest_status is RunStatus.RUNNING:
                raise RunBusyError("已有任务在运行")
            self._status = RunStatus.RUNNING
            self._options = options
            self._stage = None
            self._done = 0
            self._total = 0
            self._started_at = local_now()
            self._finished_at = None
            self._error = None
            thread = threading.Thread(target=self._work, args=(options,), name="livetv-run", daemon=True)
            self._thread = thread
        thread.start()
        return self.snapshot()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "status": self._status.value,
                "stage": self._stage.value if self._stage is not None else None,
                "progress": {"done": self._done, "total": self._total},
                "started_at": self._started_at,
                "finished_at": self._finished_at,
                "last_error": self._error,
                "options": self._options.as_dict() if self._options is not None else None,
                "has_report": self._report is not None,
                "report_generated_at": self._report.get("generated_at") if self._report else None,
                # 页面用 busy 统一禁用运行与复测入口，避免两个任务互相抢占
                "busy": self._status is RunStatus.RUNNING or self._retest_status is RunStatus.RUNNING,
                "retest": {
                    "status": self._retest_status.value,
                    "progress": {"done": self._retest_done, "total": self._retest_total},
                    "channels": self._retest_channels,
                    "urls": self._retest_urls,
                    "started_at": self._retest_started_at,
                    "finished_at": self._retest_finished_at,
                    "last_error": self._retest_error,
                    "retested_at": self._retested_at,
                },
            }

    def start_retest(
        self,
        *,
        channel: str = "",
        group: str = "",
        scope: str = "",
        channels: list[str] | None = None,
        url: str = "",
    ) -> dict:
        """按频道、分组或范围发起复测；目标不合法或超限时抛出 ValueError。"""
        with self._lock:
            if self._status is RunStatus.RUNNING or self._retest_status is RunStatus.RUNNING:
                raise RunBusyError("已有任务在运行")
            report = self._report
            if report is None:
                raise ValueError("尚未生成报告，请先运行一次检测")
            targets, url_filter = self._resolve_retest_targets(
                report, channel, group, scope, channels, url
            )
            total = _retest_url_total(targets, url_filter)
            self._retest_status = RunStatus.RUNNING
            self._retest_done = 0
            self._retest_total = total
            self._retest_channels = len(targets)
            self._retest_urls = total
            self._retest_started_at = local_now()
            self._retest_finished_at = None
            self._retest_error = None
            thread = threading.Thread(
                target=self._retest_work,
                args=(targets, url_filter),
                name="livetv-retest",
                daemon=True,
            )
            self._retest_thread = thread
        thread.start()
        return self.snapshot()

    def _resolve_retest_targets(
        self,
        report: dict,
        channel: str,
        group: str,
        scope: str,
        channels: list[str] | None,
        url: str,
    ) -> tuple[list[dict], dict[str, list[str]] | None]:
        """把页面选择器解析为频道集合与可选的地址过滤。"""
        if url and not channel:
            raise ValueError("按地址复测需要同时指定 channel")
        selectors = sum(1 for value in (channel, group, scope) if value) + (1 if channels else 0)
        if selectors != 1:
            raise ValueError("需要提供 channel、group、scope 或 channels 之一")
        items = report.get("channels") or []
        if channel:
            targets = [item for item in items if item.get("key") == channel]
            if not targets:
                raise ValueError(f"未知频道：{channel}")
        elif channels:
            keys = {str(key) for key in channels}
            targets = [item for item in items if item.get("key") in keys]
            if not targets:
                raise ValueError("指定的频道都不在报告中")
        elif group:
            targets = [item for item in items if item.get("group") == group]
            if not targets:
                raise ValueError(f"分组「{group}」没有频道")
        elif scope == "failed":
            targets = [item for item in items if not item.get("best_url")]
            if not targets:
                raise ValueError("没有测出失败的频道")
        elif scope == "all":
            targets = list(items)
        else:
            raise ValueError(f"未知范围：{scope}")
        if len(targets) > RETEST_MAX_CHANNELS:
            raise ValueError(f"单次最多复测 {RETEST_MAX_CHANNELS} 个频道，请按分组分批")
        if url:
            known = {entry.get("url") for entry in targets[0].get("urls") or []}
            if url not in known:
                raise ValueError("该地址不在该频道的候选中")
            targets = targets[:1]
            url_filter: dict[str, list[str]] | None = {str(targets[0].get("key")): [url]}
        else:
            url_filter = None
        if _retest_url_total(targets, url_filter) > RETEST_MAX_URLS:
            raise ValueError(f"单次最多复测 {RETEST_MAX_URLS} 条地址，请缩小范围")
        return targets, url_filter

    def report_payload(self) -> dict | None:
        with self._lock:
            return self._report

    def allowed_downloads(self) -> dict[str, Path]:
        """只放行报告声明的 M3U 产物，且文件必须真实存在。"""
        with self._lock:
            outputs = dict(self._outputs)
        allowed: dict[str, Path] = {}
        for value in outputs.values():
            path = resolve_output_path(value)
            if path.suffix.lower() != M3U_FILE_SUFFIX or not path.is_file():
                continue
            allowed[path.name] = path
        return allowed

    def _work(self, options: RunOptions) -> None:
        report: RunReport | None = None
        error: str | None = None
        try:
            config = load_config(self._config_path)
            self._min_fluency = (
                options.min_fluency if options.min_fluency is not None else config.min_fluency
            )
            report = self._runner(config, options, self._on_progress)
        except Exception as exc:  # 后台线程的失败必须落进状态，而不是静默退出
            error = f"{type(exc).__name__}: {exc}"
        payload = build_report_dict(report) if report is not None else None
        with self._lock:
            if payload is not None:
                self._report = payload
                self._outputs = _outputs_of(payload)
                self._status = RunStatus.DONE
            else:
                self._status = RunStatus.FAILED
                self._error = error
            self._finished_at = local_now()
            self._thread = None

    def _on_progress(self, stage, done: int, total: int) -> None:
        with self._lock:
            self._stage = stage
            self._done = done
            self._total = total

    def _on_retest_progress(self, stage, done: int, total: int) -> None:
        with self._lock:
            self._retest_done = done
            self._retest_total = total

    def _retest_work(self, targets: list[dict], url_filter: dict[str, list[str]] | None) -> None:
        """后台线程执行复测，成功后把结果合并回内存报告。"""
        updated: list[dict] | None = None
        error: str | None = None
        try:
            config = load_config(self._config_path)
            updated = self._retester(config, targets, url_filter, self._on_retest_progress)
        except Exception as exc:  # 复测失败只影响本次操作，不改动已有报告
            error = f"{type(exc).__name__}: {exc}"
        with self._lock:
            try:
                if updated is not None:
                    self._merge_retest(updated)
                    self._retest_done = self._retest_total
                    self._retest_status = RunStatus.DONE
                    self._retested_at = local_now()
                else:
                    self._retest_status = RunStatus.FAILED
                    self._retest_error = error
            except Exception as exc:  # 合并阶段异常也必须落状态，避免页面一直停在复测中
                self._retest_status = RunStatus.FAILED
                self._retest_error = f"{type(exc).__name__}: {exc}"
            finally:
                self._retest_finished_at = local_now()
                self._retest_thread = None

    def _merge_retest(self, updated: list[dict]) -> None:
        """按 key 合并复测结果，并重算可用数、输出频道数与失败清单。"""
        report = self._report
        if report is None:
            return
        channels = report.get("channels") or []
        positions = {item.get("key"): index for index, item in enumerate(channels)}
        for payload in updated:
            position = positions.get(payload.get("key"))
            if position is not None:
                channels[position] = payload
        stats = report.get("stats")
        if isinstance(stats, dict):
            stats["ok_urls"] = sum(
                1
                for item in channels
                for entry in item.get("urls") or []
                if entry.get("status") == CheckStatus.OK.value
            )
            stats["output_channels"] = sum(
                1
                for item in channels
                if item.get("best_url") and float(item.get("fluency") or 0) >= self._min_fluency
            )
        if report.get("speed_test"):
            report["failures"] = [
                {
                    "name": item.get("name"),
                    "urls": [entry.get("url") for entry in item.get("urls") or []],
                }
                for item in channels
                if not item.get("best_url")
            ]

    def _load_report(self) -> None:
        """启动时读取输出目录中的最新报告，让页面直接显示上次结果。"""
        config = load_config(self._config_path)
        self._min_fluency = config.min_fluency
        path = resolve_output_path(config.output_report)
        if not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        self._report = payload
        self._outputs = _outputs_of(payload)


def _retest_url_total(targets: list[dict], url_filter: dict[str, list[str]] | None) -> int:
    """统计本次复测要请求的地址数，用于进度与上限判断。"""
    if url_filter is not None:
        return sum(len(urls) for urls in url_filter.values())
    return sum(len(item.get("urls") or []) for item in targets)


def _outputs_of(payload: dict) -> dict[str, str]:
    outputs = payload.get("outputs")
    if not isinstance(outputs, dict):
        return {}
    return {str(key): str(value) for key, value in outputs.items() if isinstance(value, str)}
