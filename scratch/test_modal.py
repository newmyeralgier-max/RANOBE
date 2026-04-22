#!/usr/bin/env python3
import urllib.request
import json

API_KEY = "modalresearch_uietVw_rtk2dHzmd09aDiDtipE-dJyYW60d2S1F-4WY"
API_URL = "https://api.us-west-2.modal.direct/v1/chat/completions"
MODEL = "zai-org/GLM-5.1-FP8"

payload = {
    "model": MODEL,
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 10
}

data = json.dumps(payload).encode()
req = urllib.request.Request(
    API_URL,
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
