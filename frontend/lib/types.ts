export type Mode = 'spread' | 'ratio' | 'weighted';
export type Combo = {
  id?: string;
  position?: number;
  name: string;
  leg_a: string;
  leg_b: string;
  mode: Mode;
  coefficient_a: number;
  coefficient_b: number;
  favorite: boolean;
};
export type Quote = Combo & {
  id: string;
  current: number;
  change: number;
  leg_a_price: number;
  leg_b_price: number;
  leg_a_change: number;
  leg_b_change: number;
  percentile60: number;
  percentile250: number;
  zscore: number | null;
  status: string;
  sparkline: number[];
  samples60: number;
  samples250: number;
};
export type Contract = {
  symbol: string;
  name: string;
  exchange: string;
  product: string;
};
export type Market = {
  source: string;
  timestamp: string;
  refreshed_at?: string;
  combinations: Quote[];
};
export type RealtimeSnapshot = {
  timestamp: string;
  quotes: Record<string, { price: number; observed_at: string }>;
};
export type Bar = {
  time: string;
  timestamp: number;
  open: number;
  close: number;
  low: number;
  high: number;
  incomplete: boolean;
};
export type Stats = {
  current: number;
  change: number;
  change_pct: number | null;
  mean: number;
  median: number;
  std: number;
  zscore: number | null;
  percentile: number;
  high: number;
  low: number;
  sample_count: number;
};
export type Analysis = {
  source: string;
  timestamp: string;
  period: string;
  bars: Bar[];
  stats: Stats;
  ma: Record<string, (number | null)[]>;
  histogram: { low: number; high: number; count: number }[];
  seasonality: {
    available: boolean;
    dates: string[];
    years: { year: number; values: (number | null)[] }[];
    average: (number | null)[];
    average_label: string;
    note: string;
  };
  window_start: string;
  window_end: string;
  methodology: string;
};
export type AlertRule = {
  id?: string;
  combination_id: string;
  metric: 'price' | 'percentile' | 'zscore';
  operator: 'gte' | 'lte';
  threshold: number;
  enabled: boolean;
};
export type Term = {
  source: string;
  contracts: (Contract & { price: number })[];
  matrix: number[][];
};
export const blankCombo: Combo = {
  name: '',
  leg_a: 'RB2610',
  leg_b: 'RB2611',
  mode: 'spread',
  coefficient_a: 1,
  coefficient_b: 1,
  favorite: false,
};
export const bodyCombo = (c: Combo): Combo => ({
  name: c.name,
  leg_a: c.leg_a,
  leg_b: c.leg_b,
  mode: c.mode,
  coefficient_a: c.coefficient_a,
  coefficient_b: c.coefficient_b,
  favorite: c.favorite,
});
export const formula = (c: Combo) =>
  c.mode === 'weighted'
    ? `${c.coefficient_a} × ${c.leg_a} − ${c.coefficient_b} × ${c.leg_b}`
    : `${c.leg_a} ${c.mode === 'ratio' ? '÷' : '−'} ${c.leg_b}`;
export const fmt = (x: number | null | undefined, digits = 2) =>
  x == null || !Number.isFinite(x)
    ? '—'
    : x.toLocaleString('en-US', {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });
export const signed = (x: number, digits = 2) =>
  (x > 0 ? '+' : '') + fmt(x, digits);
export const tone = (x: number) => (x > 0 ? 'up' : x < 0 ? 'down' : 'muted');
export async function api<T>(
  path: string,
  method = 'GET',
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const options: RequestInit = {
    method,
    signal: signal
      ? AbortSignal.any([signal, AbortSignal.timeout(20000)])
      : AbortSignal.timeout(20000),
  };
  if (body !== undefined && method !== 'GET' && method !== 'HEAD') {
    options.headers = { 'Content-Type': 'application/json' };
    options.body = JSON.stringify(body);
  }
  const response = await fetch(`/api${path}`, options);
  if (!response.ok) {
    let message = `服务请求失败 (${response.status})`;
    try {
      const data = (await response.json()) as {
        detail?: string | { msg: string }[];
      };
      message =
        typeof data.detail === 'string'
          ? data.detail
          : Array.isArray(data.detail)
            ? data.detail.map((e: { msg: string }) => e.msg).join('；')
            : message;
    } catch {}
    throw new Error(message);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
