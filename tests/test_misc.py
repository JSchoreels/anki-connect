import os
from unittest.mock import Mock

import aqt
import plugin
import pytest

from plugin import AnkiConnect, _parse_anki_version
from conftest import ac, anki_connect_config_loaded, \
    set_up_test_deck_and_test_model_and_two_notes, \
    current_decks_and_models_etc_preserved, wait


def test_parse_anki_version_ignores_build_metadata():
    assert _parse_anki_version("25.09.4+fsrs7") == (25, 9, 4)


def test_export_package_uses_collection_api():
    collection = Mock()
    collection.decks.by_name.return_value = {"id": 123}
    anki_connect = AnkiConnect.__new__(AnkiConnect)
    anki_connect.collection = Mock(return_value=collection)

    assert anki_connect.exportPackage(
        deck="test_deck", path="/tmp/export.apkg", includeSched=True
    )

    kwargs = collection.export_anki_package.call_args.kwargs
    assert kwargs["out_path"] == "/tmp/export.apkg"
    assert kwargs["limit"].deck_id == 123
    assert kwargs["options"].with_scheduling
    assert kwargs["options"].with_deck_configs
    assert kwargs["options"].with_media
    assert kwargs["options"].legacy


def test_export_package_uses_pre_2312_collection_signature(monkeypatch):
    monkeypatch.setattr(plugin, "anki_version", (23, 10, 0))
    collection = Mock()
    collection.decks.by_name.return_value = {"id": 123}
    anki_connect = AnkiConnect.__new__(AnkiConnect)
    anki_connect.collection = Mock(return_value=collection)

    assert anki_connect.exportPackage(
        deck="test_deck", path="/tmp/export.apkg", includeSched=True
    )

    kwargs = collection.export_anki_package.call_args.kwargs
    assert kwargs["out_path"] == "/tmp/export.apkg"
    assert kwargs["limit"].deck_id == 123
    assert kwargs["with_scheduling"]
    assert kwargs["with_media"]
    assert kwargs["legacy_support"]
    assert "options" not in kwargs


def test_import_package_uses_collection_api():
    collection = Mock()
    anki_connect = AnkiConnect.__new__(AnkiConnect)
    anki_connect.collection = Mock(return_value=collection)
    anki_connect.startEditing = Mock()

    assert anki_connect.importPackage(path="/tmp/import.apkg")

    anki_connect.startEditing.assert_called_once_with()
    request = collection.import_anki_package.call_args.args[0]
    assert request.package_path == "/tmp/import.apkg"
    assert request.options.with_scheduling
    assert request.options.with_deck_configs


# version is retrieved from config
def test_version(session_with_profile_loaded):
    with anki_connect_config_loaded(
        session=session_with_profile_loaded,
        web_bind_port=0,
    ):
        assert ac.version() == 6


def test_reloadCollection(setup):
    ac.reloadCollection()


def test_apiReflect(setup):
    result = ac.apiReflect(
        scopes=["actions", "invalidType"],
        actions=["apiReflect", "invalidMethod"]
    )
    assert result == {
        "scopes": ["actions"],
        "actions": ["apiReflect"]
    }


class TestProfiles:
    def test_getProfiles(self, session_with_profile_loaded):
        result = ac.getProfiles()
        assert result == ["test_user"]

    # waiting a little while gets rid of the cryptic warning:
    #   Qt warning: QXcbConnection: XCB error: 8 (BadMatch), sequence: 658,
    #   resource id: 2097216, major code: 42 (SetInputFocus), minor code: 0
    def test_loadProfile(self, session_with_profile_loaded):
        aqt.mw.unloadProfileAndShowProfileManager()
        wait(0.1)
        ac.loadProfile(name="test_user")


class TestExportImport:
    # since Anki 2.1.50, exporting media for some wild reason
    # will change the current working directory, which then gets removed.
    # see `exporting.py`, ctrl-f `os.chdir(self.mediaDir)`
    @pytest.fixture(autouse=True)
    def current_working_directory_preserved(self):
        cwd = os.getcwd()
        yield

        try:
            os.getcwd()
        except FileNotFoundError:
            os.chdir(cwd)

    def test_exportPackage(self,  session_with_profile_loaded, setup):
        filename = session_with_profile_loaded.base + "/export.apkg"
        ac.exportPackage(deck="test_deck", path=filename)

    def test_importPackage(self, session_with_profile_loaded):
        filename = session_with_profile_loaded.base + "/export.apkg"

        with current_decks_and_models_etc_preserved():
            set_up_test_deck_and_test_model_and_two_notes()
            ac.exportPackage(deck="test_deck", path=filename)

        with current_decks_and_models_etc_preserved():
            assert "test_deck" not in ac.deckNames()
            ac.importPackage(path=filename)
            assert "test_deck" in ac.deckNames()
