import json
import ssl
import socket
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

try:
    import certifi
    ssl_context = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    ssl_context = ssl.create_default_context()

# Timeouts
CONNECT_TIMEOUT = 10  # seconds to establish connection
READ_TIMEOUT = 120    # seconds to wait for LLM response (can be slow)
MODELS_TIMEOUT = 15   # seconds for fetching model list


class OpenRouterClient:
    """Direct OpenRouter client for BYOK (bring-your-own-key) mode."""

    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, api_key):
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _request(self, method, endpoint, data=None, timeout=READ_TIMEOUT):
        req = Request(
            f"{self.BASE_URL}/{endpoint}",
            data=json.dumps(data).encode("utf-8") if data else None,
            headers=self.headers,
            method=method,
        )
        try:
            with urlopen(req, context=ssl_context, timeout=timeout) as response:
                if response.status == 204:
                    return None
                body = response.read().decode("utf-8")
                if not body:
                    return {} if 200 <= response.status < 300 else None
                return json.loads(body)
        except socket.timeout:
            raise Exception(
                "Request timed out. The AI model is taking too long to respond. "
                "Try again or switch to a faster model."
            )
        except HTTPError as e:
            try:
                error_body = e.read().decode("utf-8")
                error_json = json.loads(error_body)
                msg = error_json.get("error", {}).get("message", "")
                if not msg:
                    msg = error_json.get("error", str(e))
            except Exception:
                msg = f"HTTP Error {e.code}"

            if e.code == 401:
                raise Exception("Invalid OpenRouter API key. Check your key in RevAI Config.")
            elif e.code == 402:
                raise Exception("OpenRouter account has insufficient credits.")
            elif e.code == 429:
                raise Exception("Rate limited by OpenRouter. Wait a moment and try again.")
            elif e.code >= 500:
                raise Exception(f"OpenRouter server error ({e.code}). Try again later.")
            else:
                raise Exception(f"OpenRouter API Error: {msg}") from e
        except URLError as e:
            if "timed out" in str(e.reason).lower():
                raise Exception(
                    "Connection timed out. Check your internet connection."
                )
            raise Exception(f"Network error: {e.reason}") from e

    def get_models(self):
        try:
            response = self._request("GET", "models", timeout=MODELS_TIMEOUT)
            return response.get("data", []) if response else []
        except Exception:
            return []

    def generate(self, model_name, prompt_text):
        if not model_name:
            raise ValueError("No model selected. Go to RevAI Config > Model Config.")
        if not prompt_text:
            raise ValueError("Prompt is empty. Check your action's prompt template.")

        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt_text}],
        }
        response = self._request("POST", "chat/completions", data=payload)
        if response and response.get("choices"):
            content = response["choices"][0].get("message", {}).get("content")
            if content is not None:
                return content.strip()
        raise Exception(
            "AI model returned an empty response. Try again or switch to a different model."
        )
