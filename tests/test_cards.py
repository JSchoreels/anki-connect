import pytest
from anki.errors import NotFoundError  # noqa

from conftest import ac


def test_findCards(setup):
    card_ids = ac.findCards(query="deck:test_deck")
    assert len(card_ids) == 4


def test_findCards_with_fields(setup):
    result = ac.findCards(query="deck:test_deck", fields=["prop:r", "prop:s"])

    assert [item["cardId"] for item in result] == setup.card_ids
    assert all("prop:r" in item and "prop:s" in item for item in result)


def test_findCards_with_noteFields(setup):
    result = ac.findCards(query="deck:test_deck", noteFields=["field1"])

    assert [item["cardId"] for item in result] == setup.card_ids
    assert all(set(item.keys()) == {"cardId", "fields"} for item in result)
    assert all(set(item["fields"]) == {"field1"} for item in result)


def test_cardsDetails_with_cards_param(setup):
    target_cards = setup.card_ids[:2]
    result = ac.cardsDetails(cards=target_cards, fields=["prop:r"], noteFields=["field1"])

    assert [item["cardId"] for item in result] == target_cards
    assert all("prop:r" in item for item in result)
    assert all(set(item["fields"]) == {"field1"} for item in result)


def test_cardsDetails_with_invalid_cards_type(setup):
    with pytest.raises(Exception, match="cards should be a list"):
        ac.cardsDetails(cards="123")


def test_findCards_with_extended_fields_and_noteFields(setup):
    result = ac.findCards(
        query="deck:test_deck",
        fields=["prop:r", "prop:s", "prop:d", "due", "queue", "type", "interval", "reps"],
        noteFields=["field1"],
    )

    assert [item["cardId"] for item in result] == setup.card_ids
    assert all(set(item["fields"]) == {"field1"} for item in result)
    assert all(
        "prop:r" in item
        and "prop:s" in item
        and "prop:d" in item
        and "due" in item
        and "queue" in item
        and "type" in item
        and "interval" in item
        and "reps" in item
        for item in result
    )


def test_findCards_with_invalid_field(setup):
    with pytest.raises(Exception, match="unsupported field requested"):
        ac.findCards(query="deck:test_deck", fields=["prop:invalid"])


def test_findCards_with_invalid_noteFields_type(setup):
    with pytest.raises(Exception, match="noteFields should be a list"):
        ac.findCards(query="deck:test_deck", noteFields="field1")


class TestEaseFactors:
    def test_setEaseFactors(self, setup):
        result = ac.setEaseFactors(cards=setup.card_ids, easeFactors=[4200] * 4)
        assert result == [True] * 4

    def test_setEaseFactors_with_invalid_card_id(self, setup):
        result = ac.setEaseFactors(cards=[123], easeFactors=[4200])
        assert result == [False]

    def test_getEaseFactors(self, setup):
        ac.setEaseFactors(cards=setup.card_ids, easeFactors=[4200] * 4)
        result = ac.getEaseFactors(cards=setup.card_ids)
        assert result == [4200] * 4

    def test_getEaseFactors_with_invalid_card_id(self, setup):
        assert ac.getEaseFactors(cards=[123]) == [None]


class TestSuspending:
    def test_suspend(self, setup):
        assert ac.suspend(cards=setup.card_ids) is True

    def test_suspend_fails_with_incorrect_id(self, setup):
        with pytest.raises(NotFoundError):
            assert ac.suspend(cards=[123])

    def test_areSuspended_returns_False_for_regular_cards(self, setup):
        result = ac.areSuspended(cards=setup.card_ids)
        assert result == [False] * 4

    def test_areSuspended_returns_True_for_suspended_cards(self, setup):
        ac.suspend(setup.card_ids)
        result = ac.areSuspended(cards=setup.card_ids)
        assert result == [True] * 4


def test_areDue_returns_True_for_new_cards(setup):
    result = ac.areDue(cards=setup.card_ids)
    assert result == [True] * 4


def test_getIntervals(setup):
    ac.getIntervals(cards=setup.card_ids, complete=False)
    ac.getIntervals(cards=setup.card_ids, complete=True)


def test_cardsToNotes(setup):
    result = ac.cardsToNotes(cards=setup.card_ids)
    assert {*result} == {setup.note1_id, setup.note2_id}


class TestCardInfo:
    def test_with_valid_ids(self, setup):
        result = ac.cardsInfo(cards=setup.card_ids)
        assert [item["cardId"] for item in result] == setup.card_ids
        assert all("question" in item and "answer" in item and "nextReviews" in item for item in result)

    def test_with_requested_props(self, setup):
        result = ac.cardsInfo(cards=setup.card_ids, fields=["prop:r", "prop:s", "prop:d"])
        assert [item["cardId"] for item in result] == setup.card_ids
        assert all("prop:r" in item and "prop:s" in item and "prop:d" in item for item in result)

    def test_with_retrieved_info_mode_compact(self, setup):
        result = ac.cardsInfo(
            cards=setup.card_ids,
            fields=["prop:r"],
            noteFields=["field1"],
            retrieved_info_mode="COMPACT",
        )
        assert [item["cardId"] for item in result] == setup.card_ids
        assert all("prop:r" in item for item in result)
        assert all(set(item["fields"]) == {"field1"} for item in result)
        assert all("question" not in item and "answer" not in item and "css" not in item and "nextReviews" not in item for item in result)
        assert all("deckName" in item and "interval" in item for item in result)

    def test_with_retrieved_info_mode_fields_only(self, setup):
        result = ac.cardsInfo(
            cards=setup.card_ids,
            fields=["prop:r"],
            noteFields=["field1"],
            retrieved_info_mode="FIELDS_ONLY",
        )
        assert [item["cardId"] for item in result] == setup.card_ids
        assert all(set(item) == {"cardId", "fields", "prop:r"} for item in result)
        assert all(set(item["fields"]) == {"field1"} for item in result)

    def test_with_retrieved_info_mode_all(self, setup):
        result = ac.cardsInfo(cards=setup.card_ids, retrieved_info_mode="ALL")
        assert [item["cardId"] for item in result] == setup.card_ids
        assert all("question" in item and "answer" in item and "nextReviews" in item for item in result)

    def test_with_invalid_requested_prop(self, setup):
        with pytest.raises(Exception, match="unsupported field requested"):
            ac.cardsInfo(cards=setup.card_ids, fields=["prop:x"])

    def test_with_invalid_noteFields_type(self, setup):
        with pytest.raises(Exception, match="noteFields should be a list"):
            ac.cardsInfo(cards=setup.card_ids, noteFields="field1")

    def test_with_invalid_retrieved_info_mode(self, setup):
        with pytest.raises(Exception, match="invalid retrieved_info_mode"):
            ac.cardsInfo(cards=setup.card_ids, retrieved_info_mode="SOMETHING")

    def test_with_invalid_retrieved_info_mode_type(self, setup):
        with pytest.raises(Exception, match="retrieved_info_mode should be a string"):
            ac.cardsInfo(cards=setup.card_ids, retrieved_info_mode=123)

    def test_with_incorrect_id(self, setup):
        result = ac.cardsInfo(cards=[123])
        assert result == [{}]


def test_forgetCards(setup):
    ac.forgetCards(cards=setup.card_ids)


def test_relearnCards(setup):
    ac.relearnCards(cards=setup.card_ids)


class TestRepositionNewCards:
    def test_order_preserved_and_duplicate_card_id_deduped(self, setup):
        target = [setup.card_ids[2], setup.card_ids[0], setup.card_ids[2], setup.card_ids[1]]
        result = ac.repositionNewCards(
            orderedCardIds=target,
            startPosition=1,
            step=1,
            shift=True,
        )

        assert result == {
            "requested": 4,
            "deduped": 3,
            "eligibleNew": 3,
            "repositioned": 3,
            "skippedNotFound": [],
            "skippedNotNew": [],
            "appliedStartPosition": 1,
            "appliedStep": 1,
            "appliedShift": True,
        }
        assert ac.getCard(setup.card_ids[2]).due == 1
        assert ac.getCard(setup.card_ids[0]).due == 2
        assert ac.getCard(setup.card_ids[1]).due == 3

    def test_distinct_cards_from_same_token_both_kept(self, setup):
        same_note_cards = setup.note1_card_ids
        assert len(same_note_cards) == 2

        result = ac.repositionNewCards(
            orderedCardIds=same_note_cards,
            startPosition=1,
            step=1,
            shift=True,
        )

        assert result["deduped"] == 2
        assert result["eligibleNew"] == 2
        assert result["repositioned"] == 2
        assert ac.getCard(same_note_cards[0]).due == 1
        assert ac.getCard(same_note_cards[1]).due == 2

    def test_non_new_cards_skipped(self, setup):
        not_new_card = setup.card_ids[0]
        new_card = setup.card_ids[1]
        ac.setDueDate(cards=[not_new_card], days="1")

        result = ac.repositionNewCards(
            orderedCardIds=[not_new_card, new_card],
            startPosition=1,
            step=1,
            shift=True,
        )

        assert result["requested"] == 2
        assert result["deduped"] == 2
        assert result["eligibleNew"] == 1
        assert result["repositioned"] == 1
        assert result["skippedNotFound"] == []
        assert result["skippedNotNew"] == [not_new_card]

    def test_shift_true_preserves_relative_order_of_untouched_cards(self, setup):
        selected = [setup.card_ids[2], setup.card_ids[0]]
        untouched = [card_id for card_id in setup.card_ids if card_id not in selected]

        before_due = {card_id: ac.getCard(card_id).due for card_id in setup.card_ids}
        before_untouched_order = sorted(untouched, key=lambda card_id: before_due[card_id])

        result = ac.repositionNewCards(
            orderedCardIds=selected,
            startPosition=1,
            step=1,
            shift=True,
        )
        assert result["repositioned"] == 2

        after_due = {card_id: ac.getCard(card_id).due for card_id in setup.card_ids}
        after_untouched_order = sorted(untouched, key=lambda card_id: after_due[card_id])

        assert after_untouched_order == before_untouched_order
        assert ac.getCard(selected[0]).due == 1
        assert ac.getCard(selected[1]).due == 2

    def test_invalid_params_return_explicit_errors(self, setup):
        with pytest.raises(Exception, match="orderedCardIds should be a non-empty list"):
            ac.repositionNewCards(orderedCardIds=[], startPosition=1, step=1, shift=True)
        with pytest.raises(Exception, match="orderedCardIds should contain only integers"):
            ac.repositionNewCards(orderedCardIds=[setup.card_ids[0], "x"], startPosition=1, step=1, shift=True)
        with pytest.raises(Exception, match="startPosition should be an integer >= 1"):
            ac.repositionNewCards(orderedCardIds=[setup.card_ids[0]], startPosition=0, step=1, shift=True)
        with pytest.raises(Exception, match="step should be an integer >= 1"):
            ac.repositionNewCards(orderedCardIds=[setup.card_ids[0]], startPosition=1, step=0, shift=True)
        with pytest.raises(Exception, match="shift should be a boolean"):
            ac.repositionNewCards(orderedCardIds=[setup.card_ids[0]], startPosition=1, step=1, shift=1)


class TestAnswerCards:
    def test_answerCards(self, setup):
        ac.scheduler().reset()
        answers = [
            {"cardId": a, "ease": b} for a, b in zip(setup.card_ids, [2, 1, 4, 3])
        ]
        result = ac.answerCards(answers)
        assert result == [True] * 4

    def test_answerCards_with_invalid_card_id(self, setup):
        ac.scheduler().reset()
        result = ac.answerCards([{"cardId": 123, "ease": 2}])
        assert result == [False]


class TestGradeNow:
    def test_gradeNow(self, setup):
        reviews_before = ac.cardReviews(deck="test_deck", startID=0)
        result = ac.gradeNow(cards=setup.card_ids[:2], ease=3)
        assert result is True

        reviews_after = ac.cardReviews(deck="test_deck", startID=0)
        assert len(reviews_after) == len(reviews_before) + 2

    def test_gradeNow_with_invalid_card_id(self, setup):
        with pytest.raises(NotFoundError):
            ac.gradeNow(cards=[123], ease=3)

    def test_gradeNow_with_invalid_ease(self, setup):
        with pytest.raises(Exception, match='ease must be between 1 and 4'):
            ac.gradeNow(cards=setup.card_ids[:1], ease=5)
