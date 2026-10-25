"""Production metric selection checks without launching Anki or opening a profile."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from anki.errors import NotFoundError

from performance import load_code, new_ac


def metrics(cid=42, state=True, r=True):
    return SimpleNamespace(card_id=cid, memory_state=SimpleNamespace(stability=12.5, difficulty=7.0),
                           desired_retention=0.9, fsrs_retrievability=0.8,
                           HasField=lambda name: {'memory_state': state,
                                                 'desired_retention': True,
                                                 'fsrs_retrievability': r}[name])


@pytest.fixture
def namespace():
    return load_code(production_cards=True)


@pytest.fixture
def api(namespace):
    card = SimpleNamespace(id=42, type=2, queue=2, due=10, ivl=6, reps=9,
                           ord=0, factor=2500, nid=21, did=1, lapses=1, mod=100, flags=0, left=0,
                           question=Mock(return_value='question'), answer=Mock(return_value='answer'),
                           note=Mock(return_value=SimpleNamespace(fields=['猫', 'cat'])),
                           note_type=Mock(return_value={'name': 'Basic', 'css': '', 'flds': [
                               {'name': 'Front', 'ord': 0}, {'name': 'Back', 'ord': 1}]}))
    col = SimpleNamespace(card_stats_data=Mock(return_value=metrics()),
                          card_memory_metrics=Mock(return_value=[metrics()]),
                          find_cards=Mock(return_value=[42]),
                          _backend=SimpleNamespace(get_scheduling_states=Mock(return_value='states'),
                                                   describe_next_states=Mock(return_value=['1d'])))
    ac = new_ac(namespace)
    ac.collection = lambda: col
    ac.getCard = Mock(return_value=card)
    ac.deckNameFromId = Mock(return_value='Default')
    namespace['util'].cardQuestion = lambda card: 'question'
    namespace['util'].cardAnswer = lambda card: 'answer'
    return ac, col, card


@pytest.mark.parametrize('endpoint', ['cardsDetails', 'cardsInfo', 'findCards'])
@pytest.mark.parametrize('native', [True, False])
def test_current_difficulty_is_not_desired_retention(api, endpoint, native):
    ac, col, _ = api
    if not native:
        del col.card_memory_metrics
    args = {'query': 'deck:Default'} if endpoint == 'findCards' else {'cards': [42]}
    result = getattr(ac, endpoint)(**args, fields=['prop:s', 'prop:d'])
    assert result[0]['prop:s'] == 12.5
    assert result[0]['prop:d'] == 7.0
    if native:
        col.card_memory_metrics.assert_called_once_with([42], include_retrievability=False)
        col.card_stats_data.assert_not_called()
    else:
        col.card_stats_data.assert_called_once_with(42)


@pytest.mark.parametrize('mode', ['ALL', 'COMPACT', 'FIELDS_ONLY'])
def test_cards_info_native_metrics_preserve_all_modes(api, mode):
    ac, col, _ = api
    params = dict(cards=[42, 42], fields=['prop:r', 'prop:s', 'prop:d'],
                  noteFields=['Front'], retrieved_info_mode=mode)
    fast = ac.cardsInfo(**params)
    col.card_memory_metrics.assert_called_once_with([42], include_retrievability=True)
    col.card_stats_data.assert_not_called()
    del col.card_memory_metrics
    assert ac.cardsInfo(**params) == fast
    assert len(fast) == 2
    assert fast[0]['fields'] == {'Front': {'value': '猫', 'order': 0}}


def test_details_preserve_order_duplicates_and_selected_fields(api):
    ac, col, card = api
    other = SimpleNamespace(**{**vars(card), 'id': 7})
    ac.getCard.side_effect = lambda cid: {42: card, 7: other}[cid]
    col.card_memory_metrics.return_value = [metrics(42), metrics(7)]
    result = ac.cardsDetails(['7', 42, 7], fields=['prop:r', 'prop:d', 'due'], noteFields=['Back'])
    assert result == [dict(cardId=cid, fields={'Back': {'value': 'cat', 'order': 1}},
                          **{'prop:r': 0.8, 'prop:d': 7.0, 'due': 10}) for cid in [7, 42, 7]]
    col.card_memory_metrics.assert_called_once_with([7, 42], include_retrievability=True)
    col.card_stats_data.assert_not_called()


@pytest.mark.parametrize('fields', [None, [], ['due', 'queue', 'type', 'interval', 'reps']])
def test_plain_fields_skip_all_metric_work(api, fields):
    ac, col, _ = api
    ac.cardsDetails([42], fields=fields, noteFields=['Front'])
    col.card_memory_metrics.assert_not_called()
    col.card_stats_data.assert_not_called()


@pytest.mark.parametrize('endpoint', ['cardsDetails', 'cardsInfo'])
def test_new_cards_keep_null_state_despite_present_retention(api, endpoint):
    ac, col, card = api
    card.type = 0
    col.card_memory_metrics.return_value = [metrics(state=False, r=False)]
    result = getattr(ac, endpoint)([42], fields=['prop:r', 'prop:s', 'prop:d'])[0]
    assert [result[key] for key in ['prop:r', 'prop:s', 'prop:d']] == [None, None, None]
    col.card_stats_data.assert_not_called()


def test_reviewed_card_without_state_uses_existing_initialization(api):
    ac, col, _ = api
    col.card_memory_metrics.return_value = [metrics(state=False, r=False)]
    assert ac.cardsDetails([42], fields=['prop:d'])[0]['prop:d'] == 7.0
    col.card_stats_data.assert_called_once_with(42)


def test_missing_card_behavior_is_unchanged(api):
    ac, col, card = api
    def get_card(cid):
        if cid == 1:
            ac.raiseNotFoundError('Card was not found: 1')
        return card
    ac.getCard.side_effect = get_card
    result = ac.cardsInfo([42, 1, 42], fields=['prop:d'], retrieved_info_mode='FIELDS_ONLY')
    assert result[1] == {}
    assert result[0] == result[2]
    with pytest.raises(NotFoundError, match='Card was not found: 1'):
        ac.cardsDetails([42, 1], fields=['prop:d'])


def test_metrics_are_fresh_between_requests(api):
    ac, col, _ = api
    assert ac.cardsDetails([42], fields=['prop:s'])[0]['prop:s'] == 12.5
    updated = metrics()
    updated.memory_state.stability = 25.0
    col.card_memory_metrics.return_value = [updated]
    assert ac.cardsDetails([42], fields=['prop:s'])[0]['prop:s'] == 25.0
    assert col.card_memory_metrics.call_count == 2


def test_native_errors_are_not_silently_replaced_with_legacy_results(api):
    ac, col, _ = api
    col.card_memory_metrics.side_effect = RuntimeError('backend unavailable')
    assert ac.handler({'action': 'cardsDetails', 'version': 6,
                       'params': {'cards': [42], 'fields': ['prop:d']}}) == {
        'result': None, 'error': 'backend unavailable'}
    col.card_stats_data.assert_not_called()


@pytest.mark.parametrize('cards', [[], [2**80], ['42'], (42,)])
def test_unusual_card_info_ids_keep_original_validation_path(api, cards):
    ac, col, _ = api
    ac.cardsInfo(cards, fields=['prop:d'], retrieved_info_mode='FIELDS_ONLY')
    col.card_memory_metrics.assert_not_called()


def test_native_collection_matches_full_statistics(namespace, tmp_path):
    from anki.collection import Collection
    from anki.cards import FSRSMemoryState
    import time

    col = Collection(str(tmp_path / 'metrics.anki2'))
    try:
        if not hasattr(col, 'card_memory_metrics'):
            pytest.skip('native batch API requires the rebuilt Anki core')
        note = col.new_note(col.models.by_name('Basic'))
        note['Front'] = '猫'
        col.add_note(note, 1)
        card = note.cards()[0]
        card.type = card.queue = 2
        card.memory_state = FSRSMemoryState(stability=12.5, difficulty=7.0)
        card.last_review_time = int(time.time()) - 2 * 86400
        card.desired_retention = 0.9
        col.update_card(card)
        ac = new_ac(namespace)
        ac.collection = lambda: col
        full = col.card_stats_data(card.id)
        result = ac.cardsDetails([card.id, card.id], fields=['prop:r', 'prop:s', 'prop:d', 'due'])
        assert result[0] == result[1]
        assert result[0]['prop:s'] == full.memory_state.stability
        assert result[0]['prop:d'] == full.memory_state.difficulty
        assert result[0]['prop:r'] == pytest.approx(full.fsrs_retrievability, abs=0.00001)
    finally:
        col.close()
