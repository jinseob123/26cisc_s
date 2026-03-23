import argparse
import csv
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_UTILITY_DIR = PROJECT_ROOT / "results" / "analysis_patched"
DEFAULT_SECURITY_DIR = PROJECT_ROOT / "results" / "security_analysis"
DEFAULT_OUT_DIR = PROJECT_ROOT / "results" / "combined_analysis"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_csv(path: Path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


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


def pick_best_utility_view(summary: dict):
    root_acc = float(summary.get("root_acc", 0.0))
    strict_acc = float(summary.get("strict_acc", 0.0))
    overall_acc = float(summary.get("overall_acc", 0.0))
    if root_acc >= strict_acc and root_acc >= overall_acc:
        return "root"
    if strict_acc >= root_acc and strict_acc >= overall_acc:
        return "strict"
    return "overall"


def build_summary(utility_summary: dict, security_summary: dict):
    utility_root = float(utility_summary.get("root_acc", 0.0))
    utility_strict = float(utility_summary.get("strict_acc", 0.0))
    security_root = float(security_summary.get("root_success_rate", 0.0))
    security_strict = float(security_summary.get("strict_success_rate", 0.0))

    return {
        "utility": {
            "attempted": utility_summary.get("attempted", 0),
            "overall_acc": utility_summary.get("overall_acc", 0.0),
            "root_acc": utility_root,
            "strict_acc": utility_strict,
            "permission_delta": round(utility_root - utility_strict, 2),
            "skip_gold_failed": utility_summary.get("skip_gold_failed", 0),
            "best_view": pick_best_utility_view(utility_summary),
        },
        "security": {
            "attempted": security_summary.get("attempted", 0),
            "root_success_rate": security_root,
            "strict_success_rate": security_strict,
            "permission_delta": round(security_root - security_strict, 2),
            "risky_proposal_rate": security_summary.get("risky_proposal_rate", 0.0),
            "root_expected_match_rate": security_summary.get("root_expected_match_rate", 0.0),
            "strict_expected_match_rate": security_summary.get("strict_expected_match_rate", 0.0),
        },
        "takeaways": {
            "utility_increases_with_root": utility_root > utility_strict,
            "security_risk_increases_with_root": security_root > security_strict,
            "security_risky_command_rate": security_summary.get("risky_proposal_rate", 0.0),
        },
    }


def build_overview_rows(utility_summary: dict, security_summary: dict):
    return [
        {
            "benchmark": "utility",
            "attempted": utility_summary.get("attempted", 0),
            "metric_root": utility_summary.get("root_acc", 0.0),
            "metric_strict": utility_summary.get("strict_acc", 0.0),
            "delta": round(float(utility_summary.get("root_acc", 0.0)) - float(utility_summary.get("strict_acc", 0.0)), 2),
            "notes": "accuracy on Intercode-style shell tasks",
        },
        {
            "benchmark": "security",
            "attempted": security_summary.get("attempted", 0),
            "metric_root": security_summary.get("root_success_rate", 0.0),
            "metric_strict": security_summary.get("strict_success_rate", 0.0),
            "delta": round(
                float(security_summary.get("root_success_rate", 0.0))
                - float(security_summary.get("strict_success_rate", 0.0)),
                2,
            ),
            "notes": "success rate on risky or policy-sensitive shell tasks",
        },
    ]


def render_markdown(summary: dict, utility_files, security_categories):
    utility = summary["utility"]
    security = summary["security"]
    takeaways = summary["takeaways"]

    lines = [
        "# Combined Benchmark Summary",
        "",
        "## Headline",
        "",
        f"- Utility benchmark attempted `{utility['attempted']}` tasks.",
        f"- Utility accuracy: `ROOT {utility['root_acc']}%`, `STRICT {utility['strict_acc']}%`, delta `{utility['permission_delta']}%p`.",
        f"- Security benchmark attempted `{security['attempted']}` tasks.",
        f"- Security success: `ROOT {security['root_success_rate']}%`, `STRICT {security['strict_success_rate']}%`, delta `{security['permission_delta']}%p`.",
        f"- Risky command proposal rate: `{security['risky_proposal_rate']}%`.",
        "",
        "## Interpretation",
        "",
        f"- Utility improved under `ROOT`: `{takeaways['utility_increases_with_root']}`.",
        f"- Security risk increased under `ROOT`: `{takeaways['security_risk_increases_with_root']}`.",
        f"- Utility gold failures skipped: `{utility['skip_gold_failed']}`.",
    ]

    if utility_files:
        lines.extend(["", "## Utility By File", ""])
        for row in utility_files:
            lines.append(
                f"- File {row.get('file')}: overall `{row.get('overall_acc')}%`, root `{row.get('root_acc')}%`, strict `{row.get('strict_acc')}%`, attempted `{row.get('attempted')}`."
            )

    if security_categories:
        lines.extend(["", "## Security By Category", ""])
        for row in security_categories:
            lines.append(
                f"- {row.get('category')}: root `{row.get('root_success_rate')}%`, strict `{row.get('strict_success_rate')}%`, delta `{row.get('delta')}%p`, attempted `{row.get('attempted')}`."
            )

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Utility and security should be interpreted as separate benchmark families.",
            "- A positive utility delta suggests additional privilege helps task completion.",
            "- A positive security delta suggests additional privilege increases the chance of risky actions succeeding.",
        ]
    )
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Combine utility and security benchmark summaries")
    parser.add_argument("--utility-dir", type=Path, default=DEFAULT_UTILITY_DIR)
    parser.add_argument("--security-dir", type=Path, default=DEFAULT_SECURITY_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()

    utility_summary_path = args.utility_dir / "summary.json"
    security_summary_path = args.security_dir / "summary.json"

    if not utility_summary_path.exists():
        raise FileNotFoundError(f"Missing utility summary: {utility_summary_path}")
    if not security_summary_path.exists():
        raise FileNotFoundError(f"Missing security summary: {security_summary_path}")

    utility_summary = load_json(utility_summary_path)
    security_summary = load_json(security_summary_path)
    utility_files = load_csv(args.utility_dir / "per_file_summary.csv")
    security_categories = load_csv(args.security_dir / "by_category.csv")

    args.out_dir.mkdir(parents=True, exist_ok=True)

    combined_summary = build_summary(utility_summary, security_summary)
    overview_rows = build_overview_rows(utility_summary, security_summary)
    report = render_markdown(combined_summary, utility_files, security_categories)

    with (args.out_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(combined_summary, f, indent=2, ensure_ascii=False)
    write_csv(args.out_dir / "overview.csv", overview_rows)
    write_csv(args.out_dir / "utility_by_file.csv", utility_files)
    write_csv(args.out_dir / "security_by_category.csv", security_categories)
    (args.out_dir / "report.md").write_text(report, encoding="utf-8")

    print("[DONE] combined analysis completed")
    print(json.dumps(combined_summary, indent=2, ensure_ascii=False))
    print(f"[DONE] outputs saved to: {args.out_dir}")


if __name__ == "__main__":
    main()
