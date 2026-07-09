# Pure Qwen Shell

This repository currently runs in **Pure Qwen Shell** mode.

The active frontend and backend are intentionally minimal. They pass visible user/assistant chat messages to Qwen through the Alibaba Cloud Model Studio / Bailian OpenAI-compatible API.

## What Pure Mode Does Not Do

Pure Qwen Shell does not:

- inject a system prompt;
- load skills;
- call tools;
- enable web search;
- classify intent;
- run RAG or domain-knowledge injection;
- rewrite or repair model responses;
- run experiment loops;
- call the legacy FlowScientist agent chain.

The legacy directories are kept for future reference, but the current main entry points do not import or call them:

- `src/agents/`
- `src/skills/`
- `src/tools/`
- `src/domain_knowledge/`
- `src/workflow/`
- `src/policies/`

If a future version reintroduces a `skill_agent` or experiment-planning mode, it should be added as a separate mode and must not contaminate `pure_qwen` mode.

## Configuration

Create `.env` from `.env.example`:

```env
DASHSCOPE_API_KEY=
LLM_MODEL=qwen-turbo
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_TIMEOUT=60
RUNS_DIR=runs
```

`DASHSCOPE_API_KEY` is required for `/api/chat` and `/api/qwen_ping`. The app never stores or prints the key.

## Install

```bash
pip install -r requirements.txt
```

## Run Backend

```bash
python -m uvicorn src.main_api:app --reload --host 127.0.0.1 --port 8000
```

After changing `src/pure_qwen_client.py` or `.env`, restart uvicorn so the running backend uses the new code and environment.

Useful endpoints:

- API docs: <http://localhost:8000/docs>
- Health: <http://localhost:8000/health>
- Qwen ping: `GET /api/qwen_ping`
- Payload preview: `POST /api/debug_payload`
- Chat: `POST /api/chat`

## Qwen Connectivity

`curl.exe` and the standalone `test_qwen_openai.py` script have verified that Qwen works with:

```python
OpenAI(
    api_key=api_key,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    http_client=httpx.Client(timeout=60, trust_env=False),
)
```

`PureQwenClient` uses the same transport style. If `GET /api/qwen_ping` returns `status=ok`, then `/api/chat` should use the same Qwen path and should also succeed.

## Run Frontend

```bash
streamlit run app_streamlit.py
```

The Streamlit app only sends this payload shape:

```json
{
  "message": "user input",
  "history": [
    {"role": "user", "content": "previous user message"},
    {"role": "assistant", "content": "previous assistant reply"}
  ],
  "model": "qwen-turbo"
}
```

It does not send `system_prompt`, `skill`, `agent_type`, `task_type`, `use_web_search`, `tools_enabled`, `temperature`, `top_p`, or output-format instructions.

## Verify There Is No Hidden Prompt

Call:

```bash
curl -X POST http://localhost:8000/api/debug_payload ^
  -H "Content-Type: application/json" ^
  -d "{\"message\":\"你是谁？\",\"history\":[],\"model\":\"qwen-turbo\"}"
```

Expected response shape:

```json
{
  "messages": [
    {"role": "user", "content": "你是谁？"}
  ],
  "model": "qwen-turbo",
  "mode": "pure_qwen"
}
```

There should be no `system` role and no hidden FlowScientist, soft-swimmer, experiment-planning, strict-JSON, tool, or skill prompt unless the user typed those words.

## Tests

```bash
pytest -q
```

Pure mode tests live in:

```text
tests/test_pure_qwen_payload.py
```

They verify that payload construction stays free of hidden system prompts and legacy agent imports.
