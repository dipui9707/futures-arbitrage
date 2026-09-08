import os
import time
import json
import uuid
import asyncio
from pathlib import Path
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from .models import Combination, AnalysisRequest, Reorder, Alert
from .storage import Store
from .data_adapter.mock import MockAdapter, TZ
from .data_adapter.base import MarketDataUnavailable, spread_value
from .analytics import stats, percentile, histogram, moving_average, state

ROOT=Path(__file__).resolve().parents[1]
PROJECT=ROOT.parent
source=os.getenv('DATA_SOURCE','mock').lower()
if source == 'mock':
    adapter=MockAdapter()
elif source == 'tqsdk':
    from .data_adapter.tqsdk import TqSdkAdapter
    adapter=TqSdkAdapter()
elif source == 'simnow_tqsdk':
    from .data_adapter.simnow_tqsdk import SimNowTqSdkAdapter
    adapter=SimNowTqSdkAdapter(database=PROJECT/Path(os.getenv(
        'CTP_MINUTE_DATABASE','data/ctp_minute_bars.sqlite3'
    )))
else:
    raise RuntimeError('DATA_SOURCE 必须为 mock、tqsdk 或 simnow_tqsdk')
database=Path(os.getenv('DATABASE_PATH','data/arbitrage.sqlite3'))
store=Store(database if database.is_absolute() else PROJECT/database)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    adapter.close()

app=FastAPI(title='期货套利监控 · 只读行情',version='0.1.0',lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=[os.getenv(
    'FRONTEND_ORIGIN',f"http://127.0.0.1:{os.getenv('FRONTEND_PORT','5173')}"
)],
                   allow_methods=['GET','POST','PUT','DELETE'],allow_headers=['Content-Type'])

@app.exception_handler(MarketDataUnavailable)
async def market_data_unavailable(_request, exc: MarketDataUnavailable):
    return JSONResponse(status_code=503,content={'detail':str(exc)})

def now():
    return int(time.time()/5)*5

def validate_combo(c):
    known={x['symbol'] for x in adapter.contracts()}
    if c.leg_a not in known or c.leg_b not in known:
        raise HTTPException(422,'所选合约不在当前行情合约库中')
    return c

def read_combo(row):
    return Combination(**{k:v for k,v in row.items() if k not in ('id','position')})

def exists(identifier):
    if identifier not in {c['id'] for c in store.combinations()}:
        raise HTTPException(404,'组合不存在或已删除')

def quote(c,timestamp):
    history=adapter.quote_history(c,251,timestamp)
    prices=history['current_prices']
    previous=history['previous_prices']
    current=spread_value(c,prices[c.leg_a],prices[c.leg_b])
    previous_value=spread_value(c,previous[c.leg_a],previous[c.leg_b])
    closes=history['closes']
    summary=stats(closes[-250:],current)
    p60=percentile(closes[-60:],current)
    return dict(current=current,change=current-previous_value,
                leg_a_price=prices[c.leg_a],leg_b_price=prices[c.leg_b],
                leg_a_change=prices[c.leg_a]-previous[c.leg_a],leg_b_change=prices[c.leg_b]-previous[c.leg_b],
                percentile60=p60,percentile250=percentile(closes[-250:],current),
                zscore=summary['zscore'],status=state(p60),sparkline=closes[-42:]+[current],
                samples60=len(closes[-60:]),samples250=len(closes[-250:]))

@app.get('/api/health')
def health():
    return dict(status='ok',data_source=source,read_only_market=True,version='0.1.0',
                realtime_source=getattr(adapter,'realtime_source',source),
                history_source=getattr(adapter,'history_source',source),
                timestamp=datetime.fromtimestamp(now(),TZ).isoformat())

@app.get('/api/contracts')
def contracts():
    return adapter.contracts()

@app.get('/api/combinations')
def combinations():
    return store.combinations()

@app.post('/api/combinations',status_code=201)
def create_combination(c:Combination):
    validate_combo(c)
    identifier=str(uuid.uuid4())
    with store.connection() as db:
        position=db.execute('SELECT COALESCE(MAX(position),-1)+1 FROM combinations').fetchone()[0]
        db.execute('INSERT INTO combinations VALUES (?,?,?)',(identifier,position,c.model_dump_json()))
    return dict(id=identifier,position=position,**c.model_dump())

@app.put('/api/combinations/reorder')
def reorder(order:Reorder):
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        ids={r[0] for r in db.execute('SELECT id FROM combinations')}
        if len(order.ids)!=len(ids) or set(order.ids)!=ids:
            raise HTTPException(409,'组合列表已变化，请刷新后重试')
        for i,identifier in enumerate(order.ids):
            db.execute('UPDATE combinations SET position=? WHERE id=?',(i,identifier))
    return store.combinations()

@app.put('/api/combinations/{identifier}')
def update_combination(identifier:str,c:Combination):
    validate_combo(c)
    with store.connection() as db:
        cursor=db.execute('UPDATE combinations SET payload=? WHERE id=?',(c.model_dump_json(),identifier))
        if cursor.rowcount==0:
            raise HTTPException(404,'组合不存在')
    return dict(id=identifier,**c.model_dump())

@app.delete('/api/combinations/{identifier}',status_code=204)
def delete_combination(identifier:str):
    with store.connection() as db:
        cursor=db.execute('DELETE FROM combinations WHERE id=?',(identifier,))
        if cursor.rowcount==0:
            raise HTTPException(404,'组合不存在')

@app.get('/api/market')
def market():
    timestamp=now()
    rows=[dict(**row,**quote(read_combo(row),timestamp)) for row in store.combinations()]
    observed=adapter.observed_at()
    return dict(source=source,timestamp=observed or datetime.fromtimestamp(timestamp,TZ).isoformat(),
                refreshed_at=datetime.fromtimestamp(timestamp,TZ).isoformat(),combinations=rows)

@app.get('/api/realtime/stream')
async def realtime_stream(request:Request,symbols:str=Query('',max_length=2000)):
    if source != 'simnow_tqsdk' or not hasattr(adapter,'snapshot'):
        raise HTTPException(409,'当前数据源不支持 SimNow Tick 推送')
    requested=list(dict.fromkeys(
        symbol.strip().upper() for symbol in symbols.split(',') if symbol.strip()
    ))
    if not requested:
        requested=list(dict.fromkeys(
            symbol
            for row in store.combinations()
            for symbol in (row['leg_a'],row['leg_b'])
        ))
    known={row['symbol'] for row in adapter.contracts()}
    if not requested or len(requested)>100 or any(symbol not in known for symbol in requested):
        raise HTTPException(422,'Tick 推送合约参数无效')

    async def events():
        fingerprint=None
        last_keepalive=time.monotonic()
        last_error=''
        while not await request.is_disconnected():
            try:
                snapshot=adapter.snapshot(requested,time.time())
                current=tuple(
                    (symbol,quote['price'],quote['observed_at'])
                    for symbol,quote in snapshot['quotes'].items()
                )
                if current != fingerprint:
                    fingerprint=current
                    last_error=''
                    payload=json.dumps(snapshot,ensure_ascii=False,separators=(',',':'))
                    yield f'event: quotes\ndata: {payload}\n\n'
                    last_keepalive=time.monotonic()
            except MarketDataUnavailable as exc:
                detail=str(exc)
                if detail != last_error:
                    last_error=detail
                    payload=json.dumps({'state':'stale','detail':detail},ensure_ascii=False)
                    yield f'event: status\ndata: {payload}\n\n'
            if time.monotonic()-last_keepalive>=15:
                yield ': keepalive\n\n'
                last_keepalive=time.monotonic()
            await asyncio.sleep(.1)

    return StreamingResponse(events(),media_type='text/event-stream',headers={
        'Cache-Control':'no-cache, no-transform',
        'X-Accel-Buffering':'no',
        'Connection':'keep-alive',
    })

@app.post('/api/analysis')
def analysis(request:AnalysisRequest):
    c=validate_combo(request.combination)
    timestamp=now()
    bars=adapter.bars(c,request.period,request.count,timestamp)
    values=[b['close'] for b in bars if not b['incomplete']]
    if not values:
        raise MarketDataUnavailable('当前组合没有已完成的同步 K 线')
    prices=adapter.prices([c.leg_a,c.leg_b],timestamp)
    current=spread_value(c,prices[c.leg_a],prices[c.leg_b])
    return dict(source=source,timestamp=datetime.fromtimestamp(timestamp,TZ).isoformat(),period=request.period,
                bars=bars,stats=stats(values,current),histogram=histogram(values),
                ma={str(w):moving_average([b['close'] for b in bars],w) for w in (5,10,20,60,120)},
                seasonality=adapter.seasonality(c,timestamp),
                window_start=bars[0]['time'],window_end=[b for b in bars if not b['incomplete']][-1]['time'],
                methodology=('TqSdk 多合约时间对齐并以同步子周期采样计算历史价差 OHLC；'
                             if source in {'tqsdk','simnow_tqsdk'} else '模拟同步路径计算价差 OHLC；')+
                            ('当前值使用 SimNow CTP 最新价；' if source=='simnow_tqsdk' else '')+
                            '统计使用已完成 K 线收盘值；总体标准差；平值按 0.5 权重计分位；末根可能未完成。')

@app.get('/api/term')
def term(product:str=Query('RB',pattern='^(RB|JM|SM|J|I)$')):
    timestamp=now()
    selected=[c for c in adapter.contracts() if c['product']==product]
    symbols=[c['symbol'] for c in selected]
    prices=adapter.prices(symbols,timestamp)
    rows=[dict(**c,price=prices[c['symbol']]) for c in selected]
    return dict(source=source,contracts=rows,matrix=[[prices[a]-prices[b] for b in symbols] for a in symbols])

@app.get('/api/alerts')
def alerts():
    return store.alerts()

@app.post('/api/alerts',status_code=201)
def create_alert(alert:Alert):
    exists(alert.combination_id)
    identifier=str(uuid.uuid4())
    with store.connection() as db:
        db.execute('INSERT INTO alerts VALUES (?,?,?)',(identifier,alert.combination_id,alert.model_dump_json()))
    return dict(id=identifier,**alert.model_dump())

@app.put('/api/alerts/{identifier}')
def update_alert(identifier:str,alert:Alert):
    exists(alert.combination_id)
    with store.connection() as db:
        cursor=db.execute('UPDATE alerts SET combination_id=?,payload=? WHERE id=?',(alert.combination_id,alert.model_dump_json(),identifier))
        if cursor.rowcount==0:
            raise HTTPException(404,'告警不存在')
    return dict(id=identifier,**alert.model_dump())

@app.delete('/api/alerts/{identifier}',status_code=204)
def delete_alert(identifier:str):
    with store.connection() as db:
        cursor=db.execute('DELETE FROM alerts WHERE id=?',(identifier,))
        if cursor.rowcount==0:
            raise HTTPException(404,'告警不存在')
