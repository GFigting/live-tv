"""流畅度评分用例：档位边界与区间约束。"""

from livetv.scorer import bitrate_from_measurement, score_from_bitrate


def test_score_bands_are_ordered():
    assert score_from_bitrate(3_000_000) >= 90
    assert score_from_bitrate(1_200_000) >= 70
    assert score_from_bitrate(500_000) >= 45
    assert score_from_bitrate(150_000) >= 20
    assert score_from_bitrate(20_000) < 20


def test_score_bounds_and_invalid_input():
    assert score_from_bitrate(None) == 0.0
    assert score_from_bitrate(0) == 0.0
    assert score_from_bitrate(10_000_000) <= 100.0
    assert score_from_bitrate(-1) == 0.0


def test_bitrate_from_measurement():
    assert bitrate_from_measurement(375_000, 3.0) == 1_000_000
    assert bitrate_from_measurement(0, 3.0) is None
    assert bitrate_from_measurement(100, 0) is None
