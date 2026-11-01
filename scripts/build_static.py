"""把控制台打包成内嵌报告快照的静态页面。

构建脚本读取 `output/report.json`，把整份报告塞进页面里的 `#report-snapshot` 节点，
产出一个不依赖任何服务端的 `dist/` 目录，可直接上传到静态托管（如 EdgeOne Pages）。
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


def build(report_path: Path, out_dir: Path) -> int:
    if not report_path.is_file():
        print(f"找不到报告：{report_path}，请先运行一次检测。", file=sys.stderr)
        return 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    if MARKER not in html:
        print("index.html 里找不到 hls 脚本标记，无法注入快照。", file=sys.stderr)
        return 1
    payload = json.dumps(report, ensure_ascii=False).replace("</", "<\\/")
    snapshot = f'<script id="report-snapshot" type="application/json">{payload}</script>'
    html = html.replace(MARKER, f"{MARKER}\n{snapshot}", 1)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    lib_target = out_dir / "static" / "lib"
    lib_target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(STATIC_DIR / "lib" / "hls.min.js", lib_target / "hls.min.js")

    channels = len(report.get("channels") or [])
    size = (out_dir / "index.html").stat().st_size
    print(f"已生成 {out_dir / 'index.html'}" + f"（{channels} 个频道，{size / 1024:.0f} KB）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="生成内嵌报告快照的静态页面")
    parser.add_argument("--report", default=str(ROOT / "output" / "report.json"), help="报告路径")
    parser.add_argument("--out", default=str(ROOT / "dist"), help="输出目录")
    args = parser.parse_args()
    return build(Path(args.report), Path(args.out))


if __name__ == "__main__":
    raise SystemExit(main())
