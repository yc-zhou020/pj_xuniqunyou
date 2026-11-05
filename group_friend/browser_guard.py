"""Detect verification UI without clicking, dismissing, or solving challenges."""
from .browser_errors import ManualActionRequired


RISK_UI = (
    '[class*="captcha_verify_container"]', '[id*="captcha-verify"]',
    'iframe[src*="verify.snssdk.com"]', 'iframe[src*="captcha"]',
    'text=请完成下列验证后继续',
)


async def require_no_verification(page):
    for selector in RISK_UI:
        candidates = page.locator(selector)
        for index in range(await candidates.count()):
            candidate = candidates.nth(index)
            if not await candidate.is_visible():
                continue
            # An ordinary chat message quoting the warning is not a dialog.
            in_chat = await candidate.evaluate('''el=>!!el.closest(
                '[class*="messageMessageListlist"],[class*="messageMessageBoxmessageBox"],[data-e2e="msg-item-content"]')''')
            if not in_chat:
                raise ManualActionRequired('risk_verification_required')
