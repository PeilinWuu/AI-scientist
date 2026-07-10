# Pure Qwen Shell

This project is a clean **Pure Qwen Shell** with an optional **Qwen Search** mode.

The default path sends only visible `user` and `assistant` chat messages to Qwen through the Alibaba Cloud Model Studio / Bailian OpenAI-compatible API.

## Modes

`POST /api/chat` is **Pure Qwen**:

- no system prompt;
- no skill modules;
- no tools;
- no web search;
- no RAG;
- no intent router;
- no experiment planning;
- Qwen call sends only `model` and `messages`.

`POST /api/chat_search` is **Qwen Search**:

- one minimal search-only system message that tells Qwen the application layer has enabled web search;
- no skill modules;
- no tools;
- no RAG or intent router;
- Qwen call sends `model`, `messages`, and forced search settings.

The search-mode system message is used only by `/api/chat_search`. It does not affect `/api/chat` and does not restore any old agent, skill, tool, or experiment-planning behavior.

The Streamlit sidebar switch `启用联网搜索` selects the endpoint. It does not add `use_web_search` or any mode flag to the request payload.

## Configuration

Create `.env` from `.env.example`:

```env
DASHSCOPE_API_KEY=
LLM_MODEL=qwen-turbo
LLM_SEARCH_MODEL=qwen-plus-latest
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_TIMEOUT=60
QWEN_SEARCH_STRATEGY=turbo
RUNS_DIR=runs
```

`DASHSCOPE_API_KEY` is required for chat and ping endpoints. The key is never printed by the app.

## Install

```bash
pip install -r requirements.txt
```

## Run Backend

```bash
python -m uvicorn src.main_api:app --reload --host 127.0.0.1 --port 8000
```

Restart uvicorn after changing `.env`, `src/pure_qwen_client.py`, or `src/search_qwen_client.py`.

## Run Frontend

```bash
streamlit run app_streamlit.py
```

## Verify Pure Qwen

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

Expected:

```json
{
  "messages": [
    {"role": "user", "content": "你是谁？"}
  ],
  "model": "qwen-turbo",
  "mode": "pure_qwen"
}
```

There must be no `system` message in `/api/debug_payload`.

## Verify Qwen Search

Open:

```text
http://localhost:8000/api/search_ping
```

Payload preview:

```bash
curl -X POST http://localhost:8000/api/debug_search_payload ^
  -H "Content-Type: application/json" ^
  -d "{\"message\":\"世界杯最近的比赛结果如何？\",\"history\":[],\"model\":\"qwen-plus-latest\"}"
```

Expected:

```json
{
  "messages": [
    {
      "role": "system",
      "content": "当前应用层已经为本次请求启用了互联网搜索..."
    },
    {
      "role": "user",
      "content": "世界杯最近的比赛结果如何？"
    }
  ],
  "model": "qwen-plus-latest",
  "mode": "qwen_search",
  "extra_body": {
    "enable_search": true,
    "search_options": {
      "forced_search": true,
      "enable_source": true,
      "enable_citation": true,
      "citation_format": "[<number>]",
      "search_strategy": "turbo"
    }
  }
}
```

`/api/chat` is Pure Qwen and does not search. `/api/chat_search` enables Qwen search with:

```python
extra_body = {
    "enable_search": True,
    "search_options": {
        "forced_search": True,
        "enable_source": True,
        "enable_citation": True,
        "citation_format": "[<number>]",
        "search_strategy": "turbo",
    },
}
```

If only `enable_search=true` is set, the model may decide by itself whether to search. For real-time questions such as weather, news, or stocks, `forced_search=true` is required.

## Search Effectiveness

Search effectiveness is judged by structured fields, not by whether the model claims it searched:

- `search_effective`
- `sources`
- `source_metadata_available`
- `warning`
- `request_id`

If the provider returns source metadata, `/api/chat_search` sets `search_effective=true` and the Streamlit UI shows the source list.

If no sources are returned, the system does not pretend search succeeded. It returns:

- `source_metadata_available=false`
- `sources=[]`
- a `warning`

If the model still says it cannot access the internet and no sources are available, `search_effective=false`.

If search still fails, first check that the frontend is calling `/api/chat_search`, then call `/api/debug_search_payload` and verify the full `extra_body` above. Different models or regions may support search differently; try `qwen-plus`, `qwen-max`, `qwen-plus-latest`, or `qwen-max-latest` if search mode fails. If forced search still fails, the next implementation path is a separate Responses API plus web-search tool mode.

## Qwen Transport

Both clients use:

```python
OpenAI(
    api_key=api_key,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    http_client=httpx.Client(timeout=60, trust_env=False),
)
```

## Tests

```bash
pytest -q
```
