"""Opt-in, real-model regression check on macOS; no weights are downloaded.

Compare the new bridge against a previously released bridge using the same local
assets. Synthetic queries exercise every text length and packed document batches.
"""
import argparse
import json
from pathlib import Path
import selectors
import subprocess
import time

import numpy as np
from tokenizers import Tokenizer


class Worker:
    def __init__(self, helper, model):
        self.child = subprocess.Popen([str(helper), str(model), str(model), 'search'],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, text=True, bufsize=1)
        try:
            self.ready = self.read()
            assert self.ready.get('event') == 'ready', self.ready
        except BaseException:
            self.close()
            raise

    def read(self):
        with selectors.DefaultSelector() as selector:
            selector.register(self.child.stdout, selectors.EVENT_READ)
            if not selector.select(180):
                raise TimeoutError('Native inference timed out')
        value = json.loads(self.child.stdout.readline())
        assert 'error' not in value, value
        return value

    def send(self, op, **values):
        self.child.stdin.write(json.dumps(dict(op=op, **values)) + '\n')
        self.child.stdin.flush()
        return self.read()

    def close(self):
        self.child.stdin.close()
        try:
            self.child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.child.terminate()
            try:
                self.child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait()
        self.child.stdout.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--helper', type=Path, required=True)
    parser.add_argument('--baseline-helper', type=Path, required=True)
    args = parser.parse_args()
    prepared = subprocess.run([str(args.helper), str(args.model), str(args.model), 'prepare-search'],
                              check=True, capture_output=True, text=True, timeout=600)
    expected = {'pack_256', *(f'embed_{n}' for n in (32, 48, 64, 128, 256, 512))}
    reply = json.loads(prepared.stdout)
    assert reply['event'] == 'prepared' and set(reply['text_functions']) == expected
    tokenizer = Tokenizer.from_file(str(args.model / 'tokenizer.json'))
    queries = []
    for minimum, maximum in ((1, 32), (33, 48), (49, 64), (65, 128), (129, 256), (257, 512)):
        text = 'migration data '
        while len(tokenizer.encode('task: search result | query: ' + text).ids) < minimum:
            text += 'migration data '
        assert len(tokenizer.encode('task: search result | query: ' + text).ids) <= maximum
        queries.append((maximum, text))
    batches = [[], ['A database migration checklist.'],
               [f'Independent migration checklist number {i}.' for i in range(25)],
               ['Database migration. ', 'long technical document ' * 130, 'Apartment repairs. '],
               ['long technical document ' * 130]]
    vectors = {}
    times = {}
    for name, helper in (('baseline', args.baseline_helper), ('optimized', args.helper)):
        # Document indexing must retain pack_256 even in a text-only worker.
        worker = Worker(helper, args.model)
        try:
            if name == 'optimized':
                assert worker.ready['text_functions'] == []
            rows = []
            for batch in batches:
                result = worker.send('documents', texts=batch)
                rows.append(result['vectors'])
            if name == 'optimized':
                assert set(result['text_functions']) == {'pack_256', 'embed_512'}
        finally:
            worker.close()
        vectors[name] = rows
        worker = Worker(helper, args.model)
        try:
            loaded = set()
            for length, text in queries:
                start = time.perf_counter()
                result = worker.send('query', text=text)
                times[f'{name}_{length}'] = time.perf_counter() - start
                loaded.add(f'embed_{length}')
                if name == 'optimized':
                    assert set(result['text_functions']) == loaded
                vectors[name].append(result['vectors'])
        finally:
            worker.close()
    for old, new in zip(vectors['baseline'], vectors['optimized']):
        assert np.array_equal(np.asarray(old), np.asarray(new)), 'Text vectors changed'
    print(json.dumps(dict(exact_parity=True, query_lengths=6, document_batches=len(batches),
                          functions_available=7, request_seconds=times), indent=2))


if __name__ == '__main__':
    main()
