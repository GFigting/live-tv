"""固定大类归类与标签拆分用例。"""

from livetv.grouping import classify_group, split_group_tags


def test_split_group_tags_localizes_and_splits_composite():
    assert split_group_tags("Animation;Kids") == ["动画", "少儿"]
    assert split_group_tags("News") == ["新闻"]
    assert split_group_tags("Undefined") == []
    assert split_group_tags("地方频道;News") == ["地方频道", "新闻"]
    assert split_group_tags("📺央视频道") == ["央视频道"]
    assert split_group_tags("") == []
    assert split_group_tags(None) == []


def test_classify_uses_channel_name_first():
    assert classify_group([], "CCTV1") == "央视"
    assert classify_group([], "CGTN") == "央视"
    assert classify_group([], "湖南卫视") == "卫视"
    assert classify_group([], "凤凰卫视") == "港澳台"
    assert classify_group([], "广东体育") == "地方"
    assert classify_group([], "北京卫视") == "卫视"


def test_classify_maps_content_types():
    assert classify_group(["动画", "少儿"], "Some Channel") == "少儿"
    assert classify_group(["教育"], "Some Channel") == "纪录"
    assert classify_group(["新闻"], "Channel X") == "新闻"
    assert classify_group(["电影"], "Movie Box") == "影视"
    assert classify_group(["体育"], "Sport TV") == "体育"
    assert classify_group(["音乐"], "Stage") == "音乐"


def test_classify_foreign_and_unknown():
    assert classify_group(["Italy"], "Rai 1") == "国际"
    assert classify_group(["日本 / Japan"], "NHK") == "国际"
    assert classify_group(["其他频道"], "X") == "其他"
    assert classify_group([], "Random Channel") == "其他"
