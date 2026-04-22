#!/usr/bin/env python3
import urllib.request
import json
import os

API_KEY = "fw_5CAe1FD9DRaBZpWDDTZzhN"
URL = "https://api.fireworks.ai/inference/v1/accounts/fireworks/models"

req = urllib.request.Request(
    URL,
    headers={"Authorization": f"Bearer {API_KEY}"}
)

try:
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
        print(json.dumps(data, indent=2))
except Exception as e:
    print(f"Error: {e}")
    if hasattr(e, 'read'):
        print(e.read().decode())
