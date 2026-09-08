import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = {
  title: '期货套利监控 | ARB DESK',
  description:
    '独立的期货价差监控、统计分析与本地告警工作台，支持 SimNow CTP 实时行情与 TqSdk 历史行情。',
};
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN" className="dark">
      <body>{children}</body>
    </html>
  );
}
