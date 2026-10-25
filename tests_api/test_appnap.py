"""Check App Nap suppression at the listener startup boundary."""
from types import SimpleNamespace

import pytest

import plugin
from plugin import AnkiConnect


@pytest.fixture
def startup(monkeypatch):
    events = []
    owner = AnkiConnect()
    monkeypatch.setattr(owner.server, 'listen', lambda: events.append('listen'))
    monkeypatch.setattr(plugin.util, 'setting', lambda key: 25)
    monkeypatch.setattr(plugin, 'QTimer', lambda: SimpleNamespace(
        timeout=SimpleNamespace(connect=lambda callback: events.append('connect')),
        start=lambda interval: events.append(('timer', interval)),
    ))
    monkeypatch.setattr(plugin.QMessageBox, 'critical',
                        lambda *args: events.append(('error', args[-1])))
    return owner, events


def test_suppression_follows_listener_start(startup, monkeypatch):
    owner, events = startup
    monkeypatch.setattr(plugin, 'macos_helper', SimpleNamespace(
        disable_appnap=lambda: events.append('disable_appnap'),
    ))
    owner.startWebServer()
    assert events == ['listen', 'connect', ('timer', 25), 'disable_appnap']
    assert owner.timer is not None


def test_startup_without_macos_helper(startup, monkeypatch):
    owner, events = startup
    monkeypatch.setattr(plugin, 'macos_helper', None)
    owner.startWebServer()
    assert events == ['listen', 'connect', ('timer', 25)]


def test_failed_listener_does_not_suppress_appnap(startup, monkeypatch):
    owner, events = startup

    def fail():
        raise OSError('address already in use')

    monkeypatch.setattr(owner.server, 'listen', fail)
    monkeypatch.setattr(plugin, 'macos_helper', SimpleNamespace(
        disable_appnap=lambda: pytest.fail('suppression without a listener'),
    ))
    owner.startWebServer()
    assert events == [('error', 'address already in use')]
    assert owner.timer is None


def test_helper_failure_keeps_listener_running(startup, monkeypatch, capsys):
    owner, events = startup

    def fail():
        raise RuntimeError('native helper unavailable')

    monkeypatch.setattr(plugin, 'macos_helper', SimpleNamespace(disable_appnap=fail))
    owner.startWebServer()
    assert events == ['listen', 'connect', ('timer', 25)]
    assert owner.timer is not None
    assert 'failed to disable App Nap: native helper unavailable' in capsys.readouterr().out
