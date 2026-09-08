'use client';
import { useState, useEffect, useId } from 'react';
import {
  Plus,
  ArrowUpRight,
  Info,
  Pencil,
  Trash2,
  RefreshCw,
} from 'lucide-react';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
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
import { Switch } from '@/components/ui/switch';
import { Input } from '@/components/ui/input';
import {
  api,
  formula,
  fmt,
  tone,
  type Quote,
  type Combo,
  type Term,
  type RealtimeSnapshot,
  type AlertRule,
} from '@/lib/types';
import { Chart, baseChart } from './chart';
import { Pick } from './controls';
export function RadarView({
  rows,
  source,
  onAnalyze,
}: {
  rows: Quote[];
  source?: string;
  onAnalyze: (c: Combo) => void;
}) {
  const [filter, setFilter] = useState('全部');
  const [sort, setSort] = useState('extreme');
  const filtered = rows
    .filter((q) => filter === '全部' || q.status === filter)
    .slice()
    .sort((a, b) =>
      sort === 'extreme'
        ? Math.min(a.percentile60, 100 - a.percentile60) -
          Math.min(b.percentile60, 100 - b.percentile60)
        : sort === 'low'
          ? a.percentile60 - b.percentile60
          : b.percentile60 - a.percentile60,
    );
  return (
    <>
      <div className="page-title">
        <div>
          <h1>机会雷达</h1>
          <p>用统计位置筛选值得进一步研究的价差。</p>
        </div>
        <span className="pill">
          60 / 250 个
          {!source ? '历史样本' : source === 'tqsdk' || source === 'simnow_tqsdk' ? '交易日' : '模拟工作日'}
        </span>
      </div>
      <div className="subbar">
        <div className="filter-row">
          {['全部', '极端低', '偏低', '中性', '偏高', '极端高'].map((s) => (
            <button
              className={`filter-btn ${s === filter ? 'active' : ''}`}
              key={s}
              onClick={() => setFilter(s)}
            >
              {s}
            </button>
          ))}
        </div>
        <Pick
          label="雷达排序"
          value={sort}
          onChange={setSort}
          items={[
            { value: 'extreme', label: '极端程度优先' },
            { value: 'low', label: '分位从低到高' },
            { value: 'high', label: '分位从高到低' },
          ]}
        />
      </div>
      <div className="panel">
        <Table className="data-table">
          <TableHeader>
            <TableRow>
              {[
                '套利组合',
                '当前价差 / 比价',
                '60日分位',
                '250日分位',
                'Z-score · 250日',
                '状态',
                '操作',
              ].map((t) => (
                <TableHead key={t}>{t}</TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.map((q) => (
              <TableRow key={q.id}>
                <TableCell>
                  <button className="table-name" onClick={() => onAnalyze(q)}>
                    {q.name}
                    <small className="num">{formula(q)}</small>
                  </button>
                </TableCell>
                <TableCell className={`num ${tone(q.change)}`}>
                  {fmt(q.current, q.mode === 'ratio' ? 4 : 2)}
                </TableCell>
                <TableCell>
                  <div className="percentile-cell">
                    <div className="rank-track">
                      <i style={{ left: `${Math.min(95, q.percentile60)}%` }} />
                    </div>
                    <span className="num">{fmt(q.percentile60, 1)}%</span>
                  </div>
                </TableCell>
                <TableCell className="num">
                  {fmt(q.percentile250, 1)}%
                </TableCell>
                <TableCell className="num">{fmt(q.zscore)}</TableCell>
                <TableCell>
                  <span
                    className={`status-pill ${q.status.includes('低') ? 'low' : q.status.includes('高') ? 'high' : ''}`}
                  >
                    {q.status}
                  </span>
                </TableCell>
                <TableCell>
                  <button className="btn" onClick={() => onAnalyze(q)}>
                    分析
                    <ArrowUpRight size={14} />
                  </button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {!filtered.length && <div className="empty">暂无符合条件的组合</div>}
        <div className="annotation">
          状态按 60 日分位划分：≤5% 极端低，5%–25% 偏低，25%–75% 中性，75%–95%
          偏高，≥95% 极端高。Z-score 使用 250 日总体标准差。
        </div>
      </div>
      <p className="footnote">
        <Info size={14} />
        {!source ? '分位' : source === 'tqsdk' || source === 'simnow_tqsdk' ? '历史分位' : '模拟分位'}只表示统计位置，不构成套利收益承诺。
        样本长度以当前合约实际可用历史为准。
      </p>
    </>
  );
}
export function TermView({ matrix }: { matrix: boolean }) {
  const [product, setProduct] = useState('RB');
  const [data, setData] = useState<Term | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    api<Term>(`/term?product=${product}`, 'GET', undefined, controller.signal)
      .then(setData)
      .catch((e) => {
        if (e.name !== 'AbortError') setError(e.message);
      });
    return () => controller.abort();
  }, [product, retry]);
  const streamSymbols =
    data?.source === 'simnow_tqsdk'
      ? data.contracts.map((contract) => contract.symbol).join(',')
      : '';
  useEffect(() => {
    if (!streamSymbols) return;
    const stream = new EventSource(
      `/api/realtime/stream?symbols=${encodeURIComponent(streamSymbols)}`,
    );
    const onQuotes = (raw: Event) => {
      const snapshot = JSON.parse(
        (raw as MessageEvent<string>).data,
      ) as RealtimeSnapshot;
      setData((previous) => {
        if (!previous || previous.source !== 'simnow_tqsdk') return previous;
        const contracts = previous.contracts.map((contract) => ({
          ...contract,
          price: snapshot.quotes[contract.symbol]?.price ?? contract.price,
        }));
        const prices = contracts.map((contract) => contract.price);
        return {
          ...previous,
          contracts,
          matrix: prices.map((a) => prices.map((b) => a - b)),
        };
      });
    };
    stream.addEventListener('quotes', onQuotes);
    return () => {
      stream.removeEventListener('quotes', onQuotes);
      stream.close();
    };
  }, [streamSymbols]);
  const heat = (n: number) =>
    n === 0
      ? '#172130'
      : n > 0
        ? `rgba(177,76,96,${Math.min(0.55, 0.1 + Math.abs(n) / 230)})`
        : `rgba(39,142,127,${Math.min(0.55, 0.1 + Math.abs(n) / 230)})`;
  return (
    <>
      <div className="page-title">
        <div>
          <h1>{matrix ? '价差矩阵' : '期限结构'}</h1>
          <p>
            {matrix
              ? '同品种各交割月两两比较，快速定位跨期价差。'
              : data
                ? `观察不同交割月份的${data.source === 'simnow_tqsdk' ? 'SimNow 实时' : data.source === 'tqsdk' ? '实时' : '模拟'}价格曲线。`
                : '正在读取合约期限结构。'}
          </p>
        </div>
        <span className="pill badge-warn">
          {!data
            ? '加载行情'
            : data.source === 'simnow_tqsdk'
              ? 'SimNow Tick · TqSdk 合约'
              : data.source === 'tqsdk'
                ? 'TqSdk · 未到期合约'
              : '框架预览 · 模拟数据'}
        </span>
      </div>
      <div className="subbar">
        <div className="filter-row">
          {[
            ['RB', '螺纹钢'],
            ['JM', '焦煤'],
            ['SM', '硅锰'],
            ['J', '焦炭'],
            ['I', '铁矿石'],
          ].map(([v, l]) => (
            <button
              className={`filter-btn ${product === v ? 'active' : ''}`}
              key={v}
              onClick={() => {
                if (product !== v) {
                  setData(null);
                  setError('');
                  setProduct(v);
                }
              }}
            >
              {l} <span className="num">{v}</span>
            </button>
          ))}
        </div>
        <button className="btn" onClick={() => setRetry((v) => v + 1)}>
          <RefreshCw size={14} />
          刷新
        </button>
      </div>
      {error && (
        <div className="banner error" role="alert">
          {error}
        </div>
      )}
      {!data ? (
        <div className="empty loading">加载合约结构…</div>
      ) : matrix ? (
        <div className="panel">
          <div className="panel-title">
            <h2>行合约 − 列合约</h2>
            <small>单位：价格点</small>
          </div>
          <Table className="data-table matrix-table">
            <TableHeader>
              <TableRow>
                <TableHead>A − B</TableHead>
                {data.contracts.map((c) => (
                  <TableHead key={c.symbol} className="num">
                    {c.symbol}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.contracts.map((c, i) => (
                <TableRow key={c.symbol}>
                  <TableCell className="num">{c.symbol}</TableCell>
                  {data.matrix[i].map((n, j) => (
                    <TableCell
                      key={j}
                      className={`num matrix-cell ${tone(n)}`}
                      style={{ background: heat(n) }}
                    >
                      {i === j ? '—' : fmt(n, 1)}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <div className="matrix-legend">
            <span className="down">■ 负价差</span>
            <span>颜色深浅表示绝对值大小</span>
            <span className="up">■ 正价差</span>
          </div>
        </div>
      ) : (
        <div className="term-grid">
          <section className="panel">
            <div className="panel-title">
              <h2>{data.contracts[0]?.name} · 交割月曲线</h2>
              <small>同一时点价格</small>
            </div>
            <Chart
              label="各交割月价格期限结构曲线"
              option={{
                ...baseChart,
                animation: true,
                animationDurationUpdate: 100,
                animationEasingUpdate: 'linear',
                grid: { left: 65, right: 35, top: 40, bottom: 50 },
                xAxis: {
                  ...baseChart.xAxis,
                  data: data.contracts.map((c) => c.symbol),
                },
                series: [
                  {
                    name: data.source === 'simnow_tqsdk' ? 'SimNow 最新价' : data.source === 'tqsdk' ? 'TqSdk 最新价' : '模拟价格',
                    type: 'line',
                    data: data.contracts.map((c) => c.price),
                    symbolSize: 8,
                    lineStyle: { color: '#64cfc3', width: 2 },
                    itemStyle: { color: '#64cfc3' },
                    areaStyle: { color: '#64cfc3', opacity: 0.04 },
                    label: {
                      show: true,
                      position: 'top',
                      color: '#b2d7d5',
                      formatter: (p) => fmt(Number(p.value), 1),
                    },
                  },
                ],
              }}
            />
          </section>
          <section className="panel">
            <div className="panel-title">
              <h2>合约价格</h2>
              <small>{data.contracts.length} 个交割月</small>
            </div>
            <Table className="data-table">
              <TableHeader>
                <TableRow>
                  <TableHead>合约</TableHead>
                  <TableHead>价格</TableHead>
                  <TableHead>较近月</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.contracts.map((c) => (
                  <TableRow key={c.symbol}>
                    <TableCell className="num">{c.symbol}</TableCell>
                    <TableCell className="num">{fmt(c.price, 1)}</TableCell>
                    <TableCell
                      className={`num ${tone(c.price - data.contracts[0].price)}`}
                    >
                      {fmt(c.price - data.contracts[0].price, 1)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </section>
        </div>
      )}
      <p className="footnote">
        <Info size={14} />
        {!data
          ? '正在等待行情源返回合约与价格。'
          : data.source === 'simnow_tqsdk'
          ? '合约目录来自 TqSdk；价格来自同一批 SimNow CTP 最新快照。'
          : data.source === 'tqsdk'
          ? '合约仅包含 TqSdk 返回的未到期合约；矩阵按同一批最新价计算。'
          : '本页为模拟数据结构预览。'}
      </p>
    </>
  );
}
const metricLabels = {
  price: '价格阈值',
  percentile: '60日分位',
  zscore: '250日 Z-score',
};
export function AlertsView({
  alerts,
  combos,
  onRefresh,
}: {
  alerts: AlertRule[];
  combos: Quote[];
  onRefresh: () => Promise<void>;
}) {
  const thresholdId = useId();
  const [draft, setDraft] = useState<AlertRule | null>(null);
  const [remove, setRemove] = useState<AlertRule | null>(null);
  const [error, setError] = useState('');
  const [formError, setFormError] = useState('');
  const [busy, setBusy] = useState(false);
  const edit = (a: AlertRule) => {
    setDraft({ ...a });
    setFormError('');
  };
  const save = async (e: React.SyntheticEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!draft) return;
    setBusy(true);
    try {
      const { id, ...body } = draft;
      await api(`/alerts${id ? `/${id}` : ''}`, id ? 'PUT' : 'POST', body);
      setDraft(null);
      setError('');
      await onRefresh();
    } catch (e) {
      setFormError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const toggle = async (a: AlertRule) => {
    setBusy(true);
    try {
      const { id, ...body } = a;
      await api(`/alerts/${id}`, 'PUT', { ...body, enabled: !body.enabled });
      setError('');
      await onRefresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <div className="page-title">
        <div>
          <h1>告警设置</h1>
          <p>保存监控条件，为后续行情告警接入做准备。</p>
        </div>
        <button
          className="btn primary"
          disabled={!combos.length}
          onClick={() =>
            edit({
              combination_id: combos[0].id,
              metric: 'price',
              operator: 'lte',
              threshold: 0,
              enabled: true,
            })
          }
        >
          <Plus size={16} />
          新增告警
        </button>
      </div>
      <div className="alert-note">
        <Info size={17} />
        本阶段仅在本机保存配置。“启用”记录规则状态，暂不触发后台通知、声音或推送。
      </div>
      {error && (
        <div className="banner error" role="alert">
          {error}
        </div>
      )}
      {alerts.length ? (
        <div className="panel">
          <Table className="data-table">
            <TableHeader>
              <TableRow>
                {['套利组合', '指标', '条件', '配置状态', '操作'].map((x) => (
                  <TableHead key={x}>{x}</TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {alerts.map((a) => (
                <TableRow key={a.id}>
                  <TableCell>
                    {combos.find((c) => c.id === a.combination_id)?.name ||
                      '已删除组合'}
                  </TableCell>
                  <TableCell>{metricLabels[a.metric]}</TableCell>
                  <TableCell className="num">
                    {a.operator === 'gte' ? '≥' : '≤'}{' '}
                    {fmt(
                      a.threshold,
                      a.metric === 'price' &&
                        combos.find((c) => c.id === a.combination_id)?.mode ===
                          'ratio'
                        ? 4
                        : 2,
                    )}
                    {a.metric === 'percentile' ? '%' : ''}
                  </TableCell>
                  <TableCell>
                    <div className="alert-actions">
                      <span className="muted" style={{ fontSize: 12 }}>
                        {a.enabled ? '已启用' : '已停用'}
                      </span>
                      <Switch
                        aria-label={`启用 ${combos.find((c) => c.id === a.combination_id)?.name} ${metricLabels[a.metric]}`}
                        checked={a.enabled}
                        disabled={busy}
                        onCheckedChange={() => void toggle(a)}
                      />
                    </div>
                  </TableCell>
                  <TableCell>
                    <div className="alert-actions">
                      <button
                        className="icon-btn"
                        aria-label="编辑告警"
                        onClick={() => edit(a)}
                      >
                        <Pencil size={16} />
                      </button>
                      <button
                        className="icon-btn"
                        aria-label="删除告警"
                        onClick={() => {
                          setRemove(a);
                          setFormError('');
                        }}
                      >
                        <Trash2 size={16} />
                      </button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : (
        <div className="empty">
          <Info size={27} />
          <span>
            {combos.length
              ? '暂无告警，添加第一个监控条件。'
              : '请先在套利监控页添加组合。'}
          </span>
        </div>
      )}
      <Dialog
        open={!!draft}
        onOpenChange={(o) => {
          if (!o && !busy) setDraft(null);
        }}
      >
        <DialogContent className="modal">
          <DialogTitle>{draft?.id ? '编辑告警' : '新增告警'}</DialogTitle>
          <DialogDescription>
            配置保存在本机，关闭网页后仍会保留。
          </DialogDescription>
          {draft && (
            <form onSubmit={save}>
              <div className="form-grid">
                <div className="field wide">
                  <span>套利组合</span>
                  <Pick
                    label="告警组合"
                    value={draft.combination_id}
                    onChange={(v) => setDraft({ ...draft, combination_id: v })}
                    items={combos.map((c) => ({ value: c.id, label: c.name }))}
                  />
                </div>
                <div className="field">
                  <span>告警指标</span>
                  <Pick
                    label="告警指标"
                    value={draft.metric}
                    onChange={(v) =>
                      setDraft({
                        ...draft,
                        metric: v as AlertRule['metric'],
                        threshold: 0,
                      })
                    }
                    items={Object.entries(metricLabels).map(
                      ([value, label]) => ({ value, label }),
                    )}
                  />
                </div>
                <div className="field">
                  <span>比较条件</span>
                  <Pick
                    label="比较条件"
                    value={draft.operator}
                    onChange={(v) =>
                      setDraft({
                        ...draft,
                        operator: v as AlertRule['operator'],
                      })
                    }
                    items={[
                      { value: 'lte', label: '小于等于 ≤' },
                      { value: 'gte', label: '大于等于 ≥' },
                    ]}
                  />
                </div>
                <label className="field" htmlFor={thresholdId}>
                  <span>
                    阈值{draft.metric === 'percentile' ? '（0–100%）' : ''}
                  </span>
                  <Input
                    id={thresholdId}
                    className="input"
                    aria-label="告警阈值"
                    type="number"
                    required
                    step="any"
                    min={draft.metric === 'percentile' ? 0 : undefined}
                    max={draft.metric === 'percentile' ? 100 : undefined}
                    value={draft.threshold}
                    onChange={(e) =>
                      setDraft({ ...draft, threshold: Number(e.target.value) })
                    }
                  />
                </label>
                <div className="field">
                  <span>配置状态</span>
                  <div className="actions" style={{ height: 38 }}>
                    <Switch
                      checked={draft.enabled}
                      onCheckedChange={(v) =>
                        setDraft({ ...draft, enabled: v })
                      }
                      aria-label="启用此告警"
                    />
                    <span>{draft.enabled ? '启用' : '停用'}</span>
                  </div>
                </div>
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
                  {busy ? '正在保存…' : '保存告警'}
                </button>
              </div>
            </form>
          )}
        </DialogContent>
      </Dialog>
      <AlertDialog
        open={!!remove}
        onOpenChange={(o) => {
          if (!o && !busy) setRemove(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogTitle>删除这条告警？</AlertDialogTitle>
          <AlertDialogDescription>
            该规则会从本机配置中移除。
          </AlertDialogDescription>
          {formError && <p className="form-error">{formError}</p>}
          <div className="modal-actions">
            <AlertDialogCancel disabled={busy}>取消</AlertDialogCancel>
            <button
              className="btn danger"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await api(`/alerts/${remove?.id}`, 'DELETE');
                  setRemove(null);
                  await onRefresh();
                } catch (e) {
                  setFormError((e as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              删除告警
            </button>
          </div>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
