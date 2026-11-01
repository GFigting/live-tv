"""命令行入口：`livetv check` 执行一次采集、检测与发布。"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from .config import load_config
from .constants import DEFAULT_CONFIG, DEFAULT_WEB_HOST, DEFAULT_WEB_PORT
from .errors import LiveTvError
from .pipeline import run_pipeline
from .web.server import run_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="livetv", description="本地 IPTV 直播源采集与检测流水线")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="采集源清单、检测可用性并输出 M3U 与报告")
    check.add_argument("--config", default=DEFAULT_CONFIG, help="源清单配置文件路径")
    check.add_argument("--limit", type=int, default=None, help="只处理前 N 个去重后的频道")
    check.add_argument("--no-test", action="store_true", help="只解析与合并，不发起网络检测")
    check.add_argument("--out", default=None, help="输出 M3U 路径（覆盖配置）")
    check.add_argument("--report", default=None, help="输出 JSON 报告路径（覆盖配置）")
    check.add_argument("--concurrency", type=int, default=None, help="并发检测数（覆盖配置）")
    check.add_argument("--min-fluency", type=float, default=None, help="最低流畅度阈值（覆盖配置）")
    check.add_argument("--max-per-channel", type=int, default=None, help="每频道最多输出条数")

    serve = sub.add_parser("serve", help="启动本地查看页面，可浏览结果并触发检测")
    serve.add_argument("--config", default=DEFAULT_CONFIG, help="源清单配置文件路径")
    serve.add_argument("--host", default=DEFAULT_WEB_HOST, help="监听地址（默认仅本机回环）")
    serve.add_argument("--port", type=int, default=DEFAULT_WEB_PORT, help="监听端口")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "serve":
        return run_server(args.config, args.host, args.port)

    try:
        config = load_config(args.config)
    except LiveTvError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    if args.out:
        config.output_m3u = args.out
    if args.report:
        config.output_report = args.report
    if args.concurrency:
        config.concurrency = args.concurrency
    if args.min_fluency is not None:
        config.min_fluency = args.min_fluency
    if args.max_per_channel:
        config.max_per_channel = args.max_per_channel

    report = asyncio.run(
        run_pipeline(config, limit=args.limit, check=not args.no_test)
    )

    scope = "解析模式（未检测）" if args.no_test else "检测模式"
    print(f"运行完成：{scope}")
    print(f"  源清单：{len(report.sources)} 个（失败 {sum(1 for item in report.sources if item.error)}）")
    print(f"  解析频道：{report.parsed_channels}，去重后：{report.unique_channels}")
    if args.no_test:
        print(f"  候选 URL：{report.checked_urls}")
    else:
        print(f"  检测 URL：{report.checked_urls}，可用：{report.ok_urls}")
    print(f"  输出频道：{report.output_channels}")
    print(f"  订阅文件：{report.output_m3u}")
    print(f"  运行报告：{report.output_report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
