"""Contract checks for the native compact-card path and its legacy fallback."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from performance import new_ac
from test_card_metrics import api, metrics  # noqa: F401


def detail(cid=42, *, note=True, state=True, ctype=2):
    return SimpleNamespace(card_id=cid, ctype=ctype, queue=-1, due=-12, interval=6, reps=9,
                           metrics=metrics(cid, state=state, r=state),
                           note_fields=SimpleNamespace(fields=[
                               SimpleNamespace(name='Front', value='<b>猫</b>', order=0)]),
                           HasField=lambda name: note if name == 'note_fields' else True)


def test_native_details_keep_response_shape_without_per_card_loads(api):
    ac, col, _ = api
    col.card_details = Mock(return_value=[detail(7), detail(42)])
    response = ac.cardsDetails(['7', 42, 7], fields=['prop:d', 'prop:r', 'due', 'queue', 'interval'],
                               noteFields=['Front', 'Missing', 99, 'Front'])
    assert response == [dict(cardId=cid, fields={'Front': {'value': '<b>猫</b>', 'order': 0}},
                             **{'prop:d': 7.0, 'prop:r': 0.8, 'due': -12, 'queue': -1, 'interval': 6})
                        for cid in [7, 42, 7]]
    col.card_details.assert_called_once_with([7, 42], include_memory_state=True,
                                            include_retrievability=True,
                                            note_fields=['Front', 'Missing', 'Front'])
    ac.getCard.assert_not_called()
    col.card_memory_metrics.assert_not_called()
    col.card_stats_data.assert_not_called()


@pytest.mark.parametrize('fields', [[], ['due', 'queue', 'type', 'interval', 'reps']])
def test_plain_details_do_not_request_metrics_or_note_fields(api, fields):
    ac, col, _ = api
    col.card_details = Mock(return_value=[detail()])
    response = ac.cardsDetails([42], fields=fields)
    assert response == [{'cardId': 42, **{k: v for k, v in
                        dict(due=-12, queue=-1, type=2, interval=6, reps=9).items() if k in fields}}]
    col.card_details.assert_called_once_with([42], include_memory_state=False,
                                            include_retrievability=False, note_fields=None)
    ac.getCard.assert_not_called()


def test_new_card_missing_metrics_stay_null(api):
    ac, col, _ = api
    col.card_details = Mock(return_value=[detail(state=False, ctype=0)])
    assert ac.cardsDetails([42], fields=['prop:r', 'prop:s', 'prop:d']) == [
        {'cardId': 42, 'prop:r': None, 'prop:s': None, 'prop:d': None}]
    col.card_stats_data.assert_not_called()


@pytest.mark.parametrize('rows', [[], [detail(note=False)], [detail(state=False)]])
def test_incomplete_batch_uses_original_validation_and_initialization(api, rows):
    ac, col, _ = api
    expected = ac.cardsDetails([42], fields=['prop:d'], noteFields=['Front'])
    col.card_details = Mock(return_value=rows)
    ac.getCard.reset_mock()
    assert ac.cardsDetails([42], fields=['prop:d'], noteFields=['Front']) == expected
    ac.getCard.assert_called_once_with(42)


def test_batch_errors_are_reported_and_not_hidden_by_fallback(api):
    ac, col, _ = api
    col.card_details = Mock(side_effect=RuntimeError('backend unavailable'))
    assert ac.handler({'action': 'cardsDetails', 'version': 6,
                       'params': {'cards': [42], 'fields': ['due']}}) == {
        'result': None, 'error': 'backend unavailable'}
    ac.getCard.assert_not_called()


def test_details_do_not_cache_results_between_requests(api):
    ac, col, _ = api
    changed = detail()
    changed.due = 99
    col.card_details = Mock(side_effect=[[detail()], [changed]])
    assert ac.cardsDetails([42], fields=['due'])[0]['due'] == -12
    assert ac.cardsDetails([42], fields=['due'])[0]['due'] == 99


@pytest.mark.parametrize('note_fields', [None, [], ['Front'], ['Back', 'Front', 'Missing'],
                                        ['Missing', 9], ['Front', 'Front']])
def test_real_collection_details_match_legacy_errors_and_values(namespace, tmp_path, note_fields):
    from anki.collection import Collection
    from anki.cards import FSRSMemoryState

    col = Collection(str(tmp_path / 'details.anki2'))
    try:
        if not hasattr(col, 'card_details'):
            pytest.skip('requires the rebuilt native card_details API')
        note = col.new_note(col.models.by_name('Basic'))
        note['Front'], note['Back'] = '<b>猫</b> & "quotes"', 'one\ntwo'
        col.add_note(note, 1)
        cid = note.cards()[0].id
        card = col.get_card(cid)
        card.type, card.queue, card.due = 2, -1, -12
        card.memory_state = FSRSMemoryState(stability=12.5, difficulty=7.0)
        col.update_card(card)
        optimized, legacy = new_ac(namespace), new_ac(namespace)
        optimized.collection = legacy.collection = lambda: col
        legacy._cardsDetailsBatch = lambda *args: None
        original_get_card = optimized.getCard
        for ids in [[str(cid), cid], [cid, 1], [1, cid], [2**80], []]:
            params = dict(cards=ids, fields=['prop:s', 'prop:d', 'due', 'queue', 'type', 'interval', 'reps'],
                          noteFields=note_fields)
            request = dict(action='cardsDetails', version=6, params=params)
            expected = legacy.handler(request)
            optimized.getCard = Mock(wraps=original_get_card)
            assert optimized.handler(request) == expected
            if ids == [str(cid), cid]:
                optimized.getCard.assert_not_called()
        # A note edit must be visible on the next request, without invalidation.
        note['Front'] = '犬'
        col.update_note(note)
        assert optimized.cardsDetails([cid], noteFields=['Front'])[0]['fields'] == {
            'Front': {'value': '犬', 'order': 0}}
    finally:
        col.close()
