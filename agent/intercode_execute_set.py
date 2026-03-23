import json
from pathlib import Path
from typing import Optional

from intercode_batch import append_jsonl
from intercode_wrapper import IntercodeTaskSelection, run_task


def load_command_set(records_path: Path):
    records = []
    with records_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def build_summary_record(raw_log_path: Path):
    payload = json.loads(raw_log_path.read_text(encoding="utf-8"))
    summary = payload.get("summary", {})
    run = payload.get("run", {})
    command = run.get("command", {})
    task = payload.get("task", {})
    error = payload.get("error")
    generation = payload.get("generation")
    command_set = payload.get("command_set") or {}
    return {
        "command_set_id": command_set.get("command_set_id"),
        "command_set_path": command_set.get("records_path"),
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
        "generation_retry_count": generation.get("retry_count") if generation else None,
        "raw_log_path": str(raw_log_path),
        "error_type": error.get("type") if error else None,
        "error_message": error.get("message") if error else None,
    }


def execute_command_set(
    records_path: Path,
    profile_name: str,
    summary_path: Path,
    results_raw_dir: Path,
    image_name: Optional[str] = None,
):
    records = load_command_set(records_path)
    attempted = 0
    completed = 0
    errors = 0

    for record in records:
        split = record["selection"]["split"]
        task_index = record["selection"]["task_index"]
        command = record["command"]
        print(
            "[SET] running profile={profile} split={split} task={task}".format(
                profile=profile_name,
                split=split,
                task=task_index,
            ),
            flush=True,
        )
        raw_log_path = run_task(
            selection=IntercodeTaskSelection(split=split),
            task_index=task_index,
            image_name=image_name,
            command=command,
            command_source="command_set",
            profile_name=profile_name,
            generation_override=record.get("generation"),
            command_set_info={
                "command_set_id": record.get("command_set_id"),
                "records_path": str(records_path),
            },
            results_raw_dir=results_raw_dir,
        )
        summary_record = build_summary_record(raw_log_path)
        append_jsonl(summary_path, summary_record)
        attempted += 1
        if summary_record["run_status"] == "completed":
            completed += 1
        else:
            errors += 1

    return {
        "attempted": attempted,
        "completed": completed,
        "errors": errors,
        "summary_path": summary_path,
        "records_path": records_path,
    }
