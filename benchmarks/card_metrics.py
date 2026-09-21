"""Paired synthetic benchmark of production full-stats fallback vs native metrics.

Run with the rebuilt Anki core on PYTHONPATH. Creates/removes its own collection;
uses supported APIs only and never opens a user profile.
"""
import argparse
import hashlib
import json
import platform
import statistics
import tempfile
import time
from pathlib import Path

from anki.collection import Collection
from performance import ROOT, load_code, new_ac


def compare_values(left, right):
    assert len(left) == len(right)
    for old, new in zip(left, right):
        old, new = dict(old), dict(new)
        if 'prop:r' in old:
            r1, r2 = old.pop('prop:r'), new.pop('prop:r')
            if r1 is None or r2 is None:
                assert r1 == r2
            else:
                assert abs(r1 - r2) < 0.0001, (r1, r2)
        assert old == new, (old, new)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cards', type=int, default=1000)
    parser.add_argument('--reviews', type=int, default=20)
    parser.add_argument('--repeats', type=int, default=7)
    args = parser.parse_args()
    namespace = load_code()
    with tempfile.TemporaryDirectory(prefix='anki-current-metrics-') as directory:
        col = Collection(str(Path(directory) / 'synthetic.anki2'))
        try:
            assert hasattr(col, 'card_memory_metrics'), 'Rebuild Anki and set PYTHONPATH first'
            col.set_config('fsrs', True)
            col.set_config('fsrsPresetOverlay', {
                'presets': [{'id': 'addon:metrics-benchmark', 'name': 'FSRS-7 benchmark',
                             'fsrs_version': 'seven', 'params': [], 'desired_retention': 0.9,
                             'historical_retention': 0.9, 'ignore_revlogs_before_date': ''}],
                'rules': [{'search': 'deck:Default', 'preset_id': 'addon:metrics-benchmark'}],
            })
            model = col.models.by_name('Basic')
            ids = []
            for index in range(args.cards):
                note = col.new_note(model)
                note['Front'], note['Back'] = f'猫 {index}', f'cat {index}'
                col.add_note(note, 1)
                ids.append(note.cards()[0].id)
            for review in range(args.reviews):
                col._backend.grade_now(card_ids=ids, rating=3, card_options=[])
            # Keep current R away from the sensitive immediate post-review boundary.
            # Both paths read the same stored state and timestamp.
            for cid in ids:
                card = col.get_card(cid)
                card.last_review_time = int(time.time()) - 2 * 86400
                col.update_card(card)
            # Warm the original path, including its lazy timestamp initialization.
            for cid in ids:
                col.card_stats_data(cid)
            full, batch = new_ac(namespace), new_ac(namespace)
            full.collection = batch.collection = lambda: col
            # Isolate FSRS routing even when the core also supports compact details.
            full._cardsDetailsBatch = batch._cardsDetailsBatch = lambda *args: None
            full._cardMetrics = lambda cards, fields: {}
            cases = {
                'current S/D': dict(fields=['prop:s', 'prop:d']),
                'current R/S/D': dict(fields=['prop:r', 'prop:s', 'prop:d']),
                'reader fields and notes': dict(fields=['prop:r', 'prop:s', 'due', 'queue', 'type'],
                                                noteFields=['Front', 'Back']),
            }
            results = {}
            for label, params in cases.items():
                compare_values(full.cardsDetails(ids, **params), batch.cardsDetails(ids, **params))
                samples = {'full_stats': [], 'native_batch': []}
                for repeat in range(args.repeats):
                    order = [('full_stats', full), ('native_batch', batch)]
                    if repeat % 2:
                        order.reverse()
                    values = {}
                    for name, ac in order:
                        start = time.perf_counter()
                        values[name] = ac.cardsDetails(ids, **params)
                        samples[name].append((time.perf_counter() - start) * 1000)
                    compare_values(values['full_stats'], values['native_batch'])
                before, after = (statistics.median(samples[name]) for name in ['full_stats', 'native_batch'])
                results[label] = dict(full_stats_ms=before, native_batch_ms=after,
                                      reduction_percent=100 * (1 - after / before), samples_ms=samples)
                print(label, results[label], flush=True)
            data = dict(cards=args.cards, reviews_per_card=args.reviews, repeats=args.repeats,
                        fsrs_version=7, fsrs_parameter_count=len(col.card_stats_data(ids[0]).fsrs_params),
                        python=platform.python_version(), platform=platform.platform(),
                        source_sha256=hashlib.sha256((ROOT / 'plugin/__init__.py').read_bytes()).hexdigest(),
                        results=results,
                        note='Synthetic warm-cache component comparison, not ebook/UI load time; '
                             'S/D and non-time-dependent fields equal; R tolerance 0.0001 for elapsed time.')
            path = ROOT / 'benchmarks/results/card-metrics.json'
            path.write_text(json.dumps(data, indent=2) + '\n')
            print(path)
        finally:
            col.close()


if __name__ == '__main__':
    main()
