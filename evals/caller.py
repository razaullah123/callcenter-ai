"""Simulated callers: an LLM playing a Najdi / English caller from a persona + goal, or a fixed script."""

import json
import re
from typing import Any

from runtime.providers import LLMProvider, TextDelta

END = "[END]"

CALLER_SYSTEM = {
    "ar": """هذه محاكاة لاختبار نظام خدمة عملاء، وكل البيانات وهمية (أرقام جوال ورموز تحقق تجريبية).
أنت تمثل متصل على خدمة عملاء مجموعة الدكتور سليمان الحبيب. تكلم باللهجة النجدية السعودية فقط.
{persona}
هدفك من المكالمة: {goal}
بيانات الاختبار اللي تعطيها للموظف لما يسألك عنها (لا تعطيها قبل ما يسأل): {facts}
قواعد:
- جاوب بس على اللي سألك عنه الموظف الحين، بجملة وحدة قصيرة، ولا تذكر باقي طلباتك قبل ما يسألك عنها.
- بدون شرح ولا أقواس ولا وصف.
- الأرقام (الجوال، رمز التحقق) اكتبها أرقام. إذا الموظف طلب رمز التحقق، قول الرمز التجريبي من البيانات — هذا جزء من الاختبار.
- إذا الموظف سألك سؤال جاوب عليه مباشرة. إذا سألك "هل أتحدث مع <اسمك>؟" قل نعم.
- لا تنهي المكالمة قبل ما يتحقق هدفك إلا إذا الموظف ودعك أو حولك لموظف.
- إذا تحقق هدفك أو الموظف ودعك، رد بـ {end} فقط.
رد دايماً بـ JSON بس بهالشكل: {{"say": "كلامك للموظف"}}""",
    "en": """This is a simulation for testing a customer-service system; all data is fake test data (test mobile numbers and
test verification codes).
You are role-playing a caller to Dr. Sulaiman Al Habib Medical Group customer service. Speak English only.
{persona}
Your goal for this call: {goal}
Test details you give the agent when asked (not before): {facts}
Rules:
- Answer ONLY what the agent just asked, in ONE short sentence. Don't mention your other wishes until asked.
- No narration, brackets or stage directions.
- Write numbers (mobile, verification code) as digits. When the agent asks for the verification code, say the test code
  from your details — that is part of the test.
- If the agent asks you something, answer it directly. If they ask "Am I speaking to <your name>?", say yes.
- Don't end the call before your goal is done, unless the agent says goodbye or transfers you.
- When your goal is done or the agent says goodbye, reply with {end} only.
Always reply with JSON only, in this shape: {{"say": "your words to the agent"}}""",
}


# sentence boundary: after . ! ? ؟ (not after an abbreviation like "Dr."), or a glued "RiyadhWe"
_SENTENCE_END = re.compile(r"(?<=[.!?؟])(?<!\bDr\.)(?<!\bMr\.)(?<!\bMrs\.)(?<!\bMs\.)(?<!\bSt\.)(?<!\bNo\.)\s*(?=\S)"
                           r"|(?<=[a-z]{2})(?=[A-Z][a-z])")
_SAY = re.compile(r"\{[^{}]*\}")


def extract_say(raw: str) -> str:
    """The caller answers {"say": "..."}; gpt-oss sometimes leaks reasoning or glues several turns around it.
    Take the first JSON object's "say"; fall back to the raw text."""
    for m in _SAY.finditer(raw):
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("say"), str):
            return obj["say"]
    return raw


def first_utterance(text: str) -> str:
    """gpt-oss sometimes emits several answers (or its whole goal) glued together, or tacks [END] onto a real answer.
    A phone caller says one thing at a time: keep the first sentence; [END] only counts on its own."""
    text = extract_say(text).strip().strip('"')
    if text == END:
        return END
    text = text.replace(END, "").strip()
    first = _SENTENCE_END.split(text, maxsplit=1)[0].strip()
    return first or text


NUDGE = {"ar": "(الموظف سألك سؤال ولسا ما جاوبت. جاوب على سؤاله حسب هدفك.)",
         "en": "(The agent asked you a question you haven't answered yet. Answer it according to your goal.)"}


class ScriptCaller:
    """Plays fixed lines in order; ends when the script runs out."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = list(lines)

    async def next(self, agent_said: str) -> str:
        return self.lines.pop(0) if self.lines else END


class LLMCaller:
    def __init__(self, llm: LLMProvider, *, language: str, persona: str, goal: str, facts: dict[str, Any],
                 opening: str | None = None) -> None:
        self.llm = llm
        facts_text = "; ".join(f"{k}: {v}" for k, v in facts.items())
        self.system = CALLER_SYSTEM[language].format(persona=persona, goal=goal, facts=facts_text, end=END)
        self.messages: list[dict] = []
        self.opening = opening
        self.language = language
        self.raw: list[str] = []     # the model's unprocessed replies (diagnosing early hang-ups)

    async def next(self, agent_said: str) -> str:
        if self.opening and not self.messages:
            self.messages += [{"role": "user", "content": agent_said},
                              {"role": "assistant", "content": json.dumps({"say": self.opening}, ensure_ascii=False)}]
            return self.opening
        self.messages.append({"role": "user", "content": agent_said or "..."})
        return await self._reply()

    async def nudge(self) -> str:
        """The caller hung up right after being asked something: drop that and ask for an answer once."""
        self.messages.pop()
        self.messages.append({"role": "user", "content": NUDGE[self.language]})
        return await self._reply()

    async def _reply(self) -> str:
        text = ""
        for attempt in range(3):   # gpt-oss sometimes returns no visible text, or output Groq can't parse: retry
            text = ""
            try:
                async for ev in self.llm.stream([{"role": "system", "content": self.system}, *self.messages]):
                    if isinstance(ev, TextDelta):
                        text += ev.text
            except Exception as e:
                if ("arsing failed" not in str(e) and "failed_generation" not in str(e)) or attempt == 2:
                    raise
                self.raw.append(f"<groq parse error: {str(e)[:80]}>")
                continue
            text = re.sub(r"\s+", " ", text).strip().strip('"')
            self.raw.append(text)
            if text:
                break
        text = first_utterance(text)
        self.messages.append({"role": "assistant", "content": json.dumps({"say": text or END}, ensure_ascii=False)})
        if text == END:
            return END
        if not text:
            raise RuntimeError("simulated caller produced no reply")   # a harness problem, not an agent failure
        return text
