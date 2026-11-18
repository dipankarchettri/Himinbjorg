"""Landlock sandbox for approved commands. Skipped where the kernel has none."""
from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.security import sandbox as sb   # noqa: E402
from agent.tools import shell              # noqa: E402

ABI = sb.abi_version()
needs_landlock = pytest.mark.skipif(ABI < 1, reason="Landlock not available")
LAUNCHER = str(Path(sb.__file__).resolve())


def launch(args, code, cwd):
    return subprocess.run([sys.executable, "-I", LAUNCHER, *args, "--", sys.executable, "-c", code],
                          cwd=cwd, capture_output=True, text=True, timeout=60)


@needs_landlock
def test_writes_only_inside_the_allowed_directories(tmp_path):
    inside, outside = tmp_path / "repo", tmp_path / "elsewhere"
    inside.mkdir(), outside.mkdir()
    ok = launch(["--write", str(inside)], "open('a.txt', 'w').write('x')", inside)
    assert ok.returncode == 0, ok.stderr
    bad = launch(["--write", str(inside)], f"open({str(outside / 'b.txt')!r}, 'w')", inside)
    assert bad.returncode != 0 and "Permission denied" in bad.stderr
    assert not (outside / "b.txt").exists()


@needs_landlock
def test_denied_paths_cannot_be_read(tmp_path):
    secret = tmp_path / "vault"
    secret.mkdir()
    (secret / "key").write_text("s3cret")
    (tmp_path / "public.txt").write_text("hello")
    args = ["--write", str(tmp_path / "w"), "--deny", str(secret)]
    (tmp_path / "w").mkdir()
    assert launch(args, f"print(open({str(tmp_path / 'public.txt')!r}).read())",
                  tmp_path).stdout.strip() == "hello"
    bad = launch(args, f"print(open({str(secret / 'key')!r}).read())", tmp_path)
    assert bad.returncode != 0 and "s3cret" not in bad.stdout


@pytest.mark.skipif(ABI < 4, reason="TCP rules need Landlock ABI 4")
def test_tcp_is_blocked_unless_network_is_allowed(tmp_path):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    port = srv.getsockname()[1]
    code = f"import socket; socket.create_connection(('127.0.0.1', {port}), 5); print('connected')"
    try:
        blocked = launch(["--write", str(tmp_path)], code, tmp_path)
        assert "connected" not in blocked.stdout and "Permission denied" in blocked.stderr
        allowed = launch(["--write", str(tmp_path), "--net"], code, tmp_path)
        assert allowed.stdout.strip() == "connected", allowed.stderr
    finally:
        srv.close()


@needs_landlock
def test_run_command_is_sandboxed_by_default_mode(tmp_path):
    out = shell.run_command(["python3", "-c", "open('/etc/himin-test', 'w')"], tmp_path,
                            sandbox="auto")
    assert out.startswith("exit=1") and "Permission denied" in out


def test_required_mode_refuses_when_landlock_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "abi_version", lambda: 0)
    out = shell.run_command(["true"], tmp_path, sandbox="required")
    assert out.startswith("ERROR:") and "not run" in out
    assert shell.run_command(["true"], tmp_path, sandbox="auto").startswith("exit=0")


def test_launcher_runs_isolated_and_hides_the_install_env(tmp_path):
    argv = shell.sandboxed_argv(["make", "test"], tmp_path, network=False)
    assert argv[:3] == [sys.executable, "-I", LAUNCHER]
    assert argv[-3:] == ["--", "make", "test"]
    denied = [argv[i + 1] for i, a in enumerate(argv) if a == "--deny"]
    assert str(ROOT / ".env") in denied
    assert str(Path.home() / ".ssh") in denied
    assert "--net" not in argv
