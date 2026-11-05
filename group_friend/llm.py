import base64
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
        return self._complete({
            "model": self.model,
            "messages": [{"role": "system", "content": persona}, *messages],
            "stream": False,
            "max_tokens": 512,
            "thinking": {"type": "disabled"},
        })

    def vision(self, instruction, images, text='', *, model='deepseek-flash'):
        """Native image input, per https://api-docs.deepseek.com/guides/vision/."""
        if model != 'deepseek-flash':
            raise ModelError('vision_requires_deepseek_flash')
        if not images or len(images) > 20:
            raise ModelError('vision_image_count_out_of_range')
        content = [{'type': 'text', 'text': text}]
        for data in images:
            if not isinstance(data, bytes) or len(data) > 32 * 1024 * 1024:
                raise ModelError('vision_image_too_large')
            if not data.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ModelError('vision_requires_png_screenshots')
            content.append({'type': 'image_url', 'image_url': {
                'url': 'data:image/png;base64,' + base64.b64encode(data).decode('ascii'), 'detail': 'original'}})
        return self._complete(dict(model=model, messages=[{'role': 'system', 'content': instruction},
                              {'role': 'user', 'content': content}], stream=False, max_tokens=8192,
                              thinking={'type': 'disabled'}, response_format={'type': 'json_object'}))

    def _complete(self, data):
        payload = json.dumps(data).encode()
        if len(payload) > 48 * 1024 * 1024:
            raise ModelError('request_body_too_large')
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
        return "离线测试：我是陈，已收到这条测试消息。"
