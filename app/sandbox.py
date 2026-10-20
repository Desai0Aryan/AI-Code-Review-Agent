"""Sandboxes for running tools and tests against untrusted pull-request code.

Anything that touches PR code (even static analyzers, which read repo config) runs
inside a sandbox. DockerSandbox is the real one. LocalSandbox has no isolation and
exists only for development and unit tests.

Commands may contain the tokens {work}, {deps} and {out}; each sandbox substitutes the
right path for where that directory lives inside it.
"""

import logging
import os
import resource
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Protocol

logger = logging.getLogger("code_review_agent")

MAX_OUTPUT_CHARS = 200_000


@dataclass
class ExecResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    duration: float = 0.0


class Sandbox(Protocol):
    def run(
        self,
        cmd: list[str],
        *,
        work: Path,
        deps: Path | None = None,
        out: Path | None = None,
        deps_writable: bool = False,
        network: bool = False,
        timeout: int = 120,
        env: dict[str, str] | None = None,
    ) -> ExecResult: ...


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n...[truncated {len(text) - MAX_OUTPUT_CHARS} chars]"


def _to_text(data: str | bytes | None) -> str:
    if data is None:
        return ""
    return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data


def _subst(value: str, mapping: dict[str, str]) -> str:
    for token, replacement in mapping.items():
        value = value.replace(token, replacement)
    return value


class DockerSandbox:
    """One throwaway container per command: no network by default, read-only code,
    all capabilities dropped, unprivileged user, and memory/CPU/process limits."""

    CONTAINER_PATHS: ClassVar[dict[str, str]] = {"{work}": "/work", "{deps}": "/deps", "{out}": "/out"}

    def __init__(
        self,
        image: str = "code-review-sandbox:latest",
        memory: str = "768m",
        cpus: str = "1.0",
        pids_limit: int = 256,
        docker_bin: str = "docker",
    ):
        self.image = image
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.docker_bin = docker_bin

    def available(self) -> bool:
        try:
            probe = subprocess.run(
                [self.docker_bin, "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return probe.returncode == 0

    def build_argv(
        self,
        cmd: list[str],
        *,
        name: str,
        work: Path,
        deps: Path | None = None,
        out: Path | None = None,
        deps_writable: bool = False,
        network: bool = False,
        env: dict[str, str] | None = None,
    ) -> list[str]:
        argv = [
            self.docker_bin, "run", "--rm", "--name", name,
            "--network", "bridge" if network else "none",
            "--memory", self.memory, "--memory-swap", self.memory,
            "--cpus", self.cpus, "--pids-limit", str(self.pids_limit),
            "--read-only", "--tmpfs", "/tmp:rw,size=256m",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", "65534:65534", "--workdir", "/work",
            "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-v", f"{work.resolve()}:/work:ro",
        ]  # fmt: skip
        if deps is not None:
            argv += ["-v", f"{deps.resolve()}:/deps:{'rw' if deps_writable else 'ro'}"]
        if out is not None:
            argv += ["-v", f"{out.resolve()}:/out:rw"]
        for key, value in (env or {}).items():
            argv += ["-e", f"{key}={_subst(value, self.CONTAINER_PATHS)}"]
        argv.append(self.image)
        argv += [_subst(c, self.CONTAINER_PATHS) for c in cmd]
        return argv

    def run(
        self,
        cmd: list[str],
        *,
        work: Path,
        deps: Path | None = None,
        out: Path | None = None,
        deps_writable: bool = False,
        network: bool = False,
        timeout: int = 120,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        name = f"cra-{uuid.uuid4().hex[:12]}"
        argv = self.build_argv(
            cmd, name=name, work=work, deps=deps, out=out,
            deps_writable=deps_writable, network=network, env=env,
        )  # fmt: skip
        start = time.monotonic()
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, errors="replace", timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired as exc:
            subprocess.run(
                [self.docker_bin, "kill", name], capture_output=True, timeout=15, check=False
            )
            return ExecResult(
                -1,
                _truncate(_to_text(exc.stdout)),
                _truncate(_to_text(exc.stderr)),
                timed_out=True,
                duration=time.monotonic() - start,
            )
        return ExecResult(
            proc.returncode,
            _truncate(proc.stdout),
            _truncate(proc.stderr),
            duration=time.monotonic() - start,
        )


class LocalSandbox:
    """NO ISOLATION. Runs commands as the current user with a scrubbed environment,
    CPU/file-size limits and a hard timeout. For development and tests only."""

    def run(
        self,
        cmd: list[str],
        *,
        work: Path,
        deps: Path | None = None,
        out: Path | None = None,
        deps_writable: bool = False,
        network: bool = False,
        timeout: int = 120,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        mapping = {
            "{work}": str(work.resolve()),
            "{deps}": str(deps.resolve()) if deps else "",
            "{out}": str(out.resolve()) if out else "",
        }
        argv = [_subst(c, mapping) for c in cmd]
        if argv and argv[0] == "python":
            argv[0] = sys.executable

        # Scrubbed env: host secrets (GitHub keys, API tokens) must never reach PR code.
        child_env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": mapping["{out}"] or "/tmp",
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        child_env.update({k: _subst(v, mapping) for k, v in (env or {}).items()})

        def limits() -> None:
            resource.setrlimit(resource.RLIMIT_CPU, (timeout + 5, timeout + 5))
            resource.setrlimit(resource.RLIMIT_FSIZE, (100_000_000, 100_000_000))

        start = time.monotonic()
        proc = subprocess.Popen(
            argv,
            cwd=work,
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            start_new_session=True,
            preexec_fn=limits,  # noqa: PLW1509 - rlimits must be set in the child
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate()
            timed_out = True
        return ExecResult(
            -1 if timed_out else proc.returncode,
            _truncate(stdout or ""),
            _truncate(stderr or ""),
            timed_out=timed_out,
            duration=time.monotonic() - start,
        )


def get_sandbox(settings) -> Sandbox | None:
    """Pick the sandbox from settings. Returns None when analysis must be skipped."""
    mode = settings.sandbox.lower()
    if mode == "off":
        return None
    if mode == "local":
        if not settings.allow_unsafe_local_sandbox:
            logger.error("sandbox=local needs CRA_ALLOW_UNSAFE_LOCAL_SANDBOX=true; skipping analysis")
            return None
        logger.warning("using LocalSandbox: PR code runs WITHOUT isolation")
        return LocalSandbox()
    sandbox = DockerSandbox(image=settings.sandbox_image)
    if not sandbox.available():
        logger.error("docker is not available; skipping analysis")
        return None
    return sandbox
