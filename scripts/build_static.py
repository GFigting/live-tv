"""把控制台打包成内嵌报告快照的静态页面，并附带 Collect-IPTV 式参考产物。

构建脚本读取 `output/report.json` 与 `output/live.m3u`，产出可直接上传到
静态托管（GitHub Pages / EdgeOne Pages）的 `dist/` 目录：

- dist/index.html              控制台只读快照（报告内嵌在 #report-snapshot）
- dist/reference.html          Collect-IPTV 式监控页（运行时拉取 ./report.json）
- dist/live.m3u                已检测、可直接导入 VLC/TVBox 的播放列表
- dist/live-ipv4.m3u / live-ipv6.m3u（若生成）
- dist/report.json            原始报告（供 reference.html 实时拉取）
- dist/static/lib/hls.min.js
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "src" / "livetv" / "web" / "static"
MARKER = '<script src="/static/lib/hls.min.js"></script>'
DEFAULT_REPORT = ROOT / "output" / "report.json"
DEFAULT_M3U = ROOT / "output" / "live.m3u"
REFERENCE_TPL = STATIC_DIR / "reference.html"


def build(report_path: Path, m3u_path: Path, out_dir: Path) -> int:
    if not report_path.is_file():
        print(f"找不到报告：{report_path}，请先运行一次检测。", file=sys.stderr)
        return 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    if MARKER not in html:
        print("index.html 里找不到 hls 脚本标记，无法注入快照。", file=sys.stderr)
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) 控制台只读快照：把报告内嵌进 #report-snapshot
    payload = json.dumps(report, ensure_ascii=False).replace("</", "<\\/")
    snapshot = f'<script id="report-snapshot" type="application/json">{payload}</script>'
    html = html.replace(MARKER, f"{MARKER}\n{snapshot}", 1)
    (out_dir / "index.html").write_text(html, encoding="utf-8")

    # 2) 静态资源
    lib_target = out_dir / "static" / "lib"
    lib_target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(STATIC_DIR / "lib" / "hls.min.js", lib_target / "hls.min.js")

    # 3) 原始报告（供 reference.html 实时拉取，与部署解耦）
    shutil.copyfile(report_path, out_dir / "report.json")

    # 4) 已检测 M3U（可导入播放器）+ 协议族拆分版
    copied_m3u = []
    for src in [
        m3u_path,
        ROOT / "output" / "live-ipv4.m3u",
        ROOT / "output" / "live-ipv6.m3u",
    ]:
        if src.is_file():
            shutil.copyfile(src, out_dir / src.name)
            copied_m3u.append(src.name)

    # 5) Collect-IPTV 式参考监控页
    if REFERENCE_TPL.is_file():
        shutil.copyfile(REFERENCE_TPL, out_dir / "reference.html")

    channels = report.get("channels") or []
    alive = sum(1 for c in channels if c.get("best_url"))
    size = (out_dir / "index.html").stat().st_size
    print(f"已生成静态站点到 {out_dir}")
    print(f"  控制台快照：index.html（{len(channels)} 频道，{size // 1024} KB）")
    print(f"  参考监控页：reference.html")
    print(f"  可导入清单：{', '.join(copied_m3u) or '（无）'}")
    print(f"  存活频道：{alive}/{len(channels)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="生成内嵌报告快照 + 参考产物的静态站点")
    parser.add_argument("--report", default=str(DEFAULT_REPORT), help="报告路径")
    parser.add_argument("--m3u", default=str(DEFAULT_M3U), help="已检测 M3U 路径")
    parser.add_argument("--out", default=str(ROOT / "dist"), help="输出目录")
    args = parser.parse_args()
    return build(Path(args.report), Path(args.m3u), Path(args.out))


if __name__ == "__main__":
    raise SystemExit(main())
