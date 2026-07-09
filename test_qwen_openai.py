import os
import httpx
from openai import OpenAI

api_key = os.getenv("DASHSCOPE_API_KEY")
print("api_key_configured:", bool(api_key))
print("api_key_length:", len(api_key or ""))

client = OpenAI(
    api_key=api_key,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    http_client=httpx.Client(
        timeout=60,
        trust_env=False,
    ),
)

resp = client.chat.completions.create(
    model="qwen-turbo",
    messages=[
        {"role": "user", "content": "ping"}
    ],
)

print(resp.choices[0].message.content)
