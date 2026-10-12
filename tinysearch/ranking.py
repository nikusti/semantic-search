"""Small presentation preference without changing saved embedding scores."""

# Cosine similarity is not a probability. Prefer a photo only within this
# narrow score margin; a substantially better video still wins.
PHOTO_SCORE_MARGIN = 0.03


def rank_results(results, file_kind="all"):
    ordered = sorted(results, key=lambda result: -result["score"])
    if file_kind != "all":
        return ordered
    visual = iter(sorted(
        (result for result in ordered if result["kind"] in {"image", "video"}),
        key=lambda result: (-(result["score"] +
                              (PHOTO_SCORE_MARGIN if result["kind"] == "image" else 0)),
                            result["kind"] != "image"),
    ))
    # Only reorder photo/video slots. Document and sound results retain their
    # original positions, and each file keeps its best segment and raw score.
    return [next(visual) if result["kind"] in {"image", "video"} else result
            for result in ordered]
