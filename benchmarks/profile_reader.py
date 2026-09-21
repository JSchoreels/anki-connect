"""Profile cardsDetails and benchmark request-scoped note prefetching.

Run on an EXPORTED snapshot only. This file is an experimental benchmark, not
installed add-on code. Writes to the working collection use Anki's own API;
the optimization itself only issues SELECTs on the isolated collection copy.
"""

import argparse
import ast
import copy
import cProfile
import hashlib
import itertools
import json
import pstats
import shutil
import statistics
import sys
import time
import types
from pathlib import Path

from performance import ROOT, load_code, new_ac


def prefetch_notes(col, card_ids, requested_fields):
    # Preserve the original backend's exceptions for unusual/oversized card IDs.
    if any(not -(2**63) <= cid < 2**63 for cid in card_ids):
        return {}
    notes = {}
    for batch in itertools.batched(dict.fromkeys(card_ids), 999):
        marks = ','.join('?' for _ in batch)
        for nid, mid in col.db.all(
            f'SELECT n.id,n.mid FROM notes n JOIN cards c ON c.nid=n.id '
            f'WHERE c.id IN ({marks})', *batch
        ):
            notes[nid] = mid
    grouped = {}
    for nid, mid in notes.items():
        grouped.setdefault(mid, []).append(nid)
    rows = {}
    wanted = set(requested_fields)
    for mid, nids in grouped.items():
        model = col.models.get(mid)
        if model is None:
            continue
        indices = [field['ord'] for field in model['flds'] if field['name'] in wanted]
        if len(indices) >= 999:
            continue
        # Read only requested fields; transferring full HTML/audio fields through
        # Anki's JSON SQL bridge is slower than the original protobuf note API.
        projections = ','.join('field_at_index(flds, ?)' for _ in indices)
        for batch in itertools.batched(nids, 999 - len(indices)):
            marks = ','.join('?' for _ in batch)
            select = 'id' + (',' + projections if projections else '')
            for nid, *values in col.db.all(
                f'SELECT {select} FROM notes WHERE id IN ({marks})', *indices, *batch
            ):
                fields = [''] * len(model['flds'])
                for index, value in zip(indices, values):
                    fields[index] = value
                rows[nid] = (mid, fields)
    return rows


def prefetched_note_and_model(card, rows):
    entry = rows.get(card.nid)
    if entry is None:
        # Keep missing-note/error behavior in Anki's original APIs.
        return card.note(), card.note_type()
    mid, fields = entry
    return types.SimpleNamespace(fields=fields), card.col.models.get(mid)


def make_prefetched_details(ns):
    """Keep original validation, card access, stats calls and response building."""
    tree = ast.parse((ROOT / 'plugin/__init__.py').read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'AnkiConnect')
    fn = copy.deepcopy(next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'cardsDetails'))

    class Prefetch(ast.NodeTransformer):
        def visit_Assign(self, node):
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id == 'note':
                    return ast.parse('note, model = prefetched_note_and_model(card, prefetched)').body[0]
                if node.targets[0].id == 'model':
                    return None
                if node.targets[0].id == 'batched_result':
                    node.value = ast.Constant(value=None)
            return node

    fn = Prefetch().visit(fn)
    for index, statement in enumerate(fn.body):
        if isinstance(statement, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == 'result' for t in statement.targets
        ):
            fn.body[index:index] = ast.parse(
                'prefetched = prefetch_notes(self.collection(), card_ids, requested_note_fields) '
                'if requested_note_fields is not None and card_ids else {}'
            ).body
            break
    candidate = dict(ns, prefetch_notes=prefetch_notes,
                     prefetched_note_and_model=prefetched_note_and_model)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])),
                 'benchmark_prefetched_details', 'exec'), candidate)
    return candidate['cardsDetails']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--native-metrics', action='store_true',
                        help='Also compare forced full statistics against the native batch API')
    parser.add_argument('--anki-packages', type=Path,
                        help='Optional installed Anki app_packages directory (use matching Python ABI)')
    args = parser.parse_args()
    if args.anki_packages:
        sys.path.insert(0, str(args.anki_packages))
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / 'profile.anki2'
    shutil.copy2(args.snapshot, target)
    from anki.collection import Collection
    from anki import buildinfo
    col = Collection(str(target))
    try:
        ns = load_code()
        old, new = new_ac(ns), new_ac(ns)
        old.collection = new.collection = lambda: col
        old._cardsDetailsBatch = lambda *args: None
        new.cardsDetails = types.MethodType(make_prefetched_details(ns), new)
        variants = [('current', old), ('bulk_notes', new)]
        if args.native_metrics:
            assert hasattr(col, 'card_memory_metrics'), 'Native comparison requires the rebuilt core'
            legacy = new_ac(ns)
            legacy.collection = lambda: col
            legacy._cardMetrics = lambda cards, fields: {}
            legacy._cardsDetailsBatch = lambda *args: None
            variants = [('legacy', legacy), ('native', old), ('native_bulk_notes', new)]
            if hasattr(col, 'card_details'):
                batched = new_ac(ns)
                batched.collection = lambda: col
                variants.append(('native_details', batched))
        ids = list(col.find_cards('"deck:Japan::1. Vocabulary"'))
        options = dict(fields=['prop:r', 'prop:s', 'due', 'queue', 'type'],
                       noteFields=['Front', 'Front_Kana'])

        def fetch(ac):
            return [row for batch in itertools.batched(ids, 2500)
                    for row in ac.cardsDetails(list(batch), **options)]

        fetch(old)
        profiles = {}
        for label, ac in variants:
            profile = cProfile.Profile()
            profile.runcall(fetch, ac)
            profile.dump_stats(args.output / f'{label}.prof')
            with (args.output / f'{label}-profile.txt').open('w') as output:
                stats = pstats.Stats(profile, stream=output).sort_stats('cumulative')
                stats.print_stats(35)
            profiles[label] = {'total_seconds': stats.total_tt,
                              'functions': [{'file': file, 'line': line, 'name': name,
                                             'calls': value[1], 'self_seconds': value[2],
                                             'cumulative_seconds': value[3]}
                                            for (file, line, name), value in stats.stats.items()]}
        samples = {label: [] for label, _ in variants}
        outputs = {}
        for repeat in range(7):
            order = list(variants)
            if repeat % 2:
                order.reverse()
            for label, ac in order:
                start = time.perf_counter()
                result = fetch(ac)
                samples[label].append((time.perf_counter() - start) * 1000)
                outputs[label] = result
        reference = outputs[variants[0][0]]
        drift = max(abs(a['prop:r'] - b['prop:r']) for output in outputs.values()
                    for a, b in zip(reference, output) if a['prop:r'] is not None)
        hashes = {}
        for label, output in outputs.items():
            stable = [{k: v for k, v in row.items() if k != 'prop:r'} for row in output]
            hashes[label] = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
        assert len(set(hashes.values())) == 1, 'Stable response fields changed'
        assert drift < 0.0001, drift
        serialization = []
        for _ in range(7):
            start = time.perf_counter()
            encoded = json.dumps({'result': result, 'error': None}).encode()
            serialization.append((time.perf_counter() - start) * 1000)
        report = {'anki_version': buildinfo.version, 'anki_buildhash': buildinfo.buildhash,
                  'cards': len(ids), 'samples_ms': samples,
                  'medians_ms': {k: statistics.median(v) for k, v in samples.items()},
                  'stable_payload_hashes': hashes, 'max_retrievability_time_drift': drift,
                  'json_median_ms': statistics.median(serialization), 'response_bytes': len(encoded),
                  'profiles': profiles}
        (args.output / 'profile-results.json').write_text(json.dumps(report, indent=2))
        print(json.dumps({k: v for k, v in report.items() if k != 'profiles'}, indent=2), flush=True)
    finally:
        col.close()


if __name__ == '__main__':
    main()
