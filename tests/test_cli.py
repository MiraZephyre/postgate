import json

T = "A reply that explains one concrete failure mode and its fix."
NOW = 1_800_000_000


def ledger_cfg(tmp_path):
    cfg = tmp_path / "postgate.toml"
    cfg.write_text(f'''min_gap_seconds = 90
verifier = "local"

[verifier_options]
ledger_path = "{tmp_path / 'ledger.json'}"
''')
    return str(cfg)


def publish(tmp_path, text, ts=NOW, parent=None, pid="p1"):
    path = tmp_path / "ledger.json"
    posts = json.loads(path.read_text())["posts"] if path.exists() else []
    posts.append({"id": pid, "text": text, "parent": parent, "created_ts": ts})
    path.write_text(json.dumps({"posts": posts}))
    return pid


def test_before_after_cycle_and_exit_codes(cli, tmp_path):
    c = ledger_cfg(tmp_path)
    rc, out = cli("--config", c, "before", "--text", T)
    assert rc == 0, out
    publish(tmp_path, T)
    rc, out = cli("--config", c, "after", "--url", "https://example.com/posts/p1", "--text", T)
    assert rc == 0, out
    rc, out = cli("--config", c, "before", "--text", "Different text entirely, about caching.", now=NOW + 30)
    assert rc == 2 and "WAIT_SECONDS=" in out
    rc, out = cli("--config", c, "before", "--text", T, now=NOW + 300)
    assert rc == 3, out


def test_missing_text_file_in_before_is_usage_not_failure(cli, tmp_path):
    rc, out = cli("before", "--text-file", str(tmp_path / "gone.txt"))
    assert rc == 64 and "NOT a post failure" in out
    rc, _ = cli("status")
    assert rc == 0  # not halted


def test_vanished_temp_file_false_failure(cli, tmp_path):
    """before reads a temp file, the file disappears, after must still succeed."""
    c = ledger_cfg(tmp_path)
    tmp = tmp_path / "current.txt"
    tmp.write_text(T)
    rc, out = cli("--config", c, "before", "--text-file", str(tmp), "--parent", "parent-7")
    assert rc == 0, out
    publish(tmp_path, T, parent="parent-7")
    tmp.unlink()  # cleaned up by something else
    rc, out = cli("--config", c, "after", "--id", "p1", "--text-file", str(tmp))
    assert rc == 0, out
    assert "warning: could not read text file" in out
    # and nothing will post it twice
    rc, out = cli("--config", c, "before", "--text", T, now=NOW + 600)
    assert rc == 3, out


def test_halt_resume_cli(cli):
    assert cli("halt", "--reason", "checking by hand")[0] == 0
    rc, out = cli("before", "--text", T)
    assert rc == 6 and "checking by hand" in out
    assert cli("status")[0] == 6
    assert cli("resume")[0] == 0
    assert cli("before", "--text", T)[0] == 0


def test_json_output(cli):
    rc, out = cli("--json", "before", "--text", T)
    d = json.loads(out)
    assert rc == 0 and d["status"] == "OK" and d["code"] == 0


def test_bad_usage_is_64_not_2(cli):
    rc, _ = cli("before")  # missing --text
    assert rc == 64
    rc, _ = cli("nonsense")
    assert rc == 64


def test_bad_config(cli, tmp_path):
    p = tmp_path / "bad.json"
    p.write_text('{"no_such_key": 1}')
    rc, out = cli("--config", str(p), "status")
    assert rc == 64 and "unknown config keys" in out


def test_queue_cli(cli, tmp_path):
    c = ledger_cfg(tmp_path)
    plan = tmp_path / "plan.txt"
    plan.write_text("One standalone post about logs.\n")
    assert cli("--config", c, "queue", "load", str(plan))[0] == 0
    rc, out = cli("--config", c, "queue", "next")
    assert rc == 0
    item = json.loads(out.strip().splitlines()[-1])
    assert item["idx"] == 1 and item["text"].startswith("One standalone")
    publish(tmp_path, item["text"], pid="q1")
    assert cli("--config", c, "queue", "done", "q1")[0] == 0
    assert cli("--config", c, "queue", "next", now=NOW + 200)[0] == 10


def test_lookup_cli(cli, tmp_path):
    c = ledger_cfg(tmp_path)
    cli("--config", c, "before", "--text", T)
    publish(tmp_path, T, pid="p5")
    rc, out = cli("--config", c, "lookup", "--text", T)
    assert rc == 0 and "FOUND p5" in out
    rc, out = cli("--config", c, "lookup", "--text", "never posted this one")
    assert rc == 1


def test_dupcheck_cli(cli, tmp_path):
    c = ledger_cfg(tmp_path)
    publish(tmp_path, T, pid="a")
    publish(tmp_path, T, pid="b")
    rc, out = cli("--config", c, "dupcheck")
    assert rc == 3 and "DUPLICATE" in out
    cli("resume")
    rc, out = cli("--config", c, "dupcheck", "--ignore", "a")
    assert rc == 0, out


def test_custom_verifier_by_module_path(cli, tmp_path):
    (tmp_path / "myverify.py").write_text(
        "from postgate import Verifier, PostInfo\n"
        "class Always(Verifier):\n"
        "    def fetch(self, post_id):\n"
        "        return PostInfo(id=post_id, text=open('expected.txt').read())\n")
    (tmp_path / "expected.txt").write_text(T)
    import os
    import subprocess
    import sys
    from .conftest import ROOT
    env = dict(os.environ, PYTHONPATH=f"{ROOT}{os.pathsep}{tmp_path}", POSTGATE_STATE=str(tmp_path / "s.json"),
               POSTGATE_NOW=str(NOW))
    def run(*a):
        return subprocess.run([sys.executable, "-m", "postgate", "--verifier", "myverify:Always", *a],
                              capture_output=True, text=True, env=env, cwd=tmp_path)
    assert run("before", "--text", T).returncode == 0
    p = run("after", "--id", "z1", "--text", T)
    assert p.returncode == 0, p.stdout + p.stderr
