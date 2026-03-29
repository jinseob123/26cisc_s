import argparse
import json
from pathlib import Path


def load_jsonl(path: Path):
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def utc_timestamp(raw_payload: dict) -> str:
    return (
        raw_payload.get("created_at")
        or raw_payload.get("run", {}).get("started_at")
        or raw_payload.get("run", {}).get("finished_at")
    )


def rebuild_from_summary(summary_path: Path):
    rows = load_jsonl(summary_path)
    if not rows:
        raise ValueError(f"No rows found in summary file: {summary_path}")

    command_set_id = rows[0].get("command_set_id")
    command_set_path = rows[0].get("command_set_path")
    if not command_set_id or not command_set_path:
        raise ValueError("Summary file does not contain command set metadata")

    output_dir = Path(command_set_path).parent
    output_dir.mkdir(parents=True, exist_ok=True)
    records_path = output_dir / "commands.jsonl"
    metadata_path = output_dir / "metadata.json"

    entries = []
    for row in rows:
        raw_log_path = row.get("raw_log_path")
        if not raw_log_path:
            continue
        raw_payload = json.loads(Path(raw_log_path).read_text(encoding="utf-8"))
        task = raw_payload["task"]
        generation = raw_payload.get("generation")
        dataset_path = raw_payload.get("run", {}).get("paths", {}).get("dataset_path")
        entries.append(
            {
                "schema_version": "intercode.command_set_entry.v1",
                "command_set_id": command_set_id,
                "created_at": utc_timestamp(raw_payload),
                "selection": {
                    "split": task["split"],
                    "task_index": task["task_index"],
                },
                "task": {
                    "split": task["split"],
                    "task_index": task["task_index"],
                    "query": task["query"],
                    "gold": task["gold"],
                    "dataset_path": dataset_path,
                    "record": task["record"],
                },
                "command": raw_payload["run"]["command"]["text"],
                "generation": generation,
            }
        )

    entries.sort(key=lambda item: (item["selection"]["split"], item["selection"]["task_index"]))

    metadata = {
        "schema_version": "intercode.command_set.v1",
        "command_set_id": command_set_id,
        "created_at": entries[0]["created_at"] if entries else None,
        "benchmark": "intercode",
        "task_family": "nl2bash",
        "selection": {
            "split": entries[0]["selection"]["split"] if entries else None,
            "offset": None,
            "limit": None,
            "task_indices": [entry["selection"]["task_index"] for entry in entries],
        },
        "model_name": entries[0]["generation"]["model_name"] if entries and entries[0].get("generation") else None,
        "model_server_url": entries[0]["generation"]["model_server_url"] if entries and entries[0].get("generation") else None,
        "intercode_root": str(Path(entries[0]["task"]["dataset_path"]).parents[3]) if entries else None,
        "dataset_path": entries[0]["task"]["dataset_path"] if entries else None,
        "task_count": len(entries),
        "rebuilt_from_summary": str(summary_path),
    }

    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    with records_path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return {
        "command_set_id": command_set_id,
        "records_path": records_path,
        "metadata_path": metadata_path,
        "task_count": len(entries),
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Rebuild an InterCode command set from an existing summary/raw logs")
    parser.add_argument(
        "--summary-path",
        required=True,
        help="Path to an existing intercode_command_set_summary.jsonl file",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    result = rebuild_from_summary(Path(args.summary_path).resolve())
    print(f"Rebuilt command set: {result['command_set_id']}")
    print(f"Records path: {result['records_path']}")
    print(f"Metadata path: {result['metadata_path']}")
    print(f"Task count: {result['task_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
