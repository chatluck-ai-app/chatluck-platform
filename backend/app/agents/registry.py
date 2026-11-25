"""The twelve specialized agents + the master agent.

Agents with real free tools are fully implemented. Agents whose tools need
user-connected accounts (email, calendar) or paid/runner infrastructure are
honest stubs: they report exactly what is missing instead of pretending."""
from __future__ import annotations

import json

from ..execution.search import search_web
from .base import AgentContext, AgentResult, AgentSpec

MASTER_AGENT = "master"


class ResearchAgent(AgentSpec):
    name = "research"
    description = "بحث حي في الويب مع مصادر"
    tools = ["web_search"]

    async def run(self, ctx: AgentContext, instruction: str) -> AgentResult:
        results = await search_web(instruction, limit=5)
        if not results:
            return AgentResult(ok=False, output="لا نتائج بحث متاحة الآن")
        text = "\n".join(f"- {r.title}: {r.url}\n  {r.snippet}" for r in results)
        return AgentResult(
            ok=True,
            output=text,
            data={"sources": [r.__dict__ for r in results]},
        )


class TranslationAgent(AgentSpec):
    name = "translation"
    description = "ترجمة بين العربية وغيرها"
    tools = ["llm_translate"]

    async def run(self, ctx: AgentContext, instruction: str) -> AgentResult:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a translation engine. Translate the user's text. "
                    "Output ONLY the translation, no notes. Preserve meaning, "
                    "tone and direction markers."
                ),
            },
            {"role": "user", "content": instruction},
        ]
        text, provider, model = await ctx.router.complete(messages)
        return AgentResult(ok=True, output=text, data={"provider": provider})


class ImageAgent(AgentSpec):
    name = "image"
    description = "توليد وفهم الصور"
    tools = ["image_generate"]

    async def run(self, ctx: AgentContext, instruction: str) -> AgentResult:
        url, provider, model = await ctx.router.generate_image(instruction)
        return AgentResult(
            ok=True, output=url, data={"provider": provider, "model": model}
        )


class DataAnalysisAgent(AgentSpec):
    name = "data_analysis"
    description = "تحليل جداول ونصوص رقمية"
    tools = ["stats"]

    async def run(self, ctx: AgentContext, instruction: str) -> AgentResult:
        import re
        import statistics

        numbers = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", instruction)]
        if len(numbers) < 2:
            return AgentResult(
                ok=False,
                output="لم أجد أرقامًا كافية في المدخل لتحليلها (وجدت "
                f"{len(numbers)}).",
            )
        stats = {
            "count": len(numbers),
            "sum": sum(numbers),
            "mean": statistics.fmean(numbers),
            "median": statistics.median(numbers),
            "min": min(numbers),
            "max": max(numbers),
        }
        output = (
            f"عدد القيم: {stats['count']} — المجموع: {stats['sum']:.4g} — "
            f"المتوسط: {stats['mean']:.4g} — الوسيط: {stats['median']:.4g} — "
            f"أصغر: {stats['min']:.4g} — أكبر: {stats['max']:.4g}"
        )
        return AgentResult(ok=True, output=output, data=stats)


class CodingAgent(AgentSpec):
    name = "coding"
    description = "كتابة ومراجعة الأكواد"
    tools = ["llm_code"]

    async def run(self, ctx: AgentContext, instruction: str) -> AgentResult:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are the Chat Luck AI coding agent. Write correct, "
                    "runnable code with a brief Arabic explanation. Never claim "
                    "code is tested unless a test run is shown."
                ),
            },
            {"role": "user", "content": instruction},
        ]
        text, provider, model = await ctx.router.complete(messages)
        return AgentResult(ok=True, output=text, data={"provider": provider})


class TaskManagementAgent(AgentSpec):
    name = "task_management"
    description = "تقسيم المهام ومتابعتها"
    tools = ["plan_steps"]

    async def run(self, ctx: AgentContext, instruction: str) -> AgentResult:
        messages = [
            {
                "role": "system",
                "content": (
                    "قسّم مهمة المستخدم إلى خطوات مرقمة قابلة للتنفيذ، كل خطوة "
                    "في سطر واحد تبدأ بفعل. بدون مقدمات."
                ),
            },
            {"role": "user", "content": instruction},
        ]
        text, provider, _ = await ctx.router.complete(messages)
        return AgentResult(ok=True, output=text)


class _StubAgent(AgentSpec):
    """Honest placeholder: declares what it needs, refuses to pretend."""

    missing: str = ""

    async def run(self, ctx: AgentContext, instruction: str) -> AgentResult:
        return AgentResult(
            ok=False,
            output=f"هذا الوكيل ({self.description}) يحتاج: {self.missing}. "
            "لم يُنفَّذ شيء.",
            data={"missing": self.missing},
        )


class AndroidAgent(_StubAgent):
    name = "android_dev"
    description = "وكيل تطوير أندرويد"
    tools = ["project_write", "gradle_build"]
    missing = "بيئة بناء أندرويد (SDK/Gradle) — تُجهز في المرحلة 9"


class WebDevAgent(_StubAgent):
    name = "web_dev"
    description = "وكيل تطوير الويب"
    tools = ["project_write", "npm_build"]
    missing = "صندوق بناء معزول — يُجهز في المرحلة 9"


class BackendAgent(_StubAgent):
    name = "backend_dev"
    description = "وكيل الباك إند"
    tools = ["project_write", "run_tests"]
    missing = "صندوق بناء معزول — يُجهز في المرحلة 9"


class FileAgent(_StubAgent):
    name = "file"
    description = "وكيل الملفات"
    tools = ["file_read", "file_write", "zip"]
    missing = "تخزين الكائنات للملفات — يُجهز مع الرفع الكامل"


class AudioAgent(_StubAgent):
    name = "audio"
    description = "وكيل الصوت"
    tools = ["stt", "tts"]
    missing = "مزود صوت مجاني مُتحقق منه (Groq Whisper يحتاج مفتاح Groq)"


class BrowserAgent(_StubAgent):
    name = "browser"
    description = "وكيل المتصفح"
    tools = ["browse"]
    missing = "جلسة متصفح معزولة داخل الخادم"


class EmailCalendarAgent(_StubAgent):
    name = "email_calendar"
    description = "وكيل البريد والتقويم"
    tools = ["email", "calendar"]
    sensitive = True
    missing = "ربط حساب المستخدم (OAuth) — إجراء حساس يتطلب موافقة صريحة"


def build_registry() -> dict[str, AgentSpec]:
    agents: list[AgentSpec] = [
        ResearchAgent(),
        CodingAgent(),
        AndroidAgent(),
        WebDevAgent(),
        BackendAgent(),
        FileAgent(),
        ImageAgent(),
        AudioAgent(),
        TranslationAgent(),
        BrowserAgent(),
        DataAnalysisAgent(),
        TaskManagementAgent(),
        EmailCalendarAgent(),
    ]
    return {a.name: a for a in agents}


PLANNER_PROMPT = """You are the master agent of Chat Luck AI. Break the user's task into
steps for the specialized agents. Available agents:
{agents}

Return ONLY a JSON array of steps, each: {{"agent": "<name>", "instruction": "<what this agent should do>"}}.
Use as few steps as the task needs. If the task is a plain question, return
an empty array [] and answer directly instead."""


async def master_plan(ctx: AgentContext, task: str, registry) -> list[dict[str, str]]:
    agents_desc = "\n".join(
        f"- {a.name}: {a.description} (tools: {', '.join(a.tools)})"
        for a in registry.values()
    )
    messages = [
        {"role": "system", "content": PLANNER_PROMPT.format(agents=agents_desc)},
        {"role": "user", "content": task},
    ]
    text, _, _ = await ctx.router.complete(messages)
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        plan = json.loads(text[start : end + 1])
    except ValueError:
        return []
    return [
        {"agent": str(s.get("agent", "")), "instruction": str(s.get("instruction", ""))}
        for s in plan
        if isinstance(s, dict) and s.get("agent") in registry and s.get("instruction")
    ]
