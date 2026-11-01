# live-tv

本地 IPTV 直播源流水线：采集公开源 → 解析去重 → 可用性与流畅度检测 → 输出可直接导入 VLC / TVBox / Kodi 的 M3U，并留存 JSON 运行报告。

## 免责声明

本项目仅用于个人学习与技术研究，不生产、不存储、不托管任何媒体内容。所有直播源均为互联网公开链接，可用性、稳定性与合法性由上游提供方决定，与本项目无关。请勿用于商业传播或二次分发；使用者需自行遵守所在地法律法规。上游源可能自带广告、弹窗或赞助提示，本项目未添加也不参与。

## 参考项目

| 项目 | 借鉴点 |
|---|---|
| [iptv-org/iptv](https://github.com/iptv-org/iptv) | 频道数据与播放列表分离、按国家/分类拆分清单 |
| [Free-TV/IPTV](https://github.com/Free-TV/IPTV) | 人工维护单一数据源、质量优先于数量 |
| [vbskycn/iptv](https://github.com/vbskycn/iptv) | 自动扫描验证、TXT/M3U 双格式、台标与 EPG 属性 |
| [zilong7728/Collect-IPTV](https://github.com/zilong7728/Collect-IPTV) | 定时采集、去重、可用性与延迟筛选 |
| [goplay-source/IPTV-tools](https://github.com/goplay-source/IPTV-tools) | m3u8 头 + 首个切片双重验证、流畅度评分、黑名单 |
| [imDazui/Tvlist-awesome-m3u-m3u8](https://github.com/imDazui/Tvlist-awesome-m3u-m3u8) | 源清单与工具索引参考 |

## 环境要求

- Python 3.12+
- 依赖：`httpx`、`PyYAML`；开发依赖：`pytest`

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## 使用

```powershell
# 完整流程：采集 + 检测 + 输出
python -m livetv check --config config/sources.yaml

# 只解析与合并，不联网检测（用于确认源清单是否可解析）
python -m livetv check --config config/sources.yaml --no-test

# 小样本试跑：只处理前 30 个频道
python -m livetv check --config config/sources.yaml --limit 30

# 覆盖输出位置与阈值
python -m livetv check --config config/sources.yaml --out output/my.m3u --min-fluency 30
```

产物：

- `output/live.m3u`：分组 M3U，带 `tvg-name`、`tvg-logo`、`tvg-id`、`group-title`；配置了 EPG 时头部带 `x-tvg-url`，缺失的 `tvg-id` 会按 EPG 台名回填
- `output/live-ipv4.m3u` / `output/live-ipv6.m3u`：开启 `output.split_by_family` 时按实际连接协议族拆分的订阅
- `output/report.json`：源清单处理情况、每个频道的候选 URL、检测状态、流畅度、延迟明细与 `ip_family`

## 页面查看

```powershell
python -m livetv serve --config config/sources.yaml
# 默认 http://127.0.0.1:18765，可用 --host / --port 覆盖
```

页面直接读取最新一次 `report.json`：统计卡片（含可用率）、分组分布、频道表格（含候选 URL 与失败原因）、失败清单与 M3U 下载；也可在页面填写数量上限、并发、流畅度阈值、每频道条数与是否测速后触发一次新检测。

- 分组分布以卡片网格展示 12 个固定大类，每类固定一种颜色并标注数量与占比；零值分组保持置灰，点击卡片即筛选下方频道，再点一次取消。
- 布局为「运行检测 + 源清单」并排、其余单列；表格自带滚动容器，滚动时表头吸顶。
- 表格前三个关键列（勾选、频道、分组）在横向滚动时冻结在左侧；宽度 ≤1100px 自动收起「码率」「协议」两列并压缩内边距，1024 宽度下无需横向滚动，更窄时才出现表格内滚动且关键列保持可见。
- 快捷键：`/` 聚焦筛选框，`Esc` 依次关闭播放器、清空筛选、退出分组。
- 频道表按 200 条分页，分组列同时显示原始分类标签，筛选框支持按频道名、大类或标签搜索；「状态」下拉可筛出仅可用 / 仅低流畅 / 仅失败 / 仅未测，也可点「流畅度 / 延迟 / 码率」列头排序（空值始终沉底）。
- 每个频道按派生状态四色区分：绿色「可用」（有最优地址且流畅度 ≥ 40）、黄色「低流畅」（有最优地址但得分偏低）、红色「失败」（本次未测出可用地址）、灰色「未测」。颜色只是本次结果，不代表源永久不可用；复测后颜色与「复测 时:分」会同步刷新。
- 失败清单只列前 50 条，其余折叠为「展开其余 N 个」；每条显示失败原因（如「切片 HTTP 404」「HTTP 502」「证书无效」），卡片顶部按原因聚类统计条数，便于判断是源失效还是本机网络问题。
- 导出支持勾选：分组卡片与频道行都有勾选框（表头可全选本页可用频道），「导出选中」生成 M3U；另有 6 个预设可一键导出——国内常用（央视+卫视）、仅央视、仅卫视、地方频道、港澳台、全部可用。导出只含可用频道，并排除「支持作者」这类宣传占位条目；文件名带时间戳，头部与属性顺序和正式产物保持一致。导出卡片排在「运行检测/源清单」两栏之后、统计卡片之前，进入页面即可看到导出入口。
- 服务基于标准库 `http.server`，不新增依赖；默认只监听回环地址，显式 `--host 0.0.0.0` 时会打印无鉴权警告。
- 无前端构建：页面是单个静态 HTML，刷新即用；运行期间约每 1.2 秒轮询阶段与进度（源清单 / 检测 / 写入产物）。
- 同一进程同一时间只允许一次检测，重复提交返回冲突提示；下载接口只放行报告声明的 `.m3u` 产物。
- 频道行有「播放」按钮，点击后在页面内用内置 hls.js 播放；直播源经 `/proxy` 转发，避免浏览器跨域拦截。代理只放行报告里出现过的主机，清单引用的切片主机会在首次拉取后自动加入白名单。
- 播放不依赖「可用」判定：没有最优地址的频道显示「试播」，直接用首个候选地址打开；展开候选列表后每条地址都有独立的「播放」按钮，可以逐条验证哪条真能播。
- 复测入口分五个层级：单条地址（候选列表里的「复测」）、单频道（候选折叠区「复测全部」）、当前分组（分组卡片右下角「复测」，悬停出现）、失败项（工具栏「复测失败项」）与本页（工具栏「复测本页」）。复测会回填该频道的状态、流畅度、延迟与复测时间。
- 复测只更新页面显示的报告快照，不改写 `output/live.m3u` 与 `report.json`；需要在订阅产物里生效，仍要重新运行一次检测。复测与完整检测互斥，运行中相关按钮禁用（冲突返回 409）。
- 单次复测上限 500 个频道 / 1200 条地址，超限或参数冲突（如同时指定频道与分组）返回 400，避免误点把整站重跑一遍。
- 页面「源清单」区每行显示源名称与 M3U 地址（超长省略、悬停可看全文），并标注启用状态，点按钮即可切换，改动直接写回 `config/sources.yaml`（按行编辑，注释与排版保留），下次检测生效；检测运行中不允许修改。
- 源清单底部可手动添加源：填写「源名称」与「M3U 地址」后点「添加」或在地址框回车即可，地址需以 `http://` 或 `https://` 开头，重名会被拒绝；新源追加到 `sources` 段末尾并默认启用，同样在下次检测时生效。
- 播放受源自身限制：没有跨域头或做了防盗链的源会在播放窗口里提示失败，属正常情况。

## 分组与延迟口径

- `group-title` 固定为 12 个大类，按台名特征与原始分类依次判定：央视 → 港澳台 → 卫视 → 地方 → 内容类型（新闻/影视/体育/少儿/纪录/音乐）→ 国际 → 其他。M3U 也按这个顺序排列，所以顺序本身有意义（央视在最前，其他在最后）。
- 原始分类（如 `News`、`Italy`、`Animation;Kids`）会被拆成独立标签、去掉 emoji 前缀并中文化，写入自定义属性 `group-tags` 与报告中的 `group_tags`；播放器不识别该属性，仅用于追溯上游怎么分的组。
- 报告 `groups` 与频道表同口径，统计全部合并后的频道（含未测出可用地址的），因此各分组之和等于频道表总数，不等于可用频道数。
- 延迟拆为三段：`playlist_latency_ms` 为清单（首个请求）首字节延迟，`segment_latency_ms` 为首个切片首字节延迟，`latency_ms` 为两者之和。直链源没有切片，`segment_latency_ms` 为 `null`。总延迟可能超过单次 `check.timeout`，属于两次请求累加，不代表超时。
- `ip_family` 取自检测时实际建立的连接：`ipv4` 或 `ipv6`，连接未建立或测试桩环境为 `null`。

## EPG 与双栈

- `epg.urls` 填 XMLTV 地址后，M3U 头部输出 `#EXTM3U x-tvg-url="..."`；多个地址用逗号连接。远程地址按 XML 或 gzip 内容自动识别下载解析；非 `http(s)` 的条目按本地文件读取（相对配置文件目录解析），本地文件只用于映射、不写入头部。
- `epg.map`（默认 `true`）开启时，用 EPG 的 `<channel>` 台名建立索引，给源清单里缺少 `tvg-id` 的频道按归一化台名（忽略大小写、清晰度后缀与括注）回填 EPG 频道 ID；源清单自带 `tvg-id` 的频道不覆盖，未命中保持空值。报告 `epg` 块给出 EPG 频道数、回填数与每个来源的错误，单源失败不影响整体运行。
- `output.split_by_family: true` 时额外输出 `live-ipv4.m3u` 与 `live-ipv6.m3u`，内容按每个频道最优结果的 `ip_family` 归类；未检测模式不生成这两个文件。

## 定时执行

```powershell
# 手动跑一次，日志写入 output/run-<时间>.log
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run-weekly.ps1
```

已注册本机计划任务 `live-tv weekly`：每周一 06:00 以当前用户身份运行上述脚本。

```powershell
# 查看任务状态与下次运行时间
schtasks /query /tn "live-tv weekly"

# 改时间（例：改为每周日 20:00）
schtasks /change /tn "live-tv weekly" /st 20:00

# 改频率（例：改回每天 06:00）
schtasks /change /tn "live-tv weekly" /sc daily /st 06:00

# 删除任务
schtasks /delete /tn "live-tv weekly" /f

# 立即触发一次
schtasks /run /tn "live-tv weekly"
```

任务以当前用户身份运行，不依赖登录状态以外的服务账户；运行结果只体现在 `output/` 下的订阅与日志，不推送、不上传。任务为「仅在用户登录时运行」，到点时电脑关机或未登录会被跳过且不自动补跑，可用 `schtasks /run` 手动补一次。

## 源清单与来源

所有源均为互联网公开清单，本项目只做下载、解析、检测与重新打包，不镜像内容。

| 源 | 来源项目 | 清单地址 | 上游更新节奏 | 状态 |
|---|---|---|---|---|
| iptv-org 中国 | [iptv-org/iptv](https://github.com/iptv-org/iptv) | `https://iptv-org.github.io/iptv/countries/cn.m3u` | 每天 00:00 UTC 重建，12:00 UTC 格式化 | 启用 |
| Free-TV 全球 | [Free-TV/IPTV](https://github.com/Free-TV/IPTV) | `https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8` | 每 6 小时快检，每 2 天深检 | 启用 |
| vbskycn IPv4 | [vbskycn/iptv](https://github.com/vbskycn/iptv) | `http://live.zbds.top/tv/iptv4.m3u` | 每 6 小时同步可用源 | 启用 |
| Collect-IPTV | [zilong7728/Collect-IPTV](https://github.com/zilong7728/Collect-IPTV) | `https://raw.githubusercontent.com/zilong7728/Collect-IPTV/main/best_sorted.m3u8` | 每 4 小时自动更新 | 启用 |
| IPTV-tools | [goplay-source/IPTV-tools](https://github.com/goplay-source/IPTV-tools) | `https://raw.githubusercontent.com/goplay-source/IPTV-tools/main/channel.m3u` | 每周一 06:00 UTC 全量测速 | 启用 |
| iptv-org 全球 | [iptv-org/iptv](https://github.com/iptv-org/iptv) | `https://iptv-org.github.io/iptv/index.m3u` | 同「iptv-org 中国」 | 关闭 |

口径说明：

- 上游只负责「清单内容」，不保证单个频道长期可用；本项目每次运行都重新下载清单并重新检测，因此相邻两次结果会不同。
- 本项目不缓存源清单，磁盘上只保留最终 M3U 与 `report.json`，所以每次运行拿到的都是上游当时的最新版本。
- 各源解析条目量参考（2026-11-01 解析阶段）：Free-TV 2053、Collect-IPTV 506、vbskycn 443、IPTV-tools 226、iptv-org 中国 155，合计 3383 条，去重后 2875 个频道。
- `iptv-org 全球` 条目量数千条，全量检测耗时较长，默认关闭；需要时在 `config/sources.yaml` 将其 `enabled` 改为 `true`。

## 配置说明

`config/sources.yaml` 字段：

| 字段 | 说明 |
|---|---|
| `sources[].url` | 远程源清单地址 |
| `sources[].path` | 本地源清单文件（相对配置文件目录解析），与 `url` 二选一 |
| `sources[].group` | 频道自身没有分组时使用的兜底分组 |
| `sources[].enabled` | 是否参与本次运行 |
| `output.m3u` / `output.report` | 产物路径，相对当前工作目录 |
| `output.split_by_family` | 是否额外输出 `live-ipv4.m3u` 与 `live-ipv6.m3u` |
| `epg.urls` | XMLTV 地址或本地文件列表；远程地址写入 M3U 头部 `x-tvg-url` |
| `epg.map` | 是否用 EPG 台名回填缺失的 `tvg-id`，默认 `true` |
| `check.concurrency` | 并发检测数 |
| `check.timeout` | 单次请求超时（秒） |
| `check.speed_seconds` | 采样测速时长（秒） |
| `check.max_bytes` | 单次响应最多读取字节数 |
| `check.min_fluency` | 流畅度阈值，低于该分的频道不输出 |
| `check.max_per_channel` | 每频道最多输出的 URL 条数 |
| `blacklist.domains` | 域名黑名单 |
| `blacklist.keywords` | 关键词黑名单 |

## 检测口径

1. 请求直播源地址，读取响应头部判断是否为 m3u8 清单；请求带 `Referer`/`Origin`（目标站点根）与常规 `Accept-Language`。
2. 清单或切片首次返回 401/403/406/451 时，自动换用 `OKhttp/1.31` 客户端 UA 重试一次（404、超时不重试，避免耗时翻倍）。
3. 若是清单，取首个切片地址并实际请求切片；master 清单向下钻一层时按 `BANDWIDTH` 选最高码率子流，无该字段时回退首个子流。
4. 切片/直链在采样窗口内读取数据量，按 `字节 × 8 / 耗时` 计算比特率。
5. 比特率映射为 0–100 流畅度分：≥2 Mbps 优秀、≥800 kbps 良好、≥300 kbps 一般、<100 kbps 较差。
6. 同名频道多 URL 合并，按流畅度选最优，其余作为备选输出。

## 目录结构

```text
live-tv/
├── config/sources.yaml     # 源清单与检测配置
├── scripts/run-weekly.ps1  # 每周运行脚本（日志写入 output/）
├── src/livetv/             # 采集、解析、检测、输出
├── src/livetv/web/         # 本地查看页面与运行管理（静态 HTML + http.server）
├── tests/                  # 单元测试
└── output/                 # 运行产物（不入库）
```

## 测试

```powershell
python -m pytest
```
