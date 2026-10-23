import os
import sys
import time
from pathlib import Path

from app.config import Settings
from app.sandbox import DockerSandbox, LocalSandbox, get_sandbox


def argv_for(**kwargs):
    sb = DockerSandbox(image="img:1")
    defaults = {"name": "cra-test", "work": Path("/tmp/w"), "cmd": ["python", "-V"]}
    return sb.build_argv(**{**defaults, **kwargs})


def pair(argv, flag):
    i = argv.index(flag)
    return argv[i + 1]


def test_docker_argv_is_locked_down_by_default():
    argv = argv_for()
    assert pair(argv, "--network") == "none"
    assert "--read-only" in argv
    assert pair(argv, "--cap-drop") == "ALL"
    assert pair(argv, "--security-opt") == "no-new-privileges"
    assert pair(argv, "--user") == "65534:65534"
    assert pair(argv, "--pids-limit") == "256"
    assert pair(argv, "--memory") == pair(argv, "--memory-swap")  # no swap escape hatch
    assert any(a.endswith(":/work:ro") for a in argv)  # PR code is mounted read-only
    assert argv[-2:] == ["python", "-V"] and "img:1" in argv


def test_docker_network_only_when_requested_and_deps_rw_only_for_install():
    argv = argv_for(network=True, deps=Path("/tmp/d"), deps_writable=True)
    assert pair(argv, "--network") == "bridge"
    assert any(a.endswith(":/deps:rw") for a in argv)
    argv = argv_for(deps=Path("/tmp/d"))
    assert any(a.endswith(":/deps:ro") for a in argv)


def test_docker_never_forwards_host_environment():
    argv = argv_for(env={"PYTHONPATH": "{deps}:{work}"})
    envs = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    assert "PYTHONPATH=/deps:/work" in envs
    assert not any("KEY" in e or "SECRET" in e or "TOKEN" in e for e in envs)


def test_docker_tokens_are_substituted_in_command():
    argv = argv_for(cmd=["pytest", "--junitxml={out}/j.xml"], out=Path("/tmp/o"))
    assert "--junitxml=/out/j.xml" in argv


def test_local_sandbox_runs_and_captures(tmp_path):
    res = LocalSandbox().run(
        ["python", "-c", "print('hi'); import sys; sys.exit(3)"], work=tmp_path
    )
    assert res.stdout.strip() == "hi" and res.returncode == 3 and not res.timed_out


def test_local_sandbox_does_not_leak_host_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("CRA_GITHUB_PRIVATE_KEY", "super-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    code = "import os; print(sorted(k for k in os.environ if 'KEY' in k or 'SECRET' in k))"
    res = LocalSandbox().run(["python", "-c", code], work=tmp_path)
    assert res.stdout.strip() == "[]"


def test_local_sandbox_kills_on_timeout(tmp_path):
    start = time.monotonic()
    res = LocalSandbox().run(
        [sys.executable, "-c", "import time; time.sleep(30)"], work=tmp_path, timeout=1
    )
    assert res.timed_out and time.monotonic() - start < 10


def test_local_sandbox_truncates_huge_output(tmp_path):
    res = LocalSandbox().run(["python", "-c", "print('x' * 500_000)"], work=tmp_path)
    assert len(res.stdout) < 250_000 and "truncated" in res.stdout


def test_get_sandbox_modes(monkeypatch):
    assert get_sandbox(Settings(sandbox="off")) is None
    # local mode refuses to start unless explicitly acknowledged as unsafe
    assert get_sandbox(Settings(sandbox="local")) is None
    assert isinstance(
        get_sandbox(Settings(sandbox="local", allow_unsafe_local_sandbox=True)), LocalSandbox
    )
    monkeypatch.setattr(DockerSandbox, "available", lambda self: False)
    assert get_sandbox(Settings(sandbox="docker")) is None


def test_workdir_is_used(tmp_path):
    (tmp_path / "marker.txt").write_text("here")
    res = LocalSandbox().run(["python", "-c", "print(open('marker.txt').read())"], work=tmp_path)
    assert res.stdout.strip() == "here" and os.path.isdir(tmp_path)
