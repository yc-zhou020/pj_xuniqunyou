import io
import json
import os
import unittest
import urllib.error
from unittest.mock import patch

from group_friend.config import Config
from group_friend.llm import DeepSeek, ModelError


class LLMTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret", "DEEPSEEK_MODEL": "test-model",
                                     "DEEPSEEK_BASE_URL": "https://api.deepseek.com"})
        env.start()
        self.addCleanup(env.stop)
        self.model = DeepSeek(Config("group", "bot", api_retries=1))

    def test_request_roles_and_no_tools(self):
        response = io.BytesIO(json.dumps({"choices": [{"message": {"content": "hello"}}]}).encode())
        with patch.object(self.model.opener, "open", return_value=response) as request:
            self.assertEqual(self.model.reply("rules", [{"role": "user", "content": "untrusted"}]), "hello")
        body = json.loads(request.call_args.args[0].data)
        self.assertEqual(body["messages"][0], {"role": "system", "content": "rules"})
        self.assertEqual(body["messages"][1]["role"], "user")
        self.assertNotIn("tools", body)
        self.assertFalse(body["stream"])

    def test_timeout_retry_is_bounded(self):
        with patch.object(self.model.opener, "open", side_effect=TimeoutError()) as request:
            with patch("group_friend.llm.time.sleep"):
                with self.assertRaisesRegex(ModelError, "api_connection_failed"):
                    self.model.reply("rules", [])
            self.assertEqual(request.call_count, 2)

    def test_401_no_retry_and_response_body_not_exposed(self):
        error = urllib.error.HTTPError("https://api.deepseek.com", 401, "test-secret", {}, None)
        with patch.object(self.model.opener, "open", side_effect=error) as request:
            with self.assertRaises(ModelError) as caught:
                self.model.reply("rules", [])
            self.assertEqual(str(caught.exception), "api_http_401")
            self.assertEqual(request.call_count, 1)

    def test_rate_limit_retry_is_bounded(self):
        error = urllib.error.HTTPError("https://api.deepseek.com", 429, "limited", {}, None)
        with patch.object(self.model.opener, "open", side_effect=error) as request:
            with patch("group_friend.llm.time.sleep"):
                with self.assertRaisesRegex(ModelError, "api_http_429"):
                    self.model.reply("rules", [])
            self.assertEqual(request.call_count, 2)


if __name__ == "__main__":
    unittest.main()
