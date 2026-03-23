import json
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from intercode_agent import (
    IntercodeGenerationConfig,
    build_generation_record,
    generate_command,
)
from intercode_wrapper import (
    IntercodeTaskSelection,
    ensure_intercode_repo,
    load_tasks,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMMAND_SET_ROOT = PROJECT_ROOT / "results" / "intercode_command_sets"


def utc_timestamp() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def append_jsonl(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def select_tasks(selection, offset=0, limit=None, task_indices=None, intercode_root=None):
    tasks = load_tasks(selection, intercode_root=intercode_root)
    if task_indices:
        task_set = set(task_indices)
        return [task for task in tasks if task.task_index in task_set]
    end = None if limit is None else offset + limit
    return tasks[offset:end]


def create_command_set_dir(output_root=None):
    root = Path(output_root) if output_root is not None else DEFAULT_COMMAND_SET_ROOT
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    command_set_id = uuid.uuid4().hex[:12]
    directory = root / "{timestamp}_{command_set_id}".format(timestamp=timestamp, command_set_id=command_set_id)
    directory.mkdir(parents=True, exist_ok=True)
    return command_set_id, directory


def create_command_set(
    selection: IntercodeTaskSelection,
    model_name: Optional[str] = None,
    model_server_url: Optional[str] = None,
    output_root: Optional[Path] = None,
    offset: int = 0,
    limit: Optional[int] = None,
    task_indices: Optional[list[int]] = None,
    intercode_root: Optional[Path] = None,
):
    repo = ensure_intercode_repo(intercode_root)
    tasks = select_tasks(
        selection=selection,
        offset=offset,
        limit=limit,
        task_indices=task_indices,
        intercode_root=repo.root,
    )
    default_config = IntercodeGenerationConfig()
    config = IntercodeGenerationConfig(
        model_name=model_name or default_config.model_name,
        model_server_url=model_server_url or default_config.model_server_url,
    )

    command_set_id, output_dir = create_command_set_dir(output_root=output_root)
    records_path = output_dir / "commands.jsonl"
    metadata_path = output_dir / "metadata.json"

    metadata = {
        "schema_version": "intercode.command_set.v1",
        "command_set_id": command_set_id,
        "created_at": utc_timestamp(),
        "benchmark": "intercode",
        "task_family": "nl2bash",
        "selection": {
            "split": selection.split,
            "offset": offset,
            "limit": limit,
            "task_indices": task_indices,
        },
        "model_name": config.model_name,
        "model_server_url": config.model_server_url,
        "intercode_root": str(repo.root),
        "dataset_path": str(repo.nl2bash_dir / "nl2bash_fs_{split}.json".format(split=selection.split)),
        "task_count": len(tasks),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

    for task in tasks:
        generation_result = generate_command(task.query, config=config)
        record = {
            "schema_version": "intercode.command_set_entry.v1",
            "command_set_id": command_set_id,
            "created_at": utc_timestamp(),
            "selection": {
                "split": task.split,
                "task_index": task.task_index,
            },
            "task": {
                "split": task.split,
                "task_index": task.task_index,
                "query": task.query,
                "gold": task.gold,
                "dataset_path": str(task.dataset_path),
                "record": task.record,
            },
            "command": generation_result.command,
            "generation": build_generation_record(generation_result),
        }
        append_jsonl(records_path, record)

    return {
        "command_set_id": command_set_id,
        "output_dir": output_dir,
        "metadata_path": metadata_path,
        "records_path": records_path,
        "task_count": len(tasks),
    }
