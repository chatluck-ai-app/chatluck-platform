"""Master-agent endpoint: plan with the LLM, dispatch to specialized agents,
log every step. Sensitive agents never run without approved=true."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .agents.base import AgentContext
from .agents.registry import build_registry, master_plan
from .deps import audit, get_current_user, get_router, get_session
from .models import AgentRun, AgentStep, User

router = APIRouter(prefix="/v1/agent", tags=["agents"])


class AgentRunRequest(BaseModel):
    task: str = Field(min_length=2, max_length=8000)
    approved: bool = False  # explicit user approval for sensitive actions


class StepOut(BaseModel):
    agent: str
    instruction: str
    status: str
    output: str


class AgentRunResponse(BaseModel):
    run_id: str
    status: str
    steps: list[StepOut]
    blocked: list[str]  # sensitive agents skipped for lack of approval


@router.post("/run", response_model=AgentRunResponse)
async def run_agent(
    body: AgentRunRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    registry = build_registry()
    ctx = AgentContext(
        user_id=user.id, router=get_router(request), approved=body.approved
    )
    run = AgentRun(user_id=user.id, task=body.task, status="running")
    session.add(run)
    await session.flush()

    plan = await master_plan(ctx, body.task, registry)
    blocked: list[str] = []
    steps_out: list[StepOut] = []

    for step in plan:
        agent = registry[step["agent"]]
        record = AgentStep(
            run_id=run.id, agent=agent.name, instruction=step["instruction"]
        )
        if agent.sensitive and not ctx.approved:
            record.status = "blocked"
            record.output = "يتطلب موافقة المستخدم الصريحة"
            blocked.append(agent.name)
            session.add(record)
            steps_out.append(
                StepOut(
                    agent=agent.name,
                    instruction=step["instruction"],
                    status="blocked",
                    output=record.output,
                )
            )
            continue
        try:
            result = await agent.run(ctx, step["instruction"])
            record.status = "done" if result.ok else "failed"
            record.output = result.output[:8000]
        except Exception as exc:  # agent crashed: record honestly, continue
            record.status = "failed"
            record.output = f"{type(exc).__name__}: {str(exc)[:500]}"
        session.add(record)
        steps_out.append(
            StepOut(
                agent=agent.name,
                instruction=step["instruction"],
                status=record.status,
                output=record.output,
            )
        )

    run.status = "done" if not blocked else "done_with_blocked"
    await audit(session, user.id, "agent.run", f"steps={len(plan)} blocked={len(blocked)}")
    await session.commit()
    return AgentRunResponse(
        run_id=run.id, status=run.status, steps=steps_out, blocked=blocked
    )


@router.get("/catalog")
async def agent_catalog(user: User = Depends(get_current_user)):
    registry = build_registry()
    return [
        {
            "name": a.name,
            "description": a.description,
            "tools": a.tools,
            "sensitive": a.sensitive,
        }
        for a in registry.values()
    ]
