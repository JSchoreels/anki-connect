"""Legacy note creation validates each note natively once and keeps its outcomes."""
import hashlib
import json
from pathlib import Path

import pytest

from tests_api.test_runtime import admitted, native
from tests_api.test_yomitan import exchange


def legacy(admitted, action, **params):
    owner, _, drain, _, _ = admitted
    return exchange(owner, drain, {'action': action, 'version': 6, 'params': params})


def note(front, deck='Default', model='Basic', **extra):
    fields = {'Text': front} if model == 'Cloze' else {'Front': front, 'Back': 'back'}
    return {'deckName': deck, 'modelName': model, 'fields': fields, 'tags': ['t'], **extra}


@pytest.fixture
def checks(monkeypatch):
    from anki.notes import Note  # After the runtime imports anki.collection.
    calls = []
    original = Note.fields_check
    def counted(self):
        calls.append(tuple(self.fields))
        return original(self)
    monkeypatch.setattr(Note, 'fields_check', counted)
    return calls


def test_add_note_checks_fields_natively_once(admitted, checks):
    reply = legacy(admitted, 'addNote', note=note('single'))
    assert reply['error'] is None and len(checks) == 1


def test_add_notes_checks_each_note_natively_once(admitted, checks):
    _, service, _, _, _ = admitted
    reply = legacy(admitted, 'addNotes', notes=[note('n%d' % index) for index in range(25)])
    assert reply['error'] is None and len(reply['result']) == 25
    assert len(checks) == 25
    assert sorted(service.col.get_note(id)['Front'] for id in reply['result']) == sorted('n%d' % index for index in range(25))


def test_media_notes_check_the_stored_content_once(admitted, checks):
    _, service, _, _, _ = admitted
    picture = {'data': 'iVBORw0KGgo=', 'filename': 'first.png', 'fields': ['Front']}
    audio = {'data': 'AAAA', 'filename': 'second.mp3', 'fields': ['Back']}
    reply = legacy(admitted, 'addNote', note=note('', picture=[picture], audio=[audio]))
    assert reply['error'] is None
    stored = tuple(service.col.get_note(reply['result']).fields)
    # Distinct inputs are each checked once, and the stored content was checked.
    assert len(checks) == len(set(checks)) and stored in checks


def test_batch_duplicates_are_detected_by_normalized_first_field(admitted):
    _, service, _, _, _ = admitted
    batch = [note('w%d' % index) for index in range(200)] + [note('<b>w17</b>'), note('w42', model='Cloze')]
    reply = legacy(admitted, 'addNotes', notes=batch)
    assert reply['result'] is None
    assert json.loads(reply['error'].replace("'", '"')) == ['cannot create note because it is a duplicate',
                                                           'cannot create note for unknown reason']
    assert service.col.note_count() == 0
    scoped = [note('s1'), note('s1', deck='Other', options={'allowDuplicate': False, 'duplicateScope': 'deck'}),
              note('s1', options={'allowDuplicate': True})]
    service.col.decks.id('Other')
    assert len(legacy(admitted, 'addNotes', notes=scoped)['result']) == 3


def test_validation_results_do_not_survive_writes(admitted):
    reply = legacy(admitted, 'multi', actions=[{'action': 'addNote', 'params': {'note': note('again')}},
                                               {'action': 'addNote', 'params': {'note': note('again')}},
                                               {'action': 'addNotes', 'params': {'notes': [note('later')]}},
                                               {'action': 'addNotes', 'params': {'notes': [note('later')]}}])
    first, second, third, fourth = reply['result']
    assert isinstance(first, int) and isinstance(third, list)
    assert second['error'] == fourth['error'].strip("[]'") == 'cannot create note because it is a duplicate'


def test_media_free_add_creates_no_journal_but_still_recovers(admitted):
    _, service, _, _, _ = admitted
    media = Path(service.col.media.dir())
    root = media.parent / '.anki-connect-media-journal'
    assert legacy(admitted, 'addNote', note=note('plain'))['error'] is None
    assert not root.exists() or not list(root.iterdir())
    # An interrupted replacement of an unreferenced file is restored first.
    (media / 'held.png').write_bytes(b'replacement')
    journal = root / 'interrupted'
    journal.mkdir(parents=True)
    (journal / 'saved').write_bytes(b'original')
    (journal / 'manifest.json').write_text(json.dumps({'committed': False, 'entries': {
        'held.png': {'backup': 'saved', 'sha256': hashlib.sha256(b'replacement').hexdigest()}}}))
    assert legacy(admitted, 'addNote', note=note('plain 2'))['error'] is None
    assert (media / 'held.png').read_bytes() == b'original' and not journal.exists()
    # A conflicting journal still blocks the write, as before.
    journal.mkdir()
    (journal / 'manifest.json').write_text(json.dumps({'committed': False, 'entries': {
        'held.png': {'backup': None, 'sha256': '0' * 64}}}))
    reply = legacy(admitted, 'addNote', note=note('plain 3'))
    assert reply['error'] == 'Interrupted media replacements need recovery.'
    assert not service.col.find_notes('"Front:plain 3"')
