"""Synthesis — structured output from research findings per §7.4."""

from __future__ import annotations

import json
import time
import inspect
from datetime import datetime, timezone
from typing import Optional

from app.config import ANTHROPIC_API_KEY, RESEARCH_MODEL, COMPOSIO_CALLBACK_URL
from app.schema import (
    DraftGuideContent, Source, Attempt, ComposioInfo, ProviderInfo, Scenario,
)

SYNTHESIS_SYSTEM_PROMPT = """Turn research findings into a structured launch guide. Return JSON matching
the schema.

Evidence rules
- Use only facts in the findings, the source registry, the Composio metadata
  and the attempt records. Add no provider facts from memory.
- Cite sources ONLY by registry ID (S1, C1 …); map each finding's URL to its ID.
  Never invent IDs. Claim IDs are K1, K2 …
- Keep each claim's conditions. Copy the verbatim passage into `excerpt` only
  when its first source has status "read"; otherwise "".
- basis: documented when a registry source states it; estimated for your own
  planning estimate (say so); unknown otherwise; observed ONLY with an attempt ID.
- Preserve contradictions in conflicts and unresolved eligibility questions in
  unknowns, each with a next investigation.

Content rules
- Lead with the next action and the largest known launch dependency. Represent
  each summary statement separately with the claims that support it; one valid
  citation cannot support unrelated statements.
- Paths: own_app_testing for test/dev mode; own_app_production when the audience
  is external customers or the provider gates production. Do NOT write a
  Composio-managed path or Composio wiring steps — the application adds them
  when metadata confirms them. If managed OAuth is "available", say in the
  summary that it is an alternative that can also suit production.
- Gates: mark "required"/"not_required" only with claim IDs; mark critical gates
  you could not establish as "unknown".
- Separate setup effort from waiting. known=false when there is no defensible
  basis. Keep the provider's unit. author_estimate for your own estimates with
  assumptions; provider_estimate only when published; anecdotal for community.
- Owners: you (sign-in, account decisions, terms, payments, verification, videos,
  legal sign-off); agent (drafting, scope mapping, field values, link checks);
  agent_prepares_you_submit; your_customer_admin; provider.
- If OAuth support is unsupported or unknown, return no paths and explain.
- Exact values (full scope strings, settings, URLs) only where findings provide
  them; every step has a success condition. Composio redirect URI, when needed:
  COMPOSIO_CALLBACK
- Plain language; explain jargon once. Human review fields stay unset."""

OWNER_ENUM = ["you", "agent", "agent_prepares_you_submit", "your_customer_admin", "provider"]
UNIT_ENUM = ["minutes", "hours", "business_days", "calendar_days", "not_applicable"]
ESTIMATE_BASIS_ENUM = ["measured", "provider_estimate", "author_estimate", "anecdotal", "unknown"]
CLAIM_BASIS_ENUM = ["documented", "observed", "estimated", "unknown"]
GATE_KIND_ENUM = [
    "app_review",
    "security_assessment",
    "business_or_identity_verification",
    "partner_program",
    "paid_access",
    "cap_before_approval",
    "customer_admin_action",
    "per_customer_registration",
    "recurring_recertification",
    "ai_data_use_terms",
]
GATE_STATE_ENUM = ["required", "not_required", "unknown"]

GUIDE_SCHEMA = {
    "type": "object",
    "properties": {
        "oauth_support": {"type": "string", "enum": ["confirmed", "restricted", "unsupported", "unknown"]},
        "summary": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "claim_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "claim_ids"],
            },
        },
        "next_action": {
            "type": "object",
            "properties": {
                "instruction": {"type": "string"},
                "owner": {"type": "string", "enum": OWNER_ENUM},
                "link_or_artifact": {"type": "string"},
                "success_condition": {"type": "string"},
                "claim_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["instruction", "owner", "link_or_artifact", "success_condition", "claim_ids"],
        },
        "recommended_path_id": {"type": "string", "enum": ["own_app_testing", "own_app_production", ""]},
        "paths": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "enum": ["own_app_testing", "own_app_production"]},
                    "name": {"type": "string"},
                    "applicability": {"type": "string"},
                    "gates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {"type": "string", "enum": GATE_KIND_ENUM},
                                "state": {"type": "string", "enum": GATE_STATE_ENUM},
                                "note": {"type": "string"},
                                "claim_ids": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["kind", "state", "note", "claim_ids"],
                        },
                    },
                    "prerequisites": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "owner": {"type": "string", "enum": OWNER_ENUM},
                                "claim_ids": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["text", "owner", "claim_ids"],
                        },
                    },
                    "limits": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "owner": {"type": "string", "enum": OWNER_ENUM},
                                "claim_ids": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["text", "owner", "claim_ids"],
                        },
                    },
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "title": {"type": "string"},
                                "instructions": {"type": "string"},
                                "owner": {"type": "string", "enum": OWNER_ENUM},
                                "step_dependencies": {"type": "array", "items": {"type": "string"}},
                                "success_condition": {"type": "string"},
                                "action_url": {"type": "string"},
                                "effort": {
                                    "type": "object",
                                    "properties": {
                                        "known": {"type": "boolean"},
                                        "low": {"type": "integer"},
                                        "high": {"type": "integer"},
                                        "unit": {"type": "string", "enum": UNIT_ENUM},
                                        "basis": {"type": "string", "enum": ESTIMATE_BASIS_ENUM},
                                        "explanation": {"type": "string"},
                                        "claim_ids": {"type": "array", "items": {"type": "string"}},
                                    },
                                    "required": ["known", "low", "high", "unit", "basis", "explanation", "claim_ids"],
                                },
                                "claim_ids": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["id", "title", "instructions", "owner", "step_dependencies",
                                          "success_condition", "action_url", "effort", "claim_ids"],
                        },
                    },
                    "effort": {
                        "type": "object",
                        "properties": {
                            "known": {"type": "boolean"},
                            "low": {"type": "integer"},
                            "high": {"type": "integer"},
                            "unit": {"type": "string", "enum": UNIT_ENUM},
                            "basis": {"type": "string", "enum": ESTIMATE_BASIS_ENUM},
                            "explanation": {"type": "string"},
                            "claim_ids": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["known", "low", "high", "unit", "basis", "explanation", "claim_ids"],
                    },
                    "waiting": {
                        "type": "object",
                        "properties": {
                            "known": {"type": "boolean"},
                            "low": {"type": "integer"},
                            "high": {"type": "integer"},
                            "unit": {"type": "string", "enum": UNIT_ENUM},
                            "basis": {"type": "string", "enum": ESTIMATE_BASIS_ENUM},
                            "explanation": {"type": "string"},
                            "claim_ids": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["known", "low", "high", "unit", "basis", "explanation", "claim_ids"],
                    },
                    "costs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string"},
                                "amount": {"type": "string"},
                                "basis": {"type": "string", "enum": ESTIMATE_BASIS_ENUM},
                                "claim_ids": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["description", "amount", "basis", "claim_ids"],
                        },
                    },
                    "claim_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "name", "applicability", "gates", "prerequisites",
                              "limits", "steps", "effort", "waiting", "costs", "claim_ids"],
            },
        },
        "scopes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "scope_string": {"type": "string"},
                    "purpose": {"type": "string"},
                    "classification": {"type": "string"},
                    "claim_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["scope_string", "purpose", "classification", "claim_ids"],
            },
        },
        "blockers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "trigger": {"type": "string"},
                    "consequence": {"type": "string"},
                    "resolution": {"type": "string"},
                    "owner": {"type": "string", "enum": OWNER_ENUM},
                    "claim_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["trigger", "consequence", "resolution", "owner", "claim_ids"],
            },
        },
        "agent_assistance": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "capability": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["capability", "description"],
            },
        },
        "unknowns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                    "how_to_find_out": {"type": "string"},
                },
                "required": ["question", "why_it_matters", "how_to_find_out"],
            },
        },
        "conflicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "description": {"type": "string"},
                    "source_ids": {"type": "array", "items": {"type": "string"}},
                    "resolution": {"type": "string"},
                },
                "required": ["topic", "description", "source_ids", "resolution"],
            },
        },
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "text": {"type": "string"},
                    "conditions": {"type": "string"},
                    "source_ids": {"type": "array", "items": {"type": "string"}},
                    "attempt_ids": {"type": "array", "items": {"type": "string"}},
                    "basis": {"type": "string", "enum": CLAIM_BASIS_ENUM},
                    "excerpt": {"type": "string"},
                },
                "required": ["id", "text", "conditions", "source_ids", "attempt_ids", "basis", "excerpt"],
            },
        },
    },
    "required": ["oauth_support", "summary", "next_action", "recommended_path_id",
                  "paths", "scopes", "blockers", "agent_assistance", "unknowns",
                  "conflicts", "claims"],
}


def build_synthesis_user_message(
    provider: ProviderInfo,
    scenario: Scenario,
    composio: ComposioInfo,
    sources: list[Source],
    attempts: list[Attempt],
    findings: str,
    research_timed_out: bool = False,
) -> str:
    """Build the user message for synthesis."""
    parts = [
        f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        f"Product: {provider.name} (domain: {provider.official_domain or 'not established'})",
        "",
        f"Scenario: {json.dumps(scenario.model_dump())}",
        "",
    ]

    if research_timed_out:
        parts.append("Research completeness: PARTIAL — prefer unknowns over guesses")
        parts.append("")

    # Composio metadata
    parts.append(f"Composio lookup: {composio.lookup_status}")
    if composio.toolkit_slug:
        parts.append(f"  Toolkit slug: {composio.toolkit_slug}")
        parts.append(f"  Auth method: {composio.selected_auth_method or 'unknown'}")
        parts.append(f"  Managed OAuth: {composio.managed_oauth}")
    parts.append("")

    # Source registry
    parts.append("Source registry:")
    for s in sources:
        parts.append(
            f"  {s.id} | {s.retrieval_status} | provider_domain={s.provider_domain_status} | "
            f"{s.title or 'untitled'} | {s.url} | {s.publication_date or 'no date'}"
        )
    parts.append("")

    # Attempts
    if attempts:
        parts.append("Attempt records:")
        for a in attempts:
            parts.append(f"  {a.id}: {a.action} — {a.status} — {a.observed_result}")
        parts.append("")

    # Findings
    parts.append("Research findings:")
    parts.append(findings)

    # Composio callback
    parts.append(f"\nComposio redirect URI: {COMPOSIO_CALLBACK_URL}")

    return "\n".join(parts)


async def run_synthesis(
    provider: ProviderInfo,
    scenario: Scenario,
    composio: ComposioInfo,
    sources: list[Source],
    attempts: list[Attempt],
    findings: str,
    deadline: float,
    research_timed_out: bool = False,
) -> tuple[Optional[DraftGuideContent], dict]:
    """Run synthesis with bounded repair using AsyncAnthropic. Returns (guide_content, run_stats)."""
    if not ANTHROPIC_API_KEY:
        return None, {"error": "No API key configured"}

    import anthropic

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    system_prompt = SYNTHESIS_SYSTEM_PROMPT.replace("COMPOSIO_CALLBACK", COMPOSIO_CALLBACK_URL)
    user_message = build_synthesis_user_message(
        provider, scenario, composio, sources, attempts, findings, research_timed_out
    )

    total_input_tokens = 0
    total_output_tokens = 0
    model_calls = 0

    try:
        for attempt_num in range(2):  # Initial + one repair
            remaining = deadline - time.time()
            if remaining < 15:
                return None, {
                    "error": "Insufficient time for synthesis",
                    "model_calls": model_calls,
                    "tokens_input": total_input_tokens,
                    "tokens_output": total_output_tokens,
                }

            req_timeout = max(5.0, remaining)

            try:
                response = await client.messages.create(
                    model=RESEARCH_MODEL,
                    max_tokens=16384,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_message}],
                    tools=[{
                        "name": "submit_guide",
                        "description": "Submit the structured guide content",
                        "input_schema": GUIDE_SCHEMA,
                    }],
                    tool_choice={"type": "tool", "name": "submit_guide"},
                    timeout=req_timeout,
                )

                model_calls += 1
                total_input_tokens += response.usage.input_tokens
                total_output_tokens += response.usage.output_tokens

                # Check for cut-off token limit
                if response.stop_reason == "max_tokens":
                    if attempt_num == 0:
                        user_message += (
                            "\n\nOutput was truncated by max_tokens. "
                            "Please provide a more concise answer with shorter instructions "
                            "and only consequential claims."
                        )
                        continue
                    else:
                        return None, {
                            "error": "Synthesis output truncated by max_tokens",
                            "model_calls": model_calls,
                            "tokens_input": total_input_tokens,
                            "tokens_output": total_output_tokens,
                        }

                # Extract tool call result
                for block in response.content:
                    if getattr(block, "type", "") == "tool_use" and getattr(block, "name", "") == "submit_guide":
                        guide_data = block.input
                        guide_content = DraftGuideContent.model_validate(guide_data)
                        return guide_content, {
                            "model_calls": model_calls,
                            "tokens_input": total_input_tokens,
                            "tokens_output": total_output_tokens,
                        }

                # If tool use not found, trigger repair
                if attempt_num == 0:
                    user_message += "\n\nPlease ensure you invoke the 'submit_guide' tool with the complete guide."
                    continue

            except Exception as e:
                if attempt_num == 0:
                    user_message += f"\n\nPrevious attempt failed: {str(e)[:200]}. Please fix and resubmit."
                    continue
                else:
                    return None, {
                        "error": f"Synthesis failed after repair: {e}",
                        "model_calls": model_calls,
                        "tokens_input": total_input_tokens,
                        "tokens_output": total_output_tokens,
                    }

        return None, {
            "error": "Synthesis exhausted attempts",
            "model_calls": model_calls,
            "tokens_input": total_input_tokens,
            "tokens_output": total_output_tokens,
        }
    finally:
        close_func = getattr(client, "close", None)
        if callable(close_func):
            res = close_func()
            if inspect.isawaitable(res):
                await res
