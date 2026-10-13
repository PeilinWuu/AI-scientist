# Server release acceptance — 2026-10-13

## Release and topology

- Application release: `3497d9699d47f133800b41aa4f6b3be436792c17` (includes the application changes through `8b905a7`, bounded backup utility, and configuration exporter).
- Branch: `competition/1b-final-sprint`.
- Repository: `/home/ubuntu/ai-scientist`; live code symlink: `/opt/ai-scientist` → `/opt/ai-scientist-releases/3497d96`.
- One prior deployment retained at `/opt/ai-scientist-previous`; no additional historical code copies were created.
- Python 3.10 virtual environment belongs to the release directory. Install `requirements.lock.txt` plus `computation-requirements.txt`.
- FastAPI and Streamlit run as `aiscientist`, managed by existing enabled systemd units; Nginx exposes the application anonymously as explicitly requested by the owner.
- Direct GitHub retrieval was unreliable on the server. A verified incremental Git bundle was transferred over authenticated SSH and fast-forwarded into the repository. Application and deployment-utility commits were also pushed to GitHub.

## Configuration

The owner explicitly selected the locally verified API key and official DashScope compatible-mode endpoint, replacing the old server workspace endpoint and different key. No credentials are stored in this report or Git.

- Main model: `qwen3.8-max`; search model: `qwen3.7-plus`.
- Configuration: `/etc/ai-scientist/ai-scientist.env`, root-owned, group `aiscientist`, mode `0640`.
- Server-specific persistence paths retained: `/var/lib/ai-scientist/{projects,runs,competition}`.
- Old experimental controlled Python disabled; namespace-isolated general computation enabled after real tests passed.
- Duplicate Nginx server blocks consolidated; `/health`, `/api/`, `/docs`, `/openapi.json`, and Streamlit WebSocket proxy retained/validated. Existing HTTP-only access was retained; TLS was not introduced by this release.

## Acceptance evidence

On the server, using the new virtual environment and service account:

```bash
RUN_NAMESPACE_TESTS=1 .venv/bin/python -m pytest \
  tests/ai_scientist tests/test_conversation_api.py tests/test_bounded_backup.py -q
```

Result before the final backup-budget refinement: **714 passed**, one dependency deprecation warning, 49.11 seconds. Includes real namespace isolation, scientific-library imports, plotting, input read-only enforcement, and execution timeout tests. `pip check` passed. The final backup refinement additionally passed its four focused regression tests locally.

- All four existing project JSON files passed the new schema without modifying them; no active jobs were present before switching.
- Real Qwen chat smoke: response model `qwen3.8-max`, about 0.9 seconds.
- Real research search adapter: `qwen3.7-plus`, one provider web-search call, 27 candidates, about 41.38 seconds. Candidate count is not a scientific relevance guarantee.
- After switching, public `/health` returned success and the computation runtime probe reported available; service processes and automatic startup remained enabled.
- Public browser rendered the new research entry, project navigation, unified conversation interface, and an existing saved report.
- No new complete research round was claimed as part of deployment acceptance. Old scientific conclusions were not revalidated, and local experimental research projects were not copied to production.
- Namespace probe emits a joblib warning about read-only shared memory and falls back to serial processing; actual scientific execution/plot tests passed. Isolation was not weakened to suppress that warning.

## Bounded backups and rollback

Existing daily timer retained, running the root-owned bounded backup implementation at `/usr/local/sbin/ai-scientist-backup.sh` (Python shebang despite its legacy filename).

- At most seven successful archives in `/var/backups/ai-scientist`.
- **2 GiB combined budget for data/configuration archives plus the one previous code release.** Temporary archive bytes are also bounded against the remaining budget.
- Stop creating backups and report a service failure if less than 5 GiB is free, or there is no safe room for a new archive. Existing valid backups are not sacrificed merely to attempt a new oversized archive.
- Archives contain `/var/lib/ai-scientist` and `/etc/ai-scientist`, not active virtual environments or package caches. The previous code directory is accounted separately in the combined budget.
- Root-only backup directory and archive permissions; completed archives are read back before publication. Retention only removes recognized archive files, never live projects.
- Daily archives are best-effort snapshots while the app is online. The pre-switch checkpoint was taken with services stopped.
- At cutover verification, previous code used about 705 MiB; data/configuration archives about 4 MiB; approximately 27 GiB remained free on the 40 GiB filesystem.

Rollback artifacts in `/etc/ai-scientist`: `ai-scientist.rollback.env`, `nginx.rollback.conf`, and `backup-script.previous.sh`. These may contain sensitive configuration and must not be published. For a rollback, first stop the health-check timer and both app services, confirm there are no active jobs, repoint `/opt/ai-scientist` to `/opt/ai-scientist-previous`, restore the rollback environment with root:aiscientist `0640` permissions, and restart/verify services. Do not restore data automatically or erase newer research. Nginx configuration need only be reverted if necessary; validate with `nginx -t` before reload.

Future deployments should prepare a fresh release, validate it, then retain only the immediately preceding code release after successful cutover. Do not accumulate release directories indefinitely or run a destructive repository reset over production data.
