"""The shared name matcher: strongest overlap first, a tie decided for no
one and with no falling back to a weaker cluster, a tie an earlier match
resolved blocks nothing, and a name whose first choice was clearly taken
still gets its clear second choice."""

from __future__ import annotations

from bpp.db.face_name_matching import _match_named_clusters


def _match(named, old, new):
    remap: dict = {}
    _match_named_clusters(named, old, new, remap, set())
    return remap


def test_a_contested_cluster_is_decided_for_no_one_and_nobody_falls_back():
    remap = _match(
        {"A": "Alice", "B": "Bob"},
        {"A": {1, 2, 3, 4, 5, 6, 7, 8}, "B": {1, 2, 3, 4, 5}},
        {"X": {1, 2, 3, 4, 5}, "Y": {6, 7, 8}},
    )
    assert remap == {}, "Alice must not drop to Y, which may be a stranger"


def test_a_tie_an_earlier_match_resolved_blocks_nothing():
    remap = _match({7: "Alice", 3: "Bob"}, {7: {1, 2, 3}, 3: {1, 2}}, {7: {1, 2, 3}, 3: {1, 2}})
    assert remap == {7: 7, 3: 3}


def test_a_clear_second_choice_is_taken_when_the_first_is_clearly_someone_elses():
    remap = _match(
        {"L": "Leo", "M": "Mia"},
        {"L": {1, 2, 3, 4, 5}, "M": {6, 7, 8, 9, 10}},
        {0: {1, 2}, 1: {3, 4, 5, 6, 7, 8, 9, 10}},
    )
    assert remap == {"M": 1, "L": 0}


def test_two_names_with_equal_evidence_on_one_cluster_are_both_left_open():
    assert _match({0: "Alice", 1: "Bob"}, {0: {1, 2}, 1: {1, 2}}, {9: {1, 2}}) == {}


def _tag_match(old, new, min_jaccard=0.1):
    from bpp.db.face_name_matching import _match_tag_clusters

    remap: dict = {}
    _match_tag_clusters(set(old), old, new, remap, set(), min_jaccard)
    return remap


def test_an_unnamed_cluster_split_in_half_is_not_guessed_in_either_order():
    old = {8: {1, 2}}
    assert _tag_match(old, {0: {1}, 1: {2}}) == {}
    assert _tag_match(old, {1: {2}, 0: {1}}) == {}


def test_two_unnamed_clusters_tied_for_one_new_cluster_are_both_left_open():
    assert _tag_match({7: {1, 2}, 8: {1, 2}}, {0: {1, 2}}) == {}


def test_a_clear_unnamed_match_still_follows_its_faces():
    assert _tag_match({8: {1, 2, 3}}, {0: {1, 2}, 1: {3}}) == {8: 0}


def test_an_unnamed_cluster_exactly_on_the_threshold_is_still_matched():
    assert _tag_match({8: {1}}, {0: set(range(1, 11))}) == {8: 0}, "Jaccard 1/10 == 0.1"
