import json
import sys
import traceback
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INTERCODE_ROOT = PROJECT_ROOT / "bench" / "intercode"
DEFAULT_RESULTS_RAW_DIR = PROJECT_ROOT / "results" / "raw"
RAW_LOG_SCHEMA_VERSION = "intercode.raw.v1"
DEFAULT_NL2BASH_IMAGE_TEMPLATE = "intercode-nl2bash-fs{split}"


@dataclass(frozen=True)
class IntercodeRepoInfo:
    root: Path
    data_dir: Path
    nl2bash_dir: Path
    bash_scripts_dir: Path


@dataclass(frozen=True)
class IntercodeTaskSelection:
    split: int = 1


@dataclass(frozen=True)
class IntercodeTask:
    split: int
    task_index: int
    dataset_path: Path
    query: str
    gold: str
    record: dict[str, Any]


@dataclass(frozen=True)
class IntercodeExecutionProfile:
    name: str
    exec_user: Optional[str]
    eval_exec_user: Optional[str]


PROFILE_MAP = {
    "root": IntercodeExecutionProfile(name="root", exec_user="root", eval_exec_user="root"),
    "user": IntercodeExecutionProfile(name="user", exec_user="ubuntu", eval_exec_user="root"),
    "strict": IntercodeExecutionProfile(name="strict", exec_user="nobody", eval_exec_user="root"),
}


def ensure_intercode_repo(intercode_root: Optional[Path] = None) -> IntercodeRepoInfo:
    root = (intercode_root or DEFAULT_INTERCODE_ROOT).resolve()
    required_paths = {
        "root": root,
        "data_dir": root / "data",
        "nl2bash_dir": root / "data" / "nl2bash",
        "bash_scripts_dir": root / "docker" / "bash_scripts",
    }
    missing = [name for name, path in required_paths.items() if not path.exists()]
    if missing:
        missing_text = ", ".join(missing)
        raise FileNotFoundError(f"InterCode repo is missing required paths: {missing_text} under {root}")

    return IntercodeRepoInfo(
        root=root,
        data_dir=required_paths["data_dir"],
        nl2bash_dir=required_paths["nl2bash_dir"],
        bash_scripts_dir=required_paths["bash_scripts_dir"],
    )


def get_dataset_path(selection: IntercodeTaskSelection, repo: IntercodeRepoInfo) -> Path:
    return repo.nl2bash_dir / f"nl2bash_fs_{selection.split}.json"


def load_tasks(selection: IntercodeTaskSelection, intercode_root: Optional[Path] = None) -> list[IntercodeTask]:
    repo = ensure_intercode_repo(intercode_root)
    dataset_path = get_dataset_path(selection, repo)
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset file not found: {dataset_path}")

    raw_records = json.loads(dataset_path.read_text(encoding="utf-8"))
    tasks: list[IntercodeTask] = []
    for task_index, record in enumerate(raw_records):
        tasks.append(
            IntercodeTask(
                split=selection.split,
                task_index=task_index,
                dataset_path=dataset_path,
                query=record["query"],
                gold=record["gold"],
                record=record,
            )
        )
    return tasks


def list_tasks(
    selection: IntercodeTaskSelection,
    limit: int = 5,
    offset: int = 0,
    intercode_root: Optional[Path] = None,
) -> list[IntercodeTask]:
    tasks = load_tasks(selection, intercode_root=intercode_root)
    return tasks[offset : offset + limit]


def get_task(
    selection: IntercodeTaskSelection,
    task_index: int,
    intercode_root: Optional[Path] = None,
) -> IntercodeTask:
    tasks = load_tasks(selection, intercode_root=intercode_root)
    try:
        return tasks[task_index]
    except IndexError as exc:
        raise IndexError(f"Task index {task_index} is out of range for split {selection.split}") from exc


def add_intercode_to_sys_path(repo: IntercodeRepoInfo) -> None:
    repo_root = str(repo.root)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)


def make_log_path(task: IntercodeTask, results_raw_dir: Optional[Path] = None) -> Path:
    raw_dir = (results_raw_dir or DEFAULT_RESULTS_RAW_DIR).resolve()
    raw_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return raw_dir / f"intercode_split{task.split}_task{task.task_index}_{timestamp}.json"


def utc_timestamp() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def build_run_payload(
    task: IntercodeTask,
    repo: IntercodeRepoInfo,
    image_name: str,
    selected_command: Optional[str],
    command_source: str,
    profile: IntercodeExecutionProfile,
    generation: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "schema_version": RAW_LOG_SCHEMA_VERSION,
        "benchmark": "intercode",
        "task_family": "nl2bash",
        "created_at": utc_timestamp(),
        "run": {
            "status": "running",
            "started_at": utc_timestamp(),
            "finished_at": None,
            "image_name": image_name,
            "command": {
                "text": selected_command,
                "source": command_source,
            },
            "paths": {
                "intercode_root": str(repo.root),
                "dataset_path": str(task.dataset_path),
            },
            "profile": profile.name,
        },
        "generation": generation,
        "task": {
            "split": task.split,
            "task_index": task.task_index,
            "query": task.query,
            "gold": task.gold,
            "record": task.record,
        },
        "steps": {
            "reset": None,
            "action": None,
            "submit": None,
        },
        "artifacts": {
            "trajectory": [],
        },
        "summary": {
            "reward": None,
            "done": None,
            "action_executed": None,
            "agent_observation": None,
            "eval_observation": None,
            "corrupt_gold": None,
        },
        "error": None,
    }


def write_raw_log(payload: dict[str, Any], log_path: Path) -> None:
    log_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def summarize_submit_info(payload: dict[str, Any], submit_info: dict[str, Any]) -> None:
    summary = payload["summary"]
    summary["reward"] = payload["steps"]["submit"]["reward"]
    summary["done"] = payload["steps"]["submit"]["done"]
    summary["action_executed"] = submit_info.get("action_executed")
    summary["agent_observation"] = submit_info.get("agent_obs")
    summary["eval_observation"] = submit_info.get("eval_obs")
    summary["corrupt_gold"] = (
        submit_info.get("corrupt_gold")
        if submit_info.get("corrupt_gold") is not None
        else payload["steps"]["action"]["info"].get("corrupt_gold")
    )


def resolve_command_source(command_source: Optional[str], command: Optional[str]) -> str:
    if command_source is None:
        return "manual" if command is not None else "gold"
    if command_source not in {"gold", "manual", "model", "command_set"}:
        raise ValueError("Unsupported command source: {source}".format(source=command_source))
    if command_source in {"manual", "command_set"} and not command:
        raise ValueError("`--command-source {source}` requires `--command`".format(source=command_source))
    if command_source in {"gold", "model"} and command:
        raise ValueError("`--command` can only be used with manual command source")
    return command_source


def resolve_execution_profile(profile_name: Optional[str]) -> IntercodeExecutionProfile:
    profile_key = (profile_name or "root").strip().lower()
    if profile_key not in PROFILE_MAP:
        raise ValueError("Unsupported execution profile: {profile}".format(profile=profile_name))
    return PROFILE_MAP[profile_key]


def resolve_image_name(selection: IntercodeTaskSelection, image_name: Optional[str]) -> str:
    if image_name:
        return image_name
    return DEFAULT_NL2BASH_IMAGE_TEMPLATE.format(split=selection.split)


def resolve_run_command(
    task: IntercodeTask,
    command_source: str,
    command: Optional[str],
    model_name: Optional[str],
    model_server_url: Optional[str],
    generation_override: Optional[dict[str, Any]] = None,
):
    if command_source == "gold":
        return task.gold, None
    if command_source in {"manual", "command_set"}:
        if generation_override:
            return command, generation_override
        return command, None
    if command_source == "model":
        from intercode_agent import (
            IntercodeGenerationConfig,
            build_generation_record,
            generate_command,
        )

        default_generation_config = IntercodeGenerationConfig()
        generation_config = IntercodeGenerationConfig(
            model_name=model_name or default_generation_config.model_name,
            model_server_url=model_server_url or default_generation_config.model_server_url,
        )
        generation_result = generate_command(task.query, config=generation_config)
        generation_record = build_generation_record(generation_result)
        if not generation_result.command:
            raise RuntimeError("Model did not return a valid bash command")
        return generation_result.command, generation_record
    raise ValueError("Unsupported command source: {source}".format(source=command_source))


def run_task(
    selection: IntercodeTaskSelection,
    task_index: int,
    image_name: Optional[str] = None,
    command: Optional[str] = None,
    command_source: Optional[str] = None,
    model_name: Optional[str] = None,
    model_server_url: Optional[str] = None,
    profile_name: Optional[str] = None,
    generation_override: Optional[dict[str, Any]] = None,
    command_set_info: Optional[dict[str, Any]] = None,
    intercode_root: Optional[Path] = None,
    results_raw_dir: Optional[Path] = None,
) -> Path:
    repo = ensure_intercode_repo(intercode_root)
    add_intercode_to_sys_path(repo)
    task = get_task(selection, task_index=task_index, intercode_root=repo.root)
    resolved_command_source = resolve_command_source(command_source, command)
    execution_profile = resolve_execution_profile(profile_name)
    resolved_image_name = resolve_image_name(selection, image_name)
    log_path = make_log_path(task, results_raw_dir=results_raw_dir)
    payload = build_run_payload(
        task=task,
        repo=repo,
        image_name=resolved_image_name,
        selected_command=None,
        command_source=resolved_command_source,
        profile=execution_profile,
    )

    env = None

    try:
        selected_command, generation = resolve_run_command(
            task=task,
            command_source=resolved_command_source,
            command=command,
            model_name=model_name,
            model_server_url=model_server_url,
            generation_override=generation_override,
        )
        payload["run"]["command"]["text"] = selected_command
        payload["generation"] = generation
        if command_set_info is not None:
            payload["command_set"] = command_set_info

        from intercode.envs import BashEnv

        env = BashEnv(
            resolved_image_name,
            data_path=str(task.dataset_path),
            verbose=False,
            exec_user=execution_profile.exec_user,
            eval_exec_user=execution_profile.eval_exec_user,
        )

        reset_observation, reset_info = env.reset(index=task.task_index)
        payload["steps"]["reset"] = {
            "observation": reset_observation,
            "info": deepcopy(reset_info),
        }

        action_observation, _, _, action_info = env.step(selected_command)
        payload["steps"]["action"] = {
            "command": selected_command,
            "observation": action_observation,
            "info": deepcopy(action_info),
        }

        final_observation, reward, done, submit_info = env.step("submit")
        payload["steps"]["submit"] = {
            "command": "submit",
            "observation": final_observation,
            "reward": reward,
            "done": done,
            "info": deepcopy(submit_info),
        }
        payload["artifacts"]["trajectory"] = [
            {
                "action": action,
                "observation": observation,
            }
            for action, observation in env.trajectory
        ]
        summarize_submit_info(payload, submit_info)
        payload["run"]["status"] = "completed"
    except Exception as exc:
        payload["run"]["status"] = "error"
        payload["artifacts"]["trajectory"] = [
            {
                "action": action,
                "observation": observation,
            }
            for action, observation in getattr(env, "trajectory", [])
        ]
        payload["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
    finally:
        payload["run"]["finished_at"] = utc_timestamp()
        try:
            if env is not None:
                env.close()
        finally:
            write_raw_log(payload, log_path)

    return log_path
