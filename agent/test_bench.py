import argparse
import hashlib
import json
import os
import re
import shlex
from pathlib import Path

import docker
from openai import OpenAI

# --- [상단 설정] ---
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_BASE = PROJECT_ROOT / "bench" / "intercode" / "data" / "nl2bash"
RESULTS_DIR = PROJECT_ROOT / "results"
MODEL_SERVER_URL = os.getenv("MODEL_SERVER_URL") or os.getenv("MODEL_URL") or "http://localhost:8000/v1"
MODEL_NAME = os.getenv("MODEL_NAME", "openai/gpt-oss-safeguard-20b")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "not-needed")
MAX_FEEDBACK_STEPS = int(os.getenv("MAX_FEEDBACK_STEPS", "3"))
PREVIEW_CHARS = int(os.getenv("PREVIEW_CHARS", "240"))
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "0"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "128"))
LLM_EMPTY_CONTENT_RETRY_TOKENS = int(os.getenv("LLM_EMPTY_CONTENT_RETRY_TOKENS", "384"))
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "low")
COMMAND_TIMEOUT_SECONDS = int(os.getenv("COMMAND_TIMEOUT_SECONDS", "30"))
STRICT_MEM_LIMIT = os.getenv("STRICT_MEM_LIMIT", "512m")
ROOT_MEM_LIMIT = os.getenv("ROOT_MEM_LIMIT", "1g")
NETWORK_DISABLED = os.getenv("NETWORK_DISABLED", "0") == "1"
OVERALL_PASS_MODE = os.getenv("OVERALL_PASS_MODE", "both").strip().lower()
STATE_ROOTS = ("/testbed", "/system", "/workspace", "/home/researcher")

client_llm = OpenAI(base_url=MODEL_SERVER_URL, api_key=OPENAI_API_KEY)


def truncate_text(text, limit=PREVIEW_CHARS):
    if text is None:
        return ""
    text = str(text).replace("\n", "\\n")
    if len(text) <= limit:
        return text
    return text[:limit] + "...(truncated)"


def append_log_line(log_path, message):
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(message + "\n")


def append_jsonl(jsonl_path, payload):
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def extract_bash_command(text):
    """
    백틱 3개와 bash 선언문을 제거해 명령어 본문만 추출한다.
    """
    pattern = r"```(?:bash)?\n?(.*?)\n?```"
    match = re.search(pattern, text or "", re.DOTALL)
    if match:
        cmd = match.group(1).strip()
    else:
        cmd = (text or "").strip()
    return cmd.replace("```bash", "").replace("```", "").strip()


def strip_fence_prefix(text):
    text = (text or "").strip()
    if text.startswith("```bash"):
        return text[len("```bash") :].strip()
    if text.startswith("```"):
        return text[len("```") :].strip()
    return text


def has_balanced_pairs(text, open_ch, close_ch):
    depth = 0
    for ch in text or "":
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def has_balanced_quotes(text):
    single = 0
    double = 0
    escaped = False
    for ch in text or "":
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == "'" and double % 2 == 0:
            single ^= 1
        elif ch == '"' and single % 2 == 0:
            double ^= 1
    return single == 0 and double == 0


def looks_complete_command(command):
    command = (command or "").strip()
    if not command:
        return False
    if command in {"```", "bash", "sh"}:
        return False
    lowered = command.lower()
    if lowered.startswith(
        (
            "need ",
            "use ",
            "maybe ",
            "try ",
            "the command",
            "we need",
            "i would",
            "this command",
            "that command",
        )
    ):
        return False
    if "..." in command:
        return False
    if any(
        phrase in lowered
        for phrase in (
            "something like",
            "or use",
            "but need",
            "so:",
            " so ",
            " then ",
            " of that ",
            " actually ",
        )
    ):
        return False
    if command.endswith(("|", "&&", "||", "\\", "$(", "`")):
        return False
    if not has_balanced_pairs(command, "(", ")"):
        return False
    if not has_balanced_quotes(command):
        return False
    return True


def extract_command_from_reasoning(payload):
    if not isinstance(payload, dict):
        return ""

    texts = []
    for key in ("reasoning_content", "reasoning", "content"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            texts.append(value)

    for text in texts:
        direct = extract_bash_command(text)
        if looks_complete_command(direct):
            return direct

        fence_match = re.search(r"```(?:bash|sh)?\n?(.*?)\n?```", text, re.DOTALL)
        if fence_match:
            fenced = extract_bash_command(fence_match.group(0))
            if looks_complete_command(fenced):
                return fenced

        command_match = re.search(r"(?im)^(?:final command|command)\s*:\s*(.+)$", text)
        if command_match:
            candidate = command_match.group(1).strip().strip("`")
            if looks_complete_command(candidate):
                return candidate

        use_match = re.search(r"(?i)\b(?:use|run)\s+([^\n]+)", text)
        if use_match:
            candidate = use_match.group(1).strip().strip("`").rstrip(".")
            if any(token in candidate for token in ("/testbed", "|", " xargs ", " find ", " grep ", " awk ", " wc ")):
                if looks_complete_command(candidate):
                    return candidate

    return ""


def decode_output(raw):
    if not raw:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="ignore")
    return str(raw)


def load_dataset(file_path):
    """
    지정된 경로의 JSON 데이터셋 파일을 로드한다.
    """
    if not os.path.exists(file_path):
        print(f"Error: 파일을 찾을 수 없다 => {file_path}")
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"데이터 로드 완료: {len(data)}개 태스크")
        return data
    except json.JSONDecodeError:
        print("Error: JSON 형식이 올바르지 않습니다.")
        return None
    except Exception as e:
        print(f"Error: 알 수 없는 오류 발생 -> {e}")
        return None


def get_llm_command(query, feedback_context=""):
    """
    질의(+이전 실패 피드백)를 바탕으로 명령어 1개를 생성한다.
    """
    system_message = (
        "You are a Linux Bash expert. "
        "Return exactly one bash command. "
        "Prefer plain text or ```bash ... ```. "
        "Do not add explanation."
    )

    user_message = f"Task:\n{query}"
    if feedback_context:
        user_message += f"\n\nFeedback from previous attempt:\n{feedback_context}"

    try:
        def request_once(system_prompt, temperature, max_tokens):
            req_kwargs = {}
            if max_tokens > 0:
                req_kwargs["max_tokens"] = max_tokens
            if LLM_TIMEOUT_SECONDS > 0:
                req_kwargs["timeout"] = LLM_TIMEOUT_SECONDS
            if LLM_REASONING_EFFORT in {"low", "medium", "high"}:
                req_kwargs["reasoning_effort"] = LLM_REASONING_EFFORT
            return client_llm.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=temperature,
                **req_kwargs,
            )

        response = request_once(system_message, temperature=0.1, max_tokens=LLM_MAX_TOKENS)
        raw_payload = response.choices[0].message.model_dump()
        raw_text = response.choices[0].message.content or ""
        command = extract_bash_command(raw_text)
        if not looks_complete_command(command):
            recovered = extract_command_from_reasoning(raw_payload)
            if looks_complete_command(recovered):
                command = recovered

        if not looks_complete_command(command):
            retry_response = request_once(
                "Output only one final bash command as plain text. No explanation.",
                temperature=0,
                max_tokens=max(LLM_EMPTY_CONTENT_RETRY_TOKENS, LLM_MAX_TOKENS),
            )
            response = retry_response
            raw_payload = response.choices[0].message.model_dump()
            raw_text = response.choices[0].message.content or ""
            command = extract_bash_command(raw_text)
            if not looks_complete_command(command):
                recovered = extract_command_from_reasoning(raw_payload)
                if looks_complete_command(recovered):
                    command = recovered

        return command.strip(), (raw_text or json.dumps(raw_payload, ensure_ascii=False))
    except Exception as e:
        print(f"LLM 요청 중 오류 발생: {e}")
        return None, ""


def build_timeout_command(command):
    safe_inner = shlex.quote(f"set -o pipefail; {command}")
    return [
        "/bin/bash",
        "-lc",
        f"timeout --signal=KILL {COMMAND_TIMEOUT_SECONDS}s bash -lc {safe_inner}",
    ]


def capture_state_digest(container):
    roots_for_script = " ".join(shlex.quote(root) for root in STATE_ROOTS)
    snapshot_script = f"""
set -eu
roots="{roots_for_script}"
for root in $roots; do
    if [ -e "$root" ]; then
        echo "__ROOT__ $root"
        find "$root" -xdev -type d -printf 'D|%m|%u|%g|%p\\n' 2>/dev/null | LC_ALL=C sort
        find "$root" -xdev ! -type d ! -type l -printf 'N|%y|%m|%u|%g|%s|%p\\n' 2>/dev/null | LC_ALL=C sort
        find "$root" -xdev -type l -printf 'L|%p|%l\\n' 2>/dev/null | LC_ALL=C sort
        find "$root" -xdev -type f -exec sha256sum {{}} + 2>/dev/null | LC_ALL=C sort
    fi
done
"""
    res = container.exec_run(
        ["/bin/bash", "-lc", snapshot_script],
        user="root",
        demux=True,
    )
    snapshot_stdout = decode_output(res.output[0] if res.output else b"")
    snapshot_stderr = decode_output(res.output[1] if res.output else b"")
    raw = f"{snapshot_stdout}\n__ERR__\n{snapshot_stderr}"
    digest = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return digest, snapshot_stderr


def execute_in_sandbox(command, task_id, profile_name="STRICT"):
    """
    주어진 명령어를 도커 컨테이너에서 실행하고 stdout/stderr/exit_code/state_hash를 반환한다.
    """
    client = docker.from_env()
    profiles = {
        "STRICT": {
            "user": "researcher",
            "network_disabled": NETWORK_DISABLED,
            "mem_limit": STRICT_MEM_LIMIT,
        },
        "ROOT": {
            "user": "root",
            "network_disabled": NETWORK_DISABLED,
            "mem_limit": ROOT_MEM_LIMIT,
        },
    }
    config = profiles.get(profile_name, profiles["STRICT"])
    script_path = str(PROJECT_ROOT / "docker" / "bash_scripts")
    setup_script = f"/mnt/scripts/setup_nl2b_fs_{task_id}.sh"
    container = None

    try:
        container = client.containers.run(
            image="sandbox-target:latest",
            command=["/bin/bash", "-lc", "tail -f /dev/null"],
            user="root",
            volumes={script_path: {"bind": "/mnt/scripts", "mode": "ro"}},
            network_disabled=config["network_disabled"],
            mem_limit=config["mem_limit"],
            detach=True,
            tty=True,
        )

        setup_res = container.exec_run(
            build_timeout_command(f"bash /mnt/scripts/setup_fs.sh && bash {setup_script}"),
            user="root",
            demux=True,
        )
        setup_stdout = decode_output(setup_res.output[0] if setup_res.output else b"")
        setup_stderr = decode_output(setup_res.output[1] if setup_res.output else b"")
        if setup_res.exit_code != 0:
            return {
                "stdout": setup_stdout,
                "stderr": f"Setup Failed: {setup_stderr or setup_stdout}",
                "exit_code": setup_res.exit_code,
                "state_hash": "",
            }

        cmd_res = container.exec_run(
            build_timeout_command(command),
            user=config["user"],
            demux=True,
        )
        stdout = decode_output(cmd_res.output[0] if cmd_res.output else b"")
        stderr = decode_output(cmd_res.output[1] if cmd_res.output else b"")
        state_hash, snapshot_stderr = capture_state_digest(container)
        if snapshot_stderr:
            stderr = f"{stderr}\n[SNAPSHOT_WARN] {snapshot_stderr}".strip()
        return {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": cmd_res.exit_code,
            "state_hash": state_hash,
        }
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "exit_code": -1, "state_hash": ""}
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:
                pass


def results_match(gold_result, candidate_result):
    if gold_result["exit_code"] != candidate_result["exit_code"]:
        return False
    if gold_result["stdout"].strip() != candidate_result["stdout"].strip():
        return False
    gold_stderr = gold_result["stderr"].strip()
    if gold_stderr and gold_stderr != candidate_result["stderr"].strip():
        return False
    return gold_result.get("state_hash", "") == candidate_result.get("state_hash", "")


def is_overall_pass(root_match, strict_match):
    mode = OVERALL_PASS_MODE
    if mode == "root":
        return root_match
    if mode == "strict":
        return strict_match
    if mode == "either":
        return root_match or strict_match
    return root_match and strict_match


def run_agent_with_feedback(query, task_id, gold_root, gold_strict, log_path, max_steps=MAX_FEEDBACK_STEPS):
    """
    Agent 명령 생성 -> 샌드박스 실행 -> 실패 피드백 반영을 반복한다.
    """
    feedback_context = ""
    attempts = []

    for step in range(1, max_steps + 1):
        command, raw_text = get_llm_command(query, feedback_context)
        if not command:
            attempt = {
                "step": step,
                "command": "",
                "raw_model_output": raw_text,
                "root_exit_code": -1,
                "root_stdout": "",
                "root_stderr": "LLM command generation failed",
                "root_state_hash": "",
                "strict_exit_code": -1,
                "strict_stdout": "",
                "strict_stderr": "LLM command generation failed",
                "strict_state_hash": "",
                "root_match": False,
                "strict_match": False,
                "overall_pass": False,
            }
            attempts.append(attempt)
            append_log_line(log_path, f"[STEP {step}] FAIL: LLM command generation failed")
            print(f"  [STEP {step}] CMD 생성 실패")
            break

        root_result = execute_in_sandbox(command, task_id=task_id, profile_name="ROOT")
        strict_result = execute_in_sandbox(command, task_id=task_id, profile_name="STRICT")
        root_stdout = root_result["stdout"].strip()
        strict_stdout = strict_result["stdout"].strip()
        root_stderr = root_result["stderr"].strip()
        strict_stderr = strict_result["stderr"].strip()
        root_match = results_match(gold_root, root_result)
        strict_match = results_match(gold_strict, strict_result)
        overall_pass = is_overall_pass(root_match, strict_match)

        attempt = {
            "step": step,
            "command": command,
            "raw_model_output": raw_text,
            "root_exit_code": root_result["exit_code"],
            "root_stdout": root_result["stdout"],
            "root_stderr": root_result["stderr"],
            "root_state_hash": root_result.get("state_hash", ""),
            "strict_exit_code": strict_result["exit_code"],
            "strict_stdout": strict_result["stdout"],
            "strict_stderr": strict_result["stderr"],
            "strict_state_hash": strict_result.get("state_hash", ""),
            "root_match": root_match,
            "strict_match": strict_match,
            "overall_pass": overall_pass,
        }
        attempts.append(attempt)

        append_log_line(
            log_path,
            (
                f"[STEP {step}] cmd={command} "
                f"root_exit={root_result['exit_code']} strict_exit={strict_result['exit_code']} "
                f"root_match={root_match} strict_match={strict_match} overall={overall_pass} "
                f"root_hash={root_result.get('state_hash', '')} strict_hash={strict_result.get('state_hash', '')}"
            ),
        )
        print(f"  [STEP {step}] CMD: {command}")
        print(f"  [STEP {step}] ROOT   exit={root_result['exit_code']} match={root_match}")
        print(f"  [STEP {step}] STRICT exit={strict_result['exit_code']} match={strict_match}")
        if not overall_pass:
            print(f"  [STEP {step}] EXPECTED_ROOT    : {truncate_text(gold_root['stdout'].strip())}")
            print(f"  [STEP {step}] EXPECTED_STRICT  : {truncate_text(gold_strict['stdout'].strip())}")
            print(f"  [STEP {step}] ROOT_ACTUAL      : {truncate_text(root_stdout)}")
            print(f"  [STEP {step}] STRICT_ACTUAL    : {truncate_text(strict_stdout)}")
            print(f"  [STEP {step}] ROOT_HASH exp/act: {gold_root.get('state_hash', '')}/{root_result.get('state_hash', '')}")
            print(f"  [STEP {step}] STRICT_HASH exp/act: {gold_strict.get('state_hash', '')}/{strict_result.get('state_hash', '')}")
            if root_stderr:
                print(f"  [STEP {step}] ROOT_STDERR : {truncate_text(root_stderr)}")
            if strict_stderr:
                print(f"  [STEP {step}] STRICT_STDERR: {truncate_text(strict_stderr)}")
            append_log_line(log_path, f"[STEP {step}] expected_root={truncate_text(gold_root['stdout'].strip())}")
            append_log_line(log_path, f"[STEP {step}] expected_strict={truncate_text(gold_strict['stdout'].strip())}")
            append_log_line(log_path, f"[STEP {step}] root_actual={truncate_text(root_stdout)}")
            append_log_line(log_path, f"[STEP {step}] strict_actual={truncate_text(strict_stdout)}")

        if overall_pass:
            return {"overall_pass": True, "root_pass": root_match, "strict_pass": strict_match, "attempts": attempts}

        feedback_context = (
            f"Previous command: {command}\n"
            f"ROOT exit: {root_result['exit_code']}\n"
            f"ROOT stdout: {truncate_text(root_result['stdout'])}\n"
            f"ROOT stderr: {truncate_text(root_result['stderr'])}\n"
            f"ROOT state hash: {root_result.get('state_hash', '')}\n"
            f"STRICT exit: {strict_result['exit_code']}\n"
            f"STRICT stdout: {truncate_text(strict_result['stdout'])}\n"
            f"STRICT stderr: {truncate_text(strict_result['stderr'])}\n"
            f"STRICT state hash: {strict_result.get('state_hash', '')}\n"
            f"Expected ROOT stdout: {truncate_text(gold_root['stdout'])}\n"
            f"Expected ROOT state hash: {gold_root.get('state_hash', '')}\n"
            f"Expected STRICT stdout: {truncate_text(gold_strict['stdout'])}\n"
            f"Expected STRICT state hash: {gold_strict.get('state_hash', '')}\n"
            "Please provide a corrected single bash command."
        )

    root_any_pass = any(a.get("root_match") for a in attempts)
    strict_any_pass = any(a.get("strict_match") for a in attempts)
    return {
        "overall_pass": False,
        "root_pass": root_any_pass,
        "strict_pass": strict_any_pass,
        "attempts": attempts,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Test benchmark runner")
    parser.add_argument(
        "--files",
        default="1,2,3,4",
        help="Comma-separated file indices. Example: 3,4",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=RESULTS_DIR,
        help="Directory to write benchmark logs and metrics",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    selected_files = [int(x.strip()) for x in args.files.split(",") if x.strip()]

    results_dir = args.results_dir
    results_dir.mkdir(parents=True, exist_ok=True)

    dataset_paths = {
        file_idx: DATASET_BASE / f"nl2bash_fs_{file_idx}.json"
        for file_idx in selected_files
    }
    total_attempted = 0
    total_overall_passed = 0
    total_root_passed = 0
    total_strict_passed = 0

    print(
        f"[CONFIG] model={MODEL_NAME} model_server={MODEL_SERVER_URL} "
        f"network_disabled={NETWORK_DISABLED} overall_mode={OVERALL_PASS_MODE}"
    )

    for file_idx, path in dataset_paths.items():
        log_path = results_dir / f"file_{file_idx}.log"
        metrics_path = results_dir / f"file_{file_idx}_metrics.jsonl"
        log_path.write_text("", encoding="utf-8")
        metrics_path.write_text("", encoding="utf-8")

        print(f"\n{'=' * 60}")
        print(f"[FILE {file_idx}] {path}")
        print(f"{'=' * 60}")
        append_log_line(log_path, f"[FILE {file_idx}] start dataset={path}")

        dataset = load_dataset(path)
        if not dataset:
            append_log_line(log_path, f"[FILE {file_idx}] skip: dataset load failed")
            continue

        file_attempted = 0
        file_overall_passed = 0
        file_root_passed = 0
        file_strict_passed = 0

        for task_idx, item in enumerate(dataset, start=1):
            query = item.get("query") or item.get("task")
            gold = item.get("gold")

            if not query or not gold:
                append_log_line(log_path, f"[TASK {task_idx}] SKIP: query/gold missing")
                append_jsonl(
                    metrics_path,
                    {
                        "task_idx": task_idx,
                        "status": "skip_missing_fields",
                    },
                )
                continue

            print(f"[FILE {file_idx}][TASK {task_idx}] start")
            print(f"[FILE {file_idx}][TASK {task_idx}] query: {query}")
            append_log_line(log_path, f"[TASK {task_idx}] query={query}")
            append_log_line(log_path, f"[TASK {task_idx}] gold_cmd={gold}")

            gold_root = execute_in_sandbox(gold, task_id=file_idx, profile_name="ROOT")
            if gold_root["exit_code"] != 0:
                append_log_line(
                    log_path,
                    f"[TASK {task_idx}] SKIP: gold root failed exit={gold_root['exit_code']}",
                )
                append_jsonl(
                    metrics_path,
                    {
                        "task_idx": task_idx,
                        "status": "skip_gold_failed_root",
                        "gold_root_exit_code": gold_root["exit_code"],
                        "gold_root_stderr": gold_root["stderr"],
                    },
                )
                continue

            gold_strict = execute_in_sandbox(gold, task_id=file_idx, profile_name="STRICT")
            if gold_strict["exit_code"] != 0:
                append_log_line(
                    log_path,
                    f"[TASK {task_idx}] SKIP: gold strict failed exit={gold_strict['exit_code']}",
                )
                append_jsonl(
                    metrics_path,
                    {
                        "task_idx": task_idx,
                        "status": "skip_gold_failed_strict",
                        "gold_strict_exit_code": gold_strict["exit_code"],
                        "gold_strict_stderr": gold_strict["stderr"],
                    },
                )
                continue

            print(f"[FILE {file_idx}][TASK {task_idx}] gold_cmd: {gold}")
            print(f"[FILE {file_idx}][TASK {task_idx}] expected_root: {truncate_text(gold_root['stdout'].strip())}")
            print(f"[FILE {file_idx}][TASK {task_idx}] expected_strict: {truncate_text(gold_strict['stdout'].strip())}")
            append_log_line(log_path, f"[TASK {task_idx}] expected_root={truncate_text(gold_root['stdout'].strip())}")
            append_log_line(log_path, f"[TASK {task_idx}] expected_strict={truncate_text(gold_strict['stdout'].strip())}")

            loop_result = run_agent_with_feedback(
                query=query,
                task_id=file_idx,
                gold_root=gold_root,
                gold_strict=gold_strict,
                log_path=log_path,
                max_steps=MAX_FEEDBACK_STEPS,
            )

            file_attempted += 1
            total_attempted += 1

            if loop_result["root_pass"]:
                file_root_passed += 1
                total_root_passed += 1

            if loop_result["strict_pass"]:
                file_strict_passed += 1
                total_strict_passed += 1

            if loop_result["overall_pass"]:
                file_overall_passed += 1
                total_overall_passed += 1
                print(f"[FILE {file_idx}][TASK {task_idx}] PASS")
                append_log_line(log_path, f"[TASK {task_idx}] PASS")
            else:
                print(f"[FILE {file_idx}][TASK {task_idx}] FAIL")
                append_log_line(log_path, f"[TASK {task_idx}] FAIL")

            append_jsonl(
                metrics_path,
                {
                    "task_idx": task_idx,
                    "query": query,
                    "gold": gold,
                    "gold_root_stdout": gold_root["stdout"].strip(),
                    "gold_root_state_hash": gold_root.get("state_hash", ""),
                    "gold_strict_stdout": gold_strict["stdout"].strip(),
                    "gold_strict_state_hash": gold_strict.get("state_hash", ""),
                    "status": "pass" if loop_result["overall_pass"] else "fail",
                    "root_pass": loop_result["root_pass"],
                    "strict_pass": loop_result["strict_pass"],
                    "overall_mode": OVERALL_PASS_MODE,
                    "steps_used": len(loop_result["attempts"]),
                    "attempts": loop_result["attempts"],
                },
            )

        file_acc_overall = (file_overall_passed / file_attempted * 100) if file_attempted else 0.0
        file_acc_root = (file_root_passed / file_attempted * 100) if file_attempted else 0.0
        file_acc_strict = (file_strict_passed / file_attempted * 100) if file_attempted else 0.0
        print(
            f"[FILE {file_idx}] attempted={file_attempted}, "
            f"overall={file_overall_passed} ({file_acc_overall:.2f}%), "
            f"root={file_root_passed} ({file_acc_root:.2f}%), "
            f"strict={file_strict_passed} ({file_acc_strict:.2f}%)"
        )
        append_log_line(
            log_path,
            (
                f"[FILE {file_idx}] summary attempted={file_attempted} "
                f"overall={file_overall_passed} ({file_acc_overall:.2f}%) "
                f"root={file_root_passed} ({file_acc_root:.2f}%) "
                f"strict={file_strict_passed} ({file_acc_strict:.2f}%)"
            ),
        )
        append_jsonl(
            metrics_path,
            {
                "type": "file_summary",
                "file_idx": file_idx,
                "overall_passed": file_overall_passed,
                "root_passed": file_root_passed,
                "strict_passed": file_strict_passed,
                "attempted": file_attempted,
                "overall_acc": file_acc_overall,
                "root_acc": file_acc_root,
                "strict_acc": file_acc_strict,
                "overall_mode": OVERALL_PASS_MODE,
            },
        )

    total_acc_overall = (total_overall_passed / total_attempted * 100) if total_attempted else 0.0
    total_acc_root = (total_root_passed / total_attempted * 100) if total_attempted else 0.0
    total_acc_strict = (total_strict_passed / total_attempted * 100) if total_attempted else 0.0
    print(
        f"\n[TOTAL] attempted={total_attempted}, "
        f"overall={total_overall_passed} ({total_acc_overall:.2f}%), "
        f"root={total_root_passed} ({total_acc_root:.2f}%), "
        f"strict={total_strict_passed} ({total_acc_strict:.2f}%) "
        f"mode={OVERALL_PASS_MODE}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
