# Pure Qwen Shell

This project is a clean **Pure Qwen Shell**.

It provides a small FastAPI backend and a Streamlit chat UI. The backend sends only visible `user` and `assistant` chat messages to Qwen through the Alibaba Cloud Model Studio / Bailian OpenAI-compatible API.

## What It Does Not Do

Pure Qwen Shell does not:

- inject a system prompt;
- load skill modules;
- call tools;
- enable web search;
- run RAG;
- classify user intent;
- plan experiments;
- rewrite or repair model responses.

## Configuration

Create `.env` from `.env.example`:

```env
DASHSCOPE_API_KEY=
LLM_MODEL=qwen-turbo
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_TIMEOUT=60
RUNS_DIR=runs
```

`DASHSCOPE_API_KEY` is required for `/api/chat` and `/api/qwen_ping`. The key is never printed by the app.

## Install

```bash
pip install -r requirements.txt
```

## Run Backend

```bash
python -m uvicorn src.main_api:app --reload --host 127.0.0.1 --port 8000
```

Restart uvicorn after changing `.env` or `src/pure_qwen_client.py`.

## Run Frontend

```bash
streamlit run app_streamlit.py
```

## Verify

Open:

```text
http://localhost:8000/health
http://localhost:8000/api/qwen_ping
```

Payload preview:

```bash
curl -X POST http://localhost:8000/api/debug_payload ^
  -H "Content-Type: application/json" ^
  -d "{\"message\":\"你是谁？\",\"history\":[],\"model\":\"qwen-turbo\"}"
```

Expected response:

```json
{
  "messages": [
    {"role": "user", "content": "你是谁？"}
  ],
  "model": "qwen-turbo",
  "mode": "pure_qwen"
}
```

Chat:

```bash
curl -X POST http://localhost:8000/api/chat ^
  -H "Content-Type: application/json" ^
  -d "{\"message\":\"你好\",\"history\":[],\"model\":\"qwen-turbo\"}"
```

## Qwen Transport

`PureQwenClient` uses the same style as the standalone successful Qwen test:

```python
OpenAI(
    api_key=api_key,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    http_client=httpx.Client(timeout=60, trust_env=False),
)
```

The Qwen call sends only:

```python
client.chat.completions.create(
    model=resolved_model,
    messages=messages,
)
```

## Tests

```bash
pytest -q
```
