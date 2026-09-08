"""Fresh-escalation prompt construction for the audit hook.

FRESH ESCALATION != CHAIN HANDOFF. Finding from the 2026-09-05 ecosystem
sweep (InflationAgent): handing a stronger model the failed reasoning
chain of a weaker one can make its answer WORSE, not better -- the
authors observed substantial accuracy drops and proposed sending the
strong model a clean task instead. This project adopts that as a design
rule: escalate evidence, not reasoning.

Concretely, build_audit_messages() never includes anything about HOW the
local model arrived at its answer (no chain-of-thought, no retry
history, no prior assistant turns). It includes only:
    - the original user request, verbatim
    - the answer the local model actually produced
    - the objective, mechanical reason it is being doubted (the
      Verifier's own detail string -- never a vague "seems wrong")
    - real tool evidence (name, arguments, result, ok/failed), if any

This is a pure prompt-construction function: no I/O, no provider call.
The caller (worker/engine.py's audit hook) is responsible for actually
invoking a provider with the result.
"""

from __future__ import annotations

from meizex_mrw.providers.base import ChatMessage
from meizex_mrw.verifier import ExecutedTool

AUDITOR_SYSTEM_INSTRUCTION = (
    "Você é um auditor independente. Sua tarefa é revisar UMA resposta produzida "
    "por outro sistema, usando apenas a pergunta original e as evidências "
    "verificáveis fornecidas abaixo -- você não tem acesso ao raciocínio do outro "
    "sistema, e não deve tentar adivinhá-lo. Se a resposta produzida estiver "
    "correta e bem suportada pelas evidências, confirme-a. Se estiver incorreta, "
    "forneça a resposta correta. Se as evidências não forem suficientes para "
    "determinar a resposta correta, diga isso explicitamente em vez de adivinhar. "
    "Responda de forma direta e concisa, em português."
)


def _format_tool_evidence(executed_tools: list[ExecutedTool]) -> str:
    if not executed_tools:
        return "(nenhuma ferramenta foi executada nesta interação)"
    lines = []
    for tool in executed_tools:
        status = "ok" if tool.ok else "falhou"
        lines.append(f"- {tool.name}({tool.arguments}) -> {tool.result!r} [{status}]")
    return "\n".join(lines)


def build_audit_messages(
    *,
    original_request: str,
    produced_answer: str,
    doubt_reason: str,
    executed_tools: list[ExecutedTool] | None = None,
) -> list[ChatMessage]:
    """Build the (system, user) message pair for an audit call. Pure
    function: no network, no provider dependency, no side effects."""
    evidence_block = _format_tool_evidence(executed_tools or [])
    user_content = (
        f"Pergunta original:\n{original_request}\n\n"
        f"Resposta produzida pelo sistema local:\n{produced_answer or '(resposta vazia)'}\n\n"
        f"Motivo objetivo da dúvida:\n{doubt_reason}\n\n"
        f"Evidências de ferramentas executadas:\n{evidence_block}\n\n"
        "Analise apenas com base no que está acima e responda com a resposta correta."
    )
    return [
        ChatMessage(role="system", content=AUDITOR_SYSTEM_INSTRUCTION),
        ChatMessage(role="user", content=user_content),
    ]
