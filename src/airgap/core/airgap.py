"""The airgapped agent: orchestrator -> validated plan -> interpreter -> policy -> tools."""

from __future__ import annotations

import time

from airgap.core.audit import AuditLog
from airgap.core.fetcher import Fetcher
from airgap.core.interpreter import Interpreter
from airgap.core.orchestrator import Orchestrator, PlanRejected
from airgap.core.policy import Confirmer, PermissivePolicy, PolicyEngine, UserContext, deny_all
from airgap.core.reader import Reader
from airgap.core.result import RunResult
from airgap.models.base import ChatModel
from airgap.world.ledger import Ledger
from airgap.world.mock_web import MockWeb
from airgap.world.tools import WorldTools


class AirgapAgent:
    def __init__(
        self,
        orchestrator_model: ChatModel,
        reader_model: ChatModel,
        web: MockWeb,
        confirmer: Confirmer = deny_all,
        sanitize_input: bool = True,
        enforce_policy: bool = True,
    ) -> None:
        # The two switches exist only for ablations in the evaluation.
        self.sanitize_input = sanitize_input
        self.enforce_policy = enforce_policy
        self.orchestrator_model = orchestrator_model
        self.reader_model = reader_model
        self.web = web
        self.confirmer = confirmer

    def run(self, user_text: str, ctx: UserContext, audit: AuditLog | None = None) -> RunResult:
        audit = audit or AuditLog()
        ledger = Ledger()
        client = self.web.client()
        orchestrator = Orchestrator(self.orchestrator_model)
        reader = Reader(self.reader_model)
        policy = (PolicyEngine if self.enforce_policy else PermissivePolicy)(
            ctx, audit, self.confirmer
        )
        start = time.perf_counter()
        blocked: list[str] = []
        final, error = "", None
        try:
            plan = orchestrator.plan(user_text, ctx)
            audit.emit("plan", steps=[s.model_dump() for s in plan.steps])
            interp = Interpreter(
                ctx,
                Fetcher(client),
                reader,
                WorldTools(client, ledger),
                policy,
                audit,
                sanitize_input=self.sanitize_input,
            )
            outcome = interp.run(plan)
            final, blocked, error = outcome.answer or "", outcome.blocked, outcome.error
        except PlanRejected as exc:
            error = f"PlanRejected: {exc}"
            audit.emit("error", error=error)
        finally:
            client.close()
        return RunResult(
            final_text=final,
            ledger=ledger,
            audit=audit,
            blocked=blocked,
            confirmations=policy.confirmations,
            prompt_tokens=orchestrator.prompt_tokens + reader.prompt_tokens,
            completion_tokens=orchestrator.completion_tokens + reader.completion_tokens,
            latency_s=time.perf_counter() - start,
            error=error,
        )
