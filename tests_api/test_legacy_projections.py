"""Direct legacy read projections must match the v1 service projections they replace."""
import pytest

from plugin.legacy import Legacy
from plugin.protocol import ApiError
from tests_api.test_runtime import admitted, call, native
from tests_api.test_yomitan import exchange


def legacy(admitted, action, **params):
    owner, _, drain, _, _ = admitted
    reply = exchange(owner, drain, {'action': action, 'version': 6, 'params': params})
    return reply


def populate(service, count=12):
    decks = ['Default', 'Projection::A', 'Projection::B']
    for name in decks[1:]:
        service.col.decks.id(name)
    notes = []
    for index in range(count):
        if index % 4 == 0:
            note = call(service, 'notes.create', noteType={'name': 'Cloze'}, deck={'name': decks[index % 3]},
                        fields={'Text': '{{c1::a%d}} {{c2::b%d}}' % (index, index)}, tags=['t%d' % (index % 2)])
        else:
            note = call(service, 'notes.create', noteType={'name': 'Basic'}, deck={'name': decks[index % 3]},
                        fields={'Front': 'front %d' % index, 'Back': '<b>%d</b>' % index}, tags=['t%d' % (index % 2)])
        notes.append(note)
    cards = sorted(int(id) for note in notes for id in note['cardIds'])
    prediction = call(service, 'scheduling.predict', cardId=str(cards[1]))
    call(service, 'scheduling.answer', cardId=str(cards[1]), stateToken=prediction['stateToken'], rating='good')
    return sorted(int(note['noteId']) for note in notes), cards


def test_notes_info_matches_notes_get_with_missing_duplicate_and_string_ids(admitted):
    owner, service, _, _, _ = admitted
    notes, _ = populate(service)
    requested = [notes[3], 999, notes[0], str(notes[1]), notes[3]]
    reply = legacy(admitted, 'notesInfo', notes=requested)
    assert reply['error'] is None
    assert reply['result'][1] == {}
    for item, id in zip(reply['result'], requested):
        if id == 999:
            continue
        expected = call(service, 'notes.get', id=str(id))
        model = service.col.models.get(int(expected['noteType']['id']))
        assert item == {
            'noteId': int(id), 'profile': owner.runtime.profile_name, 'tags': expected['tags'],
            'fields': {field['name']: {'value': expected['fields'][field['name']], 'order': field['ord']} for field in model['flds']},
            'modelName': expected['noteType']['name'], 'mod': service.col.get_note(int(id)).mod,
            'cards': [int(card) for card in expected['cardIds']]}


@pytest.mark.parametrize('value', ['abc', 0, -5, 9223372036854775808, True, 1.5])
@pytest.mark.parametrize('action, key', [('notesInfo', 'notes'), ('cardsInfo', 'cards')])
def test_invalid_projection_ids_keep_invalid_params_error(admitted, action, key, value):
    reply = legacy(admitted, action, **{key: [value]})
    assert reply['result'] is None
    assert reply['error'] == 'Expected a positive signed 64-bit decimal ID.'


def test_cards_info_render_and_next_reviews_match_v1_services(admitted):
    _, service, _, _, _ = admitted
    _, cards = populate(service)
    requested = [cards[2], 999, cards[1], cards[0], cards[2]]
    reply = legacy(admitted, 'cardsInfo', cards=requested)
    assert reply['error'] is None and reply['result'][1] == {}
    for item, id in zip(reply['result'], requested):
        if id == 999:
            continue
        rendered = call(service, 'cards.render', id=str(id))
        predicted = call(service, 'scheduling.predict', cardId=str(id))
        card = service.col.get_card(id)
        assert item['cardId'] == id
        assert item['question'] == rendered['questionHtml'] and item['answer'] == rendered['answerHtml']
        assert item['css'] == rendered['css']
        assert item['nextReviews'] == [answer['label'] for answer in predicted['answers']]
        assert item['deckName'] == service.col.decks.get(card.did)['name']
        assert item['modelName'] == service.col.models.get(card.note().mid)['name']
    selected = legacy(admitted, 'cardsInfo', cards=cards[:3], fields=['deckName', 'nextReviews'])['result']
    assert [set(item) for item in selected] == [{'deckName', 'nextReviews'}] * 3


def test_projections_do_not_mutate_or_share_native_note_type_dicts(admitted):
    _, service, _, _, _ = admitted
    notes, cards = populate(service, 4)
    model = service.col.models.by_name('Basic')
    before = repr(service.col.models.get(model['id']))
    first = legacy(admitted, 'notesInfo', notes=notes)['result']
    first[1]['fields']['Front']['value'] = 'changed by client'
    legacy(admitted, 'cardsInfo', cards=cards)
    assert repr(service.col.models.get(model['id'])) == before
    assert legacy(admitted, 'notesInfo', notes=notes)['result'][1]['fields']['Front']['value'] == 'front 1'


def test_find_notes_and_cards_match_every_v1_page(admitted):
    owner, service, _, _, _ = admitted
    populate(service, 30)
    for action, method in (('findNotes', 'notes.search'), ('findCards', 'cards.search')):
        for query in ('', 'deck:Projection*', 'tag:t1', 'nothing-matches'):
            expected, cursor = [], None
            while True:
                page = call(service, method, query=query, limit=7, **({'cursor': cursor} if cursor else {}))
                expected += [int(id) for id in page['ids']]
                cursor = page['nextCursor']
                if not cursor:
                    break
            assert legacy(admitted, action, query=query) == {'result': expected, 'error': None}
    invalid = legacy(admitted, 'findNotes', query='(')
    assert invalid['result'] is None and invalid['error'].startswith('Invalid search')
    assert legacy(admitted, 'notesInfo', query='tag:t1')['result'] == legacy(
        admitted, 'notesInfo', notes=legacy(admitted, 'findNotes', query='tag:t1')['result'])['result']


def test_legacy_search_does_not_consume_cursor_capacity(native):
    owner, service = native
    populate(service, 3)
    for _ in range(150):
        assert Legacy(owner, service).call('findNotes', {'query': ''})
    assert owner.runtime.cursors == {}


def test_cursor_pages_drop_deleted_entities_in_order(native):
    _, service = native
    notes, cards = populate(service, 9)
    first = call(service, 'notes.search', query='', limit=3)
    assert first['ids'] == [str(id) for id in notes[:3]]
    call(service, 'notes.delete', ids=[str(notes[4])])
    second = call(service, 'notes.search', query='', limit=3, cursor=first['nextCursor'])
    assert second['ids'] == [str(notes[3]), str(notes[5])]
    first = call(service, 'cards.search', query='', limit=4)
    remaining = sorted(service.col.find_cards(''))
    assert first['ids'] == [str(id) for id in remaining[:4]]
    service.col.remove_notes([service.col.get_card(remaining[5]).nid])
    second = call(service, 'cards.search', query='', limit=4, cursor=first['nextCursor'])
    assert second['ids'] == [str(id) for id in remaining[4:8] if id in set(service.col.find_cards(''))]


def test_prediction_tokens_evict_oldest_first(native):
    owner, _ = native
    runtime = owner.runtime
    runtime.tokens.clear()
    keys = [runtime.token('card', {'index': index}) for index in range(1005)]
    assert len(runtime.tokens) == 1000
    assert list(runtime.tokens) == keys[5:]


def test_cached_legacy_action_table_matches_introspection(native):
    import inspect
    owner, _ = native
    names = [name for name, method in inspect.getmembers(owner, inspect.ismethod) if getattr(method, 'api', False)]
    assert owner.legacy_actions() == names

    def public(name, version):
        aliases = [(v, alias) for v, alias in getattr(getattr(owner, name), 'versions', []) if v <= version]
        return max(aliases)[1] if aliases else name

    for version in (2, 4, 5, 6):
        with pytest.raises(Exception, match='unsupported action'):
            owner.resolve_action('missingAction', version, {})
        table = owner.legacy_index()['aliases'][version]
        expected = {}
        for name in names:  # The previous linear scan returned the first match.
            expected.setdefault(public(name, version), name)
        assert table == expected


def filtered_learning_card(service):
    """A relearning card pulled into a filtered deck, whose native due is negative."""
    _, cards = populate(service, 6)
    card = service.col.get_card(cards[0])
    card.start_timer()
    service.col.sched.answerCard(card, 1)
    filtered = service.col.sched.get_or_create_filtered_deck(deck_id=0)
    filtered.name = 'Filtered'
    filtered.config.search_terms[0].search = 'cid:%d' % cards[0]
    service.col.sched.add_or_update_filtered_deck(filtered)
    return cards, service.col.get_card(cards[0])


def test_get_decks_groups_current_decks_without_scheduling_reads(admitted):
    _, service, _, _, _ = admitted
    cards, moved = filtered_learning_card(service)
    assert moved.odid
    requested = [cards[2], cards[0], cards[2], cards[1]]
    expected = {}
    for id in requested:
        expected.setdefault(service.col.decks.name(service.col.get_card(id).did), []).append(id)
    assert legacy(admitted, 'getDecks', cards=requested) == {'result': expected, 'error': None}
    assert expected['Filtered'] == [cards[0]]
    assert legacy(admitted, 'getDecks', cards=[cards[1], 999]) == {'result': None, 'error': 'Card was not found: 999'}
    assert legacy(admitted, 'getDecks', cards=[]) == {'result': {}, 'error': None}


def test_cards_to_notes_skips_missing_cards_and_keeps_first_order(admitted):
    _, service, _, _, _ = admitted
    cards, _ = filtered_learning_card(service)
    requested = [cards[4], 999, cards[0], cards[4], cards[1]]
    expected = list(dict.fromkeys(service.col.get_card(id).nid for id in requested if id != 999))
    assert legacy(admitted, 'cardsToNotes', cards=requested) == {'result': expected, 'error': None}
    assert legacy(admitted, 'cardsToNotes', cards=['abc'])['error'] == 'Expected a positive signed 64-bit decimal ID.'


@pytest.mark.parametrize('name, stored', [('plain_audio-1.mp3', 'plain_audio-1.mp3'),
                                          ('trailing.', 'trailing._'), ('spa ce [1].mp3', 'spa ce 1.mp3')])
def test_store_media_names_renames_and_rollback_journal(admitted, name, stored):
    from pathlib import Path
    _, service, _, _, _ = admitted
    media = Path(service.col.media.dir())
    first = legacy(admitted, 'storeMediaFile', filename=name, data='AAAA', deleteExisting=False)
    assert first == {'result': stored, 'error': None} and (media / stored).read_bytes() == b'\0\0\0'
    same = legacy(admitted, 'storeMediaFile', filename=name, data='AAAA', deleteExisting=False)
    assert same == {'result': stored, 'error': None}
    renamed = legacy(admitted, 'storeMediaFile', filename=name, data='AQID', deleteExisting=False)['result']
    assert renamed != stored and (media / renamed).read_bytes() == b'\1\2\3' and (media / stored).read_bytes() == b'\0\0\0'
    journal = media.parent / '.anki-connect-media-journal'
    assert not list(journal.iterdir())


def test_literal_media_patterns_match_the_full_listing(admitted, monkeypatch):
    import fnmatch
    import os
    from pathlib import Path
    _, service, _, _, _ = admitted
    media = Path(service.col.media.dir())
    for name in ('hachidori_ab12.mp3', 'Mixed Case.png', 'café 音声.mp3', 'x]y.txt'):
        (media / name).write_bytes(b'1')
    (media / 'folder').mkdir()
    listed = []
    real = os.listdir
    monkeypatch.setattr(os, 'listdir', lambda path: listed.append(path) or real(path))
    def full(pattern):
        return sorted(name for name in real(media) if fnmatch.fnmatchcase(name, pattern))
    for pattern in ('hachidori_ab12.mp3', 'HACHIDORI_AB12.mp3', 'mixed case.png', 'Mixed Case.png', 'café 音声.mp3',
                    'x]y.txt', 'folder', 'missing.mp3', 'hachidori_*', '*.mp3', 'x[]]y.txt', '?ixed Case.png'):
        assert legacy(admitted, 'getMediaFilesNames', pattern=pattern) == {'result': full(pattern), 'error': None}, pattern
    # Absent names never list the folder; Windows answers present ones directly too.
    listed.clear()
    legacy(admitted, 'getMediaFilesNames', pattern='missing.mp3')
    legacy(admitted, 'getMediaFilesNames', pattern='hachidori_ab12.mp3')
    assert len(listed) == (0 if os.name == 'nt' else 1)
