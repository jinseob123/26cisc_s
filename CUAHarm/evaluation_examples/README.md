---
license: apache-2.0
configs:
- config_name: computer use tasks
  data_files:
  - split: test
    path: computer_use_tasks.csv
- config_name: common malicious prompts
  data_files:
  - split: test
    path: common_malicious_prompts.csv
task_categories:
- image-text-to-text
tags:
- agents
- safety
- benchmark
- cybersecurity
- computer-control
---

# CUAHarm Dataset

This repository contains the **CUAHarm** benchmark, introduced in the paper [Measuring Harmfulness of Computer-Using Agents](https://huggingface.co/papers/2508.00935).

**CUAHarm** is a benchmark designed to evaluate the safety risks of Computer-Using Agents (CUAs) — AI agents that can autonomously control computers to perform multi-step actions.

**Code:** [https://github.com/db-ol/CUAHarm](https://github.com/db-ol/CUAHarm)

## Introduction

Computer-using agents (CUAs), which autonomously control computers to perform multi-step actions, might pose significant safety risks if misused. Existing benchmarks mostly evaluate language models' (LMs) safety risks in chatbots or simple tool-usage scenarios, without granting full computer access. CUAHarm addresses this by introducing a new benchmark that consists of 104 expert-written realistic misuse risks, such as disabling firewalls, leaking confidential information, launching denial-of-service attacks, or installing backdoors. The benchmark provides a sandbox environment and rule-based verifiable rewards to measure CUAs' success rates in executing these tasks (e.g., whether the firewall is indeed disabled), not just refusal.

## Key Features

*   **CUAHarm Dataset**: A collection of 104 realistic misuse scenarios, including 52 computer use tasks that require direct system interaction. These tasks cover seven categories of malicious objectives: credential theft, privilege escalation, network exploitation, system disruption, data tampering, forensic evasion, and tool utilization.
*   **Direct Terminal Access**: CUAHarm supports computer-using agents with full system access through direct terminal interaction.
*   **Verifiable Rewards**: Provides rule-based verifiable rewards to measure CUAs' success rates in executing tasks.
*   **Token Cost Calculation**: Built-in functionality to track token usage during agent execution.

## Dataset Structure

The CUAHarm dataset is composed of two main configurations, available as CSV files:
*   `computer_use_tasks.csv`: Contains the detailed computer use tasks that involve direct system interaction.
*   `common_malicious_prompts.csv`: Includes additional prompts for evaluating agents against common malicious activities.

## Usage

You can load the dataset using the Hugging Face `datasets` library:

```python
from datasets import load_dataset

# To load the computer use tasks
dataset_computer_use = load_dataset("CUAHarm/CUAHarm", "computer use tasks")

# To load the common malicious prompts
dataset_malicious_prompts = load_dataset("CUAHarm/CUAHarm", "common malicious prompts")

print(dataset_computer_use)
print(dataset_malicious_prompts)
```

For detailed installation instructions, running experiments, and understanding the virtual machine setup, please refer to the comprehensive guidelines on the [official GitHub repository](https://github.com/db-ol/CUAHarm).

## Citation

If you find this work useful, please consider citing our paper:

```bibtex
@misc{tian2025measuringharmfulnesscomputerusingagents,
      title={Measuring Harmfulness of Computer-Using Agents}, 
      author={Aaron Xuxiang Tian and Ruofan Zhang and Janet Tang and Jiaxin Wen},
      year={2025},
      eprint={2508.00935},
      archivePrefix={arXiv},
      primaryClass={cs.CR},
      url={https://arxiv.org/abs/2508.00935}, 
}
```