'use client';
import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { LineChart, BarChart, CandlestickChart } from 'echarts/charts';
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  MarkLineComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsOption } from 'echarts';
echarts.use([
  LineChart,
  BarChart,
  CandlestickChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  MarkLineComponent,
  CanvasRenderer,
]);
export function Chart({
  option,
  className = 'chart',
  label,
}: {
  option: EChartsOption;
  className?: string;
  label: string;
}) {
  const ref = useRef<HTMLElement>(null);
  const instance = useRef<ReturnType<typeof echarts.init> | null>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, undefined, { renderer: 'canvas' });
    instance.current = chart;
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      chart.dispose();
      instance.current = null;
    };
  }, []);
  useEffect(() => {
    const chart = instance.current;
    if (!chart) return;
    const previous = (chart.getOption() ?? {}) as {
      legend?: { selected?: Record<string, boolean> }[];
      dataZoom?: { start?: number; end?: number }[];
    };
    const updated = { ...option };
    if (
      option.legend &&
      previous.legend?.[0]?.selected &&
      !Array.isArray(option.legend)
    ) {
      updated.legend = {
        ...option.legend,
        selected: { ...option.legend.selected, ...previous.legend[0].selected },
      };
    }
    if (Array.isArray(option.dataZoom) && previous.dataZoom?.length) {
      updated.dataZoom = option.dataZoom.map((zoom, i) => ({
        ...zoom,
        start: previous.dataZoom?.[i]?.start ?? zoom.start,
        end: previous.dataZoom?.[i]?.end ?? zoom.end,
      }));
    }
    chart.setOption(updated, { notMerge: true });
  }, [option]);
  return <figure ref={ref} className={className} aria-label={label} />;
}
export const baseChart = {
  animation: false,
  textStyle: {
    fontFamily: 'Inter, PingFang SC, sans-serif',
    color: '#8d9fb9',
    fontSize: 12,
  },
  backgroundColor: 'transparent',
  tooltip: {
    trigger: 'axis',
    backgroundColor: '#182437',
    borderColor: '#3a4d66',
    textStyle: { color: '#e0e9f5', fontSize: 12 },
  },
  grid: { left: 60, right: 22, top: 38, bottom: 44 },
  xAxis: {
    type: 'category',
    axisLine: { lineStyle: { color: '#2b3c51' } },
    axisTick: { show: false },
    axisLabel: { color: '#748aa9', fontSize: 11 },
  },
  yAxis: {
    type: 'value',
    scale: true,
    splitLine: { lineStyle: { color: '#233044', type: 'dashed' } },
    axisLabel: { color: '#748aa9', fontSize: 11 },
  },
  legend: {
    textStyle: { color: '#92a9c8', fontSize: 11 },
    top: 8,
    itemWidth: 12,
    itemHeight: 6,
  },
} satisfies EChartsOption;
export function Spark({ values, color }: { values: number[]; color: string }) {
  return (
    <Chart
      label="价差走势"
      className="spark"
      option={{
        animation: false,
        grid: { left: 0, right: 0, top: 4, bottom: 4 },
        xAxis: { type: 'category', show: false, data: values.map((_, i) => i) },
        yAxis: { type: 'value', show: false, scale: true },
        series: [
          {
            type: 'line',
            data: values,
            showSymbol: false,
            smooth: 0.22,
            lineStyle: { width: 1.6, color },
            areaStyle: { color, opacity: 0.05 },
          },
        ],
      }}
    />
  );
}
