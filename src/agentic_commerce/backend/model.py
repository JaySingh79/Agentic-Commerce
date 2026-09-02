import os

from dotenv import load_dotenv

from agentic_commerce.backend.tools import ALL_COMMERCE_TOOLS

load_dotenv()
model_name = os.getenv("MODEL", "gemini-2.5-flash")
tools = ALL_COMMERCE_TOOLS