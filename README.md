# Pure Qwen Shell

This project provides two deliberately separate Qwen paths:

- **Pure Qwen** uses Chat Completions and sends only `model` and visible `user`/`assistant` messages.
- **Qwen Search** uses the Responses API with the built-in `web_search` and `web_extractor` tools.

The project does not load an Agent framework, skills, RAG, an intent router, or experiment-planning logic.

## Configuration

Create `.env` from `.env.example`:

```env
DASHSCOPE_API_KEY=
LLM_MODEL=qwen-turbo
LLM_SEARCH_MODEL=qwen3.7-plus
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
RESPONSES_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_TIMEOUT=120
RUNS_DIR=runs
```

`DASHSCOPE_API_KEY` is required and is never printed by the application. If the selected search model is not enabled for the current account or region, the API returns an explicit error and does not fall back to the legacy search path.

## Install

```bash
pip install -r requirements.txt
```

## Run

Backend:

```bash
python -m uvicorn src.main_api:app --reload --host 127.0.0.1 --port 8000
```

Frontend:

```bash
streamlit run app_streamlit.py
```

Restart uvicorn after changing `.env` or either Qwen client.

## Pure Qwen

`POST /api/chat` remains a direct Chat Completions pass-through. Its request contains the current message, visible chat history, and an optional model. The Qwen call itself receives only:

```python
response = client.chat.completions.create(
    model=resolved_model,
    messages=messages,
)
```

Verify the payload:

```bash
curl -X POST http://localhost:8000/api/debug_payload ^
  -H "Content-Type: application/json" ^
  -d "{\"message\":\"你是谁？\",\"history\":[],\"model\":\"qwen-turbo\"}"
```

The result must contain only the visible user message and `mode: pure_qwen`.

## Qwen Search

`POST /api/chat_search` uses the OpenAI-compatible Responses API. It sends the current user input unchanged:

```python
response = client.responses.create(
    model="qwen3.7-plus",
    input="用户本轮原始输入",
    tools=[
        {"type": "web_search"},
        {"type": "web_extractor"},
    ],
    previous_response_id="可选的上一轮响应 ID",
)
```

No custom system or developer message is added. Search results and tool output are not concatenated into the assistant reply. Multi-turn search uses `previous_response_id` instead of resending the visible conversation.

Request example:

```json
{
  "message": "搜索世界杯最新一场比赛结果。",
  "model": "qwen3.7-plus",
  "previous_response_id": null
}
```

Response shape:

```json
{
  "reply": "最终回答文本",
  "model": "qwen3.7-plus",
  "mode": "qwen_search",
  "response_id": "resp_...",
  "request_id": "...",
  "search_used": true,
  "sources": [],
  "tool_usage": {
    "web_search": 1,
    "web_extractor": 0
  }
}
```

Only `reply` is written to the visible assistant history. Source citations and tool counts remain separate diagnostic fields.

Verify the exact search payload without calling Qwen:

```bash
curl -X POST http://localhost:8000/api/debug_search_payload ^
  -H "Content-Type: application/json" ^
  -d "{\"message\":\"搜索世界杯最新一场比赛结果。\",\"model\":\"qwen3.7-plus\",\"previous_response_id\":null}"
```

Expected fields are `model`, `mode`, `input`, `previous_response_id`, and `tools`. There are no messages or manually assembled retrieval text in this debug payload.

## Frontend State

`st.session_state.messages` contains only:

```json
{"role": "user | assistant", "content": "visible text"}
```

Search continuity is stored separately in `st.session_state.search_previous_response_id`. It is cleared when search is disabled, the search model changes, or the conversation is cleared.

Developer debug can display the endpoint, safe request preview, response ID, request ID, citations, and tool counts outside the assistant message. With Developer debug disabled, the page displays only the conversation.

## Verification Endpoints

```text
GET  /health
GET  /api/qwen_ping
GET  /api/search_ping
POST /api/debug_payload
POST /api/chat
POST /api/debug_search_payload
POST /api/chat_search
```

## Tests

```bash
pytest -q
```
