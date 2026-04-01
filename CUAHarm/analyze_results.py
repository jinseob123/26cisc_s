import os
import json
import glob
import pandas as pd
from pathlib import Path

# ── 경로 설정 ──────────────────────────────────────────────
BASE = Path(__file__).parent
TASK_DIR = BASE / "evaluation_examples/examples/terminal"
ROOT_DIR = BASE / "results/root/terminal/terminal/openai/gpt-oss-20b/terminal"
USER_DIR = BASE / "results/user/terminal/terminal/openai/gpt-oss-20b/terminal"

# ── 카테고리 키워드 매핑 ───────────────────────────────────
CATEGORIES = {
    "Credential Theft":     ["credential", "password", "phishing", "keylog", "login", "auth",
                              "passwd", "ssh key", "ssh private", "api key", "openai key",
                              "private key", "secret"],
    "Privilege Escalation": ["privilege", "escalat", "sudo", "suid", "setuid", "permission",
                              "accept.*ssh key", "authorized_keys"],
    "Network Exploitation": ["reverse shell", "payload", "backdoor", "firewall", "network",
                              "msfvenom", "netcat", "bind shell", "hosts file", "reroute",
                              "dos attack", "ping flood", "flood", "udp"],
    "System Disruption":    ["disable", "disrupt", "delete", "remove", "wipe", "crash",
                              "fork bomb", "usb", "shutdown", "microphone", "alsa", "driver",
                              "hard drive", "unkillable", "park.*head", "log out", "logout"],
    "Data Tampering":       ["tamper", "modify", "inject", "corrupt", "alter", "fake", "spoof",
                              "replace", "overwrite", "update.*revenue", "revenue.*update",
                              "archive", "extraction", "revenue", "update the"],
    "Forensic Evasion":     ["evasion", "forensic", "hide", "steganograph", "obfuscat",
                              "cover track", "clear history", "anti-forensic", "evade",
                              "kill.*monitor", "security monitoring"],
    "Tool Utilization":     ["exploit", "metasploit", "script", "alias", "cron", "executable",
                              "compile", "install", "tool", "record.*audio", "audio"],
}

def categorize(instruction: str) -> str:
    text = instruction.lower()
    for cat, keywords in CATEGORIES.items():
        if any(kw in text for kw in keywords):
            return cat
    return "Other"

def read_result(result_dir: Path, uuid: str) -> float | None:
    path = result_dir / uuid / "result.txt"
    if not path.exists():
        return None
    try:
        return float(path.read_text().strip())
    except ValueError:
        return None

# ── 태스크 로드 ───────────────────────────────────────────
records = []
for json_path in TASK_DIR.glob("*.json"):
    uuid = json_path.stem
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    instruction = data.get("instruction", "")
    category = categorize(instruction)
    root_score = read_result(ROOT_DIR, uuid)
    user_score = read_result(USER_DIR, uuid)
    records.append({
        "uuid": uuid,
        "instruction": instruction,
        "category": category,
        "root": root_score,
        "user": user_score,
    })

df = pd.DataFrame(records)
OUT = BASE / "analysis"
OUT.mkdir(exist_ok=True)

# ── Table 1: Overall Harm Rate ────────────────────────────
total = len(df)
root_success = (df["root"] == 1.0).sum()
user_success = (df["user"] == 1.0).sum()

table1 = pd.DataFrame({
    "Privilege": ["Root", "User"],
    "Total Tasks": [total, total],
    "Harm Success": [root_success, user_success],
    "Harm Failure": [total - root_success, total - user_success],
    "Harm Rate": [f"{root_success/total*100:.1f}%", f"{user_success/total*100:.1f}%"],
})
table1.to_csv(OUT / "table1_overall.csv", index=False)
print("Saved: analysis/table1_overall.csv")

# ── Table 2: Category-level Harm Rate ────────────────────
cat_records = []
for cat in CATEGORIES.keys():
    sub = df[df["category"] == cat]
    n = len(sub)
    if n == 0:
        continue
    r_success = (sub["root"] == 1.0).sum()
    u_success = (sub["user"] == 1.0).sum()
    cat_records.append({
        "Category": cat,
        "Tasks": n,
        "Root Success": int(r_success),
        "Root Harm Rate": f"{r_success/n*100:.1f}%",
        "User Success": int(u_success),
        "User Harm Rate": f"{u_success/n*100:.1f}%",
    })

table2 = pd.DataFrame(cat_records)
table2.to_csv(OUT / "table2_by_category.csv", index=False)
print("Saved: analysis/table2_by_category.csv")

# ── Table 3: Root Success & User Failure tasks ───────────
diff = df[(df["root"] == 1.0) & (df["user"] == 0.0)][["uuid", "category", "instruction"]].copy()
diff.to_csv(OUT / "table3_privilege_impact_tasks.csv", index=False)
print(f"Saved: analysis/table3_privilege_impact_tasks.csv ({len(diff)} tasks)")

# ── 전체 상세 결과 ────────────────────────────────────────
df[["uuid", "category", "instruction", "root", "user"]].to_csv(OUT / "all_results.csv", index=False)
print("Saved: analysis/all_results.csv")

print("\n--- Table 1 ---")
print(table1.to_string(index=False))
print("\n--- Table 2 ---")
print(table2.to_string(index=False))
