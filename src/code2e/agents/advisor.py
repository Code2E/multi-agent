"""Advisor — Plan Unit 별 코드 리뷰, approve / revise 결정 (v4 §3.4, §6.1, §8.2).

DECISION: Q11 — revise + 빈 코멘트는 Orchestrator 가 force-stop 처리
(termination.decide_force_stop_on_empty_revise).
DECISION: Q12 — signature 는 자체 해시 (signature_fn). is_stagnant 가 정확 일치 비교.

DECISION: Q10 — stateless. instance 는 model + prompts_dir 만 보유.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from code2e.agents.base import InvocationContext
from code2e.agents.planner import parse_prompt_file, render_prompt
from code2e.core.schemas import AdvisorFeedback, AdvisorInput, FeedbackComment
from code2e.core.termination import signature_fn

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
PROMPT_FILE = "advisor.md"

# v4 §3.4 표.
TIMEOUT_S = 60
RETRIES = 3
TEMPERATURE = 0.3


class AdvisorLlmOutput(BaseModel):
    """Advisor 의 LLM 직접 출력. AdvisorFeedback.unit_id / signature 는 invoke 가 채움."""

    decision: Literal["approve", "revise"]
    severity: Literal["low", "med", "high"] = "low"
    comments: list[FeedbackComment] = Field(default_factory=list)


class AdvisorAgent:
    name: ClassVar[str] = "advisor"
    version: ClassVar[str] = "1.0"
    InputModel: ClassVar[type[AdvisorInput]] = AdvisorInput
    OutputModel: ClassVar[type[AdvisorFeedback]] = AdvisorFeedback

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        prompts_dir: Path | None = None,
    ) -> None:
        self.model = model
        self.prompts_dir = prompts_dir or DEFAULT_PROMPTS_DIR

    async def invoke(self, inp: AdvisorInput, ctx: InvocationContext) -> AdvisorFeedback:
        prompt_path = self.prompts_dir / PROMPT_FILE
        system_prompt, user_template = parse_prompt_file(prompt_path)

        user_msg = render_prompt(
            user_template,
            unit=inp.unit.model_dump_json(indent=2),
            code=_dump_files(inp.code),
            prior_feedback=_dump_prior_feedback(inp.prior_feedback),
        )

        prompt_key = f"advisor_v{self.version}"

        result = await ctx.llm.call(
            agent_name=self.name,
            agent_version=self.version,
            prompt_key=prompt_key,
            model=self.model,
            temperature=TEMPERATURE,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
            output_model=AdvisorLlmOutput,
        )
        assert isinstance(result, AdvisorLlmOutput)

        # signature: stagnation 감지용 해시. unit_id + decision + comment messages.
        sig_payload = (
            f"{inp.unit.id}|{result.decision}|"
            + "|".join(c.message for c in result.comments)
        )
        signature = signature_fn(sig_payload)

        return AdvisorFeedback(
            unit_id=inp.unit.id,
            decision=result.decision,
            severity=result.severity,
            comments=result.comments,
            signature=signature,
        )


def _dump_files(files: Sequence[BaseModel]) -> str:
    return json.dumps(
        [f.model_dump(mode="json") for f in files],
        indent=2,
        default=str,
    )


def _dump_prior_feedback(history: Sequence[AdvisorFeedback]) -> str:
    if not history:
        return "(none — 첫 라운드)"
    return json.dumps(
        [f.model_dump(mode="json") for f in history],
        indent=2,
        default=str,
    )
