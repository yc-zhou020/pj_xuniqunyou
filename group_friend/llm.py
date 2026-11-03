import json
import time
import urllib.error
import urllib.request

from .config import api_settings


class ModelError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DeepSeek:
    def __init__(self, config):
        self.config = config
        self.key, self.base, self.model = api_settings()
        self.opener = urllib.request.build_opener(NoRedirect)

    def reply(self, persona, messages):
        payload = json.dumps({
            "model": self.model,
            "messages": [{"role": "system", "content": persona}, *messages],
            "stream": False,
            "max_tokens": 512,
            "thinking": {"type": "disabled"},
        }).encode()
        for attempt in range(self.config.api_retries + 1):
            request = urllib.request.Request(
                self.base + "/chat/completions", data=payload,
                headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"},
            )
            try:
                with self.opener.open(request, timeout=self.config.timeout_seconds) as response:
                    result = json.loads(response.read(2_000_000))
                content = result["choices"][0]["message"]["content"]
                if not isinstance(content, str) or not content.strip():
                    raise ModelError("empty_model_response")
                return content.strip()
            except urllib.error.HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt == self.config.api_retries:
                    raise ModelError(f"api_http_{exc.code}") from None
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt == self.config.api_retries:
                    raise ModelError("api_connection_failed") from None
            except (ValueError, KeyError, IndexError, TypeError):
                raise ModelError("invalid_model_response") from None
            time.sleep(min(2 ** attempt, 4))


class MockModel:
    def reply(self, persona, messages):
        return "[AI] 离线测试：我是小寻，已收到这条测试消息。"
