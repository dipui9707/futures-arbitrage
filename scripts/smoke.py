#!/usr/bin/env python3
"""Live acceptance uses only its own temporary configuration and cleans it up."""
import json
import sys
import time
from pathlib import Path
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parent))
from manage import ROOT,RUNTIME,settings
STATE=RUNTIME/'smoke.json'
URL='http://127.0.0.1:'+settings()['FRONTEND_PORT']+'/api'
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))

def call(path,method='GET',body=None):
    req=urllib.request.Request(URL+path,data=json.dumps(body).encode() if body is not None else None,
                               headers={'Content-Type':'application/json'} if body is not None else {},method=method)
    with OPENER.open(req,timeout=30) as response:
        payload=response.read()
        return json.loads(payload) if payload else None

def prepare():
    if STATE.exists():raise SystemExit('Pending smoke state exists. Run finish first.')
    source=call('/health')['data_source'];assert source in ('mock','tqsdk')
    market=call('/market');assert len(market['combinations'])>=6
    pair=dict(name='验收临时组合（自动移除）',leg_a='JM2701',leg_b='J2701',mode='weighted',coefficient_a=1,coefficient_b=.55,favorite=True)
    created=call('/combinations','POST',pair);identifier=created['id']
    STATE.write_text(json.dumps({'combination_id':identifier}))
    pair.update(name='验收临时比价（自动移除）',mode='ratio',coefficient_a=1,coefficient_b=1)
    call('/combinations/'+identifier,'PUT',pair)
    alert=call('/alerts','POST',dict(combination_id=identifier,metric='percentile',operator='lte',threshold=5,enabled=False))
    STATE.write_text(json.dumps({'combination_id':identifier,'alert_id':alert['id']}))
    for period in ('1m','5m','15m','30m','60m','2h','4h','1d'):
        data=call('/analysis','POST',dict(combination=pair,period=period,count=150))
        assert len(data['bars'])==150
        assert data['stats']['sample_count'] in (149,150)
        assert sum(b['count'] for b in data['histogram'])==data['stats']['sample_count']
    for product in ('RB','JM','SM'):
        matrix=call('/term?product='+product)['matrix']
        assert all(matrix[i][i]==0 for i in range(len(matrix)))
    print(f'Live {source} health, market, eight periods, three term/matrix products and temporary CRUD passed. Restart the service, then run finish.')

def finish():
    if not STATE.exists():raise SystemExit('No pending smoke state.')
    state=json.loads(STATE.read_text());identifier=state['combination_id']
    rows=call('/combinations')
    c=next(c for c in rows if c['id']==identifier)
    assert c['name']=='验收临时比价（自动移除）' and c['mode']=='ratio' and c['favorite']
    a=next(a for a in call('/alerts') if a['id']==state['alert_id'])
    assert a['combination_id']==identifier and not a['enabled'] and a['threshold']==5
    call('/combinations/'+identifier,'DELETE')
    assert not any(c['id']==identifier for c in call('/combinations'))
    assert not any(a['id']==state['alert_id'] for a in call('/alerts'))
    STATE.unlink()
    print('Persistence after service restart passed. Temporary combination and its alert removed; user configurations preserved.')

if __name__=='__main__':
    {'prepare':prepare,'finish':finish}[sys.argv[1]]()
