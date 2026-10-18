# postgate

**A small safety gate for bots that post.** Call it right before and right
after every post your bot makes — on X, Bluesky, Mastodon, Slack, Discord, a
forum, anywhere. It's one Python file per concern, stdlib only, with local JSON
state.

## The problem

Bots that post tend to fail in the same few ways:

- **Double posting.** The platform says "failed" (or the click seemed to do
  nothing, or the request timed out), but the post actually went out. The bot
  retries and now there are two.
- **Blind retries.** The bot retries without checking whether the first try
  landed, sometimes in a loop.
- **Spam-like bursts.** Several posts go out seconds apart, or the same text
  goes out twice in a day. Platforms rate-limit, down-rank or flag accounts for
  this.
- **False failures.** Something unrelated breaks *after* a successful post (a
  temp file holding the text gets cleaned up, a log write fails) and the run
  is recorded as a failed post, which brings back the retry problem.
- **Carrying on after something went wrong.** One bad post is recoverable.
  Ten more posted on top of a broken state usually isn't.

postgate turns the careful manual procedure into a tool that refuses to let
those happen:

1. **One send per check.** `before` says OK once, then records an *attempt*.
   Sending the same text again is blocked until the attempt is resolved.
2. **Verify, don't assume.** `after` asks the platform (through your verifier)
   whether the post exists with the right text, parent and author, and checks
   there is **exactly one** copy.
3. **At most one retry, and only after confirming absence.** If a send
   "fails", run `lookup`. Found means it went out, so record it and move on.
   Retrying is only unlocked once absence has been confirmed, and only
   `max_retries` times (default 1).
4. **Minimum gap.** By default 90 s between posts, counted from the last post
   or in-flight attempt.
5. **Near-duplicate blocking.** Text that matches something posted in the last
   24 h is refused (with case, whitespace, curly quotes, URLs and auto-inserted
   leading `@mentions` normalised away).
6. **Halt on failure.** Verification failures, multiple copies, duplicates
   found by `dupcheck` and an exhausted retry budget all set a **persistent
   halt flag**. Nothing posts until a human looks and runs `postgate resume`.

## Install

```bash
pip install .            # or just put the postgate/ folder on PYTHONPATH
postgate --help          # or: python3 -m postgate --help
```

Python 3.9+ (3.11+ for TOML config; JSON works everywhere). No dependencies.
File locking uses `fcntl` (Linux/macOS).

## Quick start

```bash
# 1. before every post: 0 = send ONCE, 2 = wait, anything else = stop
postgate before --text "Hello, world"
# OK: clear to send this post ONCE.

# 2. send it with your own code, which gives you back a URL or id

# 3. after every post: verify + record
postgate after --url https://example.com/posts/123 --text "Hello, world"
# OK: verified one copy of https://example.com/posts/123 and recorded it.

postgate before --text "Another post"
# WAIT: only 12s since the last post (need 90s). WAIT_SECONDS=79

postgate before --text "hello,   WORLD"
# STOP: duplicate - near-identical text was posted 2 min ago: ...
```

When your poster reports a failure:

```bash
postgate lookup --text "Hello, world"
#  exit 0  FOUND <url>  -> it went out. Do NOT retry; run `after` with that url.
#  exit 1  NOT_FOUND    -> if absence is confirmed, ONE retry is allowed:
postgate before --text "Hello, world"     # RETRY (absence confirmed): clear to send ONCE.
```

If your verifier can't list recent posts, `lookup` can't confirm absence by
itself. Check the platform by hand, then run
`postgate lookup --text "..." --confirm-absent`.

Other commands:

```bash
postgate dupcheck [--hours 6] [--ignore ID]   # run at the start and end of each round
postgate halt --reason "investigating"        # stop everything
postgate status                               # exit 6 while halted
postgate resume                               # clear the halt flag
```

Every command takes `--json` for machine-readable output.

## Exit codes

| code | name | meaning |
|---:|---|---|
| 0 | OK | `before`: send once. `after`: verified, one copy. `lookup`: found |
| 1 | NOT_FOUND | `lookup`: no matching post |
| 2 | WAIT | too soon: sleep `WAIT_SECONDS`, then run `before` again |
| 3 | DUPLICATE | near-identical text already posted / `dupcheck` found a pair |
| 4 | VERIFY_FAILED | post missing, or wrong text, parent or author |
| 5 | UNAVAILABLE | verifier couldn't reach the platform. Unknown, not failed: don't retry |
| 6 | HALTED | halt flag set: check by hand, then `postgate resume` |
| 7 | MULTIPLE_COPIES | more than one copy of the post exists (it's recorded; decide by hand) |
| 8 | RETRY_BLOCKED | earlier attempt unresolved (run `lookup`), or retry limit reached |
| 10 | QUEUE_EMPTY | `queue next`: nothing left |
| 64 | USAGE | bad input: empty text, unreadable `--text-file`, bad config |

**Rule of thumb: 0 = go, 2 = wait, anything else = stop.** postgate never
uses exit code 2 for usage errors, so a typo can't look like "wait".

## Queues

Work through a plan of posts with automatic thread chaining:

```text
# plan.txt: one post per line
A standalone post.
parent: https://example.com/posts/1001
A reply to post 1001 (parent: applies to the next line only).
thread: Part one.
thread: Part two (automatically replies to part one).
```

```bash
postgate queue load plan.txt
postgate queue next          # runs `before` for the next item, prints it as JSON (+ not_before)
postgate queue done <url>    # runs `after` for it and advances (only on success)
postgate queue skip --reason "already covered"
postgate queue status
```

JSON plans work too: `[{"text": "...", "parent": "...", "thread": false}]`.

## Verifiers

A verifier is how postgate asks the platform what actually happened:

```python
from postgate import Verifier, PostInfo, VerifierUnavailable

class MyVerifier(Verifier):
    name = "mine"

    def fetch(self, post_id):            # -> PostInfo, or None if it doesn't exist
        ...

    def recent(self, since_ts):          # -> [PostInfo, ...] (incl. replies), or None if you can't list
        ...
```

Raise `VerifierUnavailable` on timeouts, rate limits and 5xx errors. Override
`parse_ref` if your poster returns URLs in an unusual shape.

Bundled:

- `trust`: the default. No network and no checks: any returned id counts as
  posted. You still get gaps, duplicate blocking, retry discipline, queues and
  halt, but not content verification. postgate says so in its output.
- `local`: no network. Verifies against a JSON ledger file. Use it for dry
  runs, tests, and local or mock "platforms" (`LocalVerifier.publish()` simulates
  a post).
- `examples/x_verifier.py`: a stub for X that shows the shape. Fill in
  `fetch` / `recent` with your own API access. Keep credentials in environment
  variables, not in config.

Select one in config (`verifier = "local"` or `verifier = "mypkg.verify:MyVerifier"`
with constructor kwargs in `[verifier_options]`) or with `--verifier`.

## Configuration

`--config FILE`, `$POSTGATE_CONFIG`, or `./postgate.toml` / `./postgate.json`.
All keys are optional. See [`examples/postgate.toml`](examples/postgate.toml).
The main ones are `min_gap_seconds` (90), `dup_window_hours` (24),
`max_retries` (1), `similarity` (0.9), `halt_on_failure` (true), `author`,
`ignore_ids`, `verifier`, and `state_path`. `$POSTGATE_STATE` overrides the
state path.

## Integration example

Library:

```python
from postgate import Gate, Config, Code
import time

gate = Gate(Config(state_path="state/postgate.json", verifier="mybot.verify:MyVerifier"))

def send(text, parent=None):
    while (r := gate.before(text, parent)).code == Code.WAIT:
        time.sleep(r.wait_seconds)
    if not r.ok:
        raise SystemExit(r.message)              # duplicate, halted, retry blocked...
    try:
        ref = my_platform_client.post(text, reply_to=parent)
    except Exception:
        found = gate.lookup(text)                # did it go out anyway?
        raise SystemExit(found.message)          # never retry blind
    r = gate.after(ref, text, parent)
    if not r.ok:
        raise SystemExit(r.message)              # halt flag is now set
```

Shell: see [`examples/post_loop.sh`](examples/post_loop.sh), which drives a
queue around any command that prints the new post's URL. There's also a
runnable library demo in [`examples/bot_example.py`](examples/bot_example.py).

### Notes for UI-driven bots (browser automation)

If your bot posts by clicking buttons, some apps ignore the first click.
The safe pattern, enforced by postgate between posts:

1. Click send **once**. Wait.
2. If the text is still in the composer, check *somewhere else* (a separate
   tab or the API) that the post isn't there. Then click once more. Never a
   third time.
3. Wait out the gap, then run `after` and confirm exactly one copy.
4. On anything unexpected, stop. Don't retry blind.

## State

Everything lives in one locked JSON file (`state_path`): recorded posts
(pruned after `retention_days`), pending attempts, and the halt flag. Several
processes on one machine can share it safely. A corrupt state file is an error,
never silently reset, because resetting would forget what was already posted.

## Development

```bash
pip install -e '.[test]'
pytest
```

## License

MIT. See [LICENSE](LICENSE).
