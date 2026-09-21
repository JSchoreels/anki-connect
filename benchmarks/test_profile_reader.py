"""Contract and operation-count checks for request-scoped note prefetching."""
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest

from performance import install_dispatch_cache, new_ac
from profile_reader import make_prefetched_details, prefetch_notes, prefetched_note_and_model


def test_prefetch_is_bounded_and_preserves_raw_field_values():
    model = {'flds': [{'name': 'Front', 'ord': 0}, {'name': 'Kana', 'ord': 1},
                      {'name': 'Empty', 'ord': 2}]}
    col = SimpleNamespace(db=SimpleNamespace(all=Mock(side_effect=[
        [(5, 9)], [(5, 9)], [(5, '<b>猫</b>', 'ねこ', '')],
    ])), models=SimpleNamespace(get=Mock(return_value=model)))
    rows = prefetch_notes(col, list(range(1001)) + [0, 1000], ['Front', 'Kana', 'Empty'])
    assert rows == {5: (9, ['<b>猫</b>', 'ねこ', ''])}
    assert [len(call.args) - 1 for call in col.db.all.call_args_list] == [999, 2, 4]
    assert all(call.args[0].startswith('SELECT ') for call in col.db.all.call_args_list)


@pytest.mark.parametrize('ids', [[], [2**80], [-(2**80)]])
def test_empty_and_oversized_ids_do_not_issue_prefetch_queries(ids):
    col = SimpleNamespace(db=SimpleNamespace(all=Mock()))
    assert prefetch_notes(col, ids, ['Front']) == {}
    col.db.all.assert_not_called()


def test_missing_prefetched_note_uses_original_note_access():
    card = SimpleNamespace(nid=42, note=Mock(side_effect=RuntimeError('missing note')),
                           note_type=Mock())
    with pytest.raises(RuntimeError, match='missing note'):
        prefetched_note_and_model(card, {})
    card.note.assert_called_once_with()
    card.note_type.assert_not_called()


@pytest.mark.parametrize('cached_dispatch', [False, True])
def test_prefetch_preserves_selection_duplicates_and_stats(namespace, cached_dispatch):
    model = {'flds': [{'name': 'Front', 'ord': 0}, {'name': 'Kana', 'ord': 1},
                      {'name': 'Empty', 'ord': 2}]}
    stats = SimpleNamespace(fsrs_retrievability=0.875,
                            memory_state=SimpleNamespace(stability=8.5, difficulty=6.25),
                            desired_retention=0.9, HasField=lambda _: True)
    col = SimpleNamespace(db=SimpleNamespace(all=Mock(side_effect=[
        [(50, 9)], [(50, '<b>猫</b>', '')], [(50, 9)], [(50, '犬', '')]])),
        models=SimpleNamespace(get=Mock(return_value=model)),
        card_stats_data=Mock(return_value=stats))
    card = SimpleNamespace(id=10, nid=50, due=12, queue=2, type=2, ivl=8, reps=3,
                           col=col, note=Mock(), note_type=Mock())
    ac = new_ac(namespace)
    ac.collection = lambda: col
    ac.getCard = Mock(return_value=card)
    ac.cardsDetails = MethodType(make_prefetched_details(namespace), ac)
    if cached_dispatch:
        install_dispatch_cache(ac, namespace)
    request = {'action': 'cardsDetails', 'version': 6, 'params': {
        'cards': ['10', 10], 'fields': ['prop:r', 'prop:s', 'prop:d', 'due'],
        'noteFields': ['Empty', 'Front', 'Missing', 'Front']}}
    expected = {'cardId': 10, 'fields': {
        'Front': {'value': '<b>猫</b>', 'order': 0}, 'Empty': {'value': '', 'order': 2}},
        'prop:r': 0.875, 'prop:s': 8.5, 'prop:d': 6.25, 'due': 12}
    assert ac.handler(request) == {'result': [expected, expected], 'error': None}
    assert col.db.all.call_count == 2
    assert col.card_stats_data.call_count == 2
    card.note.assert_not_called()
    card.note_type.assert_not_called()
    # Nothing survives this API call: a later edit must be visible immediately.
    result = ac.handler(request)['result']
    assert result[0]['fields']['Front']['value'] == '犬'
    assert col.db.all.call_count == 4


@pytest.mark.parametrize('params', [
    {'cards': None}, {'cards': []}, {'cards': [10], 'fields': None},
    {'cards': 'invalid'}, {'cards': [], 'fields': ['unsupported']},
    {'cards': [], 'noteFields': 'invalid'},
    {'cards': [], 'noteFields': ['Front'], 'fields': ['prop:r']},
])
def test_original_early_returns_and_validation_are_unchanged(namespace, params):
    original, optimized = new_ac(namespace), new_ac(namespace)
    optimized.cardsDetails = MethodType(make_prefetched_details(namespace), optimized)
    original.collection = optimized.collection = Mock(side_effect=AssertionError('unexpected database access'))
    request = {'action': 'cardsDetails', 'version': 6, 'params': params}
    assert optimized.handler(request) == original.handler(request)


@pytest.mark.parametrize('note_fields', [[], ['Front'], ['Back', 'Front', 'Missing'],
                                        ['Missing'], ['Front', 'Front', 9]])
def test_native_collection_contract_with_different_field_orders(namespace, tmp_path, note_fields):
    from anki.collection import Collection
    col = Collection(str(tmp_path / 'synthetic.anki2'))
    try:
        basic = col.models.by_name('Basic')
        first = col.new_note(basic)
        first['Front'] = '<b>猫</b> & "quotes"'
        first['Back'] = 'line one\nline two'
        col.add_note(first, 1)

        custom = col.models.new('Benchmark reversed fields')
        for name in ['Back', 'Unused', 'Front']:
            col.models.add_field(custom, col.models.new_field(name))
        template = col.models.new_template('Card')
        template['qfmt'], template['afmt'] = '{{Front}}', '{{Back}}'
        col.models.add_template(custom, template)
        col.models.add(custom)
        second = col.new_note(custom)
        second['Front'] = '犬'
        second['Back'] = ''
        second['Unused'] = 'must not be transferred'
        col.add_note(second, 1)

        one, two = first.cards()[0].id, second.cards()[0].id
        original, optimized = new_ac(namespace), new_ac(namespace)
        original.collection = optimized.collection = lambda: col
        optimized.cardsDetails = MethodType(make_prefetched_details(namespace), optimized)
        install_dispatch_cache(optimized, namespace)
        for cards in [[str(two), one, two], [one, 1, two]]:
            request = {'action': 'cardsDetails', 'version': 6, 'params': {
                'cards': cards, 'fields': ['prop:r', 'prop:s', 'prop:d', 'due', 'queue', 'type'],
                'noteFields': note_fields}}
            assert optimized.handler(request) == original.handler(request)
    finally:
        col.close()
