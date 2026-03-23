import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_METRICS_PATH = PROJECT_ROOT / "results" / "security_metrics.jsonl"
DEFAULT_OUT_DIR = PROJECT_ROOT / "results" / "security_analysis"


def load_rows(metrics_path):
    rows = []
    with metrics_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("type") == "summary":
                continue
            rows.append(obj)
    return rows


def write_csv(path, rows):
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = sorted({k for row in rows for k in row.keys()})
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def ratio(num, den):
    if den == 0:
        return 0.0
    return round(num / den * 100.0, 2)


def summarize(rows):
    ok_rows = [r for r in rows if r.get("status") == "ok"]
    attempted = len(ok_rows)
    root_success = sum(1 for r in ok_rows if r.get("root_success"))
    strict_success = sum(1 for r in ok_rows if r.get("strict_success"))
    root_expected_known = [r for r in ok_rows if r.get("root_expected_match") is not None]
    strict_expected_known = [r for r in ok_rows if r.get("strict_expected_match") is not None]
    root_expected_match = sum(1 for r in root_expected_known if r.get("root_expected_match"))
    strict_expected_match = sum(1 for r in strict_expected_known if r.get("strict_expected_match"))
    proposed_risky = sum(1 for r in ok_rows if r.get("risk_signals"))

    return {
        "attempted": attempted,
        "root_success": root_success,
        "root_success_rate": ratio(root_success, attempted),
        "strict_success": strict_success,
        "strict_success_rate": ratio(strict_success, attempted),
        "delta": round(ratio(root_success, attempted) - ratio(strict_success, attempted), 2),
        "risky_proposal_count": proposed_risky,
        "risky_proposal_rate": ratio(proposed_risky, attempted),
        "root_expected_match_rate": ratio(root_expected_match, len(root_expected_known)),
        "strict_expected_match_rate": ratio(strict_expected_match, len(strict_expected_known)),
    }


def summarize_by_category(rows):
    grouped = defaultdict(list)
    for row in rows:
        if row.get("status") != "ok":
            continue
        category = row.get("scenario", {}).get("category", "unknown")
        grouped[category].append(row)

    result = []
    for category, items in sorted(grouped.items()):
        attempted = len(items)
        root_success = sum(1 for r in items if r.get("root_success"))
        strict_success = sum(1 for r in items if r.get("strict_success"))
        risky = sum(1 for r in items if r.get("risk_signals"))
        result.append(
            {
                "category": category,
                "attempted": attempted,
                "root_success": root_success,
                "root_success_rate": ratio(root_success, attempted),
                "strict_success": strict_success,
                "strict_success_rate": ratio(strict_success, attempted),
                "delta": round(ratio(root_success, attempted) - ratio(strict_success, attempted), 2),
                "risky_proposal_rate": ratio(risky, attempted),
            }
        )
    return result


def summarize_risk_signals(rows):
    signal_counter = Counter()
    for row in rows:
        if row.get("status") != "ok":
            continue
        for signal in row.get("risk_signals", []):
            signal_counter[signal] += 1
    return [{"risk_signal": key, "count": value} for key, value in signal_counter.most_common()]


def main():
    parser = argparse.ArgumentParser(description="Analyze security benchmark metrics")
    parser.add_argument("--metrics-path", type=Path, default=DEFAULT_METRICS_PATH)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()

    rows = load_rows(args.metrics_path)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    summary = summarize(rows)
    by_category = summarize_by_category(rows)
    risk_signals = summarize_risk_signals(rows)

    with (args.out_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    write_csv(args.out_dir / "by_category.csv", by_category)
    write_csv(args.out_dir / "risk_signals.csv", risk_signals)
    write_csv(args.out_dir / "task_level.csv", rows)

    print("[DONE] security analysis completed")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"[DONE] outputs saved to: {args.out_dir}")


if __name__ == "__main__":
    main()
