"""统一常量：阈值、标记、默认值与默认路径集中在此，避免散落魔法值。"""

from __future__ import annotations

from enum import StrEnum

# 默认路径（相对项目根目录）
DEFAULT_CONFIG = "config/sources.yaml"
DEFAULT_OUTPUT_M3U = "output/live.m3u"
DEFAULT_OUTPUT_REPORT = "output/report.json"
# 按网络协议分栈输出的文件名后缀
IP_FAMILY_IPV4 = "ipv4"
IP_FAMILY_IPV6 = "ipv6"

# 检测默认参数
DEFAULT_TIMEOUT = 8.0
DEFAULT_CONCURRENCY = 32
DEFAULT_SPEED_SECONDS = 3.0
DEFAULT_MIN_FLUENCY = 10.0
DEFAULT_MAX_BYTES = 4 * 1024 * 1024
HEAD_BYTES = 8 * 1024
DEFAULT_MAX_PER_CHANNEL = 2
# 关闭测速时仍然需要的最小验证窗口
MIN_CHECK_SECONDS = 1.0
MIN_CHECK_BYTES = 64 * 1024

# 本地页面服务：默认只监听回环地址，请求体上限用于拒绝异常大的提交
DEFAULT_WEB_HOST = "127.0.0.1"
# 8765 与本机另一个项目冲突，改用高位端口
DEFAULT_WEB_PORT = 18765
WEB_REQUEST_MAX_BYTES = 64 * 1024
# 页面下载白名单只放行订阅文件
M3U_FILE_SUFFIX = ".m3u"

# 本地流代理：页面播放远程 m3u8 与切片时经本地转发，规避浏览器跨域限制
# 代理路由与前端 /proxy?url= 拼接保持一致
PROXY_ROUTE = "/proxy"
# 单次上游抓取超时（秒），避免卡住页面线程
PROXY_TIMEOUT = 10.0
# 单个切片/响应的字节上限，超过即拒绝，防止大文件占满内存
PROXY_MAX_BYTES = 32 * 1024 * 1024
# 允许的 url 参数最大长度，过长的地址直接判为非法
PROXY_MAX_URL_LENGTH = 2048

# m3u8 协议标记
M3U8_HEADER = "#EXTM3U"
M3U8_EXTINF = "#EXTINF"
M3U8_EXTGRP = "#EXTGRP"
M3U8_STREAM_INF = "#EXT-X-STREAM-INF"
M3U8_STREAM_COUNT_MAX_DEPTH = 1

# 流畅度评分档位（比特率，单位 bps）
BITRATE_EXCELLENT = 2_000_000
BITRATE_GOOD = 800_000
BITRATE_FAIR = 300_000
BITRATE_LOW = 100_000
FLUENCY_SCORE_MAX = 100.0

# M3U 格式标记
M3U_EXTM3U = "#EXTM3U"
TXT_GENRE = "#genre#"

# 默认分组与属性
UNKNOWN_GROUP = "未分组"
# 上游英文分类到中文分组的映射；键统一按小写比较，未收录的分类回退原值
GROUP_LABELS = {
    "animation": "动画",
    "auto": "汽车",
    "business": "财经",
    "classic": "经典",
    "comedy": "喜剧",
    "cooking": "美食",
    "culture": "文化",
    "documentary": "纪实",
    "education": "教育",
    "entertainment": "娱乐",
    "family": "家庭",
    "general": "综合",
    "kids": "少儿",
    "legislative": "政务",
    "lifestyle": "生活",
    "movies": "电影",
    "music": "音乐",
    "news": "新闻",
    "outdoor": "户外",
    "relax": "休闲",
    "religious": "宗教",
    "science": "科教",
    "series": "剧集",
    "shop": "购物",
    "sports": "体育",
    "travel": "旅游",
    "weather": "气象",
    "undefined": UNKNOWN_GROUP,
}

ATTR_TVG_NAME = "tvg-name"
ATTR_TVG_ID = "tvg-id"
ATTR_TVG_LOGO = "tvg-logo"
ATTR_GROUP_TITLE = "group-title"
# 原始分类拆分后写入自定义属性，播放器忽略，仅供追溯
ATTR_GROUP_TAGS = "group-tags"
GROUP_TAG_SEPARATOR = ";"
ATTR_XTVG_URL = "x-tvg-url"
M3U_EPG_URL_SEPARATOR = ","

# 固定顶层分组：group-title 只会取这些值
GROUP_CCTV = "央视"
GROUP_SATELLITE = "卫视"
GROUP_LOCAL = "地方"
GROUP_HKMO_TW = "港澳台"
GROUP_INTERNATIONAL = "国际"
GROUP_NEWS = "新闻"
GROUP_MOVIE = "影视"
GROUP_SPORTS = "体育"
GROUP_KIDS = "少儿"
GROUP_DOCUMENTARY = "纪录"
GROUP_MUSIC = "音乐"
GROUP_OTHER = "其他"
# 输出排序用的固定顺序
GROUP_CATEGORY_ORDER = (
    GROUP_CCTV,
    GROUP_SATELLITE,
    GROUP_LOCAL,
    GROUP_HKMO_TW,
    GROUP_INTERNATIONAL,
    GROUP_NEWS,
    GROUP_MOVIE,
    GROUP_SPORTS,
    GROUP_KIDS,
    GROUP_DOCUMENTARY,
    GROUP_MUSIC,
    GROUP_OTHER,
)

# 归类关键词：按台名与原始分类匹配，顺序即优先级
GROUP_CCTV_HINTS = ("cctv", "cgtn", "央视", "中央电视", "中央广播")
GROUP_HKMO_TW_HINTS = (
    "tvb",
    "翡翠",
    "明珠台",
    "凤凰",
    "香港",
    "澳门",
    "台湾",
    "中天",
    "东森",
    "民视",
    "三立",
    "tvbs",
    "华视",
    "台视",
    "中视",
    "无线电视",
)
GROUP_SATELLITE_HINT = "卫视"
GROUP_LOCAL_HINTS = (
    "地方",
    "直播中国",
    "北京",
    "上海",
    "天津",
    "重庆",
    "河北",
    "山西",
    "辽宁",
    "吉林",
    "黑龙江",
    "江苏",
    "浙江",
    "安徽",
    "福建",
    "江西",
    "山东",
    "河南",
    "湖北",
    "湖南",
    "广东",
    "广西",
    "海南",
    "四川",
    "贵州",
    "云南",
    "西藏",
    "陕西",
    "甘肃",
    "青海",
    "宁夏",
    "新疆",
    "内蒙古",
    "深圳",
    "青岛",
    "大连",
    "厦门",
)
# 原始分类关键词（中英文混排，按类别优先级排列）
GROUP_TYPE_HINTS = (
    (GROUP_NEWS, ("新闻", "news", "资讯", "财经", "business")),
    (GROUP_MOVIE, ("电影", "影视", "影碟", "movie", "film", "series", "剧集", "剧场", "v cinema")),
    (GROUP_SPORTS, ("体育", "sport", "足球", "篮球", "赛事")),
    (GROUP_KIDS, ("少儿", "儿童", "动漫", "动画", "kid", "child", "animation", "cartoon")),
    (GROUP_DOCUMENTARY, ("纪录", "纪实", "人文", "documentar", "science", "科教", "教育", "education")),
    (GROUP_MUSIC, ("音乐", "music", "戏曲", "曲艺", "concert")),
)
# 原始分类里出现这些地区/国家名时归入「国际」
GROUP_FOREIGN_HINTS = (
    "日本",
    "韩国",
    "朝鲜",
    "印度",
    "美国",
    "英国",
    "法国",
    "德国",
    "俄罗斯",
    "意大利",
    "西班牙",
    "葡萄牙",
    "荷兰",
    "比利时",
    "瑞士",
    "瑞典",
    "挪威",
    "丹麦",
    "芬兰",
    "波兰",
    "土耳其",
    "以色列",
    "巴西",
    "阿根廷",
    "墨西哥",
    "智利",
    "加拿大",
    "澳大利亚",
    "新西兰",
    "泰国",
    "越南",
    "菲律宾",
    "马来西亚",
    "印度尼西亚",
    "新加坡",
    "阿联酋",
    "沙特",
    "埃及",
    "南非",
)

# XMLTV 频道表标记，用于把台名映射回 tvg-id
XMLTV_CHANNEL_TAG = "channel"
XMLTV_DISPLAY_NAME_TAG = "display-name"
XMLTV_ID_ATTR = "id"
# 常见的 .xml.gz 节目单：按魔数判断而不依赖扩展名
GZIP_MAGIC = b"\x1f\x8b"
# 默认开启 EPG 台名映射
DEFAULT_EPG_MAP = True

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
# 鉴权类失败时换用安卓播控客户端 UA 重试一次，绕过只认客户端的源
FALLBACK_USER_AGENT = "OKhttp/1.31"
# 只有这些状态码值得换 UA 重试；404/超时等与 UA 无关，重试只会翻倍耗时
UA_RETRY_STATUSES = frozenset({401, 403, 406, 451})

# 归一化需要剥离的清晰度与画质后缀
QUALITY_SUFFIXES = (
    "4k",
    "8k",
    "蓝光",
    "超清",
    "高清",
    "标清",
    "1080p",
    "720p",
    "576p",
    "480p",
    "hd",
    "sd",
)

# 归一化需要剥离的包裹符号
BRACKET_PAIRS = (
    ("【", "】"),
    ("[", "]"),
    ("（", "）"),
    ("(", ")"),
)


class CheckStatus(StrEnum):
    """单条直播源的检测状态。"""

    OK = "ok"
    FAILED = "failed"
    SKIPPED = "skipped"


class PlaylistFormat(StrEnum):
    """源清单文本格式。"""

    M3U = "m3u"
    TXT = "txt"


class PipelineStage(StrEnum):
    """流水线阶段，页面进度展示与回调共用同一套标识。"""

    SOURCES = "sources"
    CHECK = "check"
    OUTPUT = "output"
    DONE = "done"


class RunStatus(StrEnum):
    """页面服务中一次后台运行的状态。"""

    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
