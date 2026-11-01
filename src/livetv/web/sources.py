"""源清单的读取与启用状态改写：按行编辑，保留原有注释与排版。"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path

import yaml

# 与 sources.yaml 的缩进风格保持一致：源条目为 "- name: xxx"，启用行缩进两级
SOURCE_NAME = re.compile(r"^\s*-\s*name:\s*(.+?)\s*$")
SOURCE_ENABLED = re.compile(r"^(\s*)enabled:\s*(true|false)\s*$", re.IGNORECASE)
SOURCE_TOP = re.compile(r"^sources:\s*$")

_LOCK = threading.Lock()


class SourceNameError(ValueError):
    """源名称在配置中不存在或不符合要求。"""


class SourceUrlError(ValueError):
    """源地址格式不合法。"""


def list_sources(config_path: str) -> list[dict[str, object]]:
    """读取配置，返回每条源的名称、地址、分组与启用状态。"""
    data = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    sources = data.get("sources") or []
    result: list[dict[str, object]] = []
    for item in sources:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        result.append({
            "name": name,
            "url": str(item.get("url") or item.get("path") or ""),
            "group": str(item.get("group") or ""),
            "enabled": bool(item.get("enabled", True)),
        })
    return result


def add_source(config_path: str, name: str, url: str, group: str = "") -> list[dict[str, object]]:
    """在 sources 段末尾追加一条源，保留原有注释与排版。"""
    name = (name or "").strip()
    url = (url or "").strip()
    group = (group or "").strip()
    if not name:
        raise SourceNameError("源名称不能为空")
    if len(name) > 60:
        raise SourceNameError("源名称过长（最多 60 字）")
    if not url.startswith(("http://", "https://")):
        raise SourceUrlError("源地址必须以 http:// 或 https:// 开头")

    path = Path(config_path)
    with _LOCK:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        for line in lines:
            matched = SOURCE_NAME.match(line.rstrip("\r\n"))
            if matched and _clean_name(matched.group(1)) == name:
                raise SourceNameError(f"已存在同名源：{name}")
        insert_at = _sources_end(lines)
        ending = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
        block: list[str] = []
        if insert_at > 0 and lines[insert_at - 1].strip():
            block.append(ending)
        block.append(f"  # 手动添加：{name}{ending}")
        block.append(f"  - name: {_quote(name)}{ending}")
        block.append(f"    url: {url}{ending}")
        if group:
            block.append(f"    group: {_quote(group)}{ending}")
        block.append(f"    enabled: true{ending}")
        if insert_at == len(lines) and lines and not lines[-1].endswith("\n"):
            lines[-1] = lines[-1] + ending
        lines[insert_at:insert_at] = block
        _atomic_write(path, "".join(lines))
    # 写回后立即重载校验，避免生成不可解析的 YAML
    return list_sources(config_path)


def _sources_end(lines: list[str]) -> int:
    """返回 sources 段末尾的插入行号，即下一个顶层键之前或文件末尾。"""
    start = None
    for index, line in enumerate(lines):
        if SOURCE_TOP.match(line.rstrip("\r\n")):
            start = index
            break
    if start is None:
        raise SourceNameError("配置中缺少 sources 段，无法追加")
    for cursor in range(start + 1, len(lines)):
        raw = lines[cursor].rstrip("\r\n")
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if raw.startswith((" ", "\t", "-")):
            continue
        return cursor
    return len(lines)


def _quote(value: str) -> str:
    """按需给 YAML 标量加引号，避免名称里的冒号或井号破坏结构。"""
    if '"' not in value and not any(mark in value for mark in (":", "#", "'")):
        return value
    if '"' not in value:
        return f'"{value}"'
    if "'" not in value:
        return f"'{value}'"
    raise SourceNameError("名称不能同时包含单引号与双引号")


def set_source_enabled(config_path: str, name: str, enabled: bool) -> list[dict[str, object]]:
    """切换指定源的启用状态并写回文件，只改该源的 enabled 行。

    用逐行替换而不是整体序列化，避免 PyYAML 丢掉配置里的中文注释与空行。
    """
    path = Path(config_path)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    target = _locate_source(lines, name)
    if target is None:
        raise SourceNameError(f"配置中不存在源：{name}")
    start, end = target
    enabled_index = _locate_enabled(lines, start, end)
    if enabled_index is None:
        raise SourceNameError(f"源缺少 enabled 字段：{name}")
    line = lines[enabled_index]
    ending = "\r\n" if line.endswith("\r\n") else "\n"
    content = line.rstrip("\r\n")
    indent, _ = SOURCE_ENABLED.match(content).groups()  # type: ignore[union-attr]
    lines[enabled_index] = f"{indent}enabled: {'true' if enabled else 'false'}{ending}"
    with _LOCK:
        _atomic_write(path, "".join(lines))
    # 写回后立即重载校验，避免生成不可解析的 YAML
    return list_sources(config_path)


def _locate_source(lines: list[str], name: str) -> tuple[int, int] | None:
    """定位某个源条目的行范围 [start, end)，end 为下一条源或文件末尾。"""
    for index, line in enumerate(lines):
        match = SOURCE_NAME.match(line.rstrip("\r\n"))
        if not match:
            continue
        if _clean_name(match.group(1)) != name:
            continue
        end = len(lines)
        for cursor in range(index + 1, len(lines)):
            if SOURCE_NAME.match(lines[cursor].rstrip("\r\n")):
                end = cursor
                break
        return index, end
    return None


def _locate_enabled(lines: list[str], start: int, end: int) -> int | None:
    """在源条目范围内查找 enabled 行。"""
    for index in range(start, end):
        if SOURCE_ENABLED.match(lines[index].rstrip("\r\n")):
            return index
    return None


def _clean_name(value: str) -> str:
    """去掉 YAML 中可能出现的引号。"""
    name = value.strip()
    if len(name) >= 2 and name[0] == name[-1] and name[0] in {'"', "'"}:
        return name[1:-1]
    return name


def _atomic_write(path: Path, text: str) -> None:
    """先写临时文件再替换，避免写入中断损坏配置。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
