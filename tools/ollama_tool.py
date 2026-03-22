"""
OllamaTool — direct Ollama API call as a CrewAI tool

Used when an agent needs to make a targeted local LLM call
as part of its task execution, rather than using its own LLM.

Example use case: an agent that needs to summarise a file
using a different model than its primary LLM.

Most agents won't need this — their primary LLM handles reasoning.
This exists for cases where a specific local model call is needed
as a tool action within a task.
"""

import os
import requests
from crewai.tools import BaseTool


class OllamaTool(BaseTool):
    name: str = "ollama_generate"
    description: str = """Make a direct call to a local Ollama model.
    Pass model (string) and prompt (string).
    Returns the model's response as a string.
    Use when you need a targeted local LLM call as part of your task.
    Example: ollama_generate(model='mistral-nemo', prompt='summarise this...')"""

    def _run(self, model: str, prompt: str) -> str:
        host = os.getenv("OLLAMA_HOST")
        url  = f"{host}/api/generate"

        try:
            resp = requests.post(
                url,
                json={
                    "model":  model,
                    "prompt": prompt,
                    "stream": False,
                },
                timeout=120
            )
            resp.raise_for_status()
            return resp.json().get("response", "").strip()

        except requests.exceptions.ConnectionError:
            return f"Error: cannot connect to Ollama at {host}. Check OLLAMA_HOST in .env"
        except requests.exceptions.Timeout:
            return f"Error: Ollama timed out after 120s"
        except Exception as e:
            return f"Error: {str(e)}"