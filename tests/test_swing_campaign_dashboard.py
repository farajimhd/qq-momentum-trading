from io import StringIO
from pathlib import Path
import sys
import json
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))

from rich.console import Console

from scripts.swing_campaign_dashboard import Dashboard
from scripts import build_swing_book_campaign as campaign


def manifest():
    return dict(workers=64, rows=[dict(ticker=f'T{i:02}', status='active',
        report=f'missing-report-{i}.json', days=420, started_epoch=0) for i in range(64)])


def test_all_workers_have_stable_rows_and_paging():
    m=manifest()
    console=Console(file=StringIO(),width=80,height=24,color_system=None)
    d=Dashboard(m,console)
    d.render(m)
    assert d.pages>1
    assert d.slots==m['rows']
    first=d.slots[0]
    m['rows'][3]['status']='completed'
    d.render(m)
    assert d.slots[0] is first
    assert d.slots[3]['status']=='completed'
    pages=[]
    for page in range(d.pages):
        d.page=page
        with console.capture() as capture:console.print(d.render(m))
        frame=capture.get()
        assert len(frame.splitlines())<=24
        pages.append(frame)
    for row in m['rows']:assert row['ticker'] in ''.join(pages)


def test_wide_dashboard_contains_all_workers_without_overflow():
    m=manifest();console=Console(file=StringIO(),width=160,height=48,color_system=None)
    d=Dashboard(m,console)
    with console.capture() as capture:console.print(d.render(m))
    frame=capture.get()
    assert d.pages==1
    assert 'T63' in frame and len(frame.splitlines())<=48


def test_redirected_output_does_not_repeat_heartbeat_or_emit_ansi():
    stream=StringIO();m=manifest();d=Dashboard(m,Console(file=stream))
    d.start()
    for _ in range(10):d.update(m)
    assert len(stream.getvalue().splitlines())==1
    assert '\x1b' not in stream.getvalue()


def test_display_compatibility_still_rejects_other_source_changes(tmp_path,monkeypatch):
    monkeypatch.setattr(campaign,'ROOT',tmp_path)
    for name in campaign.TRACKED:
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'test')
    hashes={p:campaign.sha256((tmp_path/p).read_bytes()).hexdigest() for p in campaign.TRACKED}
    hashes['scripts/build_swing_book_campaign.py']=campaign.LEGACY_DISPLAY_CONTROLLERS[0]
    legacy=campaign.P.digest(hashes)
    assert campaign.compatible_code_hash(legacy)
    (tmp_path/'src/backend/swing_book_source.py').write_bytes(b'changed')
    assert not campaign.compatible_code_hash(legacy)


@pytest.mark.parametrize('interrupt',[False,True])
def test_controller_run_completion_and_graceful_stop(tmp_path,monkeypatch,interrupt):
    rows=[]
    for ticker in ('ONE','TWO'):
        rows.append(dict(ticker=ticker,status='queued',days=1,
            progress_file=str(tmp_path/'workers'/ticker/'progress.json'),
            report=str(tmp_path/ticker/'report.json')))
    m=dict(schema_version=2,book_version='causal-swing-closing-book-6',
        workers=1,threads=2,code_hash=campaign.code_hash(),universe=[],
        universe_hash=campaign.P.digest([]),rows=rows)
    campaign.save(tmp_path,m)
    class Process:
        pid=10
        def __init__(self,command,**kwargs):
            self.polls=0
            ticker=command[command.index('--ticker')+1]
            campaign.P.save(tmp_path/'workers'/ticker/'progress.json',dict(stage='completed'))
        def poll(self):
            self.polls+=1
            return 0 if self.polls>=2 else None
        def wait(self):return 0
    monkeypatch.setattr(campaign.subprocess,'Popen',Process)
    sleeps=[]
    def pause(_):
        if interrupt and not sleeps:
            sleeps.append(True)
            raise KeyboardInterrupt
    monkeypatch.setattr(campaign.time,'sleep',pause)
    result=campaign.run(SimpleNamespace(runtime=tmp_path,retry_failed=False,
        env_file=tmp_path/'unused',progress_seconds=1))
    saved=json.loads((tmp_path/'manifest.json').read_text())
    assert result==(130 if interrupt else 0)
    assert saved['rows'][0]['status']=='completed'
    assert saved['rows'][1]['status']==('queued' if interrupt else 'completed')
