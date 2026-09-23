#!/usr/bin/env python3
"""Queue objectives for the collaboration runner and process them in order."""

from __future__ import annotations

import argparse
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from uuid import uuid4


PROJECT = Path(__file__).resolve().parents[1]
AUTOMATION = PROJECT / "automation"
QUEUE = AUTOMATION / "queue"
COMPLETED = AUTOMATION / "completed"
PAUSED = AUTOMATION / "paused"
HANDOFF = PROJECT / ".ai-handoff"
RUNNER = AUTOMATION / "runner.py"
WATCH_LOCK = HANDOFF / "watch.lock"
STOP_REQUEST = HANDOFF / "stop-watch"


def prepare_dirs() -> None:
    for directory in (QUEUE, COMPLETED, PAUSED, HANDOFF):
        directory.mkdir(parents=True, exist_ok=True)


def enqueue(objective: str) -> None:
    if not objective.strip():
        raise ValueError("The task cannot be empty")
    prepare_dirs()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"{stamp}-{uuid4().hex[:8]}"
    temporary = QUEUE / f".{name}.tmp"
    destination = QUEUE / f"{name}.md"
    temporary.write_text(objective.strip() + "\n", encoding="utf-8")
    os.replace(temporary, destination)
    print(f"Queued: {destination.relative_to(PROJECT)}")


def move_unique(source: Path, directory: Path, name: str) -> None:
    destination = directory / name
    counter = 2
    while destination.exists():
        destination = directory / f"{Path(name).stem}-{counter}{Path(name).suffix}"
        counter += 1
    os.replace(source, destination)


def watch() -> None:
    prepare_dirs()
    with WATCH_LOCK.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("A queue watcher is already running") from None

        STOP_REQUEST.unlink(missing_ok=True)

        stale_found = False
        for stale in QUEUE.glob("*.running"):
            move_unique(stale, PAUSED, stale.stem + ".md")
            stale_found = True
        if stale_found:
            print("Interrupted work was moved to paused/. Review it before restarting the watcher.", flush=True)
            return

        print("Watching for queued tasks. Press Ctrl+C to stop.", flush=True)
        while True:
            if STOP_REQUEST.exists():
                STOP_REQUEST.unlink()
                print("Watcher stopped by request.", flush=True)
                return
            pending = sorted(QUEUE.glob("*.md"))
            if not pending:
                time.sleep(10)
                continue

            task = pending[0]
            active = task.with_suffix(".running")
            os.replace(task, active)
            objective = active.read_text(encoding="utf-8").strip()
            log = HANDOFF / f"queue-{task.stem}.log"
            print(f"Starting: {task.stem}", flush=True)
            with log.open("w", encoding="utf-8") as output:
                process = subprocess.Popen(
                    [sys.executable, str(RUNNER), objective],
                    cwd=PROJECT, stdin=subprocess.DEVNULL,
                    stdout=output, stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                try:
                    result_code = process.wait()
                except KeyboardInterrupt:
                    try:
                        os.killpg(process.pid, signal.SIGINT)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait()
                    move_unique(active, PAUSED, task.name)
                    print(f"Interrupted: {task.stem}. Review paused/ before restarting.", flush=True)
                    return
            if result_code != 0 and "Another collaboration run is already active" in log.read_text(encoding="utf-8"):
                os.replace(active, task)
                time.sleep(10)
                continue
            destination = COMPLETED if result_code == 0 else PAUSED
            move_unique(active, destination, task.name)
            print(
                f"{'Completed' if result_code == 0 else 'Paused'}: {task.stem}. "
                f"See {log.relative_to(PROJECT)}",
                flush=True,
            )
            if result_code != 0:
                print("Watcher paused to protect the remaining queue. Review the run before restarting.", flush=True)
                return


def status() -> None:
    prepare_dirs()
    with WATCH_LOCK.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Watcher is running")
            return
        print("Watcher is stopped")


def main() -> int:
    parser = argparse.ArgumentParser(description="Queue or watch collaboration tasks")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--enqueue", metavar="TASK")
    mode.add_argument("--watch", action="store_true")
    mode.add_argument("--stop", action="store_true")
    mode.add_argument("--status", action="store_true")
    args = parser.parse_args()
    try:
        if args.enqueue is not None:
            enqueue(args.enqueue)
        elif args.watch:
            watch()
        elif args.stop:
            prepare_dirs()
            STOP_REQUEST.touch()
            print("Stop requested. The watcher will exit after its current task.")
        else:
            status()
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Watcher stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
