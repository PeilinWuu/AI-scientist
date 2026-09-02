# Ubuntu 22.04 LTS 部署依赖与启动清单

以下命令假设 Ubuntu 22.04 x86_64、独立服务账户 `aiscientist` 和源码目录 `/opt/ai-scientist`。请替换域名和路径；不要把密钥写进仓库。

## apt packages

```bash
sudo apt update
sudo apt install -y python3.10 python3.10-venv python3-pip build-essential nginx ca-certificates curl git
sudo apt install -y fonts-noto-cjk
```

NumPy/Pandas 等通常安装 manylinux wheels；`build-essential` 用于缺少 wheel 时的安全兜底。无需安装 Microsoft Office、Excel、COM、X server 或桌面环境。

## 用户、目录和权限

```bash
sudo useradd --system --home /opt/ai-scientist --shell /usr/sbin/nologin aiscientist
sudo mkdir -p /opt/ai-scientist /var/lib/ai-scientist/{projects,runs,competition} /var/log/ai-scientist
sudo chown -R aiscientist:aiscientist /opt/ai-scientist /var/lib/ai-scientist /var/log/ai-scientist
sudo chmod 750 /var/lib/ai-scientist /var/lib/ai-scientist/{projects,runs,competition}
```

将 `AI_SCIENTIST_PROJECTS_DIR=/var/lib/ai-scientist/projects`、`RUNS_DIR=/var/lib/ai-scientist/runs`、`AI_SCIENTIST_COMPETITION_DIR=/var/lib/ai-scientist/competition` 设置为服务器环境变量。不要把生产数据目录放在只读源码目录中。

## Python 环境

```bash
sudo -u aiscientist python3.10 -m venv /opt/ai-scientist/.venv
sudo -u aiscientist /opt/ai-scientist/.venv/bin/python -m pip install --upgrade pip
sudo -u aiscientist /opt/ai-scientist/.venv/bin/pip install -r /opt/ai-scientist/requirements.lock.txt
sudo -u aiscientist /opt/ai-scientist/.venv/bin/python -m pytest -q
```

## 环境变量

通过 systemd `EnvironmentFile=/etc/ai-scientist/ai-scientist.env` 或 ECS 密钥管理注入：

```text
DASHSCOPE_API_KEY=<server-secret>
LLM_MODEL=qwen3.8-max
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
RESPONSES_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AI_SCIENTIST_PROJECTS_DIR=/var/lib/ai-scientist/projects
RUNS_DIR=/var/lib/ai-scientist/runs
AI_SCIENTIST_COMPETITION_DIR=/var/lib/ai-scientist/competition
AI_SCIENTIST_DEFAULT_PLANNING_ONLY=true
AI_SCIENTIST_MAX_MODEL_CALLS=50
AI_SCIENTIST_ENABLE_CONTROLLED_PYTHON=0
UI_TIMEZONE=Asia/Shanghai
```

`AI_SCIENTIST_ENABLE_CONTROLLED_PYTHON=0` 是默认安全值；只有明确需要实验性沙箱时才改为 `1`。环境文件权限建议 `chmod 640`，属主为 root、组为 `aiscientist`。

## 启动命令

```bash
sudo -u aiscientist /opt/ai-scientist/.venv/bin/python -m uvicorn src.main_api:app --host 127.0.0.1 --port 8000 --proxy-headers
sudo -u aiscientist /opt/ai-scientist/.venv/bin/streamlit run /opt/ai-scientist/app_streamlit.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
```

生产环境应将两条命令分别放入 systemd service，设置 `Restart=always`、`RestartSec=5`、`WorkingDirectory=/opt/ai-scientist`、`UMask=0027`，并以 Nginx 反代公网 80/443。使用 `deployment/nginx.conf.example`，同时配置 TLS、访问认证、IP 速率限制、日志脱敏和 25 MiB 请求体上限。

## 上线验证与三个月运行要求

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/api/competition/1b/readiness
sudo -u aiscientist /opt/ai-scientist/.venv/bin/python -m src.ai_scientist.competition_readiness
```

配置 logrotate（至少保留 14 天）、磁盘使用率告警、systemd 状态监控、定期备份 `/var/lib/ai-scientist`，并在负载均衡器层配置健康探针。不要把 `.env`、API Key、token、临时项目或 sandbox 工作目录打包发布。
