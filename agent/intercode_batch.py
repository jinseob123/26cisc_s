import json
import uuid
from pathlib import Path
from typing import Optional

from intercode_wrapper import (
    DEFAULT_RESULTS_RAW_DIR,
    IntercodeTaskSelection,
    load_tasks,
    run_task,
)


DEFAULT_BATCH_SUMMARY_PATH = Path(__file__).resolve().parents[1] / "results" / "intercode_batch_summary.jsonl"


def append_jsonl(jsonl_path: Path, payload) -> None:
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def select_task_indices(selection, offset=0, limit=None, task_indices=None, intercode_root=None):
    if task_indices:
        return task_indices

    tasks = load_tasks(selection, intercode_root=intercode_root)
    end = None if limit is None else offset + limit
    return [task.task_index for task in tasks[offset:end]]


def build_summary_record(raw_log_path: Path):
    payload = load_json(raw_log_path)
    summary = payload.get("summary", {})
    run = payload.get("run", {})
    command = run.get("command", {})
    task = payload.get("task", {})
    error = payload.get("error")
    generation = payload.get("generation")
    return {
        "batch_run_id": payload.get("batch", {}).get("run_id"),
        "benchmark": payload.get("benchmark"),
        "task_family": payload.get("task_family"),
        "split": task.get("split"),
        "task_index": task.get("task_index"),
        "query": task.get("query"),
        "gold": task.get("gold"),
        "run_status": run.get("status"),
        "profile": run.get("profile"),
        "command_source": command.get("source"),
        "command_text": command.get("text"),
        "reward": summary.get("reward"),
        "done": summary.get("done"),
        "action_executed": summary.get("action_executed"),
        "agent_observation": summary.get("agent_observation"),
        "eval_observation": summary.get("eval_observation"),
        "generation_model_name": generation.get("model_name") if generation else None,
        "generation_finish_reason": generation.get("finish_reason") if generation else None,
        "raw_log_path": str(raw_log_path),
        "error_type": error.get("type") if error else None,
        "error_message": error.get("message") if error else None,
    }


def run_batch(
    selection: IntercodeTaskSelection,
    image_name: Optional[str] = None,
    command: Optional[str] = None,
    command_source: Optional[str] = None,
    model_name: Optional[str] = None,
    model_server_url: Optional[str] = None,
    profile_name: Optional[str] = None,
    intercode_root: Optional[Path] = None,
    results_raw_dir: Optional[Path] = None,
    summary_path: Optional[Path] = None,
    offset: int = 0,
    limit: Optional[int] = None,
    task_indices: Optional[list[int]] = None,
):
    batch_run_id = uuid.uuid4().hex[:12]
    selected_indices = select_task_indices(
        selection=selection,
        offset=offset,
        limit=limit,
        task_indices=task_indices,
        intercode_root=intercode_root,
    )

    resolved_raw_dir = (results_raw_dir or DEFAULT_RESULTS_RAW_DIR).resolve()
    resolved_summary_path = (summary_path or DEFAULT_BATCH_SUMMARY_PATH).resolve()

    attempted = 0
    completed = 0
    errors = 0

    for task_index in selected_indices:
        print("[BATCH] running split={split} task={task}".format(split=selection.split, task=task_index), flush=True)
        raw_log_path = run_task(
            selection=selection,
            task_index=task_index,
            image_name=image_name,
            command=command,
            command_source=command_source,
            model_name=model_name,
            model_server_url=model_server_url,
            profile_name=profile_name,
            intercode_root=intercode_root,
            results_raw_dir=resolved_raw_dir,
        )
        raw_payload = load_json(raw_log_path)
        raw_payload["batch"] = {
            "run_id": batch_run_id,
            "selection": {
                "split": selection.split,
                "offset": offset,
                "limit": limit,
                "task_indices": selected_indices if task_indices else None,
            },
        }
        raw_log_path.write_text(json.dumps(raw_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        summary_record = build_summary_record(raw_log_path)
        append_jsonl(resolved_summary_path, summary_record)

        attempted += 1
        if summary_record["run_status"] == "completed":
            completed += 1
        else:
            errors += 1

        print(
            "[BATCH] done split={split} task={task} status={status} reward={reward} log={log}".format(
                split=selection.split,
                task=task_index,
                status=summary_record["run_status"],
                reward=summary_record["reward"],
                log=raw_log_path,
            ),
            flush=True,
        )

    return {
        "batch_run_id": batch_run_id,
        "attempted": attempted,
        "completed": completed,
        "errors": errors,
        "summary_path": resolved_summary_path,
    }
