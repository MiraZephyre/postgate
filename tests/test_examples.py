import subprocess
import sys

import pytest

from postgate import load_config

from .conftest import ROOT


def test_example_config_loads():
    if sys.version_info < (3, 11):
        pytest.skip("TOML needs Python 3.11+")
    cfg = load_config(ROOT / "examples" / "postgate.toml")
    assert cfg.verifier == "local" and cfg.min_gap_seconds == 90


def test_x_verifier_stub_parses_refs():
    sys.path.insert(0, str(ROOT / "examples"))
    try:
        from x_verifier import XVerifier
    finally:
        sys.path.pop(0)
    v = XVerifier(username="example")
    assert v.parse_ref("https://x.com/example/status/1234567890?s=20") == "1234567890"
    assert v.parse_ref("1234567890") == "1234567890"
    assert v.parse_ref("not a post") == ""
    with pytest.raises(NotImplementedError):
        v.fetch("1")


def test_post_loop_script_is_valid_bash():
    assert subprocess.run(["bash", "-n", str(ROOT / "examples" / "post_loop.sh")]).returncode == 0
