from postgate import Code, Config, Gate, PostInfo, TrustVerifier, Verifier, VerifierUnavailable

from .conftest import post

A = "First post: a checklist for shipping a small tool without breaking anything."
B = "Second post: why retries without verification create duplicate posts."


# ------------------------------------------------------------------- wait / gap
def test_first_post_ok(gate):
    r = gate.before(A)
    assert r.code == Code.OK and not r.data["retry"]


def test_wait_enforces_gap(gate, ledger, clock):
    post(gate, ledger, clock, A)
    clock.advance(30)
    r = gate.before(B)
    assert r.code == Code.WAIT
    assert 59 <= r.wait_seconds <= 61
    assert "WAIT_SECONDS=" in r.message
    clock.advance(r.wait_seconds)
    assert gate.before(B).ok


def test_wait_counts_in_flight_attempts(gate, clock):
    """A post that passed `before` but isn't recorded yet still counts toward the gap."""
    assert gate.before(A).ok
    clock.advance(10)
    assert gate.before(B).code == Code.WAIT


def test_wait_does_not_record_attempt(gate, ledger, clock):
    post(gate, ledger, clock, A)
    clock.advance(5)
    assert gate.before(B).code == Code.WAIT
    clock.advance(100)
    assert gate.before(B).ok  # not RETRY_BLOCKED: the WAIT left no attempt behind


def test_custom_gap(cfg, ledger, clock):
    cfg.min_gap_seconds = 5
    g = Gate(cfg, verifier=ledger, clock=clock)
    post(g, ledger, clock, A)
    clock.advance(6)
    assert g.before(B).ok


# ---------------------------------------------------------------- duplicates
def test_duplicate_blocked(gate, ledger, clock):
    post(gate, ledger, clock, A)
    clock.advance(3600)
    r = gate.before("  " + A.upper() + " ")
    assert r.code == Code.DUPLICATE


def test_near_duplicate_blocked(gate, ledger, clock):
    post(gate, ledger, clock, A)
    clock.advance(600)
    assert gate.before(A.replace("small", "tiny")).code == Code.DUPLICATE


def test_duplicate_expires_after_window(gate, ledger, clock):
    post(gate, ledger, clock, A)
    clock.advance(25 * 3600)
    assert gate.before(A).ok


def test_empty_text_is_usage_error(gate):
    assert gate.before("   ").code == Code.USAGE


# ----------------------------------------------------------------------- halt
def test_manual_halt_blocks_until_resume(gate):
    gate.halt("maintenance")
    r = gate.before(A)
    assert r.code == Code.HALTED and "maintenance" in r.message
    assert gate.status().code == Code.HALTED
    gate.resume()
    assert gate.before(A).ok


def test_halt_persists_across_instances(cfg, ledger, clock):
    Gate(cfg, verifier=ledger, clock=clock).halt("x")
    assert Gate(cfg, verifier=ledger, clock=clock).before(A).code == Code.HALTED


def test_verify_failure_sets_halt(gate, clock):
    assert gate.before(A).ok
    r = gate.after("does-not-exist", A)
    assert r.code == Code.VERIFY_FAILED
    clock.advance(1000)
    assert gate.before(B).code == Code.HALTED


def test_halt_on_failure_can_be_disabled(cfg, ledger, clock):
    cfg.halt_on_failure = False
    g = Gate(cfg, verifier=ledger, clock=clock)
    g.before(A)
    assert g.after("nope", A).code == Code.VERIFY_FAILED
    clock.advance(1000)
    assert g.before(B).ok


# ------------------------------------------------------------- after / verify
def test_after_records_and_verifies_one_copy(gate, ledger, clock):
    assert gate.before(A).ok
    pid = ledger.publish(A, created_ts=clock())
    r = gate.after(pid, A)
    assert r.ok and r.data["copies"] == [pid] and r.data["text_verified"]


def test_after_is_idempotent(gate, ledger, clock):
    pid = post(gate, ledger, clock, A)
    assert gate.after(pid, A).ok


def test_after_detects_two_copies(gate, ledger, clock):
    """The platform shows the post twice (e.g. a 'failed' click that actually worked)."""
    assert gate.before(A).ok
    p1 = ledger.publish(A, created_ts=clock())
    clock.advance(20)
    p2 = ledger.publish(A, created_ts=clock())
    r = gate.after(p2, A)
    assert r.code == Code.MULTIPLE_COPIES
    assert sorted(r.data["copies"]) == sorted([p1, p2])
    clock.advance(1000)
    assert gate.before(B).code == Code.HALTED


def test_after_text_mismatch(gate, ledger, clock):
    gate.before(A)
    pid = ledger.publish("something else entirely", created_ts=clock())
    r = gate.after(pid, A)
    assert r.code == Code.VERIFY_FAILED and "text mismatch" in r.message


def test_after_wrong_parent(gate, ledger, clock):
    gate.before(A, parent="p-1")
    pid = ledger.publish(A, parent="p-2", created_ts=clock())
    r = gate.after(pid, A)  # parent taken from the `before` attempt
    assert r.code == Code.VERIFY_FAILED and "expected 'p-1'" in r.message


def test_after_reply_to_url_parent(gate, ledger, clock):
    gate.before(A, parent="https://example.com/posts/p-1")
    pid = ledger.publish(A, parent="p-1", created_ts=clock())
    assert gate.after(f"https://example.com/posts/{pid}", A).ok


def test_after_wrong_author(gate, ledger, clock):
    gate.before(A)
    ledger.author = "someone-else"
    pid = ledger.publish(A, created_ts=clock())
    r = gate.after(pid, A)
    assert r.code == Code.VERIFY_FAILED and "author" in r.message


def test_after_empty_ref_is_usage_not_halt(gate):
    gate.before(A)
    assert gate.after("", A).code == Code.USAGE
    assert gate.status().code == Code.OK


class DownVerifier(Verifier):
    name = "down"

    def fetch(self, post_id):
        raise VerifierUnavailable("timeout")


def test_after_verifier_unavailable_halts(cfg, clock):
    g = Gate(cfg, verifier=DownVerifier(), clock=clock)
    g.before(A)
    r = g.after("123", A)
    assert r.code == Code.UNAVAILABLE
    assert g.status().code == Code.HALTED


def test_trust_verifier_records_without_text_check(cfg, clock):
    g = Gate(cfg, verifier=TrustVerifier(), clock=clock)
    assert g.before(A).ok
    r = g.after("abc", A)
    assert r.ok and not r.data["text_verified"]
    clock.advance(200)
    assert g.before(A).code == Code.DUPLICATE  # still protects against re-posting


def test_after_counts_copies_from_state_without_listing(cfg, clock):
    g = Gate(cfg, verifier=TrustVerifier(), clock=clock)
    g.before(A)
    g.after("one", A)
    # Something bypassed `before` and posted again; `after` still notices.
    clock.advance(200)
    assert g.after("two", A).code == Code.MULTIPLE_COPIES


# ----------------------------------------------- false failure: text vanished
def test_after_without_text_uses_pending_attempt(gate, ledger, clock):
    """The temp file holding the text vanished before `after` ran.

    The post did go out. Treating that as a failure would trigger a retry and
    a double post. postgate falls back to the text recorded by `before`.
    """
    assert gate.before(A, parent="p-9").ok
    pid = ledger.publish(A, parent="p-9", created_ts=clock())
    r = gate.after(pid, None)
    assert r.ok, r.message
    assert "used the text recorded by `before`" in r.message
    clock.advance(500)
    assert gate.before(A).code == Code.DUPLICATE
    assert gate.status().code == Code.OK


def test_after_without_text_and_no_attempt_is_usage(gate):
    assert gate.after("x", None).code == Code.USAGE


# --------------------------------------------------- retry discipline / lookup
def test_unresolved_attempt_blocks_retry(gate, clock):
    assert gate.before(A).ok
    clock.advance(200)
    r = gate.before(A)
    assert r.code == Code.RETRY_BLOCKED and "lookup" in r.message


def test_lookup_found_means_do_not_retry(gate, ledger, clock):
    gate.before(A)
    pid = ledger.publish(A, created_ts=clock())  # poster said "failed" but it went out
    r = gate.lookup(A)
    assert r.code == Code.OK and r.data["found"][0]["id"] == pid
    assert gate.after(pid, A).ok


def test_one_retry_only_after_confirmed_absence(gate, ledger, clock):
    assert gate.before(A).ok
    r = gate.lookup(A)  # local ledger can list -> absence is confirmed
    assert r.code == Code.NOT_FOUND and r.data["absence_confirmed"]
    r = gate.before(A)  # absent-confirmed attempts don't count toward the gap
    assert r.ok and r.data["retry"]
    clock.advance(200)
    assert gate.lookup(A).code == Code.NOT_FOUND
    r = gate.before(A)
    assert r.code == Code.RETRY_BLOCKED and "retry limit" in r.message
    assert gate.status().code == Code.HALTED


def test_lookup_without_listing_requires_manual_confirmation(cfg, clock):
    g = Gate(cfg, verifier=TrustVerifier(), clock=clock)
    g.before(A)
    r = g.lookup(A)
    assert r.code == Code.NOT_FOUND and not r.data["absence_confirmed"]
    assert g.before(A).code == Code.RETRY_BLOCKED
    g.lookup(A, confirm_absent=True)
    assert g.before(A).ok


# ------------------------------------------------------------------- dupcheck
def test_dupcheck_clean(gate, ledger, clock):
    post(gate, ledger, clock, A)
    clock.advance(100)
    post(gate, ledger, clock, B)
    assert gate.dupcheck().ok


def test_dupcheck_finds_duplicates_on_platform(gate, ledger, clock):
    p1 = ledger.publish(A, created_ts=clock())
    ledger.publish(A + " (again)", created_ts=clock() + 5)
    clock.advance(60)
    r = gate.dupcheck()
    assert r.code == Code.DUPLICATE
    assert gate.status().code == Code.HALTED
    gate.resume()
    assert gate.dupcheck(ignore=[p1]).ok


def test_dupcheck_ignore_from_config(cfg, ledger, clock):
    p1 = ledger.publish(A, created_ts=clock())
    ledger.publish(A, created_ts=clock())
    cfg.ignore_ids = [p1]
    assert Gate(cfg, verifier=ledger, clock=clock).dupcheck().ok


def test_dupcheck_window(gate, ledger, clock):
    ledger.publish(A, created_ts=clock())
    ledger.publish(A, created_ts=clock())
    clock.advance(7 * 3600)
    assert gate.dupcheck(hours=6).ok


class ListingDown(Verifier):
    def fetch(self, post_id):
        return PostInfo(id=post_id, text=A)

    def recent(self, since_ts):
        raise VerifierUnavailable("rate limited")


def test_dupcheck_falls_back_to_state(cfg, clock):
    g = Gate(cfg, verifier=ListingDown(), clock=clock)
    r = g.dupcheck()
    assert r.ok and "local only" in r.message
