import json
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from scripts import prototype_structure_book_clickhouse as storage
from scripts import swing_reader_upgrade as upgrade
from scripts import build_swing_book_campaign as campaign


def test_real_http_connections_are_reused_and_bounded(tmp_path):
    connections=set()
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def do_POST(self):
            connections.add(self.client_address)
            self.rfile.read(int(self.headers['Content-Length']))
            body=b'{"value":1}\n'
            self.send_response(200)
            self.send_header('Content-Length',str(len(body)))
            self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    env=tmp_path/'test.env';env.write_text(f'CLICKHOUSE_URL=http://127.0.0.1:{server.server_port}')
    client=storage.Client(env)
    try:
        for _ in range(30):assert client.query('SELECT 1','reuse')==[{'value':1}]
        assert len(connections)==1
        with ThreadPoolExecutor(max_workers=16) as pool:
            assert all(pool.map(lambda _:client.query('SELECT 1','parallel'),range(64)))
        assert len(connections)<=8
    finally:
        client.close();server.shutdown();server.server_close();thread.join()


def test_only_reads_retry_after_uncertain_disconnect(tmp_path,monkeypatch):
    env=tmp_path/'test.env';env.write_text('CLICKHOUSE_URL=http://127.0.0.1:1')
    client=storage.Client(env);calls=[];delays=[]
    def response(*args):
        calls.append(1)
        if len(calls)<3:raise ConnectionResetError('connection reset')
        return b'{"value":1}\n'
    monkeypatch.setattr(client,'_response',response)
    monkeypatch.setattr(storage.time,'sleep',delays.append)
    assert client.query('SELECT 1','read')==[{'value':1}]
    assert len(calls)==3 and len(delays)==2 and delays[1]>delays[0]
    calls.clear();delays.clear()
    with pytest.raises(ConnectionResetError):client.query('INSERT INTO test VALUES (1)','write',read=False)
    assert len(calls)==1 and not delays


def test_transport_migration_keeps_identity_but_rejects_algorithm_change():
    hashes={k:'new' for k in upgrade.TRANSPORT_BASELINE}
    hashes['src/market_engine/swing_book_v6.py']='unchanged'
    old=dict(hashes,**{k:v[0] for k,v in upgrade.TRANSPORT_BASELINE.items()})
    prior=upgrade.digest(old)
    assert upgrade.build_identity(hashes,{'code_hash':prior},indexed=True)==prior
    hashes['src/market_engine/swing_book_v6.py']='changed'
    with pytest.raises(ValueError):upgrade.build_identity(hashes,{'code_hash':prior},indexed=True)


def test_manifest_reader_retries_permission_only(tmp_path,monkeypatch):
    p=tmp_path/'manifest.json';p.write_text('{"ready":true}')
    original=Path.read_text;calls=[]
    def read(path,*args,**kwargs):
        calls.append(1)
        if len(calls)<3:raise PermissionError('sharing violation')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',read)
    monkeypatch.setattr(campaign.time,'sleep',lambda _:None)
    assert campaign.read_json(p)=={'ready':True}
    p.write_text('broken')
    with pytest.raises(json.JSONDecodeError):campaign.read_json(p)


def test_resume_applies_workers_and_stops_dispatch_after_transport_failures(tmp_path,monkeypatch):
    rows=[dict(ticker=f'T{i}',status='failed',reason='old failure',days=1,
        progress_file=str(tmp_path/'workers'/f'T{i}'/'progress.json'),report='unused') for i in range(10)]
    rows.append(dict(ticker='DONE',status='completed',database='keep',progress_file=str(tmp_path/'done'/'progress.json')))
    m=dict(schema_version=2,book_version='causal-swing-closing-book-6',workers=64,threads=2,
        code_hash=campaign.code_hash(),universe=[],universe_hash=campaign.P.digest([]),rows=rows)
    campaign.save(tmp_path,m);spawned=[]
    class Process:
        def __init__(self,command,**kwargs):
            self.pid=len(spawned)+1;spawned.append(self)
            ticker=command[command.index('--ticker')+1]
            campaign.P.save(tmp_path/'workers'/ticker/'progress.json',dict(stage='failed',error='[WinError 10048] socket exhausted'))
        def poll(self):return 1
        def wait(self):return 1
    monkeypatch.setattr(campaign.subprocess,'Popen',Process)
    monkeypatch.setattr(campaign.time,'sleep',lambda _:None)
    assert campaign.run(SimpleNamespace(runtime=tmp_path,workers=2,threads=1,retry_failed=True,
        env_file=tmp_path/'unused',progress_seconds=1))==1
    saved=json.loads((tmp_path/'manifest.json').read_text())
    assert saved['workers']==2 and saved['threads']==1
    assert len(spawned)==4 and (tmp_path/'STOP').exists()
    assert saved['rows'][-1]['status']=='completed' and saved['rows'][-1]['database']=='keep'
    assert saved['rows'][0]['previous_attempt']['reason']=='old failure'
    assert sum(r['status']=='queued' for r in saved['rows'])==6
