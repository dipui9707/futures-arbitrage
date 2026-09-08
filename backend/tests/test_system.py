import math
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

PROJECT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(PROJECT/'backend'))
TEST_DIR=tempfile.TemporaryDirectory()
os.environ['DATABASE_PATH']=str(Path(TEST_DIR.name)/'api.sqlite3')
os.environ['DATA_SOURCE']='mock'
from fastapi.testclient import TestClient
from app.models import Combination
from app.data_adapter.base import spread_value,aggregate_samples
from app.data_adapter.mock import MockAdapter,ANCHOR,PERIODS
from app.analytics import stats,percentile,histogram,moving_average,state
from app.storage import Store
from app.main import app

class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.combo=Combination(name='test',leg_a='RB2610',leg_b='RB2611')
    def test_synchronized_ohlc_not_leg_extrema(self):
        a,b=[100,108,102,104],[90,99,88,96]
        result=aggregate_samples([spread_value(self.combo,x,y) for x,y in zip(a,b)])
        self.assertEqual(result,dict(open=10,high=14,low=8,close=8))
        self.assertNotEqual(result['high'],max(a)-max(b))
    def test_weighted_and_ratio(self):
        self.combo.mode='weighted';self.combo.coefficient_a=2
        self.assertEqual(spread_value(self.combo,104,96),112)
        self.combo.mode='ratio'
        self.assertAlmostEqual(spread_value(self.combo,102,88),102/88)
        with self.assertRaises(ValueError):spread_value(self.combo,5,0)
    def test_stats_ties_and_zero_variance(self):
        s=stats([1,2,3,4],3)
        self.assertEqual(s['mean'],2.5);self.assertEqual(s['median'],2.5)
        self.assertAlmostEqual(s['std'],math.sqrt(1.25));self.assertEqual(s['percentile'],62.5)
        self.assertAlmostEqual(s['zscore'],.4472135955)
        s=stats([10]*20,10)
        self.assertEqual(s['percentile'],50);self.assertIsNone(s['zscore'])
        self.assertIsNone(stats([0,1],2)['change_pct'])
    def test_histogram_and_ma(self):
        for values in ([10]*20,list(range(100))):
            self.assertEqual(sum(b['count'] for b in histogram(values)),len(values))
        self.assertEqual(moving_average([1,2,3,4],3),[None,None,2,3])
        self.assertEqual([state(p) for p in [0,5,6,25,75,94,95,100]],['极端低','极端低','偏低','中性','中性','偏高','极端高','极端高'])
    def test_all_periods_and_deterministic_prices(self):
        adapter=MockAdapter();t=ANCHOR+14*3600+10
        self.assertEqual(adapter.prices(['RB2610'],t),adapter.prices(['RB2610'],t))
        for period in PERIODS:
            rows=adapter.bars(self.combo,period,150,t)
            self.assertEqual(len(rows),150)
            for b in rows:
                self.assertLessEqual(b['low'],min(b['open'],b['close']))
                self.assertGreaterEqual(b['high'],max(b['open'],b['close']))
                self.assertLessEqual(b['timestamp'],t)
            self.assertTrue(rows[-1]['incomplete'])
    def test_cross_period_aggregation(self):
        adapter=MockAdapter();t=ANCHOR+14*3600+300
        for mode in ('spread','weighted','ratio'):
            self.combo.mode=mode;self.combo.coefficient_a=2 if mode=='weighted' else 1
            one=adapter.bars(self.combo,'1m',12,t)
            five=adapter.bars(self.combo,'5m',4,t)[-2]
            inside=[r for r in one if five['timestamp']<=r['timestamp']<five['timestamp']+300]
            self.assertEqual(len(inside),5)
            self.assertAlmostEqual(five['open'],inside[0]['open'])
            self.assertAlmostEqual(five['close'],inside[-1]['close'])
            self.assertAlmostEqual(five['high'],max(b['high'] for b in inside))
            self.assertAlmostEqual(five['low'],min(b['low'] for b in inside))
    def test_seasonality_average_excludes_current_year(self):
        data=MockAdapter().seasonality(self.combo,ANCHOR)
        self.assertEqual(len(data['years']),6)
        self.assertEqual(data['average'][0],sum(y['values'][0] for y in data['years'][:5])/5)
        self.assertIsNone(data['years'][-1]['values'][-1])

class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client=TestClient(app)
        cls.clock=patch('app.main.now',return_value=ANCHOR+14*3600+10);cls.clock.start()
    @classmethod
    def tearDownClass(cls):cls.clock.stop()
    def test_seed_and_read_only_routes(self):
        rows=self.client.get('/api/combinations').json()
        expected={('RB2610','RB2611'),('RB2610','RB2701'),('JM2610','JM2611'),('JM2611','JM2701'),('JM2701','JM2705'),('SM2611','SM2701')}
        self.assertTrue(expected.issubset({(c['leg_a'],c['leg_b']) for c in rows}))
        health=self.client.get('/api/health').json()
        self.assertEqual(health['data_source'],'mock');self.assertTrue(health['read_only_market'])
        paths=self.client.get('/openapi.json').json()['paths']
        self.assertFalse(any(s in p.split('/') for p in paths for s in ('orders','accounts','trades','positions','fills')))
    def test_crud_persistence_order_cascade(self):
        c=dict(name='temporary test',leg_a='JM2701',leg_b='J2701',mode='weighted',coefficient_a=1,coefficient_b=.55,favorite=True)
        created=self.client.post('/api/combinations',json=c)
        self.assertEqual(created.status_code,201);identifier=created.json()['id']
        c['name']='edited test';self.assertEqual(self.client.put(f'/api/combinations/{identifier}',json=c).status_code,200)
        a=dict(combination_id=identifier,metric='percentile',operator='lte',threshold=5,enabled=True)
        result=self.client.post('/api/alerts',json=a);self.assertEqual(result.status_code,201)
        alert_id=result.json()['id'];a['enabled']=False
        self.assertEqual(self.client.put(f'/api/alerts/{alert_id}',json=a).status_code,200)
        ids=[x['id'] for x in self.client.get('/api/combinations').json()][::-1]
        self.assertEqual(self.client.put('/api/combinations/reorder',json={'ids':ids}).status_code,200)
        reopened=Store(os.environ['DATABASE_PATH'])
        self.assertEqual([x['id'] for x in reopened.combinations()],ids)
        self.assertEqual(reopened.combinations()[0]['name'],'edited test')
        self.assertFalse(next(x for x in reopened.alerts() if x['id']==alert_id)['enabled'])
        self.assertEqual(self.client.put('/api/combinations/reorder',json={'ids':ids[:-1]}).status_code,409)
        self.assertEqual(self.client.delete(f'/api/combinations/{identifier}').status_code,204)
        self.assertFalse(any(x['id']==alert_id for x in self.client.get('/api/alerts').json()))
    def test_validation(self):
        c=dict(name='test',leg_a='RB9999',leg_b='RB2611')
        self.assertEqual(self.client.post('/api/combinations',json=c).status_code,422)
        c.update(leg_a='RB2610',coefficient_a=0)
        self.assertEqual(self.client.post('/api/combinations',json=c).status_code,422)
        self.assertEqual(self.client.post('/api/alerts',json=dict(combination_id='combo-1',metric='percentile',operator='gte',threshold=101)).status_code,422)
        self.assertEqual(self.client.post('/api/alerts',json=dict(combination_id='missing',metric='price',operator='gte',threshold=1)).status_code,404)
    def test_analysis_and_market_contracts(self):
        c=dict(name='test',leg_a='RB2610',leg_b='RB2611')
        before=self.client.get('/api/market').json()
        for period in PERIODS:
            r=self.client.post('/api/analysis',json={'combination':c,'period':period,'count':150})
            self.assertEqual(r.status_code,200)
            data=r.json();n=data['stats']['sample_count']
            self.assertEqual(n,149)
            self.assertEqual(sum(x['count'] for x in data['histogram']),n)
            self.assertEqual(set(data['ma']),{'5','10','20','60','120'})
        after=self.client.get('/api/market').json()
        self.assertEqual(before,after)
        self.assertTrue(all(q['samples60']==60 and q['samples250']==250 for q in after['combinations']))
    def test_term_matrix_is_antisymmetric(self):
        for product in ('RB','JM','SM','J','I'):
            data=self.client.get('/api/term',params={'product':product}).json()
            for i,row in enumerate(data['matrix']):
                self.assertEqual(row[i],0)
                for j,v in enumerate(row):self.assertAlmostEqual(v,-data['matrix'][j][i])
    def test_deleted_seed_does_not_reappear(self):
        path=Path(TEST_DIR.name)/'empty.sqlite3';store=Store(path)
        with store.connection() as db:db.execute('DELETE FROM combinations')
        self.assertEqual(Store(path).combinations(),[])
    def test_tqsdk_read_only_adapter_contracts_quotes_and_bars(self):
        from app.data_adapter.tqsdk import TqSdkAdapter

        class FakeSerial:
            def __init__(self,rows):self.rows=rows
            def to_dict(self,orient):
                if orient!='records':raise AssertionError(orient)
                return self.rows

        class FakeApi:
            def __init__(self):self.closed=False
            def query_quotes(self,**kwargs):
                product=kwargs['product_id']
                return {
                    'rb':['SHFE.rb2610','SHFE.rb2611'],
                    'jm':['DCE.jm2610','DCE.jm2611'],
                    'SM':['CZCE.SM611','CZCE.SM701'],
                    'j':['DCE.j2610','DCE.j2701'],
                    'i':['DCE.i2610','DCE.i2701'],
                }[product]
            def get_quote(self,symbol):
                second='2611' in symbol
                return SimpleNamespace(
                    last_price=102 if second else 110,
                    pre_close=100 if second else 107,
                    datetime='2026-09-07 14:59:59.500001',
                )
            def get_kline_serial(self,symbols,duration,data_length):
                end=int(datetime(2026,9,7,15,20,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp())
                if duration==86400:
                    end=int(datetime(2026,9,7,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp())
                else:end=end//duration*duration
                rows=[]
                for i in range(data_length):
                    start=end-(data_length-1-i)*duration
                    a=100+i*.1;b=94+i*.05
                    rows.append(dict(
                        datetime=start*1_000_000_000,duration=duration*1_000_000_000,
                        open=a,high=a+1,low=a-1,close=a+.2,
                        open1=b,high1=b+1,low1=b-1,close1=b+.1,
                    ))
                return FakeSerial(rows)
            def is_serial_ready(self,_serial):return True
            def wait_update(self,deadline=None):return True
            def close(self):self.closed=True

        fake=FakeApi()
        adapter=TqSdkAdapter(api_factory=lambda:fake,timeout_seconds=.1)
        combo=Combination(name='test',leg_a='RB2610',leg_b='RB2611')
        try:
            contracts=adapter.contracts()
            self.assertIn('RB2610',{c['symbol'] for c in contracts})
            self.assertIn('SM2611',{c['symbol'] for c in contracts})
            timestamp=datetime(2026,9,7,15,20,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()
            self.assertEqual(adapter.prices(['RB2610','RB2611'],timestamp),
                             {'RB2610':110.0,'RB2611':102.0})
            history=adapter.quote_history(combo,3,timestamp)
            self.assertEqual(history['previous_prices'],{'RB2610':107.0,'RB2611':100.0})
            self.assertEqual(len(history['closes']),3)
            bars=adapter.bars(combo,'5m',3,timestamp)
            self.assertEqual(len(bars),3)
            self.assertTrue(all(row['low']<=row['close']<=row['high'] for row in bars))
            self.assertEqual(adapter.observed_at(),'2026-09-07T14:59:59.500001+08:00')
        finally:
            adapter.close()
        self.assertTrue(fake.closed)

if __name__=='__main__':unittest.main()
