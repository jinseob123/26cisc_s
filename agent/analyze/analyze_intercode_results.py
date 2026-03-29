import argparse
import csv
import html
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNS_DIR = PROJECT_ROOT / "results" / "intercode_runs" / "result12"
DEFAULT_OUT_DIR = PROJECT_ROOT / "results" / "intercode_runs" / "result12"/ "analysis"
PROFILE_ALIASES = {
    "r": "root",
    "root": "root",
    "u": "user",
    "user": "user",
    "s": "strict",
    "strict": "strict",
}
PROFILE_ORDER = ["root", "user", "strict"]
PROFILE_COLORS = {
    "root": "#2a9d8f",
    "user": "#577590",
    "strict": "#e76f51",
}


def load_jsonl(path: Path):
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def find_summary_files(runs_dir: Path):
    return sorted(runs_dir.rglob("intercode_command_set_summary.jsonl"))


def parse_session_id(summary_path: Path) -> str:
    return summary_path.parent.name


def dedupe_latest_rows(rows):
    latest = {}
    for row in rows:
        key = (
            row.get("command_set_id"),
            row.get("split"),
            row.get("task_index"),
            row.get("profile"),
        )
        if key not in latest or row["session_id"] > latest[key]["session_id"]:
            latest[key] = row
    return list(latest.values())


def parse_profiles_arg(raw_profiles):
    if not raw_profiles:
        return ["root", "strict"]

    profiles = []
    for item in raw_profiles.split(","):
        token = item.strip().lower()
        if not token:
            continue
        canonical = PROFILE_ALIASES.get(token)
        if canonical is None:
            raise ValueError(f"Unsupported profile alias: {item}")
        if canonical not in profiles:
            profiles.append(canonical)
    return profiles or ["root", "strict"]


def filter_rows_by_profiles(rows, selected_profiles):
    selected = set(selected_profiles)
    return [row for row in rows if row.get("profile") in selected]


def normalize_row(row, summary_path: Path):
    normalized = dict(row)
    normalized["session_id"] = parse_session_id(summary_path)
    normalized["reward"] = float(normalized["reward"]) if normalized.get("reward") is not None else None
    normalized["split"] = int(normalized["split"])
    normalized["task_index"] = int(normalized["task_index"])
    return normalized


def classify_failure(row):
    if row.get("run_status") != "completed":
        return "infra_error"

    reward = row.get("reward")
    if reward == 1.0:
        return "exact_success"

    agent_obs = row.get("agent_observation") or ""
    eval_obs = row.get("eval_observation") or ""
    joined = f"{agent_obs}\n{eval_obs}".lower()

    if "permission denied" in joined:
        return "permission_denied"
    if "no such file or directory" in joined:
        return "missing_path"
    if "command not found" in joined or "unexpected eof" in joined or "syntax error" in joined:
        return "syntax_or_parse"
    if reward is not None and reward >= 0.67:
        return "near_miss"
    return "semantic_miss"


def build_profile_stats(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["profile"]].append(row)

    stats = {}
    for profile, profile_rows in grouped.items():
        rewards = [row["reward"] for row in profile_rows if row["reward"] is not None]
        exact = [row for row in profile_rows if row.get("reward") == 1.0]
        stats[profile] = {
            "attempted": len(profile_rows),
            "exact_successes": len(exact),
            "exact_success_rate": len(exact) / len(profile_rows) if profile_rows else 0.0,
            "average_reward": mean(rewards) if rewards else 0.0,
        }
    return stats


def build_split_stats(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["profile"], row["split"])].append(row)

    stats = []
    for (profile, split), split_rows in sorted(grouped.items()):
        rewards = [row["reward"] for row in split_rows if row["reward"] is not None]
        exact_count = sum(1 for row in split_rows if row.get("reward") == 1.0)
        stats.append(
            {
                "profile": profile,
                "split": split,
                "attempted": len(split_rows),
                "exact_success_rate": exact_count / len(split_rows) if split_rows else 0.0,
                "average_reward": mean(rewards) if rewards else 0.0,
            }
        )
    return stats


def build_paired_rows(rows):
    paired = defaultdict(dict)
    for row in rows:
        key = (row.get("command_set_id"), row.get("split"), row.get("task_index"))
        paired[key][row["profile"]] = row
    return paired


def build_delta_rows(rows, base_profile="root", comparison_profiles=None):
    deltas = []
    comparison_profiles = comparison_profiles or []
    for key, pair in build_paired_rows(rows).items():
        if base_profile not in pair:
            continue
        base_row = pair[base_profile]
        for comparison_profile in comparison_profiles:
            if comparison_profile not in pair:
                continue
            comparison_row = pair[comparison_profile]
            deltas.append(
                {
                    "command_set_id": key[0],
                    "split": key[1],
                    "task_index": key[2],
                    "base_profile": base_profile,
                    "comparison_profile": comparison_profile,
                    "base_reward": base_row.get("reward"),
                    "comparison_reward": comparison_row.get("reward"),
                    "reward_delta": (base_row.get("reward") or 0.0) - (comparison_row.get("reward") or 0.0),
                    "base_bucket": classify_failure(base_row),
                    "comparison_bucket": classify_failure(comparison_row),
                }
            )
    return deltas


def build_bucket_counts(rows):
    grouped = defaultdict(Counter)
    for row in rows:
        grouped[row["profile"]][classify_failure(row)] += 1
    return grouped


def write_csv(path: Path, rows):
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def svg_text(x, y, text, size=14, anchor="middle", weight="normal"):
    return (
        f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" '
        f'font-family="Arial, sans-serif" font-weight="{weight}">{html.escape(str(text))}</text>'
    )


def write_svg(path: Path, width: int, height: int, body: str):
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">{body}</svg>'
    )
    path.write_text(svg, encoding="utf-8")


def make_bar_chart_svg(labels, values, title, ylabel, out_path: Path, colors):
    width, height = 900, 520
    left, right, top, bottom = 90, 40, 60, 80
    plot_width = width - left - right
    plot_height = height - top - bottom
    max_value = max(values) if values else 1.0
    max_value = max(max_value, 1.0)
    bar_width = plot_width / max(len(labels), 1) * 0.6
    gap = plot_width / max(len(labels), 1)

    parts = [
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="white"/>',
        svg_text(width / 2, 30, title, size=20, weight="bold"),
        svg_text(24, height / 2, ylabel, size=14, anchor="middle"),
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}" stroke="#444"/>',
        f'<line x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" y2="{top + plot_height}" stroke="#444"/>',
    ]

    for idx, label in enumerate(labels):
        x = left + idx * gap + (gap - bar_width) / 2
        bar_height = 0 if max_value == 0 else (values[idx] / max_value) * plot_height
        y = top + plot_height - bar_height
        color = colors[idx % len(colors)]
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" fill="{color}"/>')
        parts.append(svg_text(x + bar_width / 2, top + plot_height + 24, label, size=12))
        parts.append(svg_text(x + bar_width / 2, y - 8, f"{values[idx]:.2f}", size=11))

    write_svg(out_path, width, height, "".join(parts))


def plot_profile_metrics(profile_stats, out_dir: Path, profiles):
    labels = [profile.upper() for profile in profiles if profile in profile_stats]
    exact_values = [profile_stats[profile]["exact_success_rate"] for profile in profiles if profile in profile_stats]
    reward_values = [profile_stats[profile]["average_reward"] for profile in profiles if profile in profile_stats]
    colors = [PROFILE_COLORS.get(profile, "#666666") for profile in profiles if profile in profile_stats]

    make_bar_chart_svg(
        labels,
        exact_values,
        "Exact Success Rate By Profile",
        "Exact Success Rate",
        out_dir / "exact_success_by_profile.svg",
        colors=colors,
    )
    make_bar_chart_svg(
        labels,
        reward_values,
        "Average Reward By Profile",
        "Average Reward",
        out_dir / "average_reward_by_profile.svg",
        colors=colors,
    )


def plot_split_metrics(split_stats, out_dir: Path, profiles):
    splits = sorted({row["split"] for row in split_stats})
    width, height = 980, 520
    left, right, top, bottom = 90, 40, 60, 80
    plot_width = width - left - right
    plot_height = height - top - bottom
    group_width = plot_width / max(len(splits), 1)
    bar_width = group_width * min(0.22, 0.7 / max(len(profiles), 1))

    parts = [
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="white"/>',
        svg_text(width / 2, 30, "Exact Success Rate By Split And Profile", size=20, weight="bold"),
        svg_text(30, height / 2, "Exact Success Rate", size=14),
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}" stroke="#444"/>',
        f'<line x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" y2="{top + plot_height}" stroke="#444"/>',
    ]

    for split_idx, split in enumerate(splits):
        group_x = left + split_idx * group_width
        center_x = group_x + group_width / 2
        parts.append(svg_text(center_x, top + plot_height + 24, f"Split {split}", size=12))
        for profile_idx, profile in enumerate(profiles):
            match = next((row for row in split_stats if row["profile"] == profile and row["split"] == split), None)
            value = match["exact_success_rate"] if match else 0.0
            total_bar_width = len(profiles) * bar_width + max(len(profiles) - 1, 0) * bar_width * 0.3
            start_x = group_x + (group_width - total_bar_width) / 2
            x = start_x + profile_idx * bar_width * 1.3
            bar_height = value * plot_height
            y = top + plot_height - bar_height
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" fill="{PROFILE_COLORS.get(profile, "#666666")}"/>')
            parts.append(svg_text(x + bar_width / 2, y - 8, f"{value:.2f}", size=10))

    for idx, profile in enumerate(profiles):
        y = top + idx * 24
        parts.append(f'<rect x="{width - 190}" y="{y}" width="14" height="14" fill="{PROFILE_COLORS.get(profile, "#666666")}"/>')
        parts.append(svg_text(width - 165, y + 12, profile.upper(), size=12, anchor="start"))
    write_svg(out_dir / "split_exact_success.svg", width, height, "".join(parts))


def render_reward_delta_histogram(deltas, out_path: Path, title: str):
    if not deltas:
        write_svg(
            out_path,
            800,
            300,
            '<rect width="800" height="300" fill="white"/>' + svg_text(400, 150, "No paired rows", size=20),
        )
        return

    width, height = 900, 520
    left, right, top, bottom = 90, 40, 60, 80
    plot_width = width - left - right
    plot_height = height - top - bottom
    bins = 20
    min_value = min(deltas)
    max_value = max(deltas)
    span = max(max_value - min_value, 0.01)
    bucket_counts = [0] * bins
    for value in deltas:
        idx = min(int((value - min_value) / span * bins), bins - 1)
        bucket_counts[idx] += 1
    max_count = max(bucket_counts) if bucket_counts else 1
    bar_width = plot_width / bins

    parts = [
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="white"/>',
        svg_text(width / 2, 30, title, size=20, weight="bold"),
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}" stroke="#444"/>',
        f'<line x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" y2="{top + plot_height}" stroke="#444"/>',
    ]

    for idx, count in enumerate(bucket_counts):
        x = left + idx * bar_width
        bar_height = (count / max_count) * plot_height
        y = top + plot_height - bar_height
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width - 2:.1f}" height="{bar_height:.1f}" fill="#457b9d"/>')

    parts.append(svg_text(left, top + plot_height + 24, f"{min_value:.2f}", size=12, anchor="start"))
    parts.append(svg_text(left + plot_width, top + plot_height + 24, f"{max_value:.2f}", size=12, anchor="end"))
    parts.append(svg_text(width / 2, height - 20, "Reward Delta", size=14))
    parts.append(svg_text(24, height / 2, "Task Count", size=14))
    write_svg(out_path, width, height, "".join(parts))


def plot_reward_delta_histograms(delta_rows, out_dir: Path, base_profile: str, comparison_profiles):
    rendered = []
    for comparison_profile in comparison_profiles:
        deltas = [row["reward_delta"] for row in delta_rows if row["comparison_profile"] == comparison_profile]
        if len(comparison_profiles) == 1:
            out_path = out_dir / "reward_delta_histogram.svg"
        else:
            out_path = out_dir / f"reward_delta_histogram_{base_profile}_vs_{comparison_profile}.svg"
        render_reward_delta_histogram(
            deltas,
            out_path,
            f"Reward Delta Distribution ({base_profile.upper()} - {comparison_profile.upper()})",
        )
        rendered.append(str(out_path))
    return rendered


def plot_failure_buckets(bucket_counts, out_dir: Path, profiles):
    buckets = [
        "exact_success",
        "near_miss",
        "permission_denied",
        "missing_path",
        "syntax_or_parse",
        "semantic_miss",
        "infra_error",
    ]

    width, height = 980, 560
    left, right, top, bottom_margin = 90, 40, 60, 80
    plot_width = width - left - right
    plot_height = height - top - bottom_margin
    total_counts = [sum(bucket_counts[profile].values()) for profile in profiles]
    max_total = max(total_counts) if total_counts else 1
    bar_width = plot_width / max(len(profiles), 1) * 0.4
    colors = {
        "exact_success": "#2a9d8f",
        "near_miss": "#8ecae6",
        "permission_denied": "#e76f51",
        "missing_path": "#f4a261",
        "syntax_or_parse": "#ffb703",
        "semantic_miss": "#6d597a",
        "infra_error": "#9d0208",
    }

    parts = [
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="white"/>',
        svg_text(width / 2, 30, "Outcome Buckets By Profile", size=20, weight="bold"),
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}" stroke="#444"/>',
        f'<line x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" y2="{top + plot_height}" stroke="#444"/>',
    ]

    for idx, profile in enumerate(profiles):
        x = left + (idx + 0.5) * (plot_width / len(profiles)) - bar_width / 2
        current_bottom = top + plot_height
        for bucket in buckets:
            value = bucket_counts[profile][bucket]
            segment_height = 0 if max_total == 0 else (value / max_total) * plot_height
            y = current_bottom - segment_height
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{segment_height:.1f}" fill="{colors[bucket]}"/>')
            current_bottom = y
        parts.append(svg_text(x + bar_width / 2, top + plot_height + 24, profile.upper(), size=12))

    legend_x = width - 220
    legend_y = top
    for idx, bucket in enumerate(buckets):
        y = legend_y + idx * 22
        parts.append(f'<rect x="{legend_x}" y="{y}" width="14" height="14" fill="{colors[bucket]}"/>')
        parts.append(svg_text(legend_x + 20, y + 12, bucket, size=11, anchor="start"))

    write_svg(out_dir / "failure_buckets_by_profile.svg", width, height, "".join(parts))


def write_summary_json(out_dir: Path, profile_stats, split_stats, delta_rows, bucket_counts, summary_files, selected_profiles, base_profile):
    summary = {
        "summary_files": [str(path) for path in summary_files],
        "selected_profiles": selected_profiles,
        "base_profile": base_profile,
        "profile_stats": profile_stats,
        "split_stats": split_stats,
        "paired_task_count": len(delta_rows),
        "pairwise_reward_deltas": {
            comparison_profile: mean([row["reward_delta"] for row in delta_rows if row["comparison_profile"] == comparison_profile])
            for comparison_profile in sorted({row["comparison_profile"] for row in delta_rows})
        },
        "bucket_counts": {profile: dict(counts) for profile, counts in bucket_counts.items()},
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def render_report(summary, out_dir: Path):
    profile_stats = summary["profile_stats"]
    selected_profiles = summary.get("selected_profiles", PROFILE_ORDER)
    base_profile = summary.get("base_profile", "root")
    lines = [
        "# InterCode Permission Analysis",
        "",
        "## Headline",
        "",
    ]
    for profile in selected_profiles:
        if profile not in profile_stats:
            continue
        stats = profile_stats[profile]
        lines.append(
            "- {profile}: attempted `{attempted}`, exact success `{exact:.1%}`, average reward `{reward:.3f}`.".format(
                profile=profile.upper(),
                attempted=stats["attempted"],
                exact=stats["exact_success_rate"],
                reward=stats["average_reward"],
            )
        )

    lines.extend(
        [
            "",
            "## Plot Guide",
            "",
            "- `exact_success_by_profile.svg`: strict한 exact match 기준에서 어느 권한이 더 많이 통과하는지 보여준다.",
            "- `average_reward_by_profile.svg`: 부분점수까지 포함한 전체 성능 차이를 보여준다.",
            "- `split_exact_success.svg`: split별로 어느 권한에서 성능 차이가 커지는지 보여준다.",
            "- `reward_delta_histogram*.svg`: task별 `{base}` 대비 다른 프로파일의 reward 차이 분포를 보여준다.".format(
                base=base_profile.upper()
            ),
            "- `failure_buckets_by_profile.svg`: near-miss, permission denied, semantic miss 같은 실패 유형 구성을 보여준다.",
            "",
            "## Interpretation Notes",
            "",
            "- exact success는 보수적인 지표라 near-miss를 많이 놓칠 수 있다.",
            "- average reward는 InterCode 원래 채점 기준을 따르므로 출력 mismatch와 state mismatch가 함께 반영된다.",
            "- permission denied 비중이 높으면 비root 제약이 실제로 utility를 줄였다고 해석할 수 있다.",
        ]
    )
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Analyze InterCode results and render plots")
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument(
        "--summary-files",
        nargs="*",
        type=Path,
        default=None,
        help="Optional explicit intercode_command_set_summary.jsonl files",
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--profiles",
        default="root,strict",
        help="Comma-separated profiles to include. Supports aliases r,u,s or root,user,strict.",
    )
    parser.add_argument(
        "--base-profile",
        default="root",
        help="Base profile used for paired reward deltas. Supports aliases r,u,s or root,user,strict.",
    )
    args = parser.parse_args()

    summary_files = args.summary_files or find_summary_files(args.runs_dir)
    if not summary_files:
        raise FileNotFoundError("No intercode_command_set_summary.jsonl files found")

    selected_profiles = parse_profiles_arg(args.profiles)
    base_profile = parse_profiles_arg(args.base_profile)[0]
    comparison_profiles = [profile for profile in selected_profiles if profile != base_profile]

    rows = []
    for summary_path in summary_files:
        for row in load_jsonl(summary_path):
            rows.append(normalize_row(row, summary_path))

    filtered_rows = filter_rows_by_profiles(rows, selected_profiles)
    deduped_rows = dedupe_latest_rows(filtered_rows)
    profile_stats = build_profile_stats(deduped_rows)
    split_stats = build_split_stats(deduped_rows)
    delta_rows = build_delta_rows(deduped_rows, base_profile=base_profile, comparison_profiles=comparison_profiles)
    bucket_counts = build_bucket_counts(deduped_rows)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.out_dir / "all_rows_deduped.csv", deduped_rows)
    write_csv(args.out_dir / "split_stats.csv", split_stats)
    write_csv(args.out_dir / "paired_reward_deltas.csv", delta_rows)
    summary = write_summary_json(
        args.out_dir,
        profile_stats,
        split_stats,
        delta_rows,
        bucket_counts,
        summary_files,
        selected_profiles,
        base_profile,
    )

    plot_profile_metrics(profile_stats, args.out_dir, selected_profiles)
    plot_split_metrics(split_stats, args.out_dir, selected_profiles)
    plot_reward_delta_histograms(delta_rows, args.out_dir, base_profile, comparison_profiles)
    plot_failure_buckets(bucket_counts, args.out_dir, selected_profiles)
    render_report(summary, args.out_dir)

    print("[DONE] intercode analysis completed")
    print(f"[DONE] outputs saved to: {args.out_dir}")


if __name__ == "__main__":
    main()
