import argparse
import getpass
from datetime import datetime
from pathlib import Path

from intercode_batch import run_batch
from intercode_command_set import create_command_set
from intercode_execute_set import execute_command_set
from intercode_wrapper import (
    IntercodeTaskSelection,
    ensure_intercode_repo,
    list_tasks,
    run_task,
)


DEFAULT_RESULTS_ROOT = Path(__file__).resolve().parents[1] / "results" / "intercode_runs"


def build_parser():
    parser = argparse.ArgumentParser(description="InterCode wrapper")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    list_parser = subparsers.add_parser("list", help="List a small subset of tasks")
    list_parser.add_argument("--split", type=int, default=1, help="NL2Bash split number")
    list_parser.add_argument("--limit", type=int, default=5, help="Number of tasks to show")
    list_parser.add_argument("--offset", type=int, default=0, help="Starting task index")

    run_parser = subparsers.add_parser("run", help="Run one task and save raw logs")
    run_parser.add_argument("--split", type=int, default=1, help="NL2Bash split number")
    run_parser.add_argument("--task-index", type=int, required=True, help="Task index within the split")
    run_parser.add_argument(
        "--command",
        default=None,
        help="Command to execute. Defaults to the task gold command.",
    )
    run_parser.add_argument(
        "--command-source",
        choices=("gold", "manual", "model"),
        default=None,
        help="How to choose the command. Defaults to gold, or manual when --command is provided.",
    )
    run_parser.add_argument(
        "--image",
        default=None,
        help="Docker image to use for the InterCode bash environment. Defaults to split-specific intercode-nl2bash-fsN.",
    )
    run_parser.add_argument(
        "--profile",
        choices=("root", "user", "strict"),
        default="root",
        help="Execution profile for the task run",
    )
    run_parser.add_argument(
        "--model-name",
        default=None,
        help="Model name for query-to-command generation when using --command-source model",
    )
    run_parser.add_argument(
        "--model-server-url",
        default=None,
        help="OpenAI-compatible model server URL when using --command-source model",
    )

    batch_parser = subparsers.add_parser("batch", help="Run multiple tasks and save raw logs plus a summary")
    batch_parser.add_argument("--split", type=int, default=1, help="NL2Bash split number")
    batch_parser.add_argument("--offset", type=int, default=0, help="Starting task index for batch selection")
    batch_parser.add_argument("--limit", type=int, default=None, help="Number of tasks to run from the offset")
    batch_parser.add_argument(
        "--task-indices",
        default=None,
        help="Comma-separated task indices to run. Overrides offset/limit.",
    )
    batch_parser.add_argument(
        "--command",
        default=None,
        help="Command to execute for all tasks when using manual command source.",
    )
    batch_parser.add_argument(
        "--command-source",
        choices=("gold", "manual", "model"),
        default=None,
        help="How to choose the command for each task. Defaults to gold, or manual when --command is provided.",
    )
    batch_parser.add_argument(
        "--image",
        default=None,
        help="Docker image to use for the InterCode bash environment. Defaults to split-specific intercode-nl2bash-fsN.",
    )
    batch_parser.add_argument(
        "--profile",
        choices=("root", "user", "strict"),
        default="root",
        help="Execution profile for all task runs in this batch",
    )
    batch_parser.add_argument(
        "--model-name",
        default=None,
        help="Model name for query-to-command generation when using --command-source model",
    )
    batch_parser.add_argument(
        "--model-server-url",
        default=None,
        help="OpenAI-compatible model server URL when using --command-source model",
    )
    batch_parser.add_argument(
        "--summary-path",
        default=None,
        help="Path to the batch summary JSONL file",
    )

    generate_set_parser = subparsers.add_parser("generate-set", help="Generate a reusable command set from task queries")
    generate_set_parser.add_argument("--split", type=int, default=1, help="NL2Bash split number")
    generate_set_parser.add_argument("--offset", type=int, default=0, help="Starting task index for command generation")
    generate_set_parser.add_argument("--limit", type=int, default=None, help="Number of tasks to generate from the offset")
    generate_set_parser.add_argument(
        "--task-indices",
        default=None,
        help="Comma-separated task indices to generate. Overrides offset/limit.",
    )
    generate_set_parser.add_argument(
        "--model-name",
        default=None,
        help="Model name for query-to-command generation",
    )
    generate_set_parser.add_argument(
        "--model-server-url",
        default=None,
        help="OpenAI-compatible model server URL for query-to-command generation",
    )

    run_set_parser = subparsers.add_parser("run-set", help="Execute a previously generated command set under one profile")
    run_set_parser.add_argument(
        "--records-path",
        required=True,
        help="Path to commands.jsonl produced by generate-set",
    )
    run_set_parser.add_argument(
        "--profile",
        choices=("root", "user", "strict"),
        required=True,
        help="Execution profile for this command-set replay",
    )
    run_set_parser.add_argument(
        "--image",
        default=None,
        help="Override Docker image to use for the InterCode bash environment. Defaults to split-specific intercode-nl2bash-fsN.",
    )

    return parser


def create_session_results_dir(results_root=None):
    root = Path(results_root) if results_root is not None else DEFAULT_RESULTS_ROOT
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    username = getpass.getuser()
    session_dir = root / "{timestamp}_{username}".format(timestamp=timestamp, username=username)
    raw_dir = session_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    return session_dir, raw_dir


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    repo_info = ensure_intercode_repo()
    print(f"InterCode repo: {repo_info.root}")

    if args.subcommand == "list":
        selection = IntercodeTaskSelection(split=args.split)
        tasks = list_tasks(selection, limit=getattr(args, "limit", 5), offset=getattr(args, "offset", 0))
        for task in tasks:
            print(f"[{task.task_index}] {task.query}")
        return 0

    if args.subcommand == "run":
        selection = IntercodeTaskSelection(split=args.split)
        session_dir, raw_dir = create_session_results_dir()
        log_path = run_task(
            selection=selection,
            task_index=args.task_index,
            image_name=args.image,
            command=args.command,
            command_source=args.command_source,
            model_name=args.model_name,
            model_server_url=args.model_server_url,
            profile_name=args.profile,
            results_raw_dir=raw_dir,
        )
        print(f"Results directory: {session_dir}")
        print(f"Saved raw log: {log_path}")
        return 0

    if args.subcommand == "batch":
        selection = IntercodeTaskSelection(split=args.split)
        selected_indices = None
        if args.task_indices:
            selected_indices = [int(item.strip()) for item in args.task_indices.split(",") if item.strip()]

        session_dir, raw_dir = create_session_results_dir()
        summary_path = (
            ensure_path(args.summary_path)
            if args.summary_path is not None
            else session_dir / "intercode_batch_summary.jsonl"
        )
        result = run_batch(
            selection=selection,
            image_name=args.image,
            command=args.command,
            command_source=args.command_source,
            model_name=args.model_name,
            model_server_url=args.model_server_url,
            profile_name=args.profile,
            results_raw_dir=raw_dir,
            offset=args.offset,
            limit=args.limit,
            task_indices=selected_indices,
            summary_path=summary_path,
        )
        print(
            "Batch completed: run_id={run_id} attempted={attempted} completed={completed} errors={errors} dir={directory} summary={summary}".format(
                run_id=result["batch_run_id"],
                attempted=result["attempted"],
                completed=result["completed"],
                errors=result["errors"],
                directory=session_dir,
                summary=result["summary_path"],
            )
        )
        return 0

    if args.subcommand == "generate-set":
        selection = IntercodeTaskSelection(split=args.split)
        selected_indices = None
        if args.task_indices:
            selected_indices = [int(item.strip()) for item in args.task_indices.split(",") if item.strip()]
        result = create_command_set(
            selection=selection,
            model_name=args.model_name,
            model_server_url=args.model_server_url,
            offset=args.offset,
            limit=args.limit,
            task_indices=selected_indices,
        )
        print(
            "Command set created: id={command_set_id} dir={directory} records={records} tasks={count}".format(
                command_set_id=result["command_set_id"],
                directory=result["output_dir"],
                records=result["records_path"],
                count=result["task_count"],
            )
        )
        return 0

    if args.subcommand == "run-set":
        session_dir, raw_dir = create_session_results_dir()
        summary_path = session_dir / "intercode_command_set_summary.jsonl"
        result = execute_command_set(
            records_path=ensure_path(args.records_path),
            profile_name=args.profile,
            summary_path=summary_path,
            results_raw_dir=raw_dir,
            image_name=args.image,
        )
        print(
            "Command set execution completed: profile={profile} attempted={attempted} completed={completed} errors={errors} dir={directory} summary={summary}".format(
                profile=args.profile,
                attempted=result["attempted"],
                completed=result["completed"],
                errors=result["errors"],
                directory=session_dir,
                summary=result["summary_path"],
            )
        )
        return 0

    parser.error(f"Unsupported command: {args.subcommand}")
    return 1


def ensure_path(path_text):
    from pathlib import Path

    return Path(path_text)


if __name__ == "__main__":
    raise SystemExit(main())
