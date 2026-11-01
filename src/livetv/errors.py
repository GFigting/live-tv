"""领域异常：区分配置错误与运行期失败，便于 CLI 给出可读提示。"""


class LiveTvError(Exception):
    """项目内所有可预期错误的基类。"""


class ConfigError(LiveTvError):
    """配置缺失或结构非法。"""


class SourceError(LiveTvError):
    """源清单获取失败。"""
