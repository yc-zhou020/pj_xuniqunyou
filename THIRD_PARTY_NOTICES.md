# Reference browser code

`group_friend/browser_reference/douyin_chat.py`, `page_selectors.py` and
`message_reader.js` are adapted from the user-provided
`example/douyin-bot/douyin-friend-ai-bot/src/` snapshot (l0yce/douyin-bot).
The upstream license is GNU AGPL version 3; its full text is preserved in
`group_friend/browser_reference/LICENSE`. Upstream copyright notices and code
comments are retained. Local change: package-relative selector import.

The browser runner's scheduling, durable delivery queue, snapshot model and
single-action sender are implemented separately. The upstream sender's fallback
send/retry behavior is not used by this project's delivery runner.
