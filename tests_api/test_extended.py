"""Extended contracts through upstream's real services and controlled HTTP admission."""
import pytest
from anki.collection import Collection
from anki.cards import FSRSMemoryState

from plugin import _parse_anki_version
from plugin.extended import ExtendedCards
from tests_api.test_gsm import exchange
from tests_api.test_runtime import admitted, call, native


def invoke(owner, drain, action, **params):
    reply = exchange(owner, drain, {'action': action, 'version': 6, 'params': params})
    assert reply['error'] is None, reply
    return reply['result']


def cards(service, count=3):
    return [int(call(service, 'notes.create', noteType={'name': 'Basic'},
                     deck={'name': 'Default'}, fields={'Front': str(index), 'Back': 'answer'})['cardIds'][0])
            for index in range(count)]


@pytest.mark.parametrize('version,expected', [
    ('26.9.3+fsrs7', (26, 9, 3)), ('26.9.3+fsrs7.1', (26, 9, 3)),
    ('26.08', (26, 8)), ('26.9b2+local', (26, 9, 2)), ('26.9rc1', (26, 9, 1)),
])
def test_fsrs7_version_parser(version, expected):
    assert _parse_anki_version(version) == expected


@pytest.mark.parametrize('mode', ['ALL', 'COMPACT', 'FIELDS_ONLY'])
def test_extended_card_contracts_over_http(admitted, mode):
    owner, service, drain, _, _ = admitted
    cid = cards(service, 1)[0]
    card = service.col.get_card(cid)
    card.type = card.queue = 2
    card.memory_state = FSRSMemoryState(stability=12.5, difficulty=7.0)
    service.col.update_card(card)
    fields = ['prop:s', 'prop:d']
    info = invoke(owner, drain, 'cardsInfo', cards=[cid, 999, cid], fields=fields,
                  noteFields=['Front'], retrieved_info_mode=mode)
    assert info[0] == info[2] and info[1] == {}
    assert info[0]['prop:s'] == 12.5 and info[0]['prop:d'] == 7.0
    assert info[0]['fields'] == {'Front': {'value': '0', 'order': 0}}
    assert ('question' in info[0]) == (mode == 'ALL')
    assert ('queue' in info[0]) == (mode != 'FIELDS_ONLY')
    details = invoke(owner, drain, 'cardsDetails', cards=[cid, cid], fields=fields, noteFields=['Back'])
    assert details == [dict(cardId=cid, fields={'Back': {'value': 'answer', 'order': 1}},
                            **{'prop:s': 12.5, 'prop:d': 7.0})] * 2
    assert invoke(owner, drain, 'findCards', query='cid:' + str(cid), fields=fields,
                  noteFields=['Back']) == details[:1]
    assert invoke(owner, drain, 'findCards', query='cid:' + str(cid)) == [cid]
    assert invoke(owner, drain, 'cardsInfo', cards=[cid], fields=['cardId', 'due']) == [
        {'cardId': cid, 'due': card.due}]
    full = invoke(owner, drain, 'cardsInfo', cards=[cid])
    assert invoke(owner, drain, 'cardsInfo', cards=[cid], fields=[]) == full


def test_native_details_and_metric_batches_survive_dispatch(admitted, monkeypatch):
    owner, service, drain, _, _ = admitted
    ids = cards(service)
    if not hasattr(service.col, 'card_details'):
        pytest.skip('native card_details requires the rebuilt Anki core')
    requested = [ids[2], ids[0], ids[2]]
    calls = []
    native = type(service.col).card_details
    def batch(col, card_ids, **kwargs):
        calls.append((card_ids, kwargs))
        return native(col, card_ids, **kwargs)
    monkeypatch.setattr(type(service.col), 'card_details', batch)
    monkeypatch.setattr(ExtendedCards, 'getCard', lambda *args: pytest.fail('per-card fallback ran'))
    result = invoke(owner, drain, 'cardsDetails', cards=requested, fields=['due'], noteFields=['Front'])
    assert [row['cardId'] for row in result] == requested
    assert [row['fields']['Front']['value'] for row in result] == ['2', '0', '2']
    assert calls == [([ids[2], ids[0]], {'include_memory_state': False,
                     'include_retrievability': False, 'note_fields': ['Front']})]


def test_grade_now_native_effect_and_change_notifications(admitted):
    owner, service, drain, _, notifications = admitted
    ids = cards(service)
    token = call(service, 'scheduling.predict', cardId=str(ids[0]))['stateToken']
    before = service.col.db.scalar('select count() from revlog')
    assert invoke(owner, drain, 'gradeNow', cards=ids[:2], ease=3) is True
    assert service.col.db.scalar('select count() from revlog') == before + 2
    assert notifications
    with pytest.raises(Exception, match='stale'):
        call(service, 'scheduling.answer', cardId=str(ids[0]), stateToken=token, rating='good')
    assert exchange(owner, drain, {'action': 'gradeNow', 'version': 6,
                    'params': {'cards': ids[:1], 'ease': 5}})['error'] == 'ease must be between 1 and 4'


@pytest.mark.parametrize('card_options', [True, False])
def test_grade_now_backend_signature_compatibility(admitted, monkeypatch, card_options):
    owner, service, drain, _, _ = admitted
    cid = cards(service, 1)[0]
    captured = {}
    def modern(backend, *, card_ids, rating, card_options):
        captured.update(cards=card_ids, rating=rating, card_options=card_options)
    def legacy(backend, *, card_ids, rating):
        captured.update(cards=card_ids, rating=rating)
    monkeypatch.setattr(type(service.col._backend), 'grade_now', modern if card_options else legacy)
    assert invoke(owner, drain, 'gradeNow', cards=[cid], ease=3) is True
    assert captured['cards'] == [cid]
    assert ('card_options' in captured) == card_options
    if card_options:
        assert captured['card_options'] == []


def test_reposition_preserves_order_skips_and_notifies(admitted):
    owner, service, drain, _, notifications = admitted
    ids = cards(service)
    reviewed = service.col.get_card(ids[1])
    reviewed.type = reviewed.queue = 2
    service.col.update_card(reviewed)
    result = invoke(owner, drain, 'repositionNewCards', orderedCardIds=[ids[2], ids[1], ids[0], ids[2], 999],
                    startPosition=10, step=3, shift=True)
    assert result == dict(requested=5, deduped=4, eligibleNew=2, repositioned=2,
                          skippedNotFound=[999], skippedNotNew=[ids[1]],
                          appliedStartPosition=10, appliedStep=3, appliedShift=True)
    assert [service.col.get_card(cid).due for cid in [ids[2], ids[0]]] == [10, 13]
    assert notifications


@pytest.mark.parametrize('patch,error', [
    ({'orderedCardIds': []}, 'orderedCardIds should be a non-empty list'),
    ({'orderedCardIds': [True]}, 'orderedCardIds should contain only integers'),
    ({'startPosition': 0}, 'startPosition should be an integer >= 1'),
    ({'step': False}, 'step should be an integer >= 1'),
    ({'shift': 1}, 'shift should be a boolean'),
])
def test_reposition_rejects_invalid_parameters(admitted, patch, error):
    owner, service, drain, _, _ = admitted
    params = dict(orderedCardIds=cards(service, 1), startPosition=1, step=1, shift=True)
    reply = exchange(owner, drain, {'action': 'repositionNewCards', 'version': 6,
                                    'params': {**params, **patch}})
    assert reply['result'] is None and reply['error'].startswith(error)


def test_extended_actions_keep_authentication_and_lifecycle_guards(admitted, monkeypatch):
    from plugin import util
    owner, service, drain, work, _ = admitted
    cid = cards(service, 1)[0]
    actions = [('cardsDetails', {'cards': [cid], 'fields': ['due']}),
               ('gradeNow', {'cards': [cid], 'ease': 3}),
               ('repositionNewCards', dict(orderedCardIds=[cid], startPosition=1, step=1, shift=True))]
    monkeypatch.setitem(util.DEFAULT_CONFIG, 'apiKey', 'secret')
    for action, params in actions:
        reply = owner.handler_async({'action': action, 'version': 6, 'params': params})
        assert reply.done and reply.value['error'] == 'valid api key must be provided'
    assert not work
    owner.runtime.lifecycle_active = True
    for action, params in actions:
        reply = owner.handler_async({'action': action, 'version': 6, 'key': 'secret', 'params': params})
        assert reply.done and reply.value['error'] == 'Collection lifecycle operation is active.'
    assert not work


@pytest.mark.parametrize('completion', ['shown', 'ended', 'timeout', 'stale'])
def test_fsrs7_answer_waits_for_render_and_cleans_hooks(admitted, monkeypatch, completion):
    from types import SimpleNamespace
    from aqt import gui_hooks, qt
    from plugin.protocol import ApiError
    owner, _, _, _, _ = admitted
    gui = owner.runtime.gui
    timers = []
    queued = []
    monkeypatch.setattr(owner.runtime, 'schedule', queued.append)
    monkeypatch.setattr(gui_hooks, 'reviewer_did_show_question', type(gui_hooks.reviewer_did_show_question)())
    monkeypatch.setattr(gui_hooks, 'reviewer_will_end', type(gui_hooks.reviewer_will_end)())
    reviewer = SimpleNamespace(state='question', _current_question_is_rendered=lambda: False)
    reviewer._showAnswer = lambda: setattr(reviewer, 'state', 'answer')
    monkeypatch.setattr(gui, 'checked_reviewer', lambda token: reviewer)
    monkeypatch.setattr(qt, 'QTimer', SimpleNamespace(singleShot=lambda delay, callback: timers.append(callback)))
    questions = list(gui_hooks.reviewer_did_show_question._hooks)
    endings = list(gui_hooks.reviewer_will_end._hooks)
    pending = gui.execute('reviewer.showAnswer', {'stateToken': 'captured-token'})
    assert not pending.done and reviewer.state == 'question'
    if completion == 'timeout':
        timers.pop()()
    elif completion == 'ended':
        for callback in gui_hooks.reviewer_will_end._hooks:
            callback()
    else:
        if completion == 'stale':
            def stale(token):
                raise ApiError('conflict', 'Reviewer token is stale.')
            monkeypatch.setattr(gui, 'checked_reviewer', stale)
        observed = []
        subscriber = lambda card: observed.append((pending.done, reviewer.state))
        gui_hooks.reviewer_did_show_question.append(subscriber)
        for callback in gui_hooks.reviewer_did_show_question._hooks:
            callback(None)
        assert observed == [(False, 'question')]
        gui_hooks.reviewer_did_show_question.remove(subscriber)
    while queued:
        queued.pop(0)()
    assert pending.done
    if completion == 'shown':
        assert pending.error is None and pending.value == {'changed': True}
        assert reviewer.state == 'answer'
    else:
        assert pending.error.kind == {'ended': 'invalidState', 'timeout': 'busy', 'stale': 'conflict'}[completion]
        assert reviewer.state == 'question'
    assert gui_hooks.reviewer_did_show_question._hooks == questions
    assert gui_hooks.reviewer_will_end._hooks == endings
