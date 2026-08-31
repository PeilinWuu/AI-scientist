# Reproduce Competition 1B

本次冲刺实测环境为 Windows PowerShell、Python 3.12.7；CI 同时使用 Python 3.13 验证锁定依赖。

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.lock.txt
.\.venv\Scripts\python -m pytest -q
```

复制 `.env.example` 为 `.env`；真实 Qwen 测试需要有效 `DASHSCOPE_API_KEY`，确定性旗舰案例不需要网络或密钥。

```powershell
# 一键运行 5-seed 旗舰案例、baseline 和失败案例
.\.venv\Scripts\python -m src.ai_scientist.competition_cli run-flagship --output competition/1b

# API
.\.venv\Scripts\python -m uvicorn src.main_api:app --host 127.0.0.1 --port 8000

# Streamlit（另一个终端）
.\.venv\Scripts\streamlit run app_streamlit.py

# 提交准备度；会再次运行全量 pytest
.\.venv\Scripts\python -m src.ai_scientist.competition_readiness

# 仅在需要刷新 Qwen 证据时显式产生一次真实调用
.\.venv\Scripts\python -m src.ai_scientist.competition_readiness --run-qwen-smoke
```

结果位于 `competition/1b/cases/flagship/` 和 `competition/1b/results/`。普通 readiness 不会调用
外部 API；它读取默认 168 小时内的脱敏 `qwen_smoke_evidence.json`。详情见
`QWEN_EVIDENCE_CHECKLIST.md`。
