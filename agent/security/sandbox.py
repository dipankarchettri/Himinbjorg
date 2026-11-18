"""Landlock sandbox for approved commands (Linux 5.13+, no root needed).

An approved command used to run with the user's full rights: `pytest`
could still read ~/.ssh or write anywhere in $HOME. This launcher applies a
Landlock policy to itself and then execs the command, so the command and
everything it starts inherit it:

- writes only inside the repository, the temp directory and ~/.cache;
- reads everywhere except the sensitive paths passed with --deny;
- no TCP connect or bind unless --net (Landlock ABI 4, Linux 6.7+).

Landlock only adds restrictions, never removes them, and needs no
privileges, which is why it was chosen over bubblewrap: Ubuntu 24.04 blocks
the unprivileged user namespaces bubblewrap depends on.

Limits, stated plainly: UDP is not restricted (Landlock has no UDP rules),
reads inside the repository are not limited (the secret shield and output
redaction cover those), and macOS and Windows have no equivalent here.

Usage (shell.py builds this, nobody types it):
    python -I -m agent.security.sandbox --write DIR ... --deny PATH ... [--net] -- CMD ARGS...
"""
from __future__ import annotations

import ctypes
import os
import struct
import sys
from pathlib import Path

_SYS_CREATE_RULESET, _SYS_ADD_RULE, _SYS_RESTRICT_SELF = 444, 445, 446
_PR_SET_NO_NEW_PRIVS = 38
_CREATE_RULESET_VERSION = 1
_RULE_PATH_BENEATH = 1

FS_EXECUTE = 1 << 0
FS_WRITE_FILE = 1 << 1
FS_READ_FILE = 1 << 2
FS_READ_DIR = 1 << 3
FS_REMOVE_DIR = 1 << 4
FS_REMOVE_FILE = 1 << 5
FS_MAKE_CHAR = 1 << 6
FS_MAKE_DIR = 1 << 7
FS_MAKE_REG = 1 << 8
FS_MAKE_SOCK = 1 << 9
FS_MAKE_FIFO = 1 << 10
FS_MAKE_BLOCK = 1 << 11
FS_MAKE_SYM = 1 << 12
FS_REFER = 1 << 13          # ABI 2
FS_TRUNCATE = 1 << 14       # ABI 3
NET_BIND_TCP = 1 << 0       # ABI 4
NET_CONNECT_TCP = 1 << 1    # ABI 4

_READ = FS_EXECUTE | FS_READ_FILE | FS_READ_DIR
# Rights that are valid on a rule for a regular file, not a directory.
_FILE_RIGHTS = FS_EXECUTE | FS_WRITE_FILE | FS_READ_FILE | FS_TRUNCATE

# Paths under $HOME that hold credentials or browser profiles.
HOME_DENY = (
    ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".netrc",
    ".git-credentials", ".pypirc", ".npmrc", ".password-store", ".mozilla",
    ".thunderbird", ".local/share/keyrings", ".config/gcloud", ".config/gh",
    ".config/google-chrome", ".config/chromium", ".config/BraveSoftware",
    ".config/himinbjorg", ".config/bifrost-agent",
)


def _libc():
    return ctypes.CDLL(None, use_errno=True)


def abi_version() -> int:
    """Landlock ABI the kernel supports, 0 if none (or not Linux)."""
    if not sys.platform.startswith("linux"):
        return 0
    try:
        v = _libc().syscall(_SYS_CREATE_RULESET, None, 0, _CREATE_RULESET_VERSION)
    except (OSError, AttributeError):
        return 0
    return v if v > 0 else 0


def _all_fs(abi: int) -> int:
    rights = (1 << 13) - 1              # ABI 1: bits 0-12
    if abi >= 2:
        rights |= FS_REFER
    if abi >= 3:
        rights |= FS_TRUNCATE
    return rights


def _fail(msg: str) -> None:
    err = ctypes.get_errno()
    raise OSError(err, f"landlock: {msg}: {os.strerror(err)}")


def _add_path(libc, ruleset_fd: int, path: Path, access: int) -> None:
    try:
        fd = os.open(str(path), os.O_PATH | os.O_CLOEXEC)
    except OSError:
        return                          # vanished or unreadable: no rule, so no access
    try:
        if not path.is_dir():
            access &= _FILE_RIGHTS
        # struct landlock_path_beneath_attr is packed: u64 + s32.
        attr = ctypes.create_string_buffer(struct.pack("=Qi", access, fd))
        if libc.syscall(_SYS_ADD_RULE, ruleset_fd, _RULE_PATH_BENEATH, attr, 0) != 0:
            _fail(f"add rule for {path}")
    finally:
        os.close(fd)


def _read_rules(root: Path, deny: set[Path]) -> list[Path]:
    """Paths to grant read on so that everything except `deny` is readable.

    Landlock is allow-only, so "all but X" is built by descending only into
    directories that contain a denied path and granting their other
    children whole.
    """
    out: list[Path] = []

    def grant(p: Path) -> None:
        try:
            real = p.resolve()
        except (OSError, RuntimeError):
            return
        if real in deny or any(d in real.parents for d in deny):
            return                      # denied, or inside a denied tree
        if any(real in d.parents for d in deny):
            if p.is_symlink():
                return                  # a link into a partly denied tree
            try:
                children = list(p.iterdir())
            except OSError:
                return
            for c in children:
                grant(c)
            return
        out.append(p)

    grant(root)
    return out


def apply(write: list[Path], deny: list[Path], allow_net: bool) -> int:
    """Restrict the current process. Returns the ABI used; raises if none."""
    abi = abi_version()
    if abi < 1:
        raise OSError("landlock is not available on this kernel")
    libc = _libc()
    handled_fs = _all_fs(abi)
    if abi >= 4:
        handled_net = 0 if allow_net else (NET_BIND_TCP | NET_CONNECT_TCP)
        attr = ctypes.create_string_buffer(struct.pack("=QQ", handled_fs, handled_net))
        size = 16
    else:
        attr = ctypes.create_string_buffer(struct.pack("=Q", handled_fs))
        size = 8
    ruleset_fd = libc.syscall(_SYS_CREATE_RULESET, attr, size, 0)
    if ruleset_fd < 0:
        _fail("create ruleset")
    try:
        deny_set = {Path(d).resolve() for d in deny}
        for p in _read_rules(Path("/"), deny_set):
            _add_path(libc, ruleset_fd, p, _READ)
        for w in write:
            _add_path(libc, ruleset_fd, Path(w), handled_fs)
        # /dev/null and friends: tools write to them constantly.
        for dev in ("/dev/null", "/dev/zero", "/dev/tty", "/dev/urandom"):
            _add_path(libc, ruleset_fd, Path(dev), FS_READ_FILE | FS_WRITE_FILE)
        if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            _fail("no_new_privs")
        if libc.syscall(_SYS_RESTRICT_SELF, ruleset_fd, 0) != 0:
            _fail("restrict self")
    finally:
        os.close(ruleset_fd)
    return abi


def default_deny(install_dir: Path | None = None) -> list[Path]:
    home = Path.home()
    deny = [home / p for p in HOME_DENY]
    if install_dir is not None:
        deny.append(install_dir / ".env")
    return deny


def main(argv: list[str]) -> int:
    write: list[Path] = []
    deny: list[Path] = []
    allow_net = False
    i = 0
    while i < len(argv) and argv[i] != "--":
        flag = argv[i]
        if flag == "--net":
            allow_net = True
            i += 1
        elif flag in ("--write", "--deny") and i + 1 < len(argv):
            (write if flag == "--write" else deny).append(Path(argv[i + 1]))
            i += 2
        else:
            print(f"sandbox: unknown argument {flag!r}", file=sys.stderr)
            return 2
    cmd = argv[i + 1:]
    if not cmd:
        print("sandbox: no command after --", file=sys.stderr)
        return 2
    try:
        apply(write, deny, allow_net)
    except OSError as exc:
        # Fail closed: never run the command unrestricted from here.
        print(f"sandbox: {exc}; command not run", file=sys.stderr)
        return 126
    try:
        os.execvp(cmd[0], cmd)
    except OSError as exc:
        print(f"sandbox: cannot run {cmd[0]}: {exc}", file=sys.stderr)
        return 127


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
