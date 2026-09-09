import json

from fastapi.testclient import TestClient

from app import turnlog
from app.metrics import SessionMetrics
from app.robot_backend import use_backend
from app.robot_simulation import SimulatedRobot
from app.robot_simulator import create_app


def test_simulation_metrics_are_correlated_and_content_free(monkeypatch):
    robot = SimulatedRobot()
    sid = 'a' * 32
    def forbidden(*args):
        raise AssertionError('simulation leaked to visitor log')
    monkeypatch.setattr(turnlog, '_record', forbidden)
    token = turnlog.session_id.set(sid)
    try:
        with use_backend(robot):
            turnlog.record('session_start', provider='gemini', text='PRIVATE')
            turnlog.record('said', text='PRIVATE')
            turnlog.record('mic_state', device='PRIVATE')
            turnlog.record('voice_ready')
            robot.submit('move_to_point', {'place': 'ห้องตัวอย่าง'}, 'test-command')
            metrics = SessionMetrics('gemini')
            metrics.speech_end('test')
            tid = metrics.audio()
            metrics.client({'turn_id':tid, 'name':'playback_schedule_ms', 'ms':12})
            metrics.record('metric_tool', turn_id=tid, name='go_to_place', elapsed_ms=4, args='PRIVATE')
            metrics.finish()
    finally:
        turnlog.session_id.reset(token)
    result = robot.diagnostics.export()
    assert 'PRIVATE' not in json.dumps(result)
    assert result['summary']['provider'] == 'connected'
    assert result['summary']['audio_samples'] == 1
    row = next(r for r in result['events'] if r['event']=='metric_client')
    assert (row['session_id'], row['turn_id'], row['command_id']) == (sid, tid, 'test-command')


def test_diagnostics_bounds_stale_sessions_and_nonfinite_values():
    robot = SimulatedRobot()
    d = robot.diagnostics
    d.record('session_start', {}, 'b'*32)
    d.record('provider_error', {'category':'network'}, 'a'*32)
    assert d.provider == 'connecting'
    for _ in range(950):
        d.record('metric_client', {'ms':float('nan'), 'secret':'PRIVATE'}, 'b'*32)
    assert len(d.rows)==800
    assert all('ms' not in r for r in d.rows)
    robot.reset()
    assert robot.diagnostics.summary()['retained']==0


def test_browser_observations_require_auth_and_strict_schema():
    robot=SimulatedRobot()
    body={'client_id':'a'*8+'-'+'a'*4+'-'+'a'*4+'-'+'a'*4+'-'+'a'*12,
          'session_id':None,'mic':'flowing','playback':'running',
          'wake':'listening','provider':'idle','error':'none'}
    with TestClient(create_app(robot)) as client:
        assert client.post('/ws/diagnostics',json=body,headers={'Origin':'http://testserver'}).status_code==403
        page=client.get('/voice')
        assert "frame-ancestors 'self'" in page.headers['content-security-policy']
        assert client.get('/simulator-voice.js').status_code==200
        assert client.post('/ws/diagnostics',json=body,headers={'Origin':'https://bad.invalid'}).status_code==403
        assert client.post('/ws/diagnostics',json={**body,'text':'PRIVATE'},headers={'Origin':'http://testserver'}).status_code==422
        assert client.post('/ws/diagnostics',json={**body,'session_id':'a'*32},headers={'Origin':'http://testserver'}).status_code==409
        assert client.post('/ws/diagnostics',json=body,headers={'Origin':'http://testserver'}).status_code==200
        payload=client.get('/api/diagnostics').json()
        assert payload['epoch']==robot.epoch
        assert payload['events'][-1]['event']=='browser_state'
        assert payload['summary']['provider']=='idle'  # Client claims never set server readiness.
