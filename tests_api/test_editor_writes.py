"""Real collection effects and controlled save/reload completion for open editors."""
from types import SimpleNamespace

import pytest

from plugin.protocol import ApiError, Deferred
from tests_api.test_runtime import admitted, native


@pytest.fixture
def editing(admitted, monkeypatch):
    import aqt
    owner, service, drain, work, _ = admitted
    note = service.col.new_note(service.col.models.by_name('Basic'))
    note.fields = ['original', 'answer']
    service.col.add_note(note, 1)
    reloaded = []
    editor = SimpleNamespace(note=note, currentField=0, web=object(),
                             widget=SimpleNamespace(enabled=True, setEnabled=lambda value: setattr(editor.widget, 'enabled', value)),
                             set_note=lambda note, **kwargs: reloaded.append(note))
    enabled = [True]
    dialog = SimpleNamespace(editor=editor, isEnabled=lambda: enabled[0],
                             setEnabled=lambda value: enabled.__setitem__(0, value))
    monkeypatch.setattr(aqt.dialogs, '_dialogs', {'Edit': (None, dialog)})
    save, reload = Deferred(), Deferred()
    scripts = []
    def javascript(editor, code, **kwargs):
        scripts.append(code)
        return save if len(scripts) == 1 else reload
    monkeypatch.setattr(owner.runtime.gui, 'javascript', javascript)
    return owner, service, drain, work, note, editor, dialog, enabled, save, reload, reloaded


def update(owner, id, dialect, fields=None):
    fields = fields or {'Back': '[sound:sentence.mp3]'}
    if dialect == 'legacy':
        return owner.handler_async({'action': 'updateNoteFields', 'version': 6,
                                    'params': {'note': {'id': ' 00' + str(id), 'fields': fields}}})
    return owner.runtime.dispatcher.request({'jsonrpc': '2.0', 'id': 1, 'method': 'notes.update',
                                             'params': {'id': str(id), 'patch': {'fields': fields}}})


@pytest.mark.parametrize('dialect', ['legacy', 'v1'])
@pytest.mark.parametrize('svelte', [False, True])
def test_note_update_waits_for_editor_save_and_reload(editing, dialect, svelte):
    """notes.update/updateNoteFields preserve pending content and await editor refresh."""
    owner, service, drain, work, note, editor, _, enabled, save, reload, reloaded = editing
    if svelte:
        del editor.note
        editor.nid = note.id
    pending = update(owner, note.id, dialect)
    assert not pending.done and not work and not enabled[0]
    assert service.col.get_note(note.id).fields == ['original', 'answer']
    # Pending webview content is saved before API collection work is admitted.
    note['Front'] = 'pending user edit'
    service.col.update_note(note)
    save.resolve(True)
    assert work and not pending.done
    drain()
    assert service.col.get_note(note.id).fields == ['pending user edit', '[sound:sentence.mp3]']
    assert not pending.done and not enabled[0]
    if not svelte:
        assert reloaded[-1].fields == ['pending user edit', '[sound:sentence.mp3]']
    reload.resolve(True)
    assert pending.done and pending.value.get('error') is None
    assert enabled[0] and owner.runtime.mw._background_op_count == 0
    assert not owner.runtime.gui.updating_editors


def test_failed_editor_save_preserves_input_and_does_not_write_or_reload(editing):
    owner, service, drain, work, note, _, _, enabled, save, _, reloaded = editing
    pending = update(owner, note.id, 'legacy')
    save.resolve(error=ApiError('ioError', 'Editor did not respond.'))
    drain()
    assert pending.value == {'result': None, 'error': 'Editor did not respond.'}
    assert service.col.get_note(note.id).fields == ['original', 'answer']
    assert enabled[0] and not work and not reloaded
    assert owner.runtime.mw._background_op_count == 0


@pytest.mark.parametrize('change', ['session', 'note', 'second-editor'])
def test_editor_changes_before_save_completion_do_not_admit_write(editing, monkeypatch, change):
    import aqt
    owner, service, drain, work, note, editor, _, enabled, save, _, reloaded = editing
    pending = update(owner, note.id, 'legacy')
    if change == 'session':
        owner.runtime.invalidate_session()
    elif change == 'note':
        editor.note = None
    else:
        monkeypatch.setitem(aqt.dialogs._dialogs, 'Browser', (None, SimpleNamespace(
            editor=SimpleNamespace(note=note))))
    save.resolve(True)
    drain()
    assert pending.value['error'] and enabled[0] and not work and not reloaded
    assert service.col.get_note(note.id).fields == ['original', 'answer']
    assert owner.runtime.mw._background_op_count == 0


def test_overlapping_update_is_rejected_without_replaying_write(editing):
    owner, service, drain, work, note, _, _, enabled, save, reload, _ = editing
    first = update(owner, note.id, 'legacy')
    second = update(owner, note.id, 'legacy', {'Front': 'must not run'})
    assert second.value['error'] == 'An API update is already synchronizing this note editor.'
    assert not enabled[0] and not work
    save.resolve(True)
    drain()
    reload.resolve(True)
    assert first.value == {'result': None, 'error': None}
    assert service.col.get_note(note.id).fields == ['original', '[sound:sentence.mp3]']


def test_invalid_update_releases_editor_after_native_failure(editing):
    owner, service, drain, _, note, _, _, enabled, save, reload, _ = editing
    pending = update(owner, note.id, 'v1', {'Missing': 'invalid'})
    save.resolve(True)
    drain()
    assert not enabled[0] and not pending.done
    reload.resolve(True)
    assert pending.value['error']['data']['type'] == 'validationFailed'
    assert service.col.get_note(note.id).fields == ['original', 'answer']
    assert enabled[0] and owner.runtime.mw._background_op_count == 0


def test_legacy_tag_update_uses_the_same_editor_coordination(editing):
    owner, service, drain, _, note, _, _, enabled, save, reload, reloaded = editing
    pending = owner.handler_async({'action': 'addTags', 'version': 6,
                                   'params': {'notes': [note.id], 'tags': 'asbplayer'}})
    note.tags = ['pending-user-tag']
    service.col.update_note(note)
    save.resolve(True)
    drain()
    reload.resolve(True)
    assert pending.value == {'result': None, 'error': None}
    assert set(service.col.get_note(note.id).tags) == {'pending-user-tag', 'asbplayer'}
    assert set(reloaded[-1].tags) == {'pending-user-tag', 'asbplayer'}
    assert enabled[0]


def test_multiple_editors_keep_the_existing_conflict_guard(editing, monkeypatch):
    import aqt
    owner, service, drain, work, note, _, _, enabled, save, _, _ = editing
    monkeypatch.setitem(aqt.dialogs._dialogs, 'Browser', (None, SimpleNamespace(
        editor=SimpleNamespace(note=note))))
    pending = update(owner, note.id, 'legacy')
    drain()
    assert pending.value['error'].startswith('Close the affected editor')
    assert enabled[0] and not work and not save.done
    assert service.col.get_note(note.id).fields == ['original', 'answer']


def test_editor_closed_after_write_does_not_turn_success_into_a_retry(editing):
    owner, service, drain, _, note, editor, _, enabled, save, _, reloaded = editing
    pending = update(owner, note.id, 'legacy')
    save.resolve(True)
    editor.note = None
    drain()
    assert pending.value == {'result': None, 'error': None}
    assert service.col.get_note(note.id)['Back'] == '[sound:sentence.mp3]'
    assert enabled[0] and not reloaded
    assert owner.runtime.mw._background_op_count == 0


def test_refresh_failure_blocks_stale_input_and_reports_the_applied_write(editing):
    owner, service, drain, _, note, editor, _, enabled, save, reload, _ = editing
    pending = update(owner, note.id, 'legacy')
    save.resolve(True)
    drain()
    reload.resolve(error=ApiError('ioError', 'Editor refresh failed.'))
    assert pending.value == {'result': None, 'error':
                            'The editor could not refresh after the API write. Close and reopen it before editing.'}
    assert service.col.get_note(note.id)['Back'] == '[sound:sentence.mp3]'
    assert enabled[0] and owner.runtime.mw._background_op_count == 0
    assert not editor.widget.enabled


@pytest.mark.parametrize('dialect', ['legacy', 'v1'])
def test_lifecycle_conflict_does_not_start_saving_an_editor(editing, dialect):
    owner, service, drain, work, note, _, _, enabled, save, _, _ = editing
    owner.runtime.lifecycle_active = True
    pending = update(owner, note.id, dialect)
    drain()
    assert pending.value['error'] and enabled[0] and not save.done and not work
    assert service.col.get_note(note.id).fields == ['original', 'answer']
