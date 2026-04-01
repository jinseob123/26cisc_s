# 26sisc_ss

AI 에이전트의 **능력(Capability)** 과 **안전성(Safety)** 을 동시에 평가하는 멀티 벤치마크 프레임워크입니다.

---

## 개요

터미널/데스크탑을 제어하는 AI 에이전트가 실제로 얼마나 유해한 행동을 할 수 있는지, 그리고 코드/명령어 생성 능력은 어느 수준인지를 측정합니다.

두 개의 주요 벤치마크를 통합하여 평가합니다:

- **CUAHarm** — Computer-Using Agent의 악성 행동 성공률 측정
- **InterCode** — 자연어 명령을 코드/셸 명령으로 변환하는 능력 측정

---

## 프로젝트 구조

```
26sisc_ss/
├── CUAHarm/                  # 에이전트 유해성 벤치마크
│   ├── desktop_env/          # 가상 데스크탑 환경
│   ├── mm_agents/            # 멀티모달 에이전트 (GUI + 터미널)
│   ├── evaluation_examples/  # 테스트 시나리오 (104개)
│   ├── results/              # 실행 결과
│   ├── run.py                # 평가 실행 스크립트
│   └── analyze_results.py    # 결과 분석
│
├── agent/                    # 에이전트 실행 하네스
│   ├── main.py               # 진입점
│   ├── bench_intercode.py    # InterCode 벤치마크 러너
│   ├── test_bench.py         # LLM 피드백 기반 종합 벤치마크
│   ├── security_bench.py     # 보안 중심 평가
│   └── analyze/              # 결과 분석 스크립트
│
├── bench/intercode/          # InterCode 프레임워크 및 데이터셋
│   └── data/
│       ├── nl2bash/          # NL2Bash 200개 태스크
│       ├── ctf/              # Capture The Flag
│       ├── python/           # Python 코딩 태스크
│       └── sql/              # SQL 태스크
│
├── results/                  # 전체 실행 결과
│   ├── intercode_runs/       # root / user / strict 프로파일별 결과
│   └── analysis_results/     # CSV 분석 리포트
│
├── docker-compose.yml        # vLLM 서버 + 에이전트 컨테이너
└── Makefile                  # 빌드/실행 자동화
```

---

## 사용 벤치마크

### CUAHarm
- **104개** 악성 시나리오 (터미널 52개, GUI 52개)
- 7가지 카테고리: Credential Theft, Privilege Escalation, Network Exploitation, System Disruption, Data Tampering, Forensic Evasion, Tool Utilization
- 권한 수준(root / user)별 성공률 측정

### InterCode
- **NL2Bash**: 자연어 → bash 명령어 변환 (200개, 4개 파일시스템 변형)
- **SQL**: Spider, Bird 데이터셋 기반 쿼리 생성
- **Python**: MBPP, Apps 기반 코드 생성
- **CTF**: 보안 챌린지

---

## 실행 프로파일

| 프로파일 | 설명 |
|---------|------|
| `root` | root 권한으로 실행 |
| `user` | 일반 유저 권한으로 실행 |
| `strict` | sudo 없이 실행 |

---

## 실행 방법

```bash
# Docker 환경 시작
make up

# 커맨드 셋 생성
make command_set

# 전체 프로파일 실행
make run-all-profiles

# 개별 실행
python agent/bench_intercode.py run-set \
  --records-path results/intercode_command_sets/split1/commands.jsonl \
  --profile root
```

## 결과 분석

```bash
# CUAHarm 유해성 분석
python CUAHarm/analyze_results.py

# InterCode 결과 분석
python agent/analyze/analyze_intercode_results.py
```

---

## 지원 모델

- `openai/gpt-oss-20b` (기본값)
- `Qwen/Qwen2.5-7B-Instruct`
- `meta-llama/Llama-3.2-3B-Instruct`
- OpenAI API 호환 모델

---

## 주요 기술 스택

- **모델 서빙**: vLLM
- **샌드박스**: Docker
- **데스크탑 자동화**: pyautogui, playwright
- **로깅**: loguru, wandb
