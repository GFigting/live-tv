"""流畅度评分：把实测比特率映射为 0-100 分，便于排序与阈值过滤。"""

from __future__ import annotations

from .constants import (
    BITRATE_EXCELLENT,
    BITRATE_FAIR,
    BITRATE_GOOD,
    BITRATE_LOW,
    FLUENCY_SCORE_MAX,
)


def score_from_bitrate(bitrate: float | None) -> float:
    """按比特率(bps) 计算流畅度，档位内线性过渡。"""
    if not bitrate or bitrate <= 0:
        return 0.0
    if bitrate >= BITRATE_EXCELLENT:
        extra = (bitrate - BITRATE_EXCELLENT) / BITRATE_EXCELLENT
        return _clamp(90.0 + extra * 10.0)
    if bitrate >= BITRATE_GOOD:
        ratio = (bitrate - BITRATE_GOOD) / (BITRATE_EXCELLENT - BITRATE_GOOD)
        return _clamp(70.0 + ratio * 20.0)
    if bitrate >= BITRATE_FAIR:
        ratio = (bitrate - BITRATE_FAIR) / (BITRATE_GOOD - BITRATE_FAIR)
        return _clamp(45.0 + ratio * 25.0)
    if bitrate >= BITRATE_LOW:
        ratio = (bitrate - BITRATE_LOW) / (BITRATE_FAIR - BITRATE_LOW)
        return _clamp(20.0 + ratio * 25.0)
    ratio = bitrate / BITRATE_LOW
    return _clamp(ratio * 20.0)


def bitrate_from_measurement(bytes_read: int, elapsed_seconds: float) -> float | None:
    """由读取字节数与耗时换算比特率。"""
    if bytes_read <= 0 or elapsed_seconds <= 0:
        return None
    return bytes_read * 8 / elapsed_seconds


def _clamp(value: float) -> float:
    return max(0.0, min(FLUENCY_SCORE_MAX, round(value, 1)))
