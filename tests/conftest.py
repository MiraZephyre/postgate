import os
import subprocess
import sys
from pathlib import Path

import pytest

from postgate import Config, Gate, LocalVerifier

ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def ledger(tmp_path):
    return LocalVerifier(str(tmp_path / "ledger.json"), author="examplebot")


@pytest.fixture
def cfg(tmp_path):
    return Config(state_path=str(tmp_path / "state.json"), author="examplebot")


@pytest.fixture
def gate(cfg, ledger, clock):
    return Gate(cfg, verifier=ledger, clock=clock)


def post(gate, ledger, clock, text, parent=None):
    """Simulate the full happy path: before -> publish -> after."""
    r = gate.before(text, parent)
    assert r.ok, r.message
    pid = ledger.publish(text, parent=parent, created_ts=clock())
    r = gate.after(pid, text, parent)
    assert r.ok, r.message
    return pid


@pytest.fixture
def cli(tmp_path):
    """Run the real CLI in a subprocess with a fixed clock."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["POSTGATE_STATE"] = str(tmp_path / "state.json")
    env.pop("POSTGATE_CONFIG", None)
    env["POSTGATE_NOW"] = "1800000000"

    def run(*args, now=None, cwd=None):
        e = dict(env)
        if now is not None:
            e["POSTGATE_NOW"] = str(now)
        p = subprocess.run([sys.executable, "-m", "postgate", *args], capture_output=True, text=True,
                           env=e, cwd=cwd or tmp_path)
        return p.returncode, p.stdout + p.stderr

    return run
