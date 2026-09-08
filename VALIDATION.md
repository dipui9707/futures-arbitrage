# 本机验收记录

验收日期：2026-09-07，Asia/Shanghai。

## 已完成

- `npm start`：按本机 arm64 Pi Node、uv Python 3.13 环境启动独立前后端；因 5173 已被其他项目占用，本项目网页 <http://127.0.0.1:5174/> 返回 HTTP 200。
- `npm stop` 后 `npm run status`：running=false / healthy=false。停止再立即启动通过；修复了端口 TIME_WAIT 导致的占用误判。
- `npm run status`：最终 running=true / healthy=true，服务保留运行。
- 前端 `npm run typecheck`：通过。
- 前端 `npm run lint`：通过；检查本项目业务代码，未改动的脚手架 UI primitives 与 use-mobile helper 保持原样并排除业务 lint，仍参加 TypeScript 检查。
- 前端 `npm run build`：生产构建成功。仍有图表相关 chunk 大于 500 KB 的体积提示和 Vinext 路由分类提示，不影响本地运行。
- npm 依赖安装审计：0 vulnerabilities。为此同步更新了脚手架的 React、Vinext、Vite 与相关兼容依赖；版本写入锁文件。
- 后端 `python -m unittest discover -s backend/tests -v`：19/19 通过。覆盖同步价差 OHLC、系数/比价、除零、分位平值、σ=0、MA、直方图、所有周期、跨周期聚合、季节均线、组合和告警 CRUD、持久排序、级联删除、校验、矩阵反对称、TqSdk 合约归一化、实时报价、日线历史和同步 K 线，以及 CTP 合约格式、分钟聚合、最新价快照、持久同步数据包、日汇总与 SimNow+TqSdk 混合适配器。测试数据库在临时目录。
- 真实 TqSdk HTTP 验收：健康、60 个未到期合约、6 个监控组合、RB/JM/SM/J/I 五个期限结构与矩阵、8 个分析周期全部通过；所有 OHLC 满足 low ≤ open/close ≤ high，行情时间为 `2026-09-07 14:59:59`。
- 跨进程持久化：创建临时比价组合及停用告警 → 停止服务 → 重启 → 读取并确认名称、模式、收藏及告警阈值保持 → 删除自身临时组合 → 确认关联告警随之删除。现有用户配置保持。
- 只读行情边界：OpenAPI 路由清单没有账户、下单、持仓、交易或成交接口；修改接口仅操作本项目组合/告警配置。
- 本机组合和告警最终位置为 `data/arbitrage.sqlite3`；没有读取其他项目的 SQLite。TqSdk 只读引用 `/Users/hw/projects/Quant_Platform/.env.macmini4`，没有复制或输出凭据。
- CTP 6.7.13 Linux x86-64 行情库已在 ECS 编译和运行版本检查，`GetApiVersion()` 返回 `v6.7.13_20260225 14:16:30.12079`；动态库通过 `$ORIGIN/../lib` 加载，`ldd` 无缺失依赖。
- 用户提供的 CTP Mini V1.7.5 Linux x86-64 行情库已通过完整网关编译、`GetApiVersion()` 和 `ldd` 验收；它曾部署到 ECS 并加载 `/opt/ctp-md/lib/libthostmduserapi.so`，标准 6.7.13 当时另存为回滚副本。完成接口选型后，服务器上的 Mini 和回滚副本均已清理，只保留当前标准 CTP 运行文件。
- SimNow 私密凭据已通过 SSH 部署到 `/etc/ctp-md/ctp-md.credentials`，权限为 root:root 600；本机来源文件也已改为 600 并加入 `.gitignore`。账号和密码未输出到命令结果、日志或文档。
- ECS 的 `ctp-md-gateway.service` 与 `ctp-minute-recorder.service` 均 enabled、active；网关仅监听 `127.0.0.1:19001`，记录器已连接本机流。
- 2026-09-08 09:24—09:50 CST 从 Mac 和 ECS 检查并用已部署凭据实测 SimNow 官方列出的 `10211`、`10212`、`10131`，均未建立 TCP 或触发 `OnFrontConnected`，所以尚不能验证凭据本身；随后用保留的标准 CTP 6.7.13 对 `10211` 做 15 秒隔离探针，同样只有本地监听、没有前置回调，说明问题不只来自 CTP Mini 协议差异。ECS HTTPS 出口正常、本机 UFW 未启用。最终恢复 Mini V1.7.5 和第一线 `10211`，两个正式服务均 active，分钟库仍为空，不能把进程 active 误报为行情已连接。
- 用户随后从 SimNow 账号页提供第二套环境的新地址；ECS 正式行情前置已更新为 `182.254.243.31:40011`（交易前置 `40001` 未接入）。2026-09-08 09:50 CST 探测时已超过该环境交易日 `09:00` 的服务截止时间，TCP 返回 `Connection refused`；该结果只验证了目标可达和当前未监听，行情登录、订阅及真实分钟入库留待 `16:00` 后验证。
- 2026-09-08 09:57 CST 从 SimNow 账号页取得第一套生产时段环境的新地址；ECS 对行情前置 `182.254.243.31:30011`、`30012`、`30013` 的 TCP 探测均成功，正式配置从第二套开发环境切换到第一组主线路 `30011`。交易前置未接入。
- CTP Mini V1.7.5 连接 `30011` 后触发 `OnFrontConnected`，但登录请求无响应并约 35 秒后以前置断开原因 `-3` 重连；使用相同地址、BrokerID 和凭据运行标准 CTP 6.7.13 隔离探针，登录成功且 60 个合约全部收到订阅响应。因此正式服务切回标准 CTP 6.7.13；Mini 曾临时保留用于回滚，完成稳定性确认后已按用户要求从 ECS 删除。
- 标准 CTP 6.7.13 正式服务连接 `30011` 后登录成功，60 个合约全部订阅成功；ECS 分钟库随后达到 115 条状态记录、61 条已完成分钟，Mac 在一个 20 秒同步周期后取得全部 61 条完成分钟。抽查 `JM2701`、`SM2611`、`I2612` 等合约含非零分钟成交量和连续 Tick，确认不是仅有静态订阅快照。
- ECS 清理后仅保留 `/opt/ctp-md/bin/` 运行程序、`/opt/ctp-md/lib/thostmduserapi_se.so`、当前 `/etc/ctp-md/` 配置与凭据、生产 flow 和分钟数据库。删除所有 backup、Mini 动态库、服务器 SDK/源码、示例 env 与测试 flow 后主动重启服务，标准 CTP 6.7.13 再次登录成功并完成 60 个合约订阅；两个 systemd 单元仍为 enabled、active。
- Mac 已安装并运行 `com.hw.futures-arbitrage.ctp-sync`，使用现有 `aliyun-ecs` SSH 别名维持单个 SSH 流接收 Tick 驱动最新价并增量拉取已完成分钟；本地主库已正确初始化。stderr 中保留一次主动断开 SSH 的恢复测试记录，以及应用重启窗口内一次合约目录刷新失败记录，服务均已自动恢复。
- 使用独立临时库写入一条 `SHFE.RB2610` 夜盘完成分钟，完成 ECS 导出 → SSH → Mac SQLite 的端到端验收；`trading_day=20260908`、自然时间 `2026-09-07T21:00:00+08:00`、close、volume 和 `sync_seq` 均一致，随后删除临时测试库，未向生产库写入模拟行情。
- CTP 采集单元测试 3/3 通过：郑商所代码归一、五品种订阅格式、累计成交量差分、夜盘交易日、分钟完成、SQLite 幂等写入和增量导出。

## 已修复的使用问题

- 同步模拟路径生成 OHLC，所有周期保持一致。
- 每次刷新保留 ECharts 的缩放、MA 开关及年份图例选择。
- 季节性按各年份实际月日取值，当前年份不显示未来日期。
- 筛选收藏或品种后，上下排序交换相邻可见组合，保留隐藏组合位置。
- API 错误和超时显式反馈；切换分析参数取消过期请求，避免旧结果覆盖新组合。
- SQLite 初始化只发生一次，删除默认组合后重启不会重新塞回。
- ECharts 首次挂载时允许 `getOption()` 尚未返回对象，分析页不再因读取旧图例状态崩溃。
- TqSdk 使用单 owner 线程驱动 `wait_update`，请求失败会重建连接；郑商所 `SM611` 归一为 `SM2611`。
- 真实固定到期合约不拼接模拟五年季节性，页面明确显示需等待同月连续合约映射。

## 阿里云公网映射

- ECS `47.97.26.241:5173` 通过 SSH reverse forward 映射到本机 `127.0.0.1:5174`；ECS 端确认监听 `0.0.0.0:5173`，公网首页、`/api/health`、`/api/contracts` 均返回 HTTP 200。
- ECS sshd 当前为 `GatewayPorts clientspecified`、`AllowTcpForwarding yes`；TCP 5173 的既有安全组规则仍有效，未改动 ECS 上占用 8000 的既有 gohttpserver。
- `com.hw.futures-arbitrage.app` 与 `com.hw.futures-arbitrage.aliyun-tunnel` 两个用户级 LaunchAgent 均为 running，分别负责本机应用自恢复和 SSH 隧道自动重连。
- 两个 LaunchAgent 均已通过 `launchctl enable` 显式启用，并配置 `RunAtLoad + KeepAlive`；本机 `autoLoginUser=hw`、FileVault 关闭，正常重启后会自动登录并加载服务。未执行实际整机重启，已通过 bootout/bootstrap 和 kickstart 重载验证应用、隧道及公网健康检查可恢复。
- 公网同源健康和行情 API 返回 `data_source=simnow_tqsdk`、`realtime_source=simnow`、`history_source=tqsdk`。实际浏览器 DOM 验收确认监控页显示 SimNow 实时价格和递增行情时间，分析页显示 TqSdk 历史同步 OHLC，期限结构显示 TqSdk 合约目录与 SimNow 最新价；浏览器控制台无错误。
- 2026-09-08 日盘将实时链升级为 Tick 驱动：ECS 记录器新增仅监听 `127.0.0.1:19002` 的最新价流，同一合约按 100ms 窗口合并；Mac `current_quotes` 在 3 秒观测窗内更新 15 次。经本机前端代理和公网 `47.97.26.241:5173` 的 `/api/realtime/stream` 均连续收到 SSE `quotes` 事件，事件含交易所行情毫秒时间。
- 浏览器 DOM 验收显示“SimNow Tick 推送”“Tick 驱动 · 统计每 30 秒”，行情时间从 `11:06:56.500` 自动推进到 `11:07:12.000`，没有点击刷新；期限结构页显示“SimNow Tick · TqSdk 合约”，12 个螺纹合约价格和矩阵接入同一推送流。
- 主动终止 Mac 同步进程持有的 SSH 子连接后，`sync_from_ecs.py` 保持运行并自动创建新的唯一 SSH `follow` 连接，随后 `current_quotes.observed_at` 恢复推进；验证临时网络中断不需要人工重启。
- Tick 链升级后再次执行相同演练：同步父进程 PID 保持不变，SSH 子进程从旧 PID 自动更换为新 PID，`current_quotes` 最新行情时间从 `11:10:50.500` 恢复推进到 `11:11:00.000`，本机健康接口全程恢复为 200。
- 此入口为固定 IP 的明文 HTTP，未增加密码或其他身份验证；组合与告警写接口随同源 `/api` 暴露。TqSdk 授权、后端端口、SQLite 和项目源码仍只在本机。

## 验证范围

本机真实 TqSdk 只读行情已经验收，但不代表 NAS 或长期 24 小时生产运行已验收。未发送通知、未进行交易；OpenAPI 的 11 条路径中没有账户、下单、持仓、交易或成交路由。浏览器已验收监控、分析和期限结构的真实来源标识、实际价格与行情时间；本轮完整类型检查、代码检查和生产构建通过。

首次订阅新期限结构品种约需 7–12 秒，随后缓存刷新明显更快；前端 20 秒超时范围内已通过。生产阶段仍需持续观察断网、法定节假日和跨交易日常驻连接。

后端测试期间 Starlette 对 httpx TestClient 有弃用提示，测试全部通过；后续升级可迁移测试传输依赖，不影响当前行情服务。
