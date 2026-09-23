#!/usr/bin/env python3
"""Run a bounded Codex -> Claude -> Codex project workflow."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone


PROJECT = Path(__file__).resolve().parents[1]
HANDOFF = PROJECT / ".ai-handoff"
PLAN_SCHEMA = PROJECT / "automation" / "plan.schema.json"
VERDICT_SCHEMA = PROJECT / "automation" / "verdict.schema.json"
SITE_CHECK = PROJECT / "automation" / "check.py"


def save_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run_agent(label: str, command: list[str], output: Path, errors: Path) -> None:
    print(f"  {label} is working...", flush=True)
    child_env = os.environ.copy()
    child_env.pop("CLAUDECODE", None)
    child_env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    with output.open("w", encoding="utf-8") as stdout, errors.open(
        "w", encoding="utf-8"
    ) as stderr:
        process = subprocess.Popen(
            command, cwd=PROJECT, stdin=subprocess.DEVNULL,
            stdout=stdout, stderr=stderr, env=child_env,
            start_new_session=True,
        )
        try:
            code = process.wait(timeout=3600)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            stop_group(process)
            raise
        finally:
            # Agents must not leave background shell tools editing the project.
            stop_group(process)
        if code:
            raise subprocess.CalledProcessError(code, command)


def stop_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=PROJECT, stdin=subprocess.DEVNULL,
        text=True, capture_output=True, check=True,
    )
    return result.stdout.strip()


def require_clean_tree() -> None:
    if git("rev-parse", "--show-toplevel") != str(PROJECT):
        raise ValueError("The collaboration project must be its own Git repository")
    if git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("The project has uncommitted changes; review or commit them before starting a new run")


def checkpoint(task_number: int, title: str) -> str:
    git("diff", "--check")
    if git("status", "--porcelain", "--untracked-files=all"):
        git("add", "-A")
        git("diff", "--cached", "--check")
        safe_title = " ".join(title.split())[:72]
        git("-c", "core.hooksPath=/dev/null", "commit", "-m", f"collab: task {task_number} - {safe_title}")
    return git("rev-parse", "--verify", "HEAD")


def codex_base(sandbox: str) -> list[str]:
    command = [
        "codex", "exec", "--ignore-user-config", "--sandbox", sandbox,
        "-c", "approval_policy=never",
        "-c", "sandbox_workspace_write.network_access=false",
        "-c", "web_search=disabled",
        "-c", "mcp_servers={}",
        "-c", 'plugins."browser@openai-bundled".enabled=false',
        "-c", 'plugins."chrome@openai-bundled".enabled=false',
        "-c", 'plugins."computer-use@openai-bundled".enabled=false',
    ]
    if not (PROJECT / ".git").exists():
        command.append("--skip-git-repo-check")
    return command


def newest_incomplete_run() -> Path:
    for run_dir in sorted(HANDOFF.glob("run-*"), reverse=True):
        state_file = run_dir / "state.json"
        if state_file.exists() and load_json(state_file).get("phase") != "done":
            return run_dir
    raise ValueError("No unfinished run found in .ai-handoff")


def plan(state: dict, run_dir: Path) -> None:
    output = run_dir / "plan.json"
    prompt = (
        f"Plan the following project objective as 1 to {state['max_tasks']} concrete, "
        "sequential tasks. Each task must produce or verify a real part of the "
        "objective. Make reasonable assumptions and keep the plan within this "
        "project. Do not include pushing, deployment, purchases, or external "
        "messages unless the objective explicitly asks for them. Return only "
        f"the required JSON. Objective: {state['objective']}"
    )
    command = codex_base("read-only") + [
        "--output-schema", str(PLAN_SCHEMA), "-o", str(output), prompt
    ]
    run_agent("Codex planner", command, run_dir / "plan.log", run_dir / "plan.err")
    tasks = load_json(output).get("tasks")
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= state["max_tasks"]:
        raise ValueError("Codex returned an invalid task count")
    if any(not task.get("title") or not task.get("instructions") for task in tasks):
        raise ValueError("Codex returned an incomplete task")
    state["tasks"] = tasks
    state["phase"] = "codex"
    save_json(run_dir / "state.json", state)
    print("  Plan: " + " | ".join(task["title"] for task in tasks), flush=True)


def task_prompt(state: dict) -> str:
    index = state["task_index"]
    task = state["tasks"][index]
    prompt = (
        f"Overall objective: {state['objective']}\n"
        f"Task {index + 1} of {len(state['tasks'])}: {task['title']}\n"
        f"Instructions: {task['instructions']}\n"
        "Inspect existing work first. Stay in this project. Do not push, "
        "deploy, make purchases, or message anyone. If a required choice is "
        "missing, explain it clearly."
    )
    prior = next(
        (result for result in reversed(state["results"]) if result["task_index"] == index),
        None,
    )
    if prior:
        prompt += f"\nPrevious result: {json.dumps(prior)}"
    if state.get("resume_note"):
        prompt += f"\nNew user input: {state['resume_note']}"
    return prompt


def work_task(state: dict, run_dir: Path) -> None:
    index = state["task_index"] + 1
    task_dir = run_dir / f"task-{index:02d}" / f"attempt-{state['attempt']:02d}"
    task_dir.mkdir(parents=True, exist_ok=True)
    task = state["tasks"][index - 1]
    print(f"[{index}/{len(state['tasks'])}] {task['title']}", flush=True)

    if state["phase"] == "codex":
        prompt = task_prompt(state) + (
            "\nImplement this task. Run relevant checks. In the final response, "
            "summarize the changes, checks, and points for Claude to review."
        )
        command = codex_base("workspace-write") + [
            "-o", str(task_dir / "codex.md"), prompt
        ]
        run_agent("Codex", command, task_dir / "codex.log", task_dir / "codex.err")
        state["phase"] = "claude"
        save_json(run_dir / "state.json", state)

    if state["phase"] == "claude":
        prompt = task_prompt(state) + (
            f"\nRead {task_dir / 'codex.md'} and review Codex's changes. "
            "Fix confirmed issues, run relevant checks, and report your edits "
            "and remaining concerns for Codex."
        )
        command = [
            "claude", "-p", "--restricted", "--no-chrome",
            "--tools", "Read,Glob,Grep,Edit,Write",
            "--strict-mcp-config", "--no-session-persistence",
            "--max-budget-usd", "10",
            "--permission-mode", "auto", "--permission-prompts", "none",
            prompt,
        ]
        run_agent("Claude", command, task_dir / "claude.md", task_dir / "claude.err")
        state["phase"] = "verify"
        save_json(run_dir / "state.json", state)

    if state["phase"] == "verify":
        prompt = task_prompt(state) + (
            f"\nRead {task_dir / 'codex.md'} and {task_dir / 'claude.md'}. "
            "Inspect both agents' edits, resolve remaining issues, and run "
            "final checks. Report complete only if this task is actually done. "
            "Use needs_user when a user choice or credential is essential, "
            "or blocked when execution cannot continue. Return only the "
            "required JSON."
        )
        command = codex_base("workspace-write") + [
            "--output-schema", str(VERDICT_SCHEMA),
            "-o", str(task_dir / "verdict.json"), prompt,
        ]
        run_agent("Codex verifier", command, task_dir / "verify.log", task_dir / "verify.err")
        verdict = load_json(task_dir / "verdict.json")
        if verdict.get("status") not in {"complete", "needs_user", "blocked"}:
            raise ValueError("Codex returned an invalid task status")
        if verdict["status"] == "complete":
            check = subprocess.run(
                [sys.executable, str(SITE_CHECK)], cwd=PROJECT,
                stdin=subprocess.DEVNULL, text=True, capture_output=True,
            )
            if check.returncode:
                verdict["status"] = "blocked"
                verdict["summary"] = "Independent site checks failed."
                verdict["next_step"] = (check.stdout + check.stderr).strip()[:2000]
                save_json(task_dir / "verdict.json", verdict)
        state["results"].append({"task_index": index - 1, "task": task["title"], **verdict})
        if verdict["status"] == "complete":
            state["results"][-1]["commit"] = checkpoint(index, task["title"])
            state["task_index"] += 1
            state["attempt"] = 1
            state["resume_note"] = ""
            state["phase"] = "done" if state["task_index"] == len(state["tasks"]) else "codex"
        else:
            state["phase"] = verdict["status"]
        save_json(run_dir / "state.json", state)
        print(f"  Result: {verdict['status']} — {verdict['summary']}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Let Codex and Claude work through a bounded project plan."
    )
    parser.add_argument("objective", nargs="?", help="What the agents should accomplish")
    parser.add_argument("--max-tasks", type=int, default=5, metavar="N")
    parser.add_argument("--resume", action="store_true", help="Continue the newest unfinished run")
    parser.add_argument("--note", help="New information for a resumed task")
    args = parser.parse_args()
    if not 1 <= args.max_tasks <= 8:
        parser.error("--max-tasks must be between 1 and 8")
    if not args.resume and not args.objective:
        parser.error("provide an objective or use --resume")
    if args.resume and args.objective:
        parser.error("use --note to add information when resuming")
    if args.note and not args.resume:
        parser.error("--note can only be used with --resume")
    for tool_name in ("codex", "claude"):
        if not shutil.which(tool_name):
            parser.error(f"{tool_name} is not installed or not on PATH")

    HANDOFF.mkdir(exist_ok=True)
    with (HANDOFF / "runner.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another collaboration run is already active.", file=sys.stderr)
            return 1

        if args.resume:
            try:
                run_dir = newest_incomplete_run()
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            state = load_json(run_dir / "state.json")
            if state["phase"] in {"needs_user", "blocked"}:
                state["attempt"] += 1
                state["phase"] = "codex"
            if args.note:
                state["resume_note"] = args.note
            save_json(run_dir / "state.json", state)
        else:
            try:
                require_clean_tree()
            except (subprocess.CalledProcessError, ValueError) as exc:
                print(str(exc), file=sys.stderr)
                return 1
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            run_dir = HANDOFF / f"run-{stamp}-{os.getpid()}"
            run_dir.mkdir()
            state = {
                "objective": args.objective,
                "max_tasks": args.max_tasks,
                "tasks": [],
                "task_index": 0,
                "phase": "plan",
                "attempt": 1,
                "resume_note": "",
                "results": [],
            }
            save_json(run_dir / "state.json", state)

        print(f"Run record: {run_dir.relative_to(PROJECT)}", flush=True)
        try:
            if state["phase"] == "plan":
                plan(state, run_dir)
            while state["phase"] in {"codex", "claude", "verify"}:
                work_task(state, run_dir)
        except (
            OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired,
            ValueError, json.JSONDecodeError,
        ) as exc:
            print(f"Stopped: {exc}. Inspect the run record, then use --resume.", file=sys.stderr)
            return 1

        if state["phase"] == "done":
            print("All planned tasks finished. Review the run record before publishing.")
            return 0
        if state["results"]:
            print("Next step: " + state["results"][-1]["next_step"])
        print(f"Stopped at {state['phase']}. Review the run record before resuming.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
