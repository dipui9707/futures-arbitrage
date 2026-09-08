import math
import hashlib
from functools import lru_cache
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .base import spread_value, aggregate_samples

TZ = ZoneInfo('Asia/Shanghai')
PERIODS = {'1m':60,'5m':300,'15m':900,'30m':1800,'60m':3600,'2h':7200,'4h':14400,'1d':86400}
CATALOG = [
    ('RB2610','螺纹钢','SHFE',3092),('RB2611','螺纹钢','SHFE',3106),
    ('RB2701','螺纹钢','SHFE',3150),('RB2705','螺纹钢','SHFE',3206),
    ('JM2610','焦煤','DCE',1160),('JM2611','焦煤','DCE',1192),
    ('JM2701','焦煤','DCE',1256.5),('JM2705','焦煤','DCE',1173),
    ('SM2611','硅锰','CZCE',5820),('SM2701','硅锰','CZCE',5830),
    ('SM2705','硅锰','CZCE',5910),('J2701','焦炭','DCE',1798),
    ('J2705','焦炭','DCE',1822),('I2701','铁矿石','DCE',782),('I2705','铁矿石','DCE',754),
]
ANCHOR = datetime(2026,9,7,tzinfo=TZ).timestamp()

class MockAdapter:
    source = 'mock'
    def contracts(self):
        return [dict(symbol=s, name=n, exchange=e, product=''.join(x for x in s if x.isalpha())) for s,n,e,_ in CATALOG]
    @staticmethod
    @lru_cache(maxsize=150000)
    def knot(symbol, timestamp):
        t = (timestamp - ANCHOR) / 86400
        row = next((r for r in CATALOG if r[0] == symbol), None)
        if row is None:
            raise ValueError(f'模拟合约库暂不支持 {symbol}')
        seed = int(hashlib.sha256(symbol.encode()).hexdigest()[:8],16)
        phase = seed % 500 / 100
        product = ''.join(x for x in symbol if x.isalpha())
        scale = 8 if product == 'RB' else 14 if product in ('JM','J') else 20
        common = 20*math.sin(t/31)+10*math.sin(t/7)+1.6*math.sin(t*19)
        specific = scale*(math.sin(t/17+phase)-math.sin(phase)) + scale*.35*math.sin(t/3+phase)
        intraday = 1.2*math.sin(t*70+phase)+.4*math.sin(t*770+phase)
        return max(1,row[3]+common+specific+intraday)
    def prices(self, symbols, timestamp):
        # A single piecewise-linear path underlies every period. Ratio extrema
        # are also at segment endpoints because all denominators stay positive.
        knot = math.floor(timestamp/900)*900
        fraction = (timestamp-knot)/900
        return {s:self.knot(s,knot)*(1-fraction)+self.knot(s,knot+900)*fraction for s in symbols}
    def quote_history(self, combination, count, timestamp):
        prices=self.prices([combination.leg_a,combination.leg_b],timestamp)
        bars=self.bars(combination,'1d',count,timestamp)
        complete=[b for b in bars if not b['incomplete']]
        if not complete:
            raise ValueError('没有可用的完整模拟日线')
        previous=self.prices(
            [combination.leg_a,combination.leg_b],
            complete[-1]['timestamp']+86400,
        )
        return dict(
            current_prices=prices,
            previous_prices=previous,
            closes=[b['close'] for b in complete],
        )
    def bars(self, combination, period, count, timestamp):
        step = PERIODS[period]
        if period == '1d':
            end = datetime.fromtimestamp(timestamp,TZ).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
        else:
            end = math.floor(timestamp/step)*step
        starts=[]
        cursor=end
        while len(starts)<count:
            if period != '1d' or datetime.fromtimestamp(cursor,TZ).weekday()<5:
                starts.append(cursor)
            cursor-=step
        rows=[]
        for start in reversed(starts):
            stop=min(start+step,timestamp)
            samples=[]
            knots = list(range(int(start//900+1)*900, int(stop), 900))
            for point in [start, *knots, stop]:
                p=self.prices([combination.leg_a,combination.leg_b],point)
                samples.append(spread_value(combination,p[combination.leg_a],p[combination.leg_b]))
            rows.append(dict(time=datetime.fromtimestamp(start,TZ).isoformat(),timestamp=start,
                             incomplete=start+step>timestamp,**aggregate_samples(samples)))
        return rows
    def seasonality(self, combination, timestamp):
        year=datetime.fromtimestamp(timestamp,TZ).year
        dates=[(datetime(2025,1,1)+timedelta(days=i*7)).strftime('%m-%d') for i in range(52)]
        years=[]
        # Illustrative seasonal paths, not the historical trading record of these dated contracts.
        for y in range(year-5,year+1):
            values=[]
            for i in range(52):
                point=datetime(y,int(dates[i][:2]),int(dates[i][3:]),tzinfo=TZ).timestamp()
                p=self.prices([combination.leg_a,combination.leg_b],point)
                v=spread_value(combination,p[combination.leg_a],p[combination.leg_b])
                values.append(v if y<year or point<=timestamp else None)
            years.append(dict(year=y,values=values))
        average=[sum(y['values'][i] for y in years[:5])/5 for i in range(52)]
        return dict(available=True,dates=dates,years=years,average=average,
                    average_label='过去5年平均（模拟）',
                    note='模拟季节路径；非固定到期合约的真实五年历史。')
    def observed_at(self):
        return None
    def close(self):
        return None
