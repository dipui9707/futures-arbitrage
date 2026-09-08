import json
import sqlite3
from pathlib import Path
from contextlib import contextmanager

SEEDS=[('RB2610','RB2611','螺纹 10–11'),('RB2610','RB2701','螺纹 10–01'),
       ('JM2610','JM2611','焦煤 10–11'),('JM2611','JM2701','焦煤 11–01'),
       ('JM2701','JM2705','焦煤 01–05'),('SM2611','SM2701','硅锰 11–01')]

class Store:
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS combinations (id TEXT PRIMARY KEY, position INTEGER NOT NULL, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS alerts (id TEXT PRIMARY KEY, combination_id TEXT NOT NULL REFERENCES combinations(id) ON DELETE CASCADE, payload TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS idx_alert_combination ON alerts(combination_id)')
            db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            if not db.execute("SELECT 1 FROM metadata WHERE key='seeded'").fetchone():
                for i,(a,b,name) in enumerate(SEEDS):
                    payload=dict(name=name,leg_a=a,leg_b=b,mode='spread',coefficient_a=1,coefficient_b=1,favorite=i in (0,3))
                    db.execute('INSERT INTO combinations VALUES (?,?,?)',(f'combo-{i+1}',i,json.dumps(payload)))
                db.execute("INSERT INTO metadata VALUES ('seeded','1')")
    @contextmanager
    def connection(self):
        db=sqlite3.connect(self.path,timeout=10)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()
    def combinations(self):
        with self.connection() as db:
            return [dict(id=r['id'],position=r['position'],**json.loads(r['payload'])) for r in db.execute('SELECT * FROM combinations ORDER BY position,id')]
    def alerts(self):
        with self.connection() as db:
            return [dict(id=r['id'],**json.loads(r['payload'])) for r in db.execute('SELECT * FROM alerts ORDER BY rowid')]
