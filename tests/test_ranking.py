from dataclasses import replace
import math

import numpy as np
import pytest

from tinysearch.ranking import rank_results
from tinysearch.store import Store
from test_store import FakeEngine


def hit(name, kind, score):
    return {"name": name, "kind": kind, "score": score}


@pytest.mark.parametrize("photo_score,first", [
    (0.80, "photo"), (0.78, "photo"), (0.771, "photo"), (0.77, "photo"),
    (0.769, "video"), (0.40, "video"),
])
def test_photo_preference_is_limited_to_close_visual_matches(photo_score, first):
    video = hit("video", "video", 0.80)
    photo = hit("photo", "image", photo_score)
    inputs = [video, photo]
    ranked = rank_results(inputs)
    assert ranked[0]["name"] == first
    assert photo["score"] == photo_score and video["score"] == 0.80
    assert inputs == [video, photo]


def test_documents_and_sounds_keep_their_slots_and_visual_order_is_stable():
    inputs = [hit("strong video", "video", 0.90),
              hit("close video", "video", 0.80),
              hit("document", "document", 0.795),
              hit("sound", "audio", 0.79),
              hit("photo 1", "image", 0.78),
              hit("photo 2", "image", 0.775),
              hit("weak video", "video", 0.70)]
    assert [r["name"] for r in rank_results(inputs)] == [
        "strong video", "photo 1", "document", "sound",
        "photo 2", "close video", "weak video",
    ]
    assert rank_results(inputs, "video") == inputs
    assert rank_results(inputs, "image") == inputs


class QueryEngine(FakeEngine):
    def query(self, text):
        vector = np.zeros(self.dimensions, dtype=np.float32)
        vector[0] = 1
        return vector


def add_file(store, name, kind, scores):
    root = store.config.roots[0]
    path = root / name
    path.write_bytes(b"synthetic ranking fixture")
    with store.db:
        store.db.execute("INSERT INTO files VALUES (?,?,?,?,?,?)",
                         (str(path), str(root), kind, 0, path.stat().st_size, name))
        for position, score in enumerate(scores):
            vector = np.zeros(128, dtype="<f4")
            vector[0], vector[1] = score, math.sqrt(1 - score**2)
            start = position * 10 if kind == "video" else None
            store.db.execute("""INSERT INTO chunks
                (path,position,snippet,vector,start_seconds,end_seconds,modality)
                VALUES (?,?,?,?,?,?,?)""",
                (str(path), position, "fixture", vector.tobytes(), start,
                 start + 10 if start is not None else None,
                 "video" if kind == "video" else "image"))
    return path


def test_search_ranks_visible_files_before_limiting_and_keeps_best_video_segment(tmp_path):
    from tinysearch.config import Config
    root = tmp_path / "files"
    root.mkdir()
    config = Config((root,), tmp_path / "model", "test", tmp_path / "index.sqlite3",
                    dimensions=128)
    store = Store(config, QueryEngine())
    try:
        # Many windows still yield one result with the original best timestamp.
        video = add_file(store, "a.mov", "video", [0.50] * 20 + [0.80])
        photo = add_file(store, "b.jpg", "image", [0.78])
        missing = add_file(store, "c.jpg", "image", [0.99])
        missing.unlink()
        assert store.search("scene", limit=1)["results"][0]["path"] == str(photo)
        results = store.search("scene")["results"]
        assert len(results) == 2 and results[1]["path"] == str(video)
        assert results[1]["start_seconds"] == 200
        assert results[1]["end_seconds"] == 210
        assert results[1]["score"] == pytest.approx(0.80)
        assert store.search("scene", "video", 1)["results"][0]["path"] == str(video)
        # An excluded photo must not enter the reordered candidates either.
        store.config = replace(config, excluded_paths=(photo,))
        assert store.search("scene", limit=1)["results"][0]["path"] == str(video)
    finally:
        store.close()
