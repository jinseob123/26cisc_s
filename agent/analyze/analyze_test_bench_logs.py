import argparse
import csv
import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_DIR = PROJECT_ROOT / "results"
DEFAULT_OUT_DIR = DEFAULT_LOG_DIR / "analysis"


def normalize_cmd(cmd: str) -> str:
    return re.sub(r"\s+", " ", (cmd or "").strip())


def tokenize(cmd: str):
    return re.findall(r"[^\s]+", cmd or "")


def jaccard_similarity(a: str, b: str) -> float:
    sa, sb = set(tokenize(a)), set(tokenize(b))
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def parse_metrics_file(path: Path):
    rows = []
    file_match = re.search(r"file_(\d+)_metrics\.jsonl$", path.name)
    file_index = int(file_match.group(1)) if file_match else -1
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "file_summary":
                continue
            obj["_file"] = file_index
            rows.append(obj)
    return rows


def parse_log_file_fallback(log_path: Path):
    file_match = re.search(r"file_(\d+)\.log$", log_path.name)
    file_index = int(file_match.group(1)) if file_match else -1

    records = []
    current = None

    with log_path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            m_task = re.search(r"TASK\s+(\d+)/(\d+)", line)
            if m_task:
                if current:
                    records.append(current)
                current = {
                    "_file": file_index,
                    "task_idx": int(m_task.group(1)),
                    "query": None,
                    "gold": None,
                    "agent_cmd": None,
                }
                continue
            if not current:
                continue
            if "QUERY:" in line:
                current["query"] = line.split("QUERY:", 1)[1].strip()
            elif "GOLD :" in line:
                current["gold"] = line.split("GOLD :", 1)[1].strip()
            elif "AGENT_CMD:" in line:
                current["agent_cmd"] = line.split("AGENT_CMD:", 1)[1].strip()

    if current:
        records.append(current)

    rows = []
    for r in records:
        gold = r.get("gold") or ""
        agent = r.get("agent_cmd") or ""
        rows.append(
            {
                "_file": r["_file"],
                "task_idx": r["task_idx"],
                "status": "unknown",
                "query": r.get("query"),
                "gold": gold,
                "agent_cmd": agent,
                "root_pass": None,
                "strict_pass": None,
                "cmd_exact": bool(agent) and (normalize_cmd(gold) == normalize_cmd(agent)),
                "cmd_jaccard": jaccard_similarity(gold, agent) if agent else 0.0,
            }
        )
    return rows


def ratio(num, den):
    if den == 0:
        return 0.0
    return round(num / den * 100.0, 2)


def get_last_attempt(row):
    attempts = row.get("attempts")
    if isinstance(attempts, list) and attempts:
        return attempts[-1]
    return {}


def classify_failure(row):
    if row.get("status") != "fail":
        return None

    last = get_last_attempt(row)
    command = (last.get("command") or row.get("agent_cmd") or "").strip()
    root_stderr = (last.get("root_stderr") or row.get("agent_root_stderr") or "").strip()
    strict_stderr = (last.get("strict_stderr") or row.get("agent_strict_stderr") or "").strip()
    stderr = f"{root_stderr}\n{strict_stderr}".strip()

    if not command:
        return "empty_command"
    if "command not found" in stderr:
        return "missing_command"
    if any(token in stderr for token in ("syntax error", "unexpected EOF", "missing argument")):
        return "syntax_error"
    if "Operation not permitted" in stderr or "Permission denied" in stderr:
        return "permission_denied"
    return "semantic_fail"


def summarize_rows(rows):
    total_rows = len(rows)
    attempted_rows = [r for r in rows if r.get("status") in {"pass", "fail"}]
    attempted = len(attempted_rows)

    skip_gold = sum(1 for r in rows if str(r.get("status", "")).startswith("skip_gold_failed"))
    skip_missing = sum(1 for r in rows if r.get("status") == "skip_missing_fields")

    overall_passed = sum(1 for r in attempted_rows if r.get("status") == "pass")
    root_passed = sum(1 for r in attempted_rows if bool(r.get("root_pass")))
    strict_passed = sum(1 for r in attempted_rows if bool(r.get("strict_pass")))
    empty_command_count = sum(1 for r in attempted_rows if classify_failure(r) == "empty_command")
    semantic_fail_count = sum(1 for r in attempted_rows if classify_failure(r) == "semantic_fail")
    syntax_error_count = sum(1 for r in attempted_rows if classify_failure(r) == "syntax_error")
    missing_command_count = sum(1 for r in attempted_rows if classify_failure(r) == "missing_command")
    permission_denied_count = sum(1 for r in attempted_rows if classify_failure(r) == "permission_denied")

    steps_values = [int(r.get("steps_used", 0)) for r in attempted_rows if r.get("steps_used") is not None]
    avg_steps = round(sum(steps_values) / len(steps_values), 2) if steps_values else 0.0

    return {
        "total_rows": total_rows,
        "attempted": attempted,
        "skip_gold_failed": skip_gold,
        "skip_missing_fields": skip_missing,
        "overall_passed": overall_passed,
        "overall_acc": ratio(overall_passed, attempted),
        "root_passed": root_passed,
        "root_acc": ratio(root_passed, attempted),
        "strict_passed": strict_passed,
        "strict_acc": ratio(strict_passed, attempted),
        "empty_command_count": empty_command_count,
        "empty_command_rate": ratio(empty_command_count, attempted),
        "semantic_fail_count": semantic_fail_count,
        "semantic_fail_rate": ratio(semantic_fail_count, attempted),
        "syntax_error_count": syntax_error_count,
        "syntax_error_rate": ratio(syntax_error_count, attempted),
        "missing_command_count": missing_command_count,
        "missing_command_rate": ratio(missing_command_count, attempted),
        "permission_denied_count": permission_denied_count,
        "permission_denied_rate": ratio(permission_denied_count, attempted),
        "avg_steps_used": avg_steps,
    }


def summarize_per_file(rows):
    by_file = {}
    for r in rows:
        idx = int(r.get("_file", -1))
        by_file.setdefault(idx, []).append(r)

    result = []
    for file_idx, file_rows in sorted(by_file.items()):
        attempted_rows = [r for r in file_rows if r.get("status") in {"pass", "fail"}]
        attempted = len(attempted_rows)
        overall_passed = sum(1 for r in attempted_rows if r.get("status") == "pass")
        root_passed = sum(1 for r in attempted_rows if bool(r.get("root_pass")))
        strict_passed = sum(1 for r in attempted_rows if bool(r.get("strict_pass")))
        empty_command_count = sum(1 for r in attempted_rows if classify_failure(r) == "empty_command")
        semantic_fail_count = sum(1 for r in attempted_rows if classify_failure(r) == "semantic_fail")
        syntax_error_count = sum(1 for r in attempted_rows if classify_failure(r) == "syntax_error")
        missing_command_count = sum(1 for r in attempted_rows if classify_failure(r) == "missing_command")
        permission_denied_count = sum(1 for r in attempted_rows if classify_failure(r) == "permission_denied")
        skip_gold = sum(1 for r in file_rows if str(r.get("status", "")).startswith("skip_gold_failed"))
        skip_missing = sum(1 for r in file_rows if r.get("status") == "skip_missing_fields")
        result.append(
            {
                "file": file_idx,
                "rows": len(file_rows),
                "attempted": attempted,
                "skip_gold_failed": skip_gold,
                "skip_missing_fields": skip_missing,
                "overall_passed": overall_passed,
                "overall_acc": ratio(overall_passed, attempted),
                "root_passed": root_passed,
                "root_acc": ratio(root_passed, attempted),
                "strict_passed": strict_passed,
                "strict_acc": ratio(strict_passed, attempted),
                "empty_command_count": empty_command_count,
                "empty_command_rate": ratio(empty_command_count, attempted),
                "semantic_fail_count": semantic_fail_count,
                "semantic_fail_rate": ratio(semantic_fail_count, attempted),
                "syntax_error_count": syntax_error_count,
                "syntax_error_rate": ratio(syntax_error_count, attempted),
                "missing_command_count": missing_command_count,
                "missing_command_rate": ratio(missing_command_count, attempted),
                "permission_denied_count": permission_denied_count,
                "permission_denied_rate": ratio(permission_denied_count, attempted),
            }
        )
    return result


def write_metric_bars_svg(summary: dict, output_path: Path):
    metrics = [
        ("overall", summary["overall_acc"]),
        ("root", summary["root_acc"]),
        ("strict", summary["strict_acc"]),
    ]

    w, h = 640, 420
    ml, mb, mt, mr = 60, 70, 50, 30
    pw, ph = w - ml - mr, h - mt - mb
    x0, y0 = ml, h - mb
    space = pw / len(metrics)
    bw = space * 0.6

    lines = [
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{w}' height='{h}'>",
        "<rect width='100%' height='100%' fill='white'/>",
        "<text x='20' y='32' font-size='20' font-family='sans-serif'>Benchmark Accuracy (%)</text>",
        f"<line x1='{x0}' y1='{y0}' x2='{x0 + pw}' y2='{y0}' stroke='black'/>",
        f"<line x1='{x0}' y1='{y0}' x2='{x0}' y2='{mt}' stroke='black'/>",
    ]

    for i, (name, val) in enumerate(metrics):
        x = x0 + i * space + (space - bw) / 2
        bh = ph * (val / 100.0)
        y = y0 - bh
        lines.append(f"<rect x='{x:.1f}' y='{y:.1f}' width='{bw:.1f}' height='{bh:.1f}' fill='#1f77b4'/>")
        lines.append(
            f"<text x='{x + bw/2:.1f}' y='{y - 6:.1f}' text-anchor='middle' font-size='12' font-family='sans-serif'>{val:.1f}%</text>"
        )
        lines.append(
            f"<text x='{x + bw/2:.1f}' y='{y0 + 18}' text-anchor='middle' font-size='11' font-family='sans-serif'>{name}</text>"
        )

    lines.append("</svg>")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def write_csv(path: Path, rows):
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = sorted({k for row in rows for k in row.keys()})
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main():
    parser = argparse.ArgumentParser(description="Analyze test_bench logs")
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    metrics_files = sorted(args.log_dir.glob("file_*_metrics.jsonl"))
    rows = []
    mode = "metrics_jsonl"

    if metrics_files:
        for p in metrics_files:
            rows.extend(parse_metrics_file(p))
    else:
        mode = "legacy_log_fallback"
        for p in sorted(args.log_dir.glob("file_*.log")):
            rows.extend(parse_log_file_fallback(p))

    if not rows:
        raise FileNotFoundError(f"No analyzable logs found in {args.log_dir}")

    for row in rows:
        row["failure_category"] = classify_failure(row)

    write_csv(args.out_dir / "task_level_analysis.csv", rows)

    summary = summarize_rows(rows)
    summary["mode"] = mode
    with (args.out_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    pf = summarize_per_file(rows)
    write_csv(args.out_dir / "per_file_summary.csv", pf)

    write_metric_bars_svg(summary, args.out_dir / "metric_comparison.svg")

    print("[DONE] analysis completed")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"[DONE] outputs saved to: {args.out_dir}")


if __name__ == "__main__":
    main()
