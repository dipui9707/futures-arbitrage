# 期货套利监控 · ARB DESK

独立、本地运行的深色期货套利工作台。React 19 + TypeScript + Vinext（Next.js App Router 兼容结构）+ ECharts；后端使用 FastAPI 和 SQLite。组合和告警只保存在本项目 SQLite；TqSdk 模式可只读引用外部私有授权文件。

支持 `mock` 模拟行情、`tqsdk` 单一只读行情和当前生产使用的 `simnow_tqsdk` 混合模式。混合模式由 SimNow CTP 提供最新价，TqSdk 提供未到期合约目录、历史 K 线和前收盘；不包含下单、交易账户、订单、持仓或成交 API，也不会在任一来源失败时静默回退到模拟数据。

项目已增加只读 CTP 行情链：阿里云 ECS 连接 SimNow 第一套生产时段行情前置并保留 30 天分钟缓冲，逐 Tick 最新价经 100ms 合并窗口和单个持久 SSH 流送到 Mac mini，同时增量复制已完成分钟到长期 SQLite。页面通过 SSE 按行情变化更新价格，历史与统计每 30 秒刷新。TqSdk 历史与 CTP 分钟线不拼接成同一频率：TqSdk K 线用于历史图表和统计，CTP 用于页面当前值并从正式启用之日起积累本地分钟；上线前缺失的分钟不能由日线还原。详细部署和数据口径见 [`ctp-gateway/README.md`](ctp-gateway/README.md)。

## 在本机使用

需要 Node.js 22.13+（建议 22 LTS）、npm 和 Python 3.11+。

在本项目目录执行：

```sh
npm start
```

首次运行自动创建本项目的 `.venv`、安装锁定依赖、从 `.env.example` 生成 `.env` 并启动两个本地进程；之后通常几秒启动。首次安装需要网络。服务在后台运行，关闭启动终端后仍可访问。

- 网页：<http://127.0.0.1:5173/>
- API 健康检查：<http://127.0.0.1:5173/api/health>
- API 说明：<http://127.0.0.1:8767/docs>（Swagger 静态资源默认来自 CDN；应用自身不依赖 CDN）

默认前端端口是 5173；本机若已有服务占用该端口，可在 `.env` 改为其他空闲端口。当前工作机使用 5174。

混合行情配置建议只在未跟踪的 `.env` 中引用已有私有授权文件，不复制凭据：

```dotenv
DATA_SOURCE=simnow_tqsdk
TQSDK_AUTH_FILE=/absolute/path/to/private-tqsdk.env
CTP_MINUTE_DATABASE=data/ctp_minute_bars.sqlite3
CTP_MAX_FEED_AGE_SECONDS=30
```

授权文件支持 `TQSDK_AUTH=user,password` 或分开的 `TQSDK_USER`、`TQSDK_PASSWORD`。切换数据源后需 `npm stop && npm start`。

```sh
npm stop      # 仅停止本项目管理器启动的进程
npm run status    # 查看运行状态和健康检查
npm run setup     # 仅安装依赖
npm run check     # 后端测试、类型检查、代码检查、生产构建
```

日志位于 `.runtime/frontend.log`、`.runtime/backend.log`、`.runtime/supervisor.log`。若端口被占用，管理器会退出并提示修改本项目 `.env`，不会结束占用端口的既有进程。默认只监听本机回环地址。

## 阿里云公网入口

当前工作机通过 SSH 反向隧道发布一个同源入口：<http://47.97.26.241:5173/>。公网 `5173` 转发到本机 `127.0.0.1:5174`；浏览器请求的 `/api` 仍由前端代理到本机 `127.0.0.1:8767`，TqSdk 授权文件、Python 服务和数据库均未上传 ECS。

应用和隧道由两个用户级 LaunchAgent 在登录后启动并自动重连，模板位于 `config/launchd/`，实际安装在 `~/Library/LaunchAgents/`。当前 Mac mini 已启用 `hw` 自动登录且 FileVault 关闭，因此正常重启后会自动登录并依次启动项目和 SSH 隧道，无需人工操作；如果以后关闭自动登录，这两个服务将改为登录后才启动。查看状态或日志：

```sh
launchctl print gui/$(id -u)/com.hw.futures-arbitrage.app
launchctl print gui/$(id -u)/com.hw.futures-arbitrage.aliyun-tunnel
launchctl print gui/$(id -u)/com.hw.futures-arbitrage.ctp-sync
tail -n 100 .runtime/launchd-app.stderr.log .runtime/aliyun-tunnel.stderr.log .runtime/ctp-sync.stderr.log
```

停止公网映射但保留本机应用：

```sh
launchctl bootout gui/$(id -u)/com.hw.futures-arbitrage.aliyun-tunnel
```

如需同时停止由 LaunchAgent 管理的本机应用，再执行：

```sh
launchctl bootout gui/$(id -u)/com.hw.futures-arbitrage.app
```

重新启用时，分别对 `~/Library/LaunchAgents/com.hw.futures-arbitrage.app.plist` 和 `com.hw.futures-arbitrage.aliyun-tunnel.plist` 执行 `launchctl bootstrap gui/$(id -u) <plist路径>`。LaunchAgent 启用期间，单独运行 `npm stop` 后应用会被自动拉起。

当前入口沿用固定 IP、小范围使用、无密码的方案，仅为 HTTP，组合与告警的写接口也可经同源 `/api` 访问。若扩大使用范围，先在 ECS 增加 HTTPS 与身份验证，不应直接继续公开该端口。

## 页面与操作

1. **套利监控**：默认六组 RB/JM/SM 跨期组合；每卡显示当前价差、相对前收盘的变化、两腿价格与变化、趋势缩略图和 60 日分位。支持新增、编辑、删除、收藏、品种/收藏筛选、字段排序；在“自定义顺序”下用卡片右下角上下箭头调整持久顺序。删除组合同时删除关联告警。
2. **套利分析**：可选择现有组合或直接调整 A、B、计算方式与系数，点击“应用分析”。支持 1m/5m/15m/30m/60m/2h/4h/日线、150/300/600 根样本、OHLC、MA5/10/20/60/120、区间均值与 ±1σ/±2σ、统计和直方图。点击图例开关 MA；拖动底部滑块或滚轮缩放。可将当前公式另存为监控组合。
3. **季节性**：mock 模式展示六个模拟年份；TqSdk 模式不把固定到期合约伪装成五年季节性，在同月连续合约映射实现前明确显示不可用。
4. **机会雷达**：显示当前价差、60/250 日分位、250 日 Z-score 和五档状态；可筛选和排序，点击分析进入该组合。
5. **期限结构 / 价差矩阵**：支持 RB/JM/SM/J/I 切换；混合模式由 TqSdk 动态发现未到期合约，并按同批 SimNow CTP 最新价生成“行合约价格 − 列合约价格”矩阵。
6. **告警设置**：保存价格、60 日分位、250 日 Z-score 的 ≥/≤ 阈值；支持编辑、删除及启停配置。本阶段仅保存，不运行后台通知、推送或声音。

浏览器 URL 的 `#monitor`、`#analysis`、`#radar`、`#term`、`#matrix`、`#alerts` 对应六个页面。界面针对笔记本、1440p 与 4K 使用响应式布局，窄屏表格允许横向滚动。未嵌入 Wind 截图、商标或专有代码；引用对话没有提供可读取的两张图片，因此本版以文字功能清单为视觉与交互依据。

## 目录

```text
futures-arbitrage/
├── frontend/                  React / TypeScript / ECharts
│   ├── app/                   App Router 页面与主题
│   ├── components/            监控、分析、雷达等视图
│   ├── lib/                   API 类型和数据契约
│   └── package-lock.json
├── backend/
│   ├── app/
│   │   ├── data_adapter/      MarketDataAdapter、mock、TqSdk 与 SimNow+TqSdk 只读适配器
│   │   ├── analytics.py       统计、直方图、MA
│   │   ├── storage.py         本项目 SQLite
│   │   └── main.py            FastAPI 路由
│   ├── tests/                 算法和 API / 持久化测试
│   └── requirements.lock      Python 锁定版本
├── scripts/manage.py          安装、启动、停止、状态、检查
├── scripts/ctp/               CTP 分钟聚合、ECS 导出与 Mac 增量同步
├── ctp-gateway/               Linux x86-64 只读 CTP 行情网关
├── config/                    systemd、launchd、订阅和部署配置
├── data/arbitrage.sqlite3      本地组合与告警（运行后生成）
├── data/ctp_minute_bars.sqlite3 本机长期 CTP 分钟主库（运行后生成）
├── .env.example               非敏感配置示例
└── package.json               一键命令
```

`frontend/.openai/hosting.json` 为脚手架留下的空能力清单，没有站点 ID 或云数据库。当前 ECS 承担 SSH 反向隧道和只读 CTP 行情采集，保存 CTP 运行文件及 30 天分钟缓冲；不保存 TqSdk 凭据、本机组合/告警库或完整项目部署，没有使用 Sites 或 NAS。

## 计算口径

- `spread = A − B`；`ratio = A / B`；`weighted = αA − βB`。普通价差与比价的系数自动归一为 1。系数范围与输入约束由 API 校验。
- mock 模式继续使用确定性的同步模拟路径。TqSdk 与混合模式的历史图表使用多合约时间对齐 K 线，以目标周期开收盘和同步子周期收盘采样计算价差 OHLC；不会用两腿 High–High、Low–Low 相减冒充同步价差高低。混合模式当前值单独使用 SimNow CTP 最新价。
- 历史统计使用所加载区间的**已完成 K 线收盘值**；图表末根形成中时明确标记，并从均值、标准差、分位和直方图样本中排除。MA 包含末根暂定收盘值，前 N−1 根为空。
- 标准差为总体标准差（除以 N）。Z-score =（当前值−样本均值）/σ；σ 为零时返回 null。分母为零的比值不产生 Infinity。
- 分位采用 `100 × (低于当前的样本数 + 0.5 × 等于当前的样本数) / N`。全相等时为 50%。
- 60/250 日雷达窗口使用当前固定到期合约的实际可用完整日线；新合约可能不足 250 根，页面展示实际样本数。五档状态：≤5 极端低；5–25 偏低（不含25）；25–75 中性；75–95 偏高（不含95）；≥95 极端高。
- TqSdk 合约发现使用 `expired=False`；郑商所三位合约号会归一为页面统一的 `YYMM`。混合模式行情时间取两腿 SimNow CTP 最新报价时间，不用页面刷新时间冒充行情时间。
- CTP 采集订阅同一份 TqSdk 未到期合约目录中的 RB/JM/SM/J/I，每 6 小时自动刷新；这里只使用合约元数据，不依赖 TqSdk 分钟历史。
- CTP Tick 到达后按合约在 100ms 窗口内合并为最新值，经持久 SSH 流同步到 Mac；FastAPI 的 SSE 接口每 100ms 检查变化并只推送轻量最新价，页面不再为每次报价重拉历史。TqSdk 历史、分位和统计每 30 秒刷新。连接断开后 LaunchAgent 与浏览器 EventSource 都会自动重连。该链只长期保存分钟线和各合约当前价，不保存完整 Tick 历史。
- 当日变化相对于 TqSdk `pre_close` 计算；避免负价差跨零时的百分比误读。价差与系数价差没有调整合约乘数，也不是元/手盈亏。
- 统计“最高/最低”是区间收盘值的极值；图表 OHLC 高低另行展示。图表缩放只改变视野，不改变已加载的统计窗口。

## 迁移到 Mac mini 的最少步骤

1. 停止此机服务：`npm stop`，然后复制整个项目的源码、锁文件、`.env.example`，以及需要保留的 `data/`。不要复制 `.venv/`、`frontend/node_modules/`、`frontend/dist/`、`.runtime/` 或机器相关的临时文件。也可使用本次交付的源码压缩包，它不含本机配置和数据库。
2. 在 Mac mini 安装 Node.js 22 LTS 和 Python 3.11+，进入复制后的项目目录，执行 `npm start`。会自动重建环境；默认仍为模拟行情。真实行情需在目标机的未跟踪 `.env` 配置私有 TqSdk 授权来源。若迁移配置，先停机再复制数据库，避免遗漏 SQLite WAL。
3. 在 Mac mini 打开 <http://127.0.0.1:5173/>；用 `npm run status` 确认健康。仅迁移本地 MVP 到此完成。

TqSdk 由单独 owner 线程创建和驱动，HTTP 线程不会直接操作 SDK；连接异常时重建连接并清空订阅缓存。SimNow 最新价从本机只读 SQLite 获取，交易时段内按全市场最新 CTP 报价检查数据链新鲜度；任一来源不足时显式返回 503，不回退 mock。当前交易时段识别覆盖五个品种的常规日盘和夜盘，法定节假日仍依赖行情时间与后续交易日历增强。

本阶段仍使用本地开发服务，便于调试；LaunchAgent 已提供应用自恢复、SSH 隧道重连和 CTP 分钟库增量同步。公网入口仍缺少 HTTPS、身份验证和生产前端，NAS 备份也未配置。浏览器始终走同源 `/api`，前端代码无须写死 Mac mini IP。

## 验证

具体执行结果见 `VALIDATION.md`。测试使用临时 SQLite，不清空用户配置。

接口结构与图表集成参考：[FastAPI 官方文档](https://fastapi.tiangolo.com/tutorial/)、[ECharts 官方导入说明](https://echarts.apache.org/handbook/en/basics/import/)、[Vite 服务代理配置](https://vite.dev/config/server-options)。
