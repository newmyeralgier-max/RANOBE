import urllib.request
import json

FIREWORKS_API_KEY = "fw_5CAe1FD9DRaBZpWDDTZzhN"
API_URL = "https://api.fireworks.ai/inference/v1/chat/completions"

payload = {
    "model": "accounts/fireworks/models/llama-v3p3-70b-instruct",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 10
}

data = json.dumps(payload).encode()
req = urllib.request.Request(
    API_URL,
    data=data,
    headers={
        "Authorization": f"Bearer {FIREWORKS_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
)

try:
    with urllib.request.urlopen(req) as resp:
        print(resp.read().decode())
except Exception as e:
    print(f"Error: {e}")
