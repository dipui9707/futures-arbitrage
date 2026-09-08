'use client';
import { useEffect, useState, useCallback, useRef } from 'react';
import {
  Activity,
  LayoutGrid,
  ChartCandlestick,
  Radar,
  ChartNoAxesCombined,
  Grid2X2,
  Bell,
  Plus,
  Star,
  ArrowUp,
  ArrowDown,
  ArrowUpRight,
  SlidersHorizontal,
  Pencil,
  Trash2,
  Info,
  X,
  RefreshCw,
  Database,
  Check,
} from 'lucide-react';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogCancel,
} from '@/components/ui/alert-dialog';
import {
  api,
  blankCombo,
  bodyCombo,
  formula,
  fmt,
  signed,
  tone,
  type Combo,
  type Market,
  type RealtimeSnapshot,
  type Quote,
  type Contract,
  type AlertRule,
} from '@/lib/types';
import { Pick, ComboFields } from './controls';
import { Spark } from './chart';
import { AnalysisView } from './views-analysis';
import { RadarView, TermView, AlertsView } from './views-other';
const tabs = [
  { id: 'monitor', label: '套利监控', icon: LayoutGrid },
  { id: 'analysis', label: '套利分析', icon: ChartCandlestick },
  { id: 'radar', label: '机会雷达', icon: Radar },
  { id: 'term', label: '期限结构', icon: ChartNoAxesCombined },
  { id: 'matrix', label: '价差矩阵', icon: Grid2X2 },
  { id: 'alerts', label: '告警设置', icon: Bell },
];
export default function Terminal() {
  const [view, setView] = useState('monitor');
  const [market, setMarket] = useState<Market | null>(null);
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [alerts, setAlerts] = useState<AlertRule[]>([]);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [filter, setFilter] = useState('all');
  const [sort, setSort] = useState('manual');
  const [draft, setDraft] = useState<Combo | null>(null);
  const [pendingDelete, setPendingDelete] = useState<Quote | null>(null);
  const [formError, setFormError] = useState('');
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<Combo>(blankCombo);
  const [streamState, setStreamState] = useState<
    'idle' | 'connecting' | 'open' | 'stale'
  >('idle');
  const requestSeq = useRef(0);
  const active = useRef(true);
  const refresh = useCallback(async () => {
    const seq = ++requestSeq.current;
    try {
      const [m, c, a] = await Promise.all([
        api<Market>('/market'),
        api<Contract[]>('/contracts'),
        api<AlertRule[]>('/alerts'),
      ]);
      if (active.current && seq === requestSeq.current) {
        setMarket(m);
        setContracts(c);
        setAlerts(a);
        setError('');
      }
    } catch (e) {
      if (active.current && seq === requestSeq.current)
        setError((e as Error).message + '；已显示的数据可能过期。');
    }
  }, []);
  useEffect(() => {
    active.current = true;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const run = async () => {
      await refresh();
      if (alive) timer = setTimeout(run, 30000);
    };
    void run();
    const hash = () => {
      const v = location.hash.slice(1);
      if (tabs.some((t) => t.id === v)) setView(v);
    };
    hash();
    window.addEventListener('hashchange', hash);
    return () => {
      alive = false;
      clearTimeout(timer);
      active.current = false;
      window.removeEventListener('hashchange', hash);
    };
  }, [refresh]);
  const navigate = (v: string) => {
    setView(v);
    history.replaceState(null, '', `#${v}`);
  };
  const openAnalysis = (q: Combo) => {
    setSelected(bodyCombo(q));
    navigate('analysis');
  };
  const mutate = async (fn: () => Promise<unknown>, message: string) => {
    setBusy(true);
    try {
      await fn();
      setNotice(message);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const edit = (c: Combo) => {
    setDraft({ ...c });
    setFormError('');
  };
  const save = async (e: React.SyntheticEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!draft) return;
    setBusy(true);
    setFormError('');
    try {
      await api(
        `/combinations${draft.id ? `/${draft.id}` : ''}`,
        draft.id ? 'PUT' : 'POST',
        bodyCombo(draft),
      );
      setDraft(null);
      setNotice('组合已保存到本机');
      await refresh();
    } catch (e) {
      setFormError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const rows = market?.combinations || [];
  const hybrid = market?.source === 'simnow_tqsdk';
  const live = market?.source === 'tqsdk' || hybrid;
  const streamSymbols = hybrid
    ? [...new Set(rows.flatMap((row) => [row.leg_a, row.leg_b]))]
        .sort()
        .join(',')
    : '';
  useEffect(() => {
    if (!streamSymbols) return;
    const stream = new EventSource(
      `/api/realtime/stream?symbols=${encodeURIComponent(streamSymbols)}`,
    );
    stream.onopen = () => setStreamState('open');
    stream.onerror = () => setStreamState('connecting');
    const onQuotes = (raw: Event) => {
      try {
        const snapshot = JSON.parse(
          (raw as MessageEvent<string>).data,
        ) as RealtimeSnapshot;
        setMarket((previous) => {
          if (!previous || previous.source !== 'simnow_tqsdk') return previous;
          return {
            ...previous,
            timestamp: snapshot.timestamp,
            combinations: previous.combinations.map((quote) => {
              const legA = snapshot.quotes[quote.leg_a]?.price;
              const legB = snapshot.quotes[quote.leg_b]?.price;
              if (legA == null || legB == null) return quote;
              const previousA = quote.leg_a_price - quote.leg_a_change;
              const previousB = quote.leg_b_price - quote.leg_b_change;
              const previousValue = quote.current - quote.change;
              const current =
                quote.mode === 'ratio'
                  ? legB === 0
                    ? quote.current
                    : legA / legB
                  : quote.mode === 'weighted'
                    ? quote.coefficient_a * legA - quote.coefficient_b * legB
                    : legA - legB;
              const sparkline = [...quote.sparkline];
              if (sparkline.length) sparkline[sparkline.length - 1] = current;
              else sparkline.push(current);
              return {
                ...quote,
                current,
                change: current - previousValue,
                leg_a_price: legA,
                leg_b_price: legB,
                leg_a_change: legA - previousA,
                leg_b_change: legB - previousB,
                sparkline,
              };
            }),
          };
        });
        setStreamState('open');
      } catch {
        setStreamState('stale');
      }
    };
    const onStatus = () => setStreamState('stale');
    stream.addEventListener('quotes', onQuotes);
    stream.addEventListener('status', onStatus);
    return () => {
      stream.removeEventListener('quotes', onQuotes);
      stream.removeEventListener('status', onStatus);
      stream.close();
    };
  }, [streamSymbols]);
  const list = rows.filter(
    (c) =>
      filter === 'all' ||
      (filter === 'favorites' && c.favorite) ||
      filter === c.leg_a.replace(/\d/g, ''),
  );
  if (sort === 'name')
    list.sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'));
  if (sort === 'value') list.sort((a, b) => b.current - a.current);
  if (sort === 'percentile')
    list.sort((a, b) => a.percentile60 - b.percentile60);
  const move = (q: Quote, d: number) => {
    const ids = rows.map((x) => x.id);
    const visible = list.findIndex((x) => x.id === q.id);
    const neighbor = list[visible + d];
    if (!neighbor) return;
    const i = ids.indexOf(q.id),
      j = ids.indexOf(neighbor.id);
    [ids[i], ids[j]] = [ids[j], ids[i]];
    void mutate(
      () => api('/combinations/reorder', 'PUT', { ids }),
      '自定义顺序已保存',
    );
  };
  return (
    <div className="terminal">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">
            <ChartCandlestick size={21} />
          </div>
          <strong>期货套利监控</strong>
          <small>ARB / DESK</small>
        </div>
        <div className="session">
          <span className={`pill ${error ? 'badge-warn' : ''}`}>
            <i className="dot" />
            {error
              ? '连接异常'
              : !market
                ? '连接行情'
                : live
                  ? hybrid
                    ? streamState === 'open'
                      ? 'SimNow Tick 推送'
                      : streamState === 'stale'
                        ? 'SimNow 行情滞后'
                        : 'SimNow 推送连接中'
                    : 'TqSdk 行情'
                  : '模拟行情'}
          </span>
          <span className="muted">
            {!market ? '行情状态' : live ? '行情时间' : '模拟时间'}
          </span>
          <time className="num">
            {market
              ? new Intl.DateTimeFormat('sv-SE', {
                  timeZone: 'Asia/Shanghai',
                  year: 'numeric',
                  month: '2-digit',
                  day: '2-digit',
                  hour: '2-digit',
                  minute: '2-digit',
                  second: '2-digit',
                  fractionalSecondDigits: 3,
                  hourCycle: 'h23',
                }).format(new Date(market.timestamp))
              : '连接中'}
          </time>
          <span className="muted">CST</span>
        </div>
      </header>
      <Tabs
        value={view}
        onValueChange={(v) => navigate(String(v))}
        style={{ gap: 0 }}
      >
        <div className="nav-shell">
          <TabsList className="nav-list">
            {tabs.map((t, i) => (
              <TabsTrigger key={t.id} value={t.id} className="nav-tab">
                <t.icon size={16} />
                {t.label}
                <span className="tab-index">0{i + 1}</span>
              </TabsTrigger>
            ))}
          </TabsList>
        </div>
        <main className="workspace">
          {error && (
            <div className="banner error" role="alert">
              <Info size={16} />
              <span>{error}</span>
              <button className="btn" onClick={() => void refresh()}>
                <RefreshCw size={14} />
                重新连接
              </button>
            </div>
          )}
          {notice && (
            <output className="banner">
              <Check size={16} />
              <span>{notice}</span>
              <button
                className="icon-btn"
                style={{ marginLeft: 'auto' }}
                aria-label="关闭提示"
                onClick={() => setNotice('')}
              >
                <X size={16} />
              </button>
            </output>
          )}
          <TabsContent value="monitor">
            <div className="page-title">
              <div>
                <h1>套利监控</h1>
                <p>跨期价差与比价，一屏跟踪。</p>
              </div>
              <div className="actions">
                <span className="muted" style={{ fontSize: 12 }}>
                  {hybrid ? 'Tick 驱动 · 统计每 30 秒' : '每 30 秒更新'}
                </span>
                <button
                  className="btn primary"
                  onClick={() => edit(blankCombo)}
                >
                  <Plus size={16} />
                  新增组合
                </button>
              </div>
            </div>
            <div className="overview">
              <div className="overview-item">
                <span>监控组合</span>
                <b className="num">
                  {market ? String(rows.length).padStart(2, '0') : '—'}
                </b>
              </div>
              <div className="overview-item">
                <span>极端分位 · 60日</span>
                <b className="num blue">
                  {market
                    ? String(
                        rows.filter(
                          (c) => c.percentile60 <= 5 || c.percentile60 >= 95,
                        ).length,
                      ).padStart(2, '0')
                    : '—'}
                </b>
              </div>
              <div className="overview-item">
                <span>已启用告警</span>
                <b className="num">
                  {market
                    ? String(alerts.filter((a) => a.enabled).length).padStart(
                        2,
                        '0',
                      )
                    : '—'}
                </b>
              </div>
              <div className="overview-note">
                <Activity size={21} />
                <div>
                  {!market
                    ? '等待行情数据'
                    : live
                      ? hybrid
                        ? 'SimNow 实时 · TqSdk 历史'
                        : 'TqSdk 只读行情环境'
                      : '独立模拟行情环境'}
                  <br />
                  红色上涨 · 绿色下跌
                </div>
              </div>
            </div>
            <div className="subbar">
              <div className="filter-row">
                {[
                  ['all', '全部组合'],
                  ['favorites', '我的收藏'],
                  ['RB', '螺纹'],
                  ['JM', '焦煤'],
                  ['SM', '硅锰'],
                ].map(([id, label]) => (
                  <button
                    className={`filter-btn ${filter === id ? 'active' : ''}`}
                    key={id}
                    onClick={() => setFilter(id)}
                  >
                    {label}
                    {id === 'favorites' &&
                      ` ${rows.filter((c) => c.favorite).length}`}
                  </button>
                ))}
              </div>
              <div className="subbar-info">
                <SlidersHorizontal size={14} />
                <Pick
                  label="排序方式"
                  value={sort}
                  onChange={setSort}
                  items={[
                    { value: 'manual', label: '自定义顺序' },
                    { value: 'name', label: '组合名称' },
                    { value: 'value', label: '价差从高到低' },
                    { value: 'percentile', label: '60日分位从低到高' },
                  ]}
                />
              </div>
            </div>
            {!market ? (
              <div className="empty loading">
                <Database size={26} />
                正在连接本地行情服务…
              </div>
            ) : (
              <div className="cards">
                {list.map((q) => (
                  <article className="spread-card" key={q.id}>
                    <div className="card-head">
                      <button className="name" onClick={() => openAnalysis(q)}>
                        <h2>{q.name}</h2>
                      </button>
                      <div className="card-tools">
                        <button
                          className={`icon-btn ${q.favorite ? 'starred' : ''}`}
                          aria-label={`${q.favorite ? '取消收藏' : '收藏'} ${q.name}`}
                          disabled={busy}
                          onClick={() =>
                            void mutate(
                              () =>
                                api(`/combinations/${q.id}`, 'PUT', {
                                  ...bodyCombo(q),
                                  favorite: !q.favorite,
                                }),
                              q.favorite ? '已取消收藏' : '已收藏组合',
                            )
                          }
                        >
                          <Star
                            size={15}
                            fill={q.favorite ? 'currentColor' : 'none'}
                          />
                        </button>
                        <button
                          className="icon-btn"
                          aria-label={`编辑 ${q.name}`}
                          onClick={() => edit(q)}
                        >
                          <Pencil size={14} />
                        </button>
                        <button
                          className="icon-btn"
                          aria-label={`删除 ${q.name}`}
                          onClick={() => {
                            setPendingDelete(q);
                            setFormError('');
                          }}
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </div>
                    <div className="card-formula num">{formula(q)}</div>
                    <div className="card-main">
                      <div>
                        <div className={`spread-value num ${tone(q.change)}`}>
                          {fmt(q.current, q.mode === 'ratio' ? 4 : 2)}
                        </div>
                        <div className="card-change">
                          <span className="muted">当日变化</span>
                          <span className={`num ${tone(q.change)}`}>
                            {signed(q.change, q.mode === 'ratio' ? 4 : 2)}
                          </span>
                        </div>
                      </div>
                      <Spark
                        values={q.sparkline}
                        color={q.change >= 0 ? '#c08b9a' : '#57baaa'}
                      />
                    </div>
                    <div className="leg-table">
                      <div className="leg-row muted">
                        <span>合约</span>
                        <span>最新价</span>
                        <span>涨跌</span>
                      </div>
                      {[
                        [q.leg_a, q.leg_a_price, q.leg_a_change],
                        [q.leg_b, q.leg_b_price, q.leg_b_change],
                      ].map(([symbol, price, change]) => (
                        <div className="leg-row num" key={String(symbol)}>
                          <span>{symbol}</span>
                          <span>{fmt(Number(price), 1)}</span>
                          <span className={tone(Number(change))}>
                            {signed(Number(change), 1)}
                          </span>
                        </div>
                      ))}
                    </div>
                    <div className="card-foot">
                      <div className="rank-mini">
                        <span>60日分位</span>
                        <div className="rank-track">
                          <i
                            style={{ left: `${Math.min(95, q.percentile60)}%` }}
                          />
                        </div>
                        <b className="num">{fmt(q.percentile60, 1)}%</b>
                      </div>
                      <div className="actions">
                        {sort === 'manual' && (
                          <>
                            <button
                              className="icon-btn"
                              aria-label={`上移 ${q.name}`}
                              disabled={busy || list[0]?.id === q.id}
                              onClick={() => move(q, -1)}
                            >
                              <ArrowUp size={13} />
                            </button>
                            <button
                              className="icon-btn"
                              aria-label={`下移 ${q.name}`}
                              disabled={busy || list.at(-1)?.id === q.id}
                              onClick={() => move(q, 1)}
                            >
                              <ArrowDown size={13} />
                            </button>
                          </>
                        )}
                        <button
                          className="card-open"
                          onClick={() => openAnalysis(q)}
                        >
                          分析
                          <ArrowUpRight size={14} />
                        </button>
                      </div>
                    </div>
                  </article>
                ))}
                <button className="card-add" onClick={() => edit(blankCombo)}>
                  <Plus size={25} />
                  <span>添加套利组合</span>
                  <small>A − B / A ÷ B / 自定义系数</small>
                </button>
              </div>
            )}
            {market && list.length === 0 && filter !== 'all' && (
              <p className="footnote">
                当前筛选下暂无组合，可切换到全部组合或添加收藏。
              </p>
            )}
            <p className="footnote">
              <Info size={14} />
              {!market
                ? '等待数据源确认。'
                : live
                ? hybrid
                  ? '最新价来自 SimNow CTP，历史 K 线与统计样本来自 TqSdk；全链路只读。'
                  : '行情与历史 K 线来自本机 TqSdk，只读且不包含交易接口。'
                : '图中走势及统计均来自模拟样本。'}
              价差是价格差，不等同于合约乘数调整后的盈亏。
            </p>
          </TabsContent>
          <TabsContent value="analysis">
            <AnalysisView
              key={formula(selected)}
              initial={selected}
              contracts={contracts}
              combos={rows}
              onSave={edit}
            />
          </TabsContent>
          <TabsContent value="radar">
            <RadarView rows={rows} source={market?.source} onAnalyze={openAnalysis} />
          </TabsContent>
          <TabsContent value="term">
            <TermView matrix={false} />
          </TabsContent>
          <TabsContent value="matrix">
            <TermView matrix />
          </TabsContent>
          <TabsContent value="alerts">
            <AlertsView alerts={alerts} combos={rows} onRefresh={refresh} />
          </TabsContent>
        </main>
      </Tabs>
      <footer className="statusbar">
        <span>
          <span
            className="dot"
            style={{ color: error ? '#d2ac72' : '#53bba8' }}
          />
          本机服务 · {error ? '连接异常' : !market ? '加载行情' : hybrid ? streamState === 'open' ? 'SimNow Tick / TqSdk 历史' : 'SimNow 推送重连中' : live ? 'TqSdk 真实行情' : '模拟数据'} ·
          仅行情分析
        </span>
        <span>配置保存于本机　/　Asia/Shanghai　/　MVP 0.1</span>
      </footer>
      <Dialog
        open={draft !== null}
        onOpenChange={(open) => {
          if (!open && !busy) setDraft(null);
        }}
      >
        <DialogContent className="modal">
          <DialogTitle>
            {draft?.id ? '编辑套利组合' : '新增套利组合'}
          </DialogTitle>
          <DialogDescription>
            选择两腿和计算方式，保存后加入监控。
          </DialogDescription>
          {draft && (
            <form onSubmit={save}>
              <div className="form-grid">
                <ComboFields
                  value={draft}
                  onChange={setDraft}
                  contracts={contracts}
                  names
                />
              </div>
              <div className="form-preview num" style={{ marginTop: 20 }}>
                {formula(draft)}
              </div>
              {formError && (
                <p className="form-error" role="alert">
                  {formError}
                </p>
              )}
              <div className="modal-actions">
                <button
                  type="button"
                  className="btn"
                  disabled={busy}
                  onClick={() => setDraft(null)}
                >
                  取消
                </button>
                <button className="btn primary" disabled={busy}>
                  {busy ? '正在保存…' : '保存组合'}
                </button>
              </div>
            </form>
          )}
        </DialogContent>
      </Dialog>
      <AlertDialog
        open={!!pendingDelete}
        onOpenChange={(o) => {
          if (!o && !busy) setPendingDelete(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogTitle>删除“{pendingDelete?.name}”？</AlertDialogTitle>
          <AlertDialogDescription>
            该组合及关联告警配置会从本机移除。
          </AlertDialogDescription>
          {formError && (
            <p className="form-error" role="alert">
              {formError}
            </p>
          )}
          <div className="modal-actions">
            <AlertDialogCancel disabled={busy}>取消</AlertDialogCancel>
            <button
              className="btn danger"
              disabled={busy}
              onClick={async () => {
                if (!pendingDelete) return;
                setBusy(true);
                try {
                  await api(`/combinations/${pendingDelete.id}`, 'DELETE');
                  setPendingDelete(null);
                  setNotice('组合及关联告警已删除');
                  await refresh();
                } catch (e) {
                  setFormError((e as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              删除组合
            </button>
          </div>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
