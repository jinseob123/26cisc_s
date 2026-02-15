import argparse
import json
import os
import re
from pathlib import Path

import docker
from openai import OpenAI

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_BASE = PROJECT_ROOT / "dataset" / "Intercode"
DEFAULT_SETUP_DIR = PROJECT_ROOT / "docker" / "bash_scripts"
DEFAULT_IMAGE = "sandbox-target:latest"
DEFAULT_MODEL_SERVER_URL = os.getenv("MODEL_SERVER_URL", "http://localhost:8000/v1")
DEFAULT_MODEL_NAME = os.getenv("MODEL_NAME", "Qwen/Qwen2.5-7B-Instruct")
DEFAULT_API_KEY = os.getenv("OPENAI_API_KEY", "not-needed")


def extract_bash_command(text):
    pattern = r"```(?:bash)?\n?(.*?)\n?```"
    match = re.search(pattern, text, re.DOTALL)
    cmd = match.group(1).strip() if match else text.strip()
    return cmd.replace("```bash", "").replace("```", "").strip()


def decode_output(raw):
    if not raw:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="ignore")
    return str(raw)


def run_task_with_setup(client_docker, setup_script_path, target_command, image_name, profile="STRICT"):
    profiles = {
        "STRICT": {"user": "researcher"},
        "ROOT": {"user": "root"},
    }
    conf = profiles.get(profile, profiles["STRICT"])
    container = None

    try:
        container = client_docker.containers.run(
            image=image_name,
            command=["/bin/bash", "-lc", "tail -f /dev/null"],
            detach=True,
            tty=True,
            read_only=False,
        )

        if not setup_script_path.exists():
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Setup script not found: {setup_script_path}",
            }

        script_content = setup_script_path.read_text(encoding="utf-8")
        setup_res = container.exec_run(
            ["/bin/bash", "-lc", script_content],
            user="root",
            demux=True,
        )
        setup_stdout = decode_output(setup_res.output[0] if setup_res.output else b"")
        setup_stderr = decode_output(setup_res.output[1] if setup_res.output else b"")
        if setup_res.exit_code != 0:
            return {
                "exit_code": setup_res.exit_code,
                "stdout": setup_stdout,
                "stderr": f"Setup Failed: {setup_stderr or setup_stdout}",
            }

        cmd_res = container.exec_run(
            ["/bin/bash", "-lc", target_command],
            user=conf["user"],
            demux=True,
        )

        stdout = decode_output(cmd_res.output[0] if cmd_res.output else b"")
        stderr = decode_output(cmd_res.output[1] if cmd_res.output else b"")
        return {"exit_code": cmd_res.exit_code, "stdout": stdout, "stderr": stderr}

    except Exception as e:
        return {"exit_code": -1, "stdout": "", "stderr": str(e)}
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:
                pass


def compare_gold_agent(gold_res, agent_res):
    gold_ok = gold_res["exit_code"] == 0
    agent_ok = agent_res["exit_code"] == 0

    if gold_ok and agent_ok:
        return gold_res["stdout"].strip() == agent_res["stdout"].strip()

    if (not gold_ok) and (not agent_ok):
        # Gold도 실패가 기대값인 경우가 있으므로, 둘 다 실패면 정답 처리.
        return True

    return False


def evaluate_dataset_file(
    client_docker,
    client_llm,
    model_name,
    file_path,
    setup_script_path,
    file_index,
    image_name,
    profiles,
    max_tasks=None,
):
    print(f"\n{'=' * 56}")
    print(f"[File {file_index}] dataset={file_path.name} setup={setup_script_path.name}")
    print(f"{'=' * 56}")

    if not file_path.exists():
        print(f"[SKIP] dataset not found: {file_path}")
        return {
            p: {"correct": 0, "attempted": 0, "skipped": 0}
            for p in profiles
        }

    if file_path.stat().st_size == 0:
        print(f"[SKIP] empty dataset file: {file_path}")
        return {
            p: {"correct": 0, "attempted": 0, "skipped": 0}
            for p in profiles
        }

    dataset = json.loads(file_path.read_text(encoding="utf-8"))
    if max_tasks is not None:
        dataset = dataset[:max_tasks]

    file_stats = {
        p: {"correct": 0, "attempted": 0, "skipped": 0}
        for p in profiles
    }

    for i, item in enumerate(dataset, start=1):
        task_prompt = item.get("task") or item.get("query")
        gold_cmd = item.get("gold")
        if not task_prompt or not gold_cmd:
            for p in profiles:
                file_stats[p]["skipped"] += 1
            print(f"[Task {i}] SKIP: malformed item")
            continue

        print(f"\n[Task {i}/{len(dataset)}] Q: {task_prompt}")
        print(f"[Task {i}] Gold: {gold_cmd}")

        try:
            response = client_llm.chat.completions.create(
                model=model_name,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a Linux Bash expert. Return only one bash command.",
                    },
                    {"role": "user", "content": task_prompt},
                ],
            )
            raw_content = response.choices[0].message.content or ""
            agent_cmd = extract_bash_command(raw_content)
            print(f"[Task {i}] Agent: {agent_cmd}")
        except Exception as e:
            for p in profiles:
                file_stats[p]["skipped"] += 1
            print(f"[Task {i}] SKIP: LLM call failed: {e}")
            continue

        for profile in profiles:
            gold_res = run_task_with_setup(
                client_docker=client_docker,
                setup_script_path=setup_script_path,
                target_command=gold_cmd,
                image_name=image_name,
                profile=profile,
            )

            if gold_res["exit_code"] == -1:
                file_stats[profile]["skipped"] += 1
                print(
                    f"[Task {i}][{profile}] SKIP: gold infra error: "
                    f"{gold_res['stderr'][:120]}"
                )
                continue

            agent_res = run_task_with_setup(
                client_docker=client_docker,
                setup_script_path=setup_script_path,
                target_command=agent_cmd,
                image_name=image_name,
                profile=profile,
            )
            if agent_res["exit_code"] == -1:
                file_stats[profile]["skipped"] += 1
                print(
                    f"[Task {i}][{profile}] SKIP: agent infra error: "
                    f"{agent_res['stderr'][:120]}"
                )
                continue

            file_stats[profile]["attempted"] += 1
            pass_match = compare_gold_agent(gold_res, agent_res)

            if pass_match:
                file_stats[profile]["correct"] += 1
                print(f"[Task {i}][{profile}] PASS")
            else:
                print(
                    f"[Task {i}][{profile}] FAIL: "
                    f"gold_exit={gold_res['exit_code']} agent_exit={agent_res['exit_code']} "
                    f"gold_out='{gold_res['stdout'].strip()[:60]}' "
                    f"agent_out='{agent_res['stdout'].strip()[:60]}' "
                    f"gold_err='{gold_res['stderr'][:60]}' agent_err='{agent_res['stderr'][:60]}'"
                )

    print(f"\n[File {file_index}] profile summary")
    for profile in profiles:
        correct = file_stats[profile]["correct"]
        attempted = file_stats[profile]["attempted"]
        skipped = file_stats[profile]["skipped"]
        acc = (correct / attempted * 100) if attempted else 0.0
        print(
            f"  - {profile}: correct={correct} attempted={attempted} "
            f"skipped={skipped} acc={acc:.2f}%"
        )
    return file_stats


def run_full_benchmark(
    dataset_base,
    setup_dir,
    image_name,
    model_server_url,
    model_name,
    file_indices,
    profiles,
    max_tasks=None,
):
    try:
        client_docker = docker.from_env()
    except Exception as e:
        raise RuntimeError(f"Docker client init failed: {e}") from e

    client_llm = OpenAI(base_url=model_server_url, api_key=DEFAULT_API_KEY)

    try:
        client_docker.images.get(image_name)
    except Exception as e:
        raise RuntimeError(
            f"Docker image not found or inaccessible: {image_name} ({e})"
        ) from e

    total_stats = {
        p: {"correct": 0, "attempted": 0, "skipped": 0}
        for p in profiles
    }

    for idx in file_indices:
        file_path = dataset_base / f"nl2bash_fs_{idx}.json"
        setup_script = setup_dir / f"setup_nl2b_fs_{idx}.sh"

        result = evaluate_dataset_file(
            client_docker=client_docker,
            client_llm=client_llm,
            model_name=model_name,
            file_path=file_path,
            setup_script_path=setup_script,
            file_index=idx,
            image_name=image_name,
            profiles=profiles,
            max_tasks=max_tasks,
        )
        for profile in profiles:
            total_stats[profile]["correct"] += result[profile]["correct"]
            total_stats[profile]["attempted"] += result[profile]["attempted"]
            total_stats[profile]["skipped"] += result[profile]["skipped"]

    print(f"\n{'=' * 56}")
    print("[FINAL]")
    for profile in profiles:
        correct = total_stats[profile]["correct"]
        attempted = total_stats[profile]["attempted"]
        skipped = total_stats[profile]["skipped"]
        final_acc = (correct / attempted * 100) if attempted else 0.0
        print(
            f"{profile}: correct={correct} attempted={attempted} "
            f"skipped={skipped} accuracy={final_acc:.2f}%"
        )
    print(f"{'=' * 56}")


def parse_args():
    parser = argparse.ArgumentParser(description="Intercode NL2Bash benchmark runner")
    parser.add_argument(
        "--dataset-base",
        type=Path,
        default=DEFAULT_DATASET_BASE,
        help="Dataset directory path",
    )
    parser.add_argument(
        "--setup-dir",
        type=Path,
        default=DEFAULT_SETUP_DIR,
        help="Setup script directory path",
    )
    parser.add_argument(
        "--image",
        default=DEFAULT_IMAGE,
        help="Docker image for sandbox",
    )
    parser.add_argument(
        "--model-server-url",
        default=DEFAULT_MODEL_SERVER_URL,
        help="OpenAI-compatible model server URL",
    )
    parser.add_argument(
        "--model-name",
        default=DEFAULT_MODEL_NAME,
        help="Model name",
    )
    parser.add_argument(
        "--files",
        default="1,2,3,4",
        help="Comma-separated file indices. Example: 3,4",
    )
    parser.add_argument(
        "--max-tasks",
        type=int,
        default=None,
        help="Limit tasks per dataset file",
    )
    parser.add_argument(
        "--profiles",
        default="STRICT,ROOT",
        help="Comma-separated profiles. Example: STRICT,ROOT",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    selected_files = [int(x.strip()) for x in args.files.split(",") if x.strip()]
    selected_profiles = [x.strip().upper() for x in args.profiles.split(",") if x.strip()]
    try:
        run_full_benchmark(
            dataset_base=args.dataset_base,
            setup_dir=args.setup_dir,
            image_name=args.image,
            model_server_url=args.model_server_url,
            model_name=args.model_name,
            file_indices=selected_files,
            profiles=selected_profiles,
            max_tasks=args.max_tasks,
        )
    except Exception as e:
        print(f"[FATAL] {e}")
