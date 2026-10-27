"""Disposable PG execution timing and sampled lock waits; no statements/parameters exported."""
import threading
from time import perf_counter
import psycopg
from sqlalchemy.engine import make_url


class ServerProfile:
    def __init__(self, admin, target):
        if make_url(admin).host not in ('127.0.0.1','localhost','::1') or not target.startswith('audli_claims_'):
            raise ValueError('Disposable loopback server required')
        self.admin,self.target=admin,target
        self.db=psycopg.connect(admin,autocommit=True)
        self.db.execute('CREATE EXTENSION IF NOT EXISTS pg_stat_statements')
        self.dbid=self.db.execute('SELECT oid FROM pg_database WHERE datname=%s',(target,)).fetchone()[0]

    def start(self):
        self.db.execute('SELECT pg_stat_statements_reset()')
        self.stop_event=threading.Event();self.samples=[]
        def sample():
            with psycopg.connect(self.admin,autocommit=True) as db:
                while not self.stop_event.is_set():
                    row=db.execute("SELECT count(*) FILTER(WHERE wait_event_type='Lock'), count(*) FILTER(WHERE state='active') FROM pg_stat_activity WHERE datname=%s",(self.target,)).fetchone()
                    self.samples.append(tuple(row))
                    self.stop_event.wait(.01)
        self.thread=threading.Thread(target=sample,daemon=True);self.thread.start()

    def finish(self):
        self.stop_event.set();self.thread.join(2)
        rows=self.db.execute('SELECT toplevel,sum(calls),sum(total_exec_time),sum(total_plan_time) FROM pg_stat_statements WHERE dbid=%s GROUP BY toplevel',(self.dbid,)).fetchall()
        return dict(execution=[dict(toplevel=r[0],calls=int(r[1]),exec_ms=round(r[2],3),plan_ms=round(r[3],3)) for r in rows],
                    lock_samples=len(self.samples),samples_with_lock_wait=sum(r[0]>0 for r in self.samples),
                    max_lock_waiters=max((r[0] for r in self.samples),default=0),
                    max_active=max((r[1] for r in self.samples),default=0))

    def close(self):self.db.close()
