'use client';
import { useId } from 'react';
import type { Combo, Contract } from '@/lib/types';
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/ui/select';
import { Input } from '@/components/ui/input';
export function Pick({
  value,
  onChange,
  items,
  label,
}: {
  value: string;
  onChange: (value: string) => void;
  items: { value: string; label: string }[];
  label: string;
}) {
  return (
    <Select
      value={value}
      onValueChange={(v) => v !== null && onChange(String(v))}
    >
      <SelectTrigger className="pick" aria-label={label}>
        <SelectValue>
          {items.find((x) => x.value === value)?.label || label}
        </SelectValue>
      </SelectTrigger>
      <SelectContent>
        {items.map((x) => (
          <SelectItem key={x.value} value={x.value}>
            {x.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
export function ComboFields({
  value,
  onChange,
  contracts,
  names = false,
}: {
  value: Combo;
  onChange: (c: Combo) => void;
  contracts: Contract[];
  names?: boolean;
}) {
  const id = useId();
  const set = (k: keyof Combo, v: string | number) =>
    onChange({ ...value, [k]: v });
  const items = contracts.map((c) => ({
    value: c.symbol,
    label: `${c.symbol} · ${c.name}`,
  }));
  return (
    <>
      {names && (
        <label className="field wide" htmlFor={`${id}-name`}>
          <span>组合名称</span>
          <Input
            id={`${id}-name`}
            className="input"
            required
            maxLength={60}
            value={value.name}
            onChange={(e) => set('name', e.target.value)}
            placeholder="例如：焦煤 01–05"
            aria-label="组合名称"
          />
        </label>
      )}
      <div className="field">
        <span>计算方式</span>
        <Pick
          label="计算方式"
          value={value.mode}
          onChange={(v) =>
            onChange({
              ...value,
              mode: v as Combo['mode'],
              coefficient_a: 1,
              coefficient_b: 1,
            })
          }
          items={[
            { value: 'spread', label: '价差 A − B' },
            { value: 'ratio', label: '比价 A / B' },
            { value: 'weighted', label: '自定义系数' },
          ]}
        />
      </div>
      <div className="field">
        <span>第一腿 A</span>
        <Pick
          value={value.leg_a}
          onChange={(v) => set('leg_a', v)}
          items={items}
          label="第一腿 A"
        />
      </div>
      {value.mode === 'weighted' && (
        <label className="field" htmlFor={`${id}-a`}>
          <span>A 系数</span>
          <Input
            id={`${id}-a`}
            className="input coefficient"
            aria-label="A 系数"
            type="number"
            step="any"
            min="0.000001"
            max="100"
            required
            value={value.coefficient_a}
            onChange={(e) => set('coefficient_a', Number(e.target.value))}
          />
        </label>
      )}
      <div className="field">
        <span>第二腿 B</span>
        <Pick
          value={value.leg_b}
          onChange={(v) => set('leg_b', v)}
          items={items}
          label="第二腿 B"
        />
      </div>
      {value.mode === 'weighted' && (
        <label className="field" htmlFor={`${id}-b`}>
          <span>B 系数</span>
          <Input
            id={`${id}-b`}
            className="input coefficient"
            aria-label="B 系数"
            type="number"
            step="any"
            min="0.000001"
            max="100"
            required
            value={value.coefficient_b}
            onChange={(e) => set('coefficient_b', Number(e.target.value))}
          />
        </label>
      )}
    </>
  );
}
