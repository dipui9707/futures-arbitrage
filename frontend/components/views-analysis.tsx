'use client';
import { useState, useEffect, useMemo, useId } from 'react';
import { Plus, RefreshCw, Info } from 'lucide-react';
import type { EChartsOption, SeriesOption } from 'echarts';
import { Checkbox } from '@/components/ui/checkbox';
import {
  api,
  bodyCombo,
  formula,
  fmt,
  signed,
  tone,
  type Combo,
  type Contract,
  type Analysis,
} from '@/lib/types';
import { Chart, baseChart } from './chart';
import { Pick, ComboFields } from './controls';
const periods = [
  ['1m', '1m'],
  ['5m', '5m'],
  ['15m', '15m'],
  ['30m', '30m'],
  ['60m', '60m'],
  ['2h', '2h'],
  ['4h', '4h'],
  ['1d', '日线'],
];
const named = (c: Combo) => ({ ...bodyCombo(c), name: c.name || formula(c) });
export function AnalysisView({
  initial,
  contracts,
  combos,
  onSave,
}: {
  initial: Combo;
  contracts: Contract[];
  combos: Combo[];
  onSave: (c: Combo) => void;
}) {
  const sigmaId = useId();
  const [draft, setDraft] = useState<Combo>(named(initial));
  const [applied, setApplied] = useState<Combo>(named(initial));
  const [period, setPeriod] = useState('1d');
  const [count, setCount] = useState('300');
  const [data, setData] = useState<Analysis | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [sigma, setSigma] = useState(true);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let stopped = false;
    const load = async () => {
      setLoading(true);
      try {
        const result = await api<Analysis>(
          '/analysis',
          'POST',
          { combination: bodyCombo(applied), period, count: Number(count) },
          controller.signal,
        );
        if (!stopped) {
          setData(result);
          setError('');
        }
      } catch (e) {
        if (!stopped) setError((e as Error).message);
      } finally {
        if (!stopped) {
          setLoading(false);
          timer = setTimeout(load, 15000);
        }
      }
    };
    void load();
    return () => {
      stopped = true;
      clearTimeout(timer);
      controller.abort();
    };
  }, [applied, period, count, retry]);
  const candle = useMemo<EChartsOption>(() => {
    if (!data) return {};
    const colors = ['#e2c183', '#89bfe8', '#aa8beb', '#77c0a7', '#cc8da1'];
    const series: SeriesOption[] = [
      {
        name: '价差 OHLC',
        type: 'candlestick',
        data: data.bars.map((b) => [b.open, b.close, b.low, b.high]),
        itemStyle: {
          color: '#e3757f',
          color0: '#45b99d',
          borderColor: '#ff8990',
          borderColor0: '#52cfae',
        },
      },
    ];
    Object.entries(data.ma).forEach(([w, values], i) =>
      series.push({
        name: `MA${w}`,
        type: 'line',
        data: values,
        showSymbol: false,
        lineStyle: { width: 1, color: colors[i] },
        itemStyle: { color: colors[i] },
      }),
    );
    if (sigma) {
      [
        [-2, '−2σ'],
        [-1, '−1σ'],
        [0, '均值'],
        [1, '+1σ'],
        [2, '+2σ'],
      ].forEach(([n, label]) =>
        series.push({
          name: String(label),
          type: 'line',
          data: data.bars.map(
            () => data.stats.mean + Number(n) * data.stats.std,
          ),
          showSymbol: false,
          silent: true,
          lineStyle: {
            width: n === 0 ? 1.3 : 1,
            type: 'dashed',
            color: n === 0 ? '#7da6df' : '#506680',
            opacity: n === 0 ? 1 : 0.75,
          },
          itemStyle: { color: '#617e9e' },
        }),
      );
    }
    return {
      ...baseChart,
      grid: { left: 66, right: 24, top: 48, bottom: 70 },
      legend: {
        ...baseChart.legend,
        selected: { MA10: false, MA120: false },
        type: 'scroll',
      },
      xAxis: {
        ...baseChart.xAxis,
        type: 'category',
        data: data.bars.map((b) =>
          period === '1d'
            ? b.time.slice(0, 10)
            : b.time.slice(5, 16).replace('T', ' '),
        ),
        boundaryGap: true,
      },
      yAxis: {
        ...baseChart.yAxis,
        axisLabel: {
          formatter: (v: number) => fmt(v, applied.mode === 'ratio' ? 4 : 1),
          color: '#8195b3',
          fontSize: 11,
        },
      },
      dataZoom: [
        { type: 'inside', start: 45, end: 100 },
        {
          type: 'slider',
          height: 17,
          bottom: 14,
          start: 45,
          end: 100,
          borderColor: '#2c3c51',
          fillerColor: '#536b8e2a',
          textStyle: { color: '#7a8eac', fontSize: 10 },
          dataBackground: {
            lineStyle: { color: '#3d5775' },
            areaStyle: { color: '#24384e' },
          },
          handleStyle: { color: '#7288a6' },
        },
      ],
      series,
    };
  }, [data, sigma, period, applied.mode]);
  const seasonal = useMemo<EChartsOption>(
    () =>
      !data
        ? {}
        : {
            ...baseChart,
            color: [
              '#526a91',
              '#808ccc',
              '#be9d6e',
              '#739b9d',
              '#9a80ae',
              '#5cddcb',
              '#d1dae7',
            ],
            legend: { ...baseChart.legend, type: 'scroll' },
            grid: { left: 64, right: 25, top: 48, bottom: 38 },
            xAxis: { ...baseChart.xAxis, data: data.seasonality.dates },
            series: [
              ...data.seasonality.years.map((y, i) => ({
                name: String(y.year),
                type: 'line' as const,
                data: y.values,
                showSymbol: false,
                connectNulls: false,
                lineStyle: {
                  width: i === data.seasonality.years.length - 1 ? 2.2 : 1.2,
                  opacity: i === data.seasonality.years.length - 1 ? 1 : 0.65,
                },
              })),
              {
                name: data.seasonality.average_label,
                type: 'line',
                data: data.seasonality.average,
                showSymbol: false,
                lineStyle: { width: 2, type: 'dashed' },
              },
            ],
          },
    [data],
  );
  const hist = useMemo<EChartsOption>(
    () =>
      !data
        ? {}
        : {
            ...baseChart,
            grid: { left: 40, right: 16, top: 18, bottom: 30 },
            tooltip: { ...baseChart.tooltip, trigger: 'axis' },
            xAxis: {
              ...baseChart.xAxis,
              data: data.histogram.map((b) =>
                fmt((b.low + b.high) / 2, applied.mode === 'ratio' ? 3 : 0),
              ),
              axisLabel: { fontSize: 10, color: '#7288a6', interval: 5 },
            },
            yAxis: {
              ...baseChart.yAxis,
              min: 0,
              axisLabel: { fontSize: 10, color: '#7288a6' },
            },
            series: [
              {
                type: 'bar',
                data: data.histogram.map((b) => ({
                  value: b.count,
                  itemStyle: {
                    color:
                      data.stats.current >= b.low &&
                      data.stats.current <= b.high
                        ? '#6fd6c7'
                        : '#486383',
                  },
                })),
                barWidth: '75%',
              },
            ],
          },
    [data, applied.mode],
  );
  const digits = applied.mode === 'ratio' ? 4 : 2;
  return (
    <>
      <div className="page-title">
        <div>
          <h1>套利分析</h1>
          <p>同步价差 K 线 · 区间统计 · 季节性比较</p>
        </div>
        <div className="actions">
          <Pick
            label="切换监控组合"
            value={
              combos.find((c) => formula(c) === formula(applied))?.id || ''
            }
            onChange={(id) => {
              const c = combos.find((x) => x.id === id);
              if (c) {
                setDraft(named(c));
                setData(null);
                setApplied(named(c));
              }
            }}
            items={combos.map((c) => ({ value: c.id!, label: c.name }))}
          />
          <button
            className="btn"
            onClick={() =>
              onSave({ ...draft, id: undefined, name: formula(draft) })
            }
          >
            <Plus size={15} />
            存为组合
          </button>
        </div>
      </div>
      <form
        className="panel combo-toolbar"
        onSubmit={(e) => {
          e.preventDefault();
          setData(null);
          setApplied(named(draft));
        }}
      >
        <ComboFields value={draft} onChange={setDraft} contracts={contracts} />
        <button className="btn primary" type="submit">
          应用分析
        </button>
        <div className="formula-readout num">{formula(applied)}</div>
      </form>
      {error && (
        <div className="banner error" role="alert">
          {error}
          <button className="btn" onClick={() => setRetry((x) => x + 1)}>
            <RefreshCw size={14} />
            重试
          </button>
        </div>
      )}
      <div className="analysis-grid">
        <section className="panel">
          <div className="panel-title">
            <h2 className="num">{formula(applied)}</h2>
            <small>
              {applied.mode === 'ratio' ? '比值 · 无量纲' : '价差 · 价格点'}
              　/　
              {!data
                ? '同步采样 OHLC'
                : data.source === 'tqsdk' || data.source === 'simnow_tqsdk'
                  ? 'TqSdk 同步采样 OHLC'
                  : '模拟采样 OHLC'}
            </small>
          </div>
          <div className="chart-toolbar">
            <div className="periods">
              {periods.map(([v, l]) => (
                <button
                  key={v}
                  className={`period ${period === v ? 'active' : ''}`}
                  onClick={() => {
                    if (period !== v) {
                      setData(null);
                      setPeriod(v);
                    }
                  }}
                >
                  {l}
                </button>
              ))}
            </div>
            <div className="actions">
              <label className="legend-checks" htmlFor={sigmaId}>
                <Checkbox
                  id={sigmaId}
                  checked={sigma}
                  onCheckedChange={(v) => setSigma(!!v)}
                />
                均值与 ±σ / ±2σ
              </label>
              <Pick
                label="分析样本范围"
                value={count}
                onChange={(v) => {
                  setData(null);
                  setCount(v);
                }}
                items={['150', '300', '600'].map((v) => ({
                  value: v,
                  label: `最近 ${v} 根`,
                }))}
              />
            </div>
          </div>
          {data ? (
            <Chart
              option={candle}
              label={`${formula(applied)} ${period}价差K线及移动平均线`}
            />
          ) : (
            <div className="empty loading" style={{ height: 400, border: 0 }}>
              正在计算价差 K 线…
            </div>
          )}
          <p className="chart-note">
            {data
              ? `${data.window_start.slice(0, 10)} → ${data.window_end.slice(0, 10)} · ${data.stats.sample_count} 根已完成 K 线`
              : '加载统计区间'}
            　{data?.bars.at(-1)?.incomplete ? '· 末根形成中' : ''}　
            {loading && data ? '· 更新中' : ''}
            <br />
            缩放只改变图表视野；右侧统计、均值及标准差基于全部已加载的完整 K
            线。点击图例可开关各条 MA。
          </p>
        </section>
        <aside className="panel">
          <div className="panel-title">
            <h2>区间统计</h2>
            <small>收盘值口径</small>
          </div>
          {data ? (
            <>
              <div className="stats">
                <div className="stat-row hero">
                  <span>当前值</span>
                  <b className="num">{fmt(data.stats.current, digits)}</b>
                </div>
                {[
                  [
                    '区间变化',
                    signed(data.stats.change, digits),
                    tone(data.stats.change),
                  ],
                  ['均值', fmt(data.stats.mean, digits), ''],
                  ['中位数', fmt(data.stats.median, digits), ''],
                  ['标准差 σ', fmt(data.stats.std, digits), ''],
                  ['Z-score', fmt(data.stats.zscore), 'blue'],
                  ['历史分位', `${fmt(data.stats.percentile, 1)}%`, 'blue'],
                  ['最高收盘', fmt(data.stats.high, digits), ''],
                  ['最低收盘', fmt(data.stats.low, digits), ''],
                ].map(([label, value, color]) => (
                  <div className="stat-row" key={label}>
                    <span>{label}</span>
                    <b className={`num ${color}`}>{value}</b>
                  </div>
                ))}
              </div>
              <div className="hist">
                <div className="panel-title">
                  <h2>历史频率分布</h2>
                  <small>纵轴：样本数</small>
                </div>
                <Chart option={hist} label="价差收盘值频率分布直方图" />
              </div>
            </>
          ) : (
            <div className="annotation">数据加载后显示统计结果。</div>
          )}
        </aside>
      </div>
      <section className="panel season">
        <div className="panel-title">
          <h2>季节性 / 历史同期</h2>
          <span className="pill badge-warn">
            {!data
              ? '加载季节性口径'
              : data.source === 'tqsdk' || data.source === 'simnow_tqsdk'
                ? '固定合约 · 不拼接模拟历史'
                : '模拟季节路径'}
          </span>
        </div>
        {data?.seasonality.available ? (
          <Chart
            option={seasonal}
            label="按年份叠加的季节性曲线"
          />
        ) : data ? (
          <div className="empty">{data.seasonality.note}</div>
        ) : (
          <div className="empty loading">加载季节性曲线…</div>
        )}
        <p className="chart-note">
          {data?.seasonality.note || '加载季节性说明。'}
        </p>
      </section>
      <p className="footnote">
        <Info size={14} />
        标准差采用总体口径；分位对平值计半权重。比价分母为零或标准差为零时，相应结果显示“—”。
      </p>
    </>
  );
}
