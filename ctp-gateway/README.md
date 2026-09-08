# CTP 分钟行情采集

本目录是只读 CTP `mduserapi` 网关。SimNow 第一套环境当前只使用标准 CTP 6.7.13 的 `thostmduserapi_se.so`。ECS 上的 CTP Mini、SDK ZIP、编译源码和测试副本均已清理；本机原始 ZIP 仍保留。不包含交易 API、下单、账户、持仓或成交功能。

## 数据链

```text
期货公司 CTP 行情前置
  -> ECS ctp-md-gateway（逐笔深度行情，仅监听 127.0.0.1:19001）
  -> ECS ctp-minute-recorder（1 分钟聚合 + 127.0.0.1:19002 Tick 最新价流）
  -> 持久 SSH 流（100ms 合并后的 Tick 最新价 + 已完成分钟增量）
  -> Mac mini data/ctp_minute_bars.sqlite3（当前价缓存 + 长期分钟主库）
  -> FastAPI SSE（按变化推送）-> 浏览器局部更新最新价
```

TqSdk 不参与 CTP 分钟线补齐。当前项目每 6 小时只使用 TqSdk 的未到期合约目录刷新 RB、JM、SM、J、I 的 CTP 订阅列表；TqSdk 日线继续作为历史日线和每日收盘校验基准。CTP 上线前的分钟历史无法由日线还原。

## 已部署位置

- ECS 二进制：`/opt/ctp-md/bin/ctp-md-gateway`
- ECS 当前 SDK：`/opt/ctp-md/lib/thostmduserapi_se.so`（标准 CTP 6.7.13）
- ECS 订阅：`/etc/ctp-md/instruments.txt`
- ECS 短期库：`/var/lib/ctp-md/minute_bars.sqlite3`
- ECS 服务：`ctp-md-gateway.service`、`ctp-minute-recorder.service`
- Mac 长期库：`data/ctp_minute_bars.sqlite3`
- Mac 服务：`com.hw.futures-arbitrage.ctp-sync`

两个 ECS 服务已经 enabled 并运行。公共连接配置位于 `/etc/ctp-md/ctp-md.env`，私密凭据位于 `/etc/ctp-md/ctp-md.credentials` 且权限为 600。不要把真实密码写入项目、日志或聊天。

## 当前 SimNow 配置与私密凭据

当前 ECS 使用 SimNow 第一套生产时段环境：主行情前置 `tcp://182.254.243.31:30011`，备用行情前置为 `30012`、`30013`，BrokerID `9999`。该环境服务时间与真实生产环境一致，适合采集日盘和夜盘真实行情。交易前置 `30001`、`30002`、`30003` 均未接入本只读行情网关。

公共连接参数保存在 `/etc/ctp-md/ctp-md.env`。私密凭据文件格式如下：

```sh
install -m 600 /dev/null /etc/ctp-md/ctp-md.credentials
# 使用服务器上的编辑器填写，不要通过聊天发送：
# CTP_USER_ID=你的SimNow账号
# CTP_PASSWORD=你的SimNow密码
chmod 600 /etc/ctp-md/ctp-md.credentials
systemctl start ctp-md-gateway.service ctp-minute-recorder.service
systemctl status ctp-md-gateway.service ctp-minute-recorder.service --no-pager
journalctl -u ctp-md-gateway.service -u ctp-minute-recorder.service -n 100 --no-pager
```

`mduserapi` 只提供 `ReqUserLogin`，没有终端认证请求；`APPID=simnow_client_test` 和认证码只属于 `traderapi` 的 `ReqAuthenticate`，本只读行情服务不保存或发送它们。

2026-09-08 09:50 CST 从 ECS 探测第二套开发环境 `182.254.243.31:40011` 返回 `Connection refused`，当时已超过该环境交易日 `09:00` 的服务截止时间。09:57 CST 获取账号页最新第一套环境地址后，`30011`、`30012`、`30013` 从 ECS 均通过 TCP 连通测试，正式配置随即切换到主线路 `30011`。CTP Mini V1.7.5 能建立 TCP 但登录请求无响应，约 35 秒后以前置断开原因 `-3` 重连；相同账号和地址使用标准 CTP 6.7.13 则登录成功并完成全部 60 个合约订阅，因此正式服务改用标准 CTP 6.7.13。切换后真实 Tick 已聚合为分钟并通过 SSH 同步到 Mac 长期库，行情链路完成首次生产验收。确认稳定后，ECS 上不参与当前运行的 Mini、备份、源码、SDK ZIP、示例配置及测试 flow 已全部删除。

服务成功登录并收到行情后，检查两端数据：

```sh
# ECS
/usr/bin/python3 /opt/ctp-md/bin/record_stream.py status --db /var/lib/ctp-md/minute_bars.sqlite3

# Mac mini
.venv/bin/python scripts/ctp/record_stream.py status --db data/ctp_minute_bars.sqlite3
.venv/bin/python scripts/ctp/record_stream.py daily --db data/ctp_minute_bars.sqlite3 --trading-day YYYYMMDD
launchctl print gui/$(id -u)/com.hw.futures-arbitrage.ctp-sync
```

## SQLite 口径

`minute_bars` 以 `(symbol, minute)` 唯一，包含 `trading_day`、OHLC、分钟增量成交量/成交额、分钟末持仓量、tick 数、完成标记和同步序号。代码统一为 `SHFE.RB2610`、`DCE.JM2701`、`CZCE.SM2701` 等格式；夜盘自然时间保存在 `minute`，归属交易日保存在 `trading_day`。

`daily` 命令按 `trading_day` 将 CTP 分钟聚合为日 OHLC、成交量和成交额，可与下载的 TqSdk 日线按“合约 + 交易日”核对。两者有差异时保留两份来源和差异记录，不用 TqSdk 日线覆盖 CTP 分钟路径。

当前报价由记录器在内存中按合约维护，原始 Tick 到达后进入仅监听 `127.0.0.1:19002` 的发布流。为控制 SSH、SQLite 和浏览器重绘压力，同一合约在 100ms 窗口内只发送最后一笔；这是 Tick 驱动的最新价，不是 100ms 定时伪行情，也不长期保存逐笔历史。Mac 的单个持久 SSH 进程接收后更新 `current_quotes`，FastAPI SSE 再按变化推送给页面；不为每个网页请求新建 SSH，也不在每个 Tick 上重算 TqSdk 历史统计。

已完成分钟使用同一 SSH 连接按 `sync_seq` 增量复制，数据库兜底快照周期为 2 秒，正常情况下每分钟结束约 15 秒后完成。ECS 保留 30 天用于覆盖 Mac 断网或重启；若 Mac 离线超过保留期导致同步序号断层，服务会在日志中明确报警，不会静默伪造缺失数据。`19001` 和 `19002` 均只监听 ECS 回环地址。
