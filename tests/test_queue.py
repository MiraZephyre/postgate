import json

from postgate import Code, Queue, parse_plan

PLAN = """# a plan
A standalone post about testing small tools.
parent: https://example.com/posts/p-100
A reply that adds one concrete number to post p-100.
thread: Thread part one: what broke.
thread: Thread part two: how we fixed it.
"""


def test_parse_text_plan(tmp_path):
    p = tmp_path / "plan.txt"
    p.write_text(PLAN)
    items = parse_plan(p)
    assert [i["parent"] for i in items] == [None, "https://example.com/posts/p-100", None, "@thread"]
    assert items[2]["thread"] and items[3]["thread"]


def test_parse_json_plan(tmp_path):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps({"items": [{"text": "one"}, {"text": "two", "parent": "x"}]}))
    assert [i["text"] for i in parse_plan(p)] == ["one", "two"]


def _load(tmp_path, gate):
    p = tmp_path / "plan.txt"
    p.write_text(PLAN)
    q = Queue(gate)
    assert q.load(p).ok
    return q


def test_queue_full_run(tmp_path, gate, ledger, clock):
    q = _load(tmp_path, gate)
    posted = []
    for _ in range(4):
        r = q.next()
        if r.code == Code.WAIT:
            clock.advance(r.wait_seconds)
            r = q.next()
        assert r.ok, r.message
        parent = r.data["parent"]
        pid = ledger.publish(r.data["text"], parent=gate.verifier.parse_ref(parent) if parent else None,
                             created_ts=clock())
        posted.append(pid)
        d = q.done(pid)
        assert d.ok, d.message
    # the second thread part replied to the first one
    assert ledger.fetch(posted[3]).parent == posted[2]
    assert q.next().code == Code.QUEUE_EMPTY
    assert q.status().data == {"done": 4, "total": 4, "left": 0}


def test_queue_next_waits(tmp_path, gate, ledger, clock):
    q = _load(tmp_path, gate)
    r = q.next()
    q.done(ledger.publish(r.data["text"], created_ts=clock()))
    clock.advance(10)
    r = q.next()
    assert r.code == Code.WAIT and r.data["idx"] == 2 and r.data["not_before"]


def test_queue_done_failure_does_not_advance(tmp_path, gate):
    q = _load(tmp_path, gate)
    q.next()
    assert q.done("missing").code == Code.VERIFY_FAILED
    assert q.status().data["done"] == 0


def test_queue_peek_records_nothing(tmp_path, gate):
    q = _load(tmp_path, gate)
    assert q.next(peek=True).ok
    assert q.next(peek=True).ok
    assert q.next().ok


def test_queue_refuses_to_clobber(tmp_path, gate):
    q = _load(tmp_path, gate)
    p = tmp_path / "plan.txt"
    assert q.load(p).code == Code.USAGE
    assert q.load(p, force=True).ok


def test_queue_skip(tmp_path, gate):
    q = _load(tmp_path, gate)
    assert q.skip("already covered").ok
    assert q.next().data["idx"] == 2
