"""Library usage with the no-network LocalVerifier standing in for a platform.

Run from the repo root:  python3 examples/bot_example.py
"""
import tempfile
import time
from pathlib import Path

from postgate import Code, Config, Gate, LocalVerifier

work = Path(tempfile.mkdtemp())
platform = LocalVerifier(str(work / "ledger.json"))            # pretend platform
gate = Gate(Config(state_path=str(work / "state.json"), min_gap_seconds=2), verifier=platform)


def send(text: str) -> None:
    while True:
        r = gate.before(text)
        if r.code == Code.WAIT:
            print(r.message)
            time.sleep(r.wait_seconds)
            continue
        if not r.ok:
            print("blocked:", r.message)
            return
        break
    post_id = platform.publish(text)                           # your real posting call goes here
    print(gate.after(post_id, text).message)


send("Hello from a careful bot.")
send("A second, different post.")                              # waits for the gap
send("Hello from a careful bot.")                              # blocked as a duplicate
