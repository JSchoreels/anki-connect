"""Extended legacy card contracts, executed inside upstream's serialized collection work."""
import inspect

from anki.errors import NotFoundError
from anki.scheduler_pb2 import CardAnswer


class ExtendedCards:
    def __init__(self, service):
        self.service = service

    def collection(self):
        return self.service.col

    def getCard(self, card_id):
        try:
            return self.collection().get_card(card_id)
        except NotFoundError:
            raise NotFoundError('Card was not found: {}'.format(card_id), None, None, None) from None

    def deckNameFromId(self, deck_id):
        deck = self.collection().decks.get(deck_id)
        if deck is None:
            raise Exception('deck was not found: {}'.format(deck_id))
        return deck['name']

    def findCards(self, query=None, fields=None, noteFields=None):
        if query is None:
            return []

        card_ids = list(map(int, self.collection().find_cards(query)))
        if fields is None and noteFields is None:
            return card_ids

        return self.cardsDetails(card_ids, fields, noteFields)


    def cardsDetails(self, cards, fields=None, noteFields=None):
        if cards is None:
            return []
        if not isinstance(cards, list):
            raise Exception('cards should be a list: {}'.format(cards))
        card_ids = list(map(int, cards))

        requested_fields = fields
        requested_note_fields = noteFields

        if requested_fields is None and requested_note_fields is None:
            return card_ids

        if requested_fields is not None and not isinstance(requested_fields, list):
            raise Exception('fields should be a list: {}'.format(requested_fields))

        if requested_note_fields is not None and not isinstance(requested_note_fields, list):
            raise Exception('noteFields should be a list: {}'.format(requested_note_fields))
        requested_note_fields_set = set(requested_note_fields) if requested_note_fields is not None else None

        for field in requested_fields or []:
            if field not in ['prop:r', 'prop:s', 'prop:d', 'due', 'queue', 'type', 'interval', 'reps']:
                raise Exception('unsupported field requested: {}'.format(field))

        batched_result = self._cardsDetailsBatch(card_ids, requested_fields, requested_note_fields)
        if batched_result is not None:
            return batched_result

        needs_stats = any(field in ['prop:r', 'prop:s', 'prop:d'] for field in requested_fields or [])
        metrics = self._cardMetrics(card_ids, requested_fields)
        result = []
        for cid in card_ids:
            card = self.getCard(cid)
            card_result = {'cardId': card.id}

            if requested_note_fields is not None:
                note = card.note()
                model = card.note_type()
                note_fields = {}
                for info in model['flds']:
                    order = info['ord']
                    name = info['name']
                    if requested_note_fields_set is not None and name not in requested_note_fields_set:
                        continue
                    note_fields[name] = {'value': note.fields[order], 'order': order}
                card_result['fields'] = note_fields

            if requested_fields:
                if needs_stats:
                    stats = self._cardStatsForFields(card, metrics)
                for field in requested_fields:
                    if field == 'prop:r':
                        card_result[field] = stats.fsrs_retrievability if stats.HasField('fsrs_retrievability') else None
                    elif field == 'prop:s':
                        card_result[field] = stats.memory_state.stability if stats.HasField('memory_state') else None
                    elif field == 'prop:d':
                        card_result[field] = stats.memory_state.difficulty if stats.HasField('memory_state') else None
                    elif field == 'due':
                        card_result[field] = card.due
                    elif field == 'queue':
                        card_result[field] = card.queue
                    elif field == 'type':
                        card_result[field] = card.type
                    elif field == 'interval':
                        card_result[field] = card.ivl
                    elif field == 'reps':
                        card_result[field] = card.reps
            result.append(card_result)

        return result


    def _cardsDetailsBatch(self, cards, fields, note_fields):
        if not cards:
            return []
        if not all(-(2 ** 63) <= cid < 2 ** 63 for cid in cards):
            return None
        get_details = getattr(self.collection(), 'card_details', None)
        if get_details is None:
            return None
        needs_stats = any(field in ['prop:r', 'prop:s', 'prop:d'] for field in fields or [])
        # Non-string names historically match no note fields; keep that behavior.
        selected_names = [name for name in note_fields if isinstance(name, str)] if note_fields is not None else None
        entries = {entry.card_id: entry for entry in get_details(
            list(dict.fromkeys(cards)), include_memory_state=needs_stats,
            include_retrievability='prop:r' in (fields or []), note_fields=selected_names
        )}
        # Preserve the legacy error order and missing-state initialization path.
        if any(cid not in entries or
               (note_fields is not None and not entries[cid].HasField('note_fields')) or
               (needs_stats and entries[cid].ctype != 0 and
                not entries[cid].metrics.HasField('memory_state')) for cid in cards):
            return None

        result = []
        for cid in cards:
            entry = entries[cid]
            row = {'cardId': cid}
            if note_fields is not None:
                row['fields'] = {field.name: {'value': field.value, 'order': field.order}
                                 for field in entry.note_fields.fields}
            values = {'due': entry.due, 'queue': entry.queue, 'type': entry.ctype,
                      'interval': entry.interval, 'reps': entry.reps}
            if needs_stats:
                stats = entry.metrics
                values.update({
                    'prop:r': stats.fsrs_retrievability if stats.HasField('fsrs_retrievability') else None,
                    'prop:s': stats.memory_state.stability if stats.HasField('memory_state') else None,
                    'prop:d': stats.memory_state.difficulty if stats.HasField('memory_state') else None,
                })
            row.update((field, values[field]) for field in fields or [])
            result.append(row)
        return result


    def _cardMetrics(self, cards, fields):
        if not fields or not any(field in ['prop:r', 'prop:s', 'prop:d'] for field in fields):
            return {}
        # Leave unusual inputs to the original per-card validation/error path.
        if not isinstance(cards, list) or not cards or not all(
            type(cid) is int and -(2 ** 63) <= cid < 2 ** 63 for cid in cards
        ):
            return {}
        get_metrics = getattr(self.collection(), 'card_memory_metrics', None)
        if get_metrics is None:
            return {}
        return {item.card_id: item for item in get_metrics(
            list(dict.fromkeys(cards)), include_retrievability='prop:r' in fields
        )}


    def _cardStatsForFields(self, card, metrics):
        stats = metrics.get(card.id)
        # Full stats initializes missing state on reviewed cards in newer Anki.
        # Retain that behavior and the legacy path on builds without batch metrics.
        if stats is None or (card.type != 0 and not stats.HasField('memory_state')):
            return self.collection().card_stats_data(card.id)
        return stats


    def cardsInfo(self, cards, fields=None, noteFields=None, retrieved_info_mode='ALL'):
        requested_fields = fields
        if requested_fields is not None:
            if not isinstance(requested_fields, list):
                raise Exception('fields should be a list: {}'.format(requested_fields))

            for field in requested_fields:
                if field not in ['prop:r', 'prop:s', 'prop:d']:
                    raise Exception('unsupported field requested: {}'.format(field))

        requested_note_fields = noteFields
        if requested_note_fields is not None and not isinstance(requested_note_fields, list):
            raise Exception('noteFields should be a list: {}'.format(requested_note_fields))
        requested_note_fields_set = set(requested_note_fields) if requested_note_fields is not None else None

        if not isinstance(retrieved_info_mode, str):
            raise Exception('retrieved_info_mode should be a string: {}'.format(retrieved_info_mode))
        info_mode = retrieved_info_mode.upper()
        if info_mode not in ['ALL', 'COMPACT', 'FIELDS_ONLY']:
            raise Exception('invalid retrieved_info_mode: {}'.format(info_mode))

        metrics = self._cardMetrics(cards, requested_fields)
        result = []
        for cid in cards:
            try:
                card = self.getCard(cid)
                model = card.note_type()
                note = card.note()
                note_fields = {}
                for info in model['flds']:
                    order = info['ord']
                    name = info['name']
                    if requested_note_fields_set is not None and name not in requested_note_fields_set:
                        continue
                    note_fields[name] = {'value': note.fields[order], 'order': order}

                card_result = {
                    'cardId': card.id,
                    'fields': note_fields,
                }

                if info_mode != 'FIELDS_ONLY':
                    card_result.update({
                        'fieldOrder': card.ord,
                        'ord': card.ord,
                        'deckName': self.deckNameFromId(card.did),
                        'factor': card.factor,
                        #This factor is 10 times the ease percentage,
                        # so an ease of 310% would be reported as 3100
                        'interval': card.ivl,
                        'note': card.nid,
                        'type': card.type,
                        'queue': card.queue,
                        'due': card.due,
                        'reps': card.reps,
                        'lapses': card.lapses,
                        'mod': card.mod,
                        'flags': card.flags,
                    })

                if info_mode == 'ALL':
                    states = self.collection()._backend.get_scheduling_states(card.id)
                    nextReviews = self.collection()._backend.describe_next_states(states)
                    card_result.update({
                        'question': card.question(),
                        'answer': card.answer(),
                        'modelName': model['name'],
                        'css': model['css'],
                        'left': card.left,
                        'nextReviews': list(nextReviews),
                    })

                if requested_fields:
                    stats = self._cardStatsForFields(card, metrics)
                    for field in requested_fields:
                        if field == 'prop:r':
                            card_result[field] = stats.fsrs_retrievability if stats.HasField('fsrs_retrievability') else None
                        elif field == 'prop:s':
                            card_result[field] = stats.memory_state.stability if stats.HasField('memory_state') else None
                        elif field == 'prop:d':
                            card_result[field] = stats.memory_state.difficulty if stats.HasField('memory_state') else None

                result.append(card_result)
            except NotFoundError:
                # Anki will give a NotFoundError if the card ID does not exist.
                # Best behavior is probably to add an 'empty card' to the
                # returned result, so that the items of the input and return
                # lists correspond.
                result.append({})

        return result


    def gradeNow(self, cards, ease):
        if ease < 1 or ease > 4:
            raise Exception('ease must be between 1 and 4')

        rating = {
            1: CardAnswer.AGAIN,
            2: CardAnswer.HARD,
            3: CardAnswer.GOOD,
            4: CardAnswer.EASY,
        }[ease]

        grade_now = self.collection()._backend.grade_now
        grade_now_params = {
            'card_ids': cards,
            'rating': rating,
        }

        if 'card_options' in inspect.signature(grade_now).parameters:
            grade_now_params['card_options'] = []

        self.service.change(grade_now(**grade_now_params))
        return True


    def repositionNewCards(self, orderedCardIds, startPosition, step, shift):
        if not isinstance(orderedCardIds, list) or len(orderedCardIds) == 0:
            raise Exception('orderedCardIds should be a non-empty list: {}'.format(orderedCardIds))
        if not all(isinstance(card_id, int) and not isinstance(card_id, bool) for card_id in orderedCardIds):
            raise Exception('orderedCardIds should contain only integers: {}'.format(orderedCardIds))
        if not isinstance(startPosition, int) or isinstance(startPosition, bool) or startPosition < 1:
            raise Exception('startPosition should be an integer >= 1: {}'.format(startPosition))
        if not isinstance(step, int) or isinstance(step, bool) or step < 1:
            raise Exception('step should be an integer >= 1: {}'.format(step))
        if not isinstance(shift, bool):
            raise Exception('shift should be a boolean: {}'.format(shift))

        deduped_card_ids = []
        seen_card_ids = set()
        for card_id in orderedCardIds:
            if card_id in seen_card_ids:
                continue
            seen_card_ids.add(card_id)
            deduped_card_ids.append(card_id)

        eligible_new_cards = []
        skipped_not_found = []
        skipped_not_new = []

        for card_id in deduped_card_ids:
            try:
                self.getCard(card_id)
            except NotFoundError:
                skipped_not_found.append(card_id)
                continue

            if self.findCards('cid:{} is:new'.format(card_id)):
                eligible_new_cards.append(card_id)
            else:
                skipped_not_new.append(card_id)

        if len(eligible_new_cards) > 0:
            self.service.change(self.collection()._backend.sort_cards(
                card_ids=eligible_new_cards,
                starting_from=startPosition,
                step_size=step,
                randomize=False,
                shift_existing=shift,
            ))

        return {
            'requested': len(orderedCardIds),
            'deduped': len(deduped_card_ids),
            'eligibleNew': len(eligible_new_cards),
            'repositioned': len(eligible_new_cards),
            'skippedNotFound': skipped_not_found,
            'skippedNotNew': skipped_not_new,
            'appliedStartPosition': startPosition,
            'appliedStep': step,
            'appliedShift': shift,
        }
