# 下一阶段部署边界

当前链路：浏览器 → 本机 React 开发服务（同源 /api 代理）→ FastAPI → MockAdapter 或只读 TqSdkAdapter；SQLite 与程序同目录。

TqSdk 已由独立 owner 线程持有，连接失败和数据过期显式反馈且不会静默返回 mock。Mac mini 生产阶段仍建议把前端生产构建与 FastAPI 作为独立受管进程，由同源反向代理统一入口。

TqSdk 适配契约位于 `backend/app/data_adapter/base.py`：contracts / prices / quote_history / bars / seasonality / observed_at / close。当前实现动态发现未到期合约、处理郑商所代码、批量报价、多合约同步 K 线、刷新驱动与断线重建；不得增加下单、账户或成交接口。固定到期合约和跨年季节性需要不同映射规则，不能直接拼成“真实五年历史”。

本次没有 Docker 依赖；可在生产阶段为现有 frontend、backend 补 Dockerfile/Compose，SQLite 挂载 data 目录，配置经环境变量注入。Apple Silicon Python 环境和依赖须在目标机器重建。

已完成当前工作机到阿里云 ECS 的 SSH 反向隧道：公网 `47.97.26.241:5173` → 本机 `127.0.0.1:5174`。应用与隧道均由 `config/launchd/` 对应的用户级 LaunchAgent 常驻，并已显式设为 enabled；当前 Mac mini 通过 `hw` 自动登录在重启后加载这两个服务。ECS 的 sshd 使用 `GatewayPorts clientspecified`，安全组已放行 TCP 5173。后端继续只监听 `127.0.0.1:8767`，由前端同源 `/api` 代理访问。

此部署未上传源码、SQLite 或 TqSdk 凭据到 ECS，也未增加交易接口。当前公网入口无域名、无身份验证且为明文 HTTP；HTTPS、访问认证、生产前端与 NAS 备份仍未执行，不适合扩大公开范围。

## CTP 分钟行情阶段

CTP `mduserapi` 已按“ECS 采集、Mac 使用并长期保存”部署：ECS 运行只读行情网关、Tick 最新价发布器和 30 天 SQLite 缓冲，Mac 的 `com.hw.futures-arbitrage.ctp-sync` LaunchAgent 通过单个持久 SSH 流接收 100ms 合并后的 Tick 驱动最新价，并按序增量复制完成分钟。FastAPI 通过 SSE 将价格变化推送到页面；TqSdk 历史统计独立按 30 秒刷新。两个 ECS systemd 单元与 Mac LaunchAgent 均已设为开机/登录后自动运行。

当前公共连接配置已切换到 SimNow 第一套生产时段环境，主行情前置为 `182.254.243.31:30011`，备用线路为 `30012`、`30013`，BrokerID 为 `9999`；ECS 正式行情运行时只保留标准 CTP 6.7.13，CTP Mini V1.7.5、SDK ZIP、编译源码、测试副本及旧 flow 均已从服务器删除，本机原始 ZIP 保留。私密凭据已部署到 ECS `/etc/ctp-md/ctp-md.credentials`（权限 600），网关和记录器均由 systemd 常驻。登录、60 合约订阅、ECS 分钟聚合和 Mac 增量同步均已用真实日盘行情验收。未来切换期货公司生产 CTP 时需从本机 SDK 重新部署对应库，分钟库与 Mac 同步链不变。配置方法和验收命令见 `ctp-gateway/README.md`。

看板当前使用 `simnow_tqsdk` 混合适配器：SimNow CTP 提供监控、分析当前值和期限结构实时价；TqSdk 继续提供历史 K 线、前收盘、统计样本和未到期合约目录。TqSdk 日线不能补出 CTP 分钟路径，因此 CTP 分钟历史的起点是生产采集首次成功的时间。
