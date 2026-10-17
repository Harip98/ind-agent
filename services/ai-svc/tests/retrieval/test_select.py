from dataclasses import replace

from copilot.retrieval import Hit, select_hits


def _hit(section_id, score, current=True):
    doc, rest = section_id.split("@v")
    version, no = rest.split("#")
    return Hit(f"{section_id}/c0", section_id, doc, int(version), no, "h", "t", "text",
               "_shared", current, score)


def test_one_hit_per_section_best_chunk_wins():
    a = _hit("D@v2#1", 0.9)
    b = replace(a, chunk_id="D@v2#1/c1", score=0.5)
    assert [h.chunk_id for h in select_hits([b, a], k=5)] == ["D@v2#1/c0"]


def test_superseded_twin_is_dropped():
    hits = select_hits([_hit("D@v1#4.1", 0.95, current=False), _hit("D@v2#4.1", 0.9)], k=5)
    assert [h.section_id for h in hits] == ["D@v2#4.1"]
    assert not hits[0].stale


def test_superseded_only_match_is_flagged_stale():
    hits = select_hits([_hit("D@v1#5.3", 0.95, current=False), _hit("E@v1#2", 0.4)], k=5)
    assert [(h.section_id, h.stale) for h in hits] == [("D@v1#5.3", True), ("E@v1#2", False)]


def test_weak_superseded_hit_is_dropped():
    hits = select_hits([_hit("D@v1#5.3", 0.3, current=False), _hit("E@v1#2", 0.4)], k=5)
    assert [h.section_id for h in hits] == ["E@v1#2"]


def test_k_limits_results():
    assert len(select_hits([_hit(f"D@v1#{i}", 1 - i / 10) for i in range(8)], k=5)) == 5


def test_renumbered_section_is_not_a_twin():
    """v1 §5.3 'Grease Top-Up' was removed; v2 reused 5.3 for 'Instrument Fault'."""
    stale = replace(_hit("D@v1#5.3", 0.9, current=False), heading="Grease Top-Up")
    current = replace(_hit("D@v2#5.3", 0.5), heading="Instrument Fault")
    hits = select_hits([stale, current], k=5)
    assert [(h.section_id, h.stale) for h in hits] == [("D@v1#5.3", True), ("D@v2#5.3", False)]
