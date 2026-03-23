import json
import os
import re
from dataclasses import dataclass
from typing import Any, Optional

from openai import OpenAI


DEFAULT_MODEL_SERVER_URL = os.getenv("MODEL_SERVER_URL") or os.getenv("MODEL_URL") or "http://localhost:8000/v1"
DEFAULT_MODEL_NAME = os.getenv("MODEL_NAME", "openai/gpt-oss-20b")
DEFAULT_API_KEY = os.getenv("OPENAI_API_KEY", "not-needed")
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "0"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "256"))
LLM_EMPTY_CONTENT_RETRY_TOKENS = int(os.getenv("LLM_EMPTY_CONTENT_RETRY_TOKENS", "768"))
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "low")


@dataclass(frozen=True)
class IntercodeGenerationConfig:
    model_server_url: str = DEFAULT_MODEL_SERVER_URL
    model_name: str = DEFAULT_MODEL_NAME
    api_key: str = DEFAULT_API_KEY
    timeout_seconds: int = LLM_TIMEOUT_SECONDS
    max_tokens: int = LLM_MAX_TOKENS
    retry_max_tokens: int = LLM_EMPTY_CONTENT_RETRY_TOKENS
    reasoning_effort: str = LLM_REASONING_EFFORT


@dataclass(frozen=True)
class CommandGenerationResult:
    command: str
    finish_reason: Optional[str]
    raw_output_text: str
    raw_output_payload: Any
    model_name: str
    model_server_url: str
    extraction_source: str
    retry_count: int
    command_is_complete: bool


def extract_bash_command(text):
    pattern = r"```(?:bash)?\n?(.*?)\n?```"
    match = re.search(pattern, text or "", re.DOTALL)
    if match:
        command = match.group(1).strip()
    else:
        command = (text or "").strip()
    return command.replace("```bash", "").replace("```", "").strip()


def has_closed_code_fence(text):
    if not text:
        return True
    return text.count("```") % 2 == 0


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
    if command.endswith(("|", "&&", "||", "\\", "$(", "`", "-", "--")):
        return False
    if re.search(r"\s-\w*$", command):
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

    return ""


def build_generation_record(result):
    return {
        "model_name": result.model_name,
        "model_server_url": result.model_server_url,
        "finish_reason": result.finish_reason,
        "generated_command": result.command,
        "extraction_source": result.extraction_source,
        "retry_count": result.retry_count,
        "command_is_complete": result.command_is_complete,
        "raw_output_text": result.raw_output_text,
        "raw_output_payload": result.raw_output_payload,
    }


def generate_command(query, config=None):
    config = config or IntercodeGenerationConfig()
    client_kwargs = {
        "base_url": config.model_server_url,
        "api_key": config.api_key,
    }
    if config.timeout_seconds > 0:
        client_kwargs["timeout"] = config.timeout_seconds
    client = OpenAI(**client_kwargs)

    system_prompt = (
        "You are a Linux Bash expert. "
        "Return exactly one bash command. "
        "Prefer plain text or ```bash ... ```. "
        "Do not add explanation."
    )
    user_prompt = "Task:\n{query}".format(query=query)

    def request_once(prompt, temperature, max_tokens):
        request_kwargs = {}
        if max_tokens > 0:
            request_kwargs["max_tokens"] = max_tokens
        if config.reasoning_effort in {"low", "medium", "high"}:
            request_kwargs["reasoning_effort"] = config.reasoning_effort
        return client.chat.completions.create(
            model=config.model_name,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            **request_kwargs,
        )

    def parse_response(response):
        payload = response.choices[0].message.model_dump()
        text = response.choices[0].message.content or ""
        finish = response.choices[0].finish_reason
        direct = extract_bash_command(text)
        direct_complete = looks_complete_command(direct)
        direct_usable = direct_complete and has_closed_code_fence(text)
        if direct_usable and finish != "length":
            return direct, "content", True, payload, text, finish

        recovered = extract_command_from_reasoning(payload)
        recovered_complete = looks_complete_command(recovered)
        if recovered_complete:
            return recovered, "reasoning", True, payload, text, finish

        return direct, "content", direct_complete and has_closed_code_fence(text), payload, text, finish

    attempts = []
    response = request_once(system_prompt, temperature=0.1, max_tokens=config.max_tokens)
    command, extraction_source, command_is_complete, raw_payload, raw_text, finish_reason = parse_response(response)
    attempts.append(
        {
            "finish_reason": finish_reason,
            "extraction_source": extraction_source,
            "command_is_complete": command_is_complete,
        }
    )

    needs_retry = finish_reason == "length" or not command_is_complete
    if needs_retry:
        retry_response = request_once(
            "Output only one complete final bash command on one line. No markdown. No explanation.",
            temperature=0,
            max_tokens=max(config.retry_max_tokens, config.max_tokens),
        )
        response = retry_response
        command, extraction_source, command_is_complete, raw_payload, raw_text, finish_reason = parse_response(response)
        attempts.append(
            {
                "finish_reason": finish_reason,
                "extraction_source": extraction_source,
                "command_is_complete": command_is_complete,
            }
        )

    raw_output_text = raw_text or json.dumps(raw_payload, ensure_ascii=False)
    return CommandGenerationResult(
        command=command.strip(),
        finish_reason=finish_reason,
        raw_output_text=raw_output_text,
        raw_output_payload=raw_payload,
        model_name=config.model_name,
        model_server_url=config.model_server_url,
        extraction_source=extraction_source,
        retry_count=max(len(attempts) - 1, 0),
        command_is_complete=command_is_complete,
    )
