"""Small keyword matcher and probability gate for recalling off-page memories."""
from dataclasses import dataclass
from difflib import SequenceMatcher
import random
import re
import unicodedata


FILLER = re.compile('谢谢|你好|什么|怎么|这个|那个|可以|就是|觉得|记得|知道|自己|现在|今天|之前|一下|时候|我们|你们|他们|已经|还是|你的|我的|喜欢|我是|我|你|他|她|的|了|吗|呢')
ENGLISH_STOP = {'the', 'and', 'this', 'that', 'what', 'when', 'where', 'with', 'have', 'you', 'your',
                'remember', 'please', 'ai', 'is', 'are', 'was', 'were', 'for', 'from', 'not', 'but',
                'can', 'could', 'would', 'should', 'will', 'has', 'had', 'our', 'their', 'they',
                'them', 'then', 'there', 'here', 'just', 'like', 'about'}


def keywords(text):
    normalized = unicodedata.normalize('NFKC', text[:4096]).lower()
    result = set(re.findall(r'[a-z0-9_]{2,32}', normalized)) - ENGLISH_STOP
    for chunk in re.findall(r'[\u3400-\u9fff]+', FILLER.sub(' ', normalized)):
        if 2 <= len(chunk) <= 12:
            result.add(chunk)
        for width in (2, 3):
            result.update(chunk[i:i + width] for i in range(len(chunk) - width + 1))
    return tuple(sorted(result, key=lambda word: (-len(word), word))[:64])


def fuzzy_score(query, text):
    candidates = keywords(text)
    if set(query).intersection(candidates):
        return 1.0
    best = 0.0
    for left in query:
        for right in candidates:
            # Two-character terms require equality; a single shared Chinese
            # character is too weak to count as a relevant keyword.
            if min(len(left), len(right)) < 3 or not set(left).intersection(right):
                continue
            matcher = SequenceMatcher(None, left, right, autojunk=False)
            if matcher.quick_ratio() > best:
                best = max(best, matcher.ratio())
    return best


@dataclass(frozen=True)
class RecallDecision:
    context_messages: int
    matched_items: int
    best_score: float
    probability: float
    draw: float | None
    included: bool
    reason: str


class MemoryRecallPolicy:
    def __init__(self, config, *, rng=None):
        self.config = config
        self.rng = rng if rng is not None else random.random

    def select(self, snapshot, records, profile):
        latest = max(snapshot.selected)
        recent = [message.text for message in snapshot.messages[:latest + 1]
                  if message.kind == 'text' and message.direction in ('incoming', 'outgoing')
                  and message.text.strip()][-self.config.memory_recall_context_messages:]
        query = tuple(dict.fromkeys(word for text in reversed(recent) for word in keywords(text)[:16]))[:128]
        threshold = self.config.memory_keyword_similarity
        ranked = [(fuzzy_score(query, record['text']), record) for record in records] if query else []
        ranked = [(score, record) for score, record in ranked if score >= threshold]
        ranked.sort(key=lambda item: (item[0], item[1]['recorded_at']), reverse=True)
        # Ignore generated Markdown headings when matching editable notes.
        notes = '\n'.join(line for line in profile.splitlines() if not line.lstrip().startswith('#'))
        profile_score = fuzzy_score(query, notes) if query and notes.strip() else 0.0
        profile_match = profile_score >= threshold
        matched = len(ranked) + int(profile_match)
        best = max([score for score, _ in ranked] + [profile_score])
        draw = self.rng() if matched else None
        if draw is not None and (type(draw) not in (int, float) or not 0 <= draw < 1):
            raise ValueError('recall_random_draw_out_of_range')
        included = matched > 0 and draw < self.config.memory_recall_probability
        decision = RecallDecision(len(recent), matched, round(best, 6), self.config.memory_recall_probability,
                                  draw, included, 'included' if included else 'random_rejected' if matched else 'no_match')
        return [record for _, record in ranked[:self.config.memory_context_items]], decision
