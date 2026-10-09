import os
from google import genai
from core import config

client = genai.Client(api_key=config.settings.gemini_api_key)
models = client.models.list()
for m in models:
    if "flash" in m.name:
        print(m.name)
