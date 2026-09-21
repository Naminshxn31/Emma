"""Observer watchdog tests with no cameras, network, or robot commands."""
from threading import Event
from types import SimpleNamespace
import pytest
from scripts.record_robot_arm_probe import wait_for_probe
from scripts.probe_robot_arm_stop import parse_probe_args, validate_step


def test_probe_rejects_unknown_channel_before_any_io():
    with pytest.raises(SystemExit):
        parse_probe_args(['--send-stop', '--output', 'unused.json', '--centre-channel', '14'])


def test_probe_cannot_select_two_channels_or_actions():
    with pytest.raises(SystemExit):
        parse_probe_args(['--send-stop', '--output', 'unused.json', '--centre-channel', '11', '--step-channel', '7'])


def test_step_requires_baseline_for_selected_channel_not_another_channel():
    args = parse_probe_args(['--send-stop', '--output', 'unused.json', '--step-channel', '11'])
    with pytest.raises(RuntimeError, match='selected channel'):
        validate_step(args, {'commanded':{'1':1500}})
    validate_step(args, {'commanded':{'11':1500}})


def test_default_probe_stays_stop_only():
    args = parse_probe_args(['--send-stop', '--output', 'unused.json'])
    assert not args.has_motion


def test_reference_group_is_explicit_and_excludes_channel_motion():
    args = parse_probe_args(['--send-stop', '--output', 'unused.json', '--group', '3'])
    assert args.group == 3 and args.has_motion and not args.is_step
    with pytest.raises(SystemExit):
        parse_probe_args(['--send-stop', '--output', 'unused.json', '--group', '3', '--centre-channel', '1'])
    with pytest.raises(SystemExit):
        parse_probe_args(['--send-stop', '--output', 'unused.json', '--group', '99'])


def test_commissioning_cannot_be_a_sweep_or_mix_with_normal_motion():
    args = parse_probe_args(['--send-stop','--output','unused.json','--map-channel','2'])
    assert args.channel == 2 and args.has_motion and not args.is_step
    with pytest.raises(SystemExit):
        parse_probe_args(['--send-stop','--output','unused.json','--map-channel','21'])
    with pytest.raises(SystemExit):
        parse_probe_args(['--send-stop','--output','unused.json','--map-channel','2','--group','3'])
    with pytest.raises(SystemExit):
        parse_probe_args(['--send-stop','--output','unused.json','--map-channel','2','--map-target','2500'])
    with pytest.raises(SystemExit):
        parse_probe_args(['--send-stop','--output','unused.json','--map-target','1540'])


@pytest.mark.parametrize('motion_args', [
    ['--map-channel', '5'], ['--group', '3'],
    ['--centre-channel', '7'], ['--step-channel', '8'],
    ['--centre-channel-1'], ['--step-channel-1'],
])
def test_suspended_commissioning_fails_before_config_or_robot_io(monkeypatch, motion_args):
    import asyncio
    from scripts import probe_robot_arm_stop as probe
    monkeypatch.setattr('sys.argv',['probe','--send-stop','--output','unused.json', *motion_args])
    def unexpected_io(*args, **kwargs):
        pytest.fail('Suspended commissioning must not read configuration or start I/O')
    monkeypatch.setattr(probe,'dotenv_values',unexpected_io)
    with pytest.raises(RuntimeError, match='Commissioning suspended'):
        asyncio.run(probe.main())


def test_camera_failure_requests_stop_while_probe_is_running():
    failed = Event()
    failed.set()
    stopped = []
    with pytest.raises(RuntimeError, match='physical halt remains unverified'):
        wait_for_probe(SimpleNamespace(poll=lambda:None), failed,
                       lambda:stopped.append(True))
    assert stopped == [True]


def test_timeout_requests_stop_even_if_camera_is_healthy():
    times = iter([0, 26])
    stopped = []
    with pytest.raises(RuntimeError, match='timed out'):
        wait_for_probe(SimpleNamespace(poll=lambda:None), Event(),
                       lambda:stopped.append(True), clock=lambda:next(times))
    assert stopped == [True]


def test_completed_probe_does_not_send_an_extra_stop():
    stopped = []
    wait_for_probe(SimpleNamespace(poll=lambda:0), Event(), lambda:stopped.append(True))
    assert stopped == []
