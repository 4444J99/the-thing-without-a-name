#!/usr/bin/env python3
"""Venue-gated foreground supervisor for a canonical Danse installation release.

This program is deliberately on-demand. It does not install, generate, or invoke
any persistent host service. The venue owns how this foreground command is
started after power restoration, and that exact launcher must appear in the
external evidence receipt before `--run` is admitted.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

try:
    from .contract import (
        ContractError,
        file_sha256,
        load_json,
        load_reference_contracts,
        runtime_plan,
        safe_file,
    )
except ImportError:  # Direct `python3 installation/runtime.py` execution.
    from contract import (  # type: ignore[no-redef]
        ContractError,
        file_sha256,
        load_json,
        load_reference_contracts,
        runtime_plan,
        safe_file,
    )

TELEMETRY_SCHEMA = "danse.installation.telemetry.v1"


class Telemetry:
    """Append-only JSONL health events with no credentials or local paths."""

    def __init__(
        self, stream: TextIO, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.stream = stream
        self.clock = clock
        self.started = clock()
        self.sequence = 0

    def emit(self, event: str, **fields: Any) -> None:
        record = {
            "schema": TELEMETRY_SCHEMA,
            "sequence": self.sequence,
            "elapsed_seconds": round(max(0.0, self.clock() - self.started), 3),
            "event": event,
            **fields,
        }
        self.stream.write(
            json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        )
        self.stream.flush()
        self.sequence += 1


def probe_health(url: str, timeout: float) -> bool:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=timeout) as response:  # noqa: S310 - numeric loopback URL is prevalidated
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError, ValueError):
        return False


def terminate(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass


def supervise(
    plan: dict[str, Any],
    release_root: Path,
    telemetry: Telemetry,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    popen: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
    health_probe: Callable[[str, float], bool] = probe_health,
) -> int:
    """Run one admitted launcher and recover only within its declared budget."""
    policy = plan["policy"]
    health = policy["health"]
    recovery = policy["recovery"]
    root = release_root.resolve(strict=True)
    relative_argv = plan["argv"]
    launcher = plan["launcher"]
    environment = {
        key: os.environ[key]
        for key in ("PATH", "LANG", "LC_ALL", "TZ", "TMPDIR")
        if key in os.environ
    }
    environment.update(
        {
            "DANSE_INSTALLATION_CONTRACT_SHA256": plan["spec_contract_sha256"],
            "DANSE_INSTALLATION_EVIDENCE_ID": plan["evidence_id"],
            "DANSE_INSTALLATION_EVIDENCE_SHA256": plan["evidence_sha256"],
            "DANSE_INSTALLATION_LAUNCHER_SHA256": launcher["sha256"],
            "DANSE_INSTALLATION_OUTPUTS": ",".join(plan["outputs"]),
            "DANSE_RIVER_SEED": str(plan["river"]["seed"]),
            "DANSE_RIVER_STREAM": str(plan["river"]["stream"]),
            "DANSE_RIVER_EPOCH_MS": str(plan["river"]["epoch_ms"]),
        }
    )

    restart_times: list[float] = []
    attempt = 0
    while True:
        now = clock()
        restart_times = [
            stamp
            for stamp in restart_times
            if now - stamp <= recovery["window_seconds"]
        ]
        if attempt > 0:
            if len(restart_times) >= recovery["max_restarts"]:
                telemetry.emit(
                    "recovery-budget-exhausted",
                    attempt=attempt,
                    restarts=len(restart_times),
                )
                return 75
            delay = recovery["backoff_seconds"][len(restart_times)]
            telemetry.emit(
                "restart-admitted", attempt=attempt + 1, backoff_seconds=delay
            )
            sleep(delay)
            restart_times.append(clock())

        attempt += 1
        try:
            executable = safe_file(root, relative_argv[0], "runtime executable")
            launcher_holds = (
                launcher["path"] == relative_argv[0]
                and executable.stat().st_size == launcher["bytes"]
                and file_sha256(executable) == launcher["sha256"]
                and bool(executable.stat().st_mode & 0o111)
            )
        except (ContractError, OSError):
            launcher_holds = False
        if not launcher_holds:
            telemetry.emit("launcher-integrity-failed", attempt=attempt)
            return 78
        argv = [str(executable), *relative_argv[1:]]
        telemetry.emit("launcher-start", attempt=attempt)
        try:
            process = popen(argv, cwd=root, env=environment, shell=False)
        except OSError as exc:
            telemetry.emit("launcher-error", attempt=attempt, error=type(exc).__name__)
            continue

        started = clock()
        ever_healthy = plan["health_url"] is None
        consecutive_failures = 0
        forced_failure: str | None = None
        try:
            while process.poll() is None:
                if plan["health_url"] is not None:
                    ok = health_probe(
                        plan["health_url"], health["probe_timeout_seconds"]
                    )
                    if ok:
                        if not ever_healthy:
                            telemetry.emit("health-ready", attempt=attempt)
                        ever_healthy = True
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1
                        telemetry.emit(
                            "health-failed",
                            attempt=attempt,
                            consecutive=consecutive_failures,
                        )
                        elapsed = clock() - started
                        startup_failed = (
                            not ever_healthy
                            and elapsed >= health["startup_timeout_seconds"]
                        )
                        runtime_failed = (
                            ever_healthy
                            and consecutive_failures
                            >= health["max_consecutive_failures"]
                        )
                        if startup_failed or runtime_failed:
                            forced_failure = (
                                "startup-health" if startup_failed else "runtime-health"
                            )
                            terminate(process)
                            break
                sleep(health["probe_interval_seconds"])
        except KeyboardInterrupt:
            terminate(process)
            telemetry.emit("operator-stop", attempt=attempt)
            return 130

        returncode = process.poll()
        if returncode is None:
            try:
                returncode = process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                terminate(process)
                returncode = process.poll()
        duration = max(0.0, clock() - started)
        if forced_failure is not None:
            telemetry.emit("launcher-unhealthy", attempt=attempt, reason=forced_failure)
        elif returncode == 0:
            telemetry.emit("launcher-exit", attempt=attempt, returncode=0)
            return 0
        else:
            telemetry.emit("launcher-exit", attempt=attempt, returncode=returncode)
        if duration >= recovery["stable_seconds"]:
            restart_times.clear()
            telemetry.emit("recovery-window-reset", attempt=attempt)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    mode = value.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check",
        action="store_true",
        help="validate and print the admitted plan without launching",
    )
    mode.add_argument(
        "--run", action="store_true", help="run the admitted foreground launcher"
    )
    value.add_argument("--evidence", type=Path, required=True)
    value.add_argument("--release-root", type=Path, required=True)
    value.add_argument(
        "--telemetry", default="-", help="JSONL receipt path; - writes to stdout"
    )
    return value


def telemetry_stream(target: str) -> tuple[TextIO, bool]:
    if target == "-":
        return sys.stdout, False
    path = Path(target)
    if path.exists() or path.is_symlink():
        raise ContractError(
            "telemetry receipt path must be new and may not be a symlink"
        )
    try:
        stream = path.open("x", encoding="utf-8")
    except OSError as exc:
        raise ContractError(f"telemetry receipt cannot be created: {exc}") from exc
    return stream, True


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    stream: TextIO | None = None
    close_stream = False
    try:
        spec, _, _ = load_reference_contracts()
        evidence = load_json(args.evidence)
        plan = runtime_plan(evidence, spec, args.release_root)
        if args.check:
            print(json.dumps(plan, indent=2, sort_keys=True))
            return 0
        stream, close_stream = telemetry_stream(args.telemetry)
        telemetry = Telemetry(stream)
        telemetry.emit(
            "runtime-admitted",
            spec_contract_sha256=plan["spec_contract_sha256"],
            evidence_id=plan["evidence_id"],
            evidence_sha256=plan["evidence_sha256"],
            release_manifest_sha256=plan["release_manifest_sha256"],
            launcher_sha256=plan["launcher"]["sha256"],
        )

        def interrupt_foreground(_signum: int, _frame: Any) -> None:
            raise KeyboardInterrupt

        watched = [signal.SIGTERM]
        if hasattr(signal, "SIGHUP"):
            watched.append(signal.SIGHUP)
        previous = {
            signum: signal.signal(signum, interrupt_foreground) for signum in watched
        }
        try:
            return supervise(plan, args.release_root, telemetry)
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
    except (ContractError, OSError) as exc:
        print(f"installation runtime: {exc}", file=sys.stderr)
        return 1
    finally:
        if close_stream and stream is not None:
            stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
