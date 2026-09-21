"""Pin observable behavior and compare optimized prototypes to current code."""

from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import jsonschema
import pytest

import performance as bench


@pytest.mark.parametrize("payload", [
    {"action": "version"},
    {"action": "version", "version": 4, "params": {}},
    {"action": "version", "version": 6.0, "extra": True},
    {"action": "multi", "params": {"actions": [{"action": "version"}]}},
])
def test_cached_validation_accepts_existing_requests(namespace, payload):
    schema = namespace["request_schema"]
    assert jsonschema.validate(payload, schema) is None
    assert bench.cached_validation(schema)(payload) is None


@pytest.mark.parametrize("payload", [
    None, [], 1, {}, {"action": ""}, {"action": 3},
    {"action": "version", "version": True},
    {"action": "version", "version": "6", "params": []},
    {"action": False, "version": 6.5, "params": None},
])
def test_cached_validation_preserves_error_selection_and_text(namespace, payload):
    schema = namespace["request_schema"]
    with pytest.raises(jsonschema.ValidationError) as original:
        jsonschema.validate(payload, schema)
    with pytest.raises(jsonschema.ValidationError) as optimized:
        bench.cached_validation(schema)(payload)
    assert str(optimized.value) == str(original.value)
    assert optimized.value.path == original.value.path
    assert optimized.value.schema_path == original.value.schema_path


@pytest.fixture(params=[False, True], ids=["current", "cached"])
def dispatch(namespace, request):
    ac = bench.new_ac(namespace)
    state = []

    @namespace["util"].api()
    def remember(self, value):
        state.append(value)
        return list(state)

    @namespace["util"].api((5, "previousName"), (6, "currentName"))
    def renamed(self):
        return "alias result"

    ac.remember = MethodType(remember, ac)
    ac.renamed = MethodType(renamed, ac)
    if request.param:
        bench.install_dispatch_cache(ac, namespace)
    return ac


@pytest.mark.parametrize("version, expected", [
    (None, 6), (0, 6), (4, 6), (5, {"result": 6, "error": None}),
    (6, {"result": 6, "error": None}), (6.0, {"result": 6, "error": None}),
])
def test_dispatch_response_version(dispatch, version, expected):
    request = {"action": "version"}
    if version is not None:
        request["version"] = version
    assert dispatch.handler(request) == expected


@pytest.mark.parametrize("version, name, expected", [
    (4, "renamed", "alias result"),
    (5, "previousName", {"result": "alias result", "error": None}),
    (6, "currentName", {"result": "alias result", "error": None}),
    (6, "previousName", {"result": None, "error": "unsupported action"}),
    (5, "currentName", {"result": None, "error": "unsupported action"}),
])
def test_dispatch_versioned_aliases(dispatch, version, name, expected):
    assert dispatch.handler({"action": name, "version": version}) == expected


def test_dispatch_checks_authentication_even_after_cache_warmup(namespace, dispatch):
    settings = namespace["util"].setting.__self__
    assert dispatch.handler({"action": "version", "version": 6})["result"] == 6
    settings["apiKey"] = "secret"
    assert dispatch.handler({"action": "version", "version": 6}) == {
        "result": None, "error": "valid api key must be provided"}
    assert dispatch.handler({"action": "version", "version": 6, "key": "secret"}) == {
        "result": 6, "error": None}


def test_multi_preserves_order_versions_and_continues_after_an_error(dispatch):
    assert dispatch.handler({"action": "multi", "version": 6, "params": {"actions": [
        {"action": "remember", "params": {"value": "first"}},
        {"action": "notAnAction", "version": 6},
        {"action": "multi", "version": 6, "params": {"actions": [
            {"action": "remember", "version": 6, "params": {"value": "second"}},
        ]}},
    ]}}) == {"result": [
        ["first"],
        {"result": None, "error": "unsupported action"},
        {"result": [{"result": ["first", "second"], "error": None}], "error": None},
    ], "error": None}


def test_multi_does_not_inherit_outer_key_or_version(namespace, dispatch):
    namespace["util"].setting.__self__["apiKey"] = "secret"
    assert dispatch.handler({"action": "multi", "version": 6, "key": "secret", "params": {"actions": [
        {"action": "version"},
        {"action": "version", "key": "secret"},
    ]}}) == {"result": [
        {"result": None, "error": "valid api key must be provided"}, 6,
    ], "error": None}


def test_dispatch_resolves_replaced_method_after_cache_warmup(dispatch):
    request = {"action": "version", "version": 6}
    assert dispatch.handler(request)["result"] == 6
    dispatch.version = MethodType(lambda self: 7, dispatch)
    dispatch.version.__func__.api = True
    assert dispatch.handler(request) == {"result": 7, "error": None}


def test_dispatch_bad_arguments_keep_exact_error(namespace):
    original, optimized = bench.new_ac(namespace), bench.new_ac(namespace)
    bench.install_dispatch_cache(optimized, namespace)
    request = {"action": "version", "version": 6, "params": {"unexpected": 1}}
    assert optimized.handler(request) == original.handler(request)
    assert "unexpected keyword argument 'unexpected'" in optimized.handler(request)["error"]


@pytest.mark.parametrize("cached", [False, True])
def test_lazy_details_remains_a_registered_api_action(namespace, cached):
    ac = bench.new_ac(namespace)
    ac.cardsDetails = MethodType(bench.make_lazy_details(namespace), ac)
    if cached:
        bench.install_dispatch_cache(ac, namespace)
    assert ac.handler({"action": "cardsDetails", "version": 6,
                       "params": {"cards": [], "fields": ["prop:r", "prop:s"]}}) == {
        "result": [], "error": None}


@pytest.mark.parametrize("raw", [
    b"GET / HTTP/1.1\r\n\r\n",
    b"OPTIONS / HTTP/1.1\r\nOrigin: http://localhost\r\n\r\n",
    b"POST / HTTP/1.1\r\ncOnTeNt-LeNgTh: 3\r\n\r\n" + "猫".encode(),
    b"POST / HTTP/1.1\r\nContent-Length: 1\r\nContent-Length: 2\r\nX-Empty\r\n\r\n{}",
    bench.request_bytes({"action": "multi", "params": {"actions": []}}),
])
def test_incremental_parser_matches_current_at_every_split(namespace, raw):
    expected, consumed = namespace["WebClient"](None, None).parseRequest(raw)
    assert consumed == len(raw)
    for split in range(1, len(raw)):
        parser = bench.IncrementalParser(namespace["WebRequest"])
        assert parser.feed(raw[:split]) is None
        assert vars(parser.feed(raw[split:])) == vars(expected)


def test_incremental_parser_counts_body_bytes_and_ignores_trailing_request(namespace):
    raw = b"POST / HTTP/1.1\r\nContent-Length: 3\r\n\r\n" + "猫".encode()
    parser = bench.IncrementalParser(namespace["WebRequest"])
    for byte in raw[:-1]:
        assert parser.feed(bytes([byte])) is None
    parsed = parser.feed(raw[-1:] + b"GET / HTTP/1.1\r\n\r\n")
    assert parsed.method == b"POST"
    assert parsed.body == "猫".encode()


@pytest.mark.parametrize("value", [b"invalid", b"1.5"])
def test_incremental_parser_preserves_invalid_length_error(namespace, value):
    raw = b"POST / HTTP/1.1\r\nContent-Length: " + value + b"\r\n\r\n"
    original = namespace["WebClient"](None, None)
    parser = bench.IncrementalParser(namespace["WebRequest"])
    assert bench.outcome(lambda: parser.feed(raw)) == bench.outcome(lambda: original.parseRequest(raw))
    with pytest.raises(ValueError):
        bench.IncrementalParser(namespace["WebRequest"]).feed(raw)


@pytest.mark.parametrize("table, key, getter, method", [
    ("cards", "cardId", "getCard", "cardsModTime"),
    ("notes", "noteId", "getNote", "notesModTime"),
])
def test_bulk_metadata_preserves_order_duplicates_missing_ids(namespace, table, key, getter, method):
    original = bench.new_ac(namespace)
    def get_record(identifier):
        if identifier == 999:
            original.raiseNotFoundError("missing")
        return SimpleNamespace(id=identifier, mod={10: 1700, 20: 1800}[identifier])
    setattr(original, getter, get_record)
    fallback = getattr(original, method)
    col = SimpleNamespace(db=Mock())
    col.db.all.return_value = [(10, 1700), (20, 1800)]  # Deliberately different database order.
    ids = [20, 999, 10, 20]
    expected = [{key: 20, "mod": 1800}, {}, {key: 10, "mod": 1700}, {key: 20, "mod": 1800}]
    assert fallback(ids) == expected
    assert bench.bulk_mod(col, table, ids, key, fallback) == expected
    assert ids == [20, 999, 10, 20]


def test_bulk_metadata_empty_input_does_not_query():
    col, fallback = SimpleNamespace(db=Mock()), Mock()
    assert bench.bulk_mod(col, "cards", [], "cardId", fallback) == []
    col.db.all.assert_not_called()
    fallback.assert_not_called()


def test_bulk_metadata_reassembles_across_query_boundary():
    col, fallback = SimpleNamespace(db=Mock()), Mock()
    col.db.all.side_effect = [[(7, 70)], [(1000, 10000)]]
    ids = list(range(1, 1001)) + [7, 1000]
    result = bench.bulk_mod(col, "cards", ids, "cardId", fallback)
    assert len(result) == 1002
    assert result[:6] == [{}] * 6
    assert result[6] == {"cardId": 7, "mod": 70}
    assert result[999:] == [{"cardId": 1000, "mod": 10000},
                           {"cardId": 7, "mod": 70}, {"cardId": 1000, "mod": 10000}]
    # A structural performance assertion: no per-record queries, no oversized binds.
    assert [len(call.args) - 1 for call in col.db.all.call_args_list] == [999, 1]
    fallback.assert_not_called()


@pytest.mark.parametrize("ids", [["123"], [None], [True], [1.5], [2**80], [-(2**80)]])
def test_bulk_metadata_delegates_unusual_ids_without_changing_error(ids):
    col = SimpleNamespace(db=Mock())
    fallback = Mock(side_effect=TypeError("original Anki error"))
    with pytest.raises(TypeError, match="^original Anki error$"):
        bench.bulk_mod(col, "cards", ids, "cardId", fallback)
    fallback.assert_called_once_with(ids)
    col.db.all.assert_not_called()


@pytest.fixture
def details(namespace):
    stats = SimpleNamespace(fsrs_retrievability=0.8, desired_retention=0.9,
                            memory_state=SimpleNamespace(stability=12.5, difficulty=7.0),
                            HasField=lambda name: True)
    card = SimpleNamespace(id=42, due=10, queue=2, type=2, ivl=6, reps=9,
                           note=Mock(return_value=SimpleNamespace(fields=["猫", "cat"])),
                           note_type=Mock(return_value={"flds": [{"name": "Front", "ord": 0},
                                                                 {"name": "Back", "ord": 1}]}))
    col = SimpleNamespace(card_stats_data=Mock(return_value=stats))
    original, optimized = bench.new_ac(namespace), bench.new_ac(namespace)
    for ac in (original, optimized):
        ac.getCard = Mock(return_value=card)
        ac.collection = lambda: col
    optimized.cardsDetails = MethodType(bench.make_lazy_details(namespace), optimized)
    return original, optimized, col


def test_basic_details_keep_values_and_do_not_calculate_stats(details):
    old, new, col = details
    fields = ["due", "queue", "type", "interval", "reps"]
    expected = [{"cardId": 42, "due": 10, "queue": 2, "type": 2, "interval": 6, "reps": 9}]
    assert old.cardsDetails([42], fields=fields) == expected
    col.card_stats_data.reset_mock()
    assert new.cardsDetails([42], fields=fields) == expected
    col.card_stats_data.assert_not_called()


def test_fsrs_details_preserve_values_and_only_calculate_once_per_card(details):
    old, new, col = details
    fields = ["prop:r", "prop:s", "prop:d", "due"]
    # prop:d is difficulty in both endpoints; desired retention is distinct.
    expected = [{"cardId": 42, "prop:r": 0.8, "prop:s": 12.5, "prop:d": 7.0, "due": 10}]
    assert old.cardsDetails([42], fields=fields) == expected
    col.card_stats_data.reset_mock()
    assert new.cardsDetails([42], fields=fields) == expected
    col.card_stats_data.assert_called_once_with(42)


def test_absent_fsrs_data_stays_null(details):
    old, new, col = details
    col.card_stats_data.return_value = SimpleNamespace(HasField=lambda name: False)
    for ac in (old, new):
        assert ac.cardsDetails([42], fields=["prop:r", "prop:s", "prop:d"]) == [
            {"cardId": 42, "prop:r": None, "prop:s": None, "prop:d": None}]


@pytest.mark.parametrize("fields", [None, []])
def test_note_field_filter_and_empty_stats_selection(details, fields):
    old, new, col = details
    for ac in (old, new):
        assert ac.cardsDetails([42], fields=fields, noteFields=["Front"]) == [
            {"cardId": 42, "fields": {"Front": {"value": "猫", "order": 0}}}]
    col.card_stats_data.assert_not_called()


@pytest.mark.parametrize("params", [
    {"cards": None}, {"cards": []}, {"cards": [42]},
    {"cards": "42"}, {"cards": [42], "fields": "due"},
    {"cards": [42], "fields": ["invalid"]}, {"cards": [42], "noteFields": "Front"},
])
def test_details_validation_and_early_returns_match(details, params):
    old, new, _ = details
    assert bench.outcome(lambda: new.cardsDetails(**params)) == bench.outcome(lambda: old.cardsDetails(**params))
    new.getCard.assert_not_called()


def test_required_stats_failures_are_still_reported(details):
    old, new, col = details
    col.card_stats_data.side_effect = RuntimeError("backend unavailable")
    for ac in (old, new):
        with pytest.raises(RuntimeError, match="^backend unavailable$"):
            ac.cardsDetails([42], fields=["prop:r"])
