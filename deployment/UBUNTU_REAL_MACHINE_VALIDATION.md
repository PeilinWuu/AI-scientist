# Ubuntu 22.04 LTS Real-Machine Validation

Date: 2026-09-02 (Asia/Shanghai)  
Classification: **FIXED** (application/lock compatibility), **MANUAL** (host operations), **BLOCKER: none**

## Environment

- OS: Ubuntu 22.04.5 LTS (Jammy)
- Kernel: Linux 6.8.0-138-generic
- Architecture: x86_64
- Python: 3.10.12 (`/usr/bin/python3`)
- RAM: 31 GiB total (developer machine; 25 GiB available at baseline)
- Disk: repository filesystem `/data`, 98G total, 12G used, 82G available (13%)
- Locale: `LANG=zh_CN.UTF-8`, effective `LC_ALL=C.UTF-8`; UTF-8
- Shell: `/bin/bash`
- Git: 2.34.1

## Clean Clone

- Branch: `competition/1b-final-sprint`
- HEAD: `7dda174 chore: record final Ubuntu audit readiness test run`
- Recent expected commits present: `7dda174`, `b401b9a`, `5a82c8d`, `a0d4303`
- Worktree was clean before validation; repository is on local ext4 (`/dev/nvme0n1p8`), not NTFS/shared Windows storage.

## System Packages

Installed: git, curl, ca-certificates, python3, python3-venv, python3-pip, build-essential, fonts-noto-cjk.  
Missing: `nginx`. `sudo -n` was unavailable, so no apt installation was claimed. **MANUAL:** run the documented `sudo apt update && sudo apt install -y nginx` on the target host.

## requirements.lock

**PASS after minimal compatibility fix.** A new `.venv` was created with Ubuntu Python 3.10.12. The original lock failed on `numpy==2.5.1`, `pandas==3.0.5`, and `rpds-py==2026.6.3` (Python 3.11/3.12 requirements). `requirements.lock.txt` now uses Python markers: NumPy 2.2.6, Pandas 2.3.3, rpds-py 0.30.0 on Python 3.10; original pins remain for newer Python. Strict lock installation completed successfully.

## Pytest

**PASS: 213 passed in 8.06s.** No warning was emitted on Ubuntu. The only initial failures were three Streamlit AppTest path-resolution cases; tests now resolve the repository entrypoint with `Path(__file__).resolve()`.

## Readiness

**PASS:** `tests_passed=true`, `readiness_percent=100.0`, Qwen evidence status `PASSED` (existing repository evidence; no new call made).

## Qwen Ubuntu Smoke

**PASS.** The local `.env` contained a credential (only `configured=true` and length were inspected; the secret was never printed). A single independent Ubuntu SDK/network call using `qwen3.8-max` returned exactly `QWEN_UBUNTU_OK` in 1871 ms. See the redacted `deployment/qwen_ubuntu_smoke.json`. Existing competition evidence was not modified.

## Controlled Python Sandbox

**PASS:** dedicated tests `15 passed`. Real Linux checks: normal NumPy/Pandas statistic succeeded; infinite loop timed out and was terminated; `import os`, `/etc/passwd` open, subprocess, and socket attempts were rejected; no orphan child process remained; request/result temporary files were removed. Worker baseline peak was about 100 MiB. A large allocation was stopped by the restricted worker before completion (about 251 MiB observed), not treated as a security bypass. Flag restored to `AI_SCIENTIST_ENABLE_CONTROLLED_PYTHON=0`.

## File Parsers

**PASS:** real temporary UTF-8 TXT, Markdown, CSV, TSV, JSON, XML, XLSX and blank PDF. All returned parser name, summary/provenance and structured metadata (columns/rows or page count); Chinese text remained intact. XLS was **NOT_AVAILABLE** (no fixture supplied).

## Deterministic Executors

**PASS:** temporary Linux project ran dataset inspection, missingness, descriptive statistics, correlation, linear regression and scatter PNG generation. JSON, CSV and PNG artifacts were written successfully with audit metadata.

## Damped Oscillator

**PASS:** temporary project, seed `20260831`, completed Round 1 → feedback/plan adjustment → Round 2. Two iterations persisted; RMSE improved `0.0576837 → 0.0330893` and all execution/feedback artifacts were saved outside formal competition evidence.

## FastAPI

**PASS:** Uvicorn started without traceback. `/health` HTTP 200, `/api/competition/1b/readiness` HTTP 200, and `/docs` HTTP 200 with Swagger title `AI Scientist API - Swagger UI`.

## API Workflow

**PASS:** real POST `/api/research/start` returned project `project_b5b9146e4a60444db2d102d7013e72c3`; real `/step_async` returned job `job_140e3fd78dfc4cec841343fdd93d16b2`, which reached `completed` and advanced to `QUESTION_FORMULATION`.

## Streamlit

**PASS:** headless process on `0.0.0.0:8501` started without DISPLAY/traceback; `curl -I http://127.0.0.1:8501` returned HTTP 200. Browser network URL was printed by Streamlit for manual visual review.

## Upload / Persistence

**PASS:** real CSV upload returned parsed tabular columns, row count, missingness and `parsed_artifact_id`. After stopping and restarting FastAPI, the same project and uploaded asset remained queryable. Atomic write/replace/delete and UTF-8 Chinese file checks passed on `data/research_projects`, `runs`, competition, uploads and sandbox temp directories. Linux case sensitivity confirmed.

## Nginx

**MANUAL:** package is absent and `nginx -t` cannot run until installed. `deployment/nginx.conf.example` has `/api/ → 127.0.0.1:8000`, `/ → 127.0.0.1:8501`, WebSocket Upgrade/Connection headers and 25 MiB request limit. Install and validate on ECS without overwriting an existing production config; add TLS there.

## systemd

**MANUAL_ECS_SYSTEMD_INSTALL:** added generic `deployment/systemd/ai-scientist-api.service` and `ai-scientist-web.service` using `/opt/ai-scientist`, `/etc/ai-scientist/ai-scientist.env`, `aiscientist:aiscientist`, `.venv`, `Restart=always`, `RestartSec=5`, and `UMask=0027`. Local `systemd-analyze verify` is blocked by host-wide NVIDIA ordering/permission issues and correctly reports the target paths do not exist on this workstation.

## Resource Usage

Baseline developer host: 31 GiB RAM, 25 GiB available. Service RSS during test: FastAPI about 158 MiB; Streamlit about 53 MiB; sandbox worker about 100 MiB baseline. This host has unrelated Chrome/Codex processes, so these are process-level measurements rather than a 4 GiB host benchmark.

## Windows → Linux Fixes

1. `requirements.lock.txt`: Python-version markers for NumPy, Pandas and rpds-py, preserving original newer-Python pins.
2. `tests/ai_scientist/test_unified_product_entry.py`: `Path(__file__).resolve().parents[2] / "app_streamlit.py"` for Streamlit AppTest, fixing Linux-relative resolution without changing product behavior.
3. Added deployment-only systemd templates; no Windows behavior or sandbox security restriction was removed.

## ECS Manual Tasks

Create `aiscientist`; install Nginx; deploy source to `/opt/ai-scientist`; persist projects/runs/competition under `/var/lib/ai-scientist`; set `/etc/ai-scientist/ai-scientist.env` mode 0640; enable both services; configure Nginx WebSocket proxy and HTTPS; configure journald/log rotation and limits; health/RAM/CPU/disk (>80%, >85%) alerts; Qwen budget and upload limits; daily backups; sandbox temp cleanup; artifact retention; consider 4 GiB swap on a 4 GiB ECS host.

## Classification

- PASS: Ubuntu runtime, strict lock installation after fix, pytest, readiness, parsers, deterministic executors, oscillator, FastAPI, Streamlit, API, upload and persistence.
- FIXED: three Python-version lock pins and Streamlit AppTest path portability.
- MANUAL: Nginx/apt, ECS secret provisioning, ECS systemd installation, TLS, monitoring, backups, long-run operations.
- BLOCKER: none.

## Capacity Recommendation

The measured application processes are small enough for a 4 GiB/4 vCPU server, but Qwen calls, concurrent pandas/pyarrow work and sandbox workers add burst usage. Recommend **1 concurrent research workflow and 1 sandbox worker** initially; raise only after ECS observation. Keep sandbox disabled by default and add **4 GiB swap** on ECS if permitted. The proposed Beijing Ubuntu 22.04 LTS 4C4G/40GB/3Mbps instance is suitable for a controlled initial deployment, subject to the manual production tasks above.

## Windows Regression

Because cross-platform files changed, run the final `python -m pytest -q` on the Windows baseline machine before release. No Windows regression was possible from this Ubuntu host.
