#!/usr/bin/env python3
import urllib.request
import json
import os

API_KEY = "fw_5CAe1FD9DRaBZpWDDTZzhN"
MODEL = "accounts/fireworks/models/llama-v3p3-70b-instruct"
URL = "https://api.fireworks.ai/inference/v1/chat/completions"

payload = {
    "model": MODEL,
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 10
}

data = json.dumps(payload).encode()
req = urllib.request.Request(
    URL,
    data=data,
    headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
)

try:
    with urllib.request.urlopen(req) as resp:
        print(resp.read().decode())
except Exception as e:
    print(f"Error: {e}")
    if hasattr(e, 'read'):
        print(e.read().decode())
