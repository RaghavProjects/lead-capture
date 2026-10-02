"""Provider-agnostic AI adapter with structured-output validation and retry.

Implements S1-03 (analyze a lead with validated structured output) and the
guardrails from 03_AI_Prompt_and_Behavior_Spec. No autonomous sending occurs.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import PROJECT_ROOT, Settings, settings
from models.schemas import (
    Activity,
    AIAnalysisOutput,
    BuyingSignal,
    BusinessProfile,
    Confidence,
    Lead,
    NextAction,
    Priority,
    RiskFlag,
    Sentiment,
    Stage,
)
from services import rules

PROMPT_VERSION = "2026-10-01.v1"
PROMPTS_DIR = PROJECT_ROOT / "prompts"


class AIUnavailable(RuntimeError):
    """Raised when the model cannot be reached or returns nothing usable."""


class AnalysisValidationError(RuntimeError):
    """Raised when structured output fails validation after the repair attempt."""


# --------------------------------------------------------------------- prompt
def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _format_activities(activities: List[Activity]) -> str:
    if not activities:
        return "(none)"
    lines = []
    for activity in activities:
        stamp = activity.occurred_at.isoformat() if activity.occurred_at else ""
        parts = [activity.activity_type, activity.outcome or "", activity.notes or ""]
        lines.append(f"- {stamp}: " + " | ".join(p for p in parts if p))
    return "\n".join(lines)


def build_analyze_prompt(lead: Lead, profile: BusinessProfile, activities: List[Activity]) -> str:
    template = load_prompt("analyze_lead.md")
    values = {
        "business_name": profile.business_name,
        "business_description": profile.description,
        "services": profile.services,
        "tone": profile.tone,
        "allowed_claims": profile.allowed_claims,
        "restricted_topics": profile.restricted_topics,
        "follow_up_rules": profile.follow_up_rules,
        "name": lead.name,
        "company": lead.company or "(not provided)",
        "source": lead.source or "(not provided)",
        "received_at": lead.received_at.date().isoformat() if lead.received_at else "",
        "stage": str(lead.stage),
        "inquiry": lead.inquiry,
        "notes": lead.notes or "(none)",
        "last_contact_at": lead.last_contact_at.date().isoformat() if lead.last_contact_at else "(none)",
        "response_status": str(lead.response_status),
        "activities": _format_activities(activities),
    }
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", str(value))
    return template


# ------------------------------------------------------------------- adapters
class AIAdapter:
    """Base adapter. ``complete`` returns the raw model text.

    ``context`` carries the structured lead/profile/activities so offline
    adapters do not have to parse them back out of the rendered prompt.
    """

    name = "base"

    def complete(self, system_prompt: str, user_prompt: str,
                 context: Optional[Dict[str, Any]] = None) -> str:  # pragma: no cover
        raise NotImplementedError


class MockAdapter(AIAdapter):
    """Deterministic offline analyzer.

    Used for local demos and tests when no API key is configured. It applies the
    same guardrail rules as the real adapter so behaviour stays consistent.
    """

    name = "mock"

    def complete(self, system_prompt: str, user_prompt: str,
                 context: Optional[Dict[str, Any]] = None) -> str:
        context = context or {}
        lead = context.get("lead")
        profile = context.get("profile") or BusinessProfile()
        if lead is None:
            raise AIUnavailable("MockAdapter requires lead context")
        output = self._analyse(lead, profile)
        return output.model_dump_json()

    def _analyse(self, lead: Lead, profile: BusinessProfile) -> AIAnalysisOutput:
        text = (lead.inquiry or "").lower()
        flags = rules.detect_risk_flags(lead)
        words = re.findall(r"[a-z]+", text)
        is_junk = bool(words) and set(words) <= {
            "test", "testing", "asdf", "foo", "bar", "xxx", "n", "a",
        }

        low_signal = any(
            phrase in text
            for phrase in [
                "just curious", "exploratory", "information about your services",
                "send information", "in a month", "next month", "budget is frozen",
                "in one month",
            ]
        )
        explicit_future_timing = any(
            phrase in text for phrase in ["in a month", "next month", "in one month", "follow up with me in"]
        )
        high_signal = any(
            phrase in text
            for phrase in [
                "next week", "this week", "asap", "today", "immediately", "right away",
                "ready to", "are ready to begin", "start monday", "start next", "proposal looks good",
                "quote", "demo", "availability", "schedule", "call me", "need someone",
                "fourth request", "urgent", "get leads", "120 internet leads", "need a faster",
                "pain point", "miss dms", "after hours",
            ]
        )
        scheduling = any(
            phrase in text
            for phrase in [
                "next week", "schedule", "demo", "availability", "start monday",
                "start next", "talk", "discovery",
            ]
        )
        negative = any(
            phrase in text
            for phrase in [
                "refund", "lawyer", "misleading", "did not meet", "didn't meet", "complaint",
                "unhappy", "take action", "terrible", "worst",
            ]
        )
        positive = any(
            phrase in text
            for phrase in ["thanks", "thank you", "looks good", "ready", "interested", "good fit"]
        )
        asks_price = any(phrase in text for phrase in ["how much", "cost", "price", "pricing", "quote for"])
        asks_guarantee = "guarantee" in text

        # ---------------------------------------------------------- priority
        if rules.has_risk(flags) or is_junk is False and (high_signal and not low_signal):
            priority = Priority.HIGH
        elif explicit_future_timing or low_signal:
            priority = Priority.LOW
        else:
            priority = Priority.MEDIUM
        if is_junk:
            priority = Priority.LOW

        # ------------------------------------------------------- buying signal
        if is_junk:
            buying = BuyingSignal.NONE
        elif priority == Priority.HIGH:
            buying = BuyingSignal.STRONG
        elif priority == Priority.MEDIUM:
            buying = BuyingSignal.MODERATE
        else:
            buying = BuyingSignal.WEAK

        sentiment = Sentiment.NEGATIVE if negative else (Sentiment.POSITIVE if positive else Sentiment.NEUTRAL)

        # ----------------------------------------------------------- summary
        summary = self._summary(lead)

        # ------------------------------------------------------- next action
        if rules.has_risk(flags):
            next_action = NextAction.ESCALATE
        elif is_junk:
            next_action = NextAction.CLOSE_DISQUALIFY
        elif explicit_future_timing:
            next_action = NextAction.NURTURE
        elif asks_guarantee or asks_price:
            next_action = NextAction.RESPOND
        elif any(phrase in text for phrase in ["information about your services", "send information"]):
            next_action = NextAction.ASK_CLARIFYING_QUESTION if lead.stage == Stage.NEW else NextAction.FOLLOW_UP
        elif any(phrase in text for phrase in ["we need help", "need help; call", "not sure", "help?"]):
            next_action = NextAction.ASK_CLARIFYING_QUESTION
        elif priority == Priority.LOW:
            next_action = NextAction.NURTURE
        elif high_signal and scheduling:
            next_action = NextAction.SCHEDULE_DISCOVERY
        elif high_signal:
            next_action = NextAction.RESPOND if lead.stage == Stage.NEW else NextAction.FOLLOW_UP
        elif lead.stage == Stage.NEW:
            next_action = NextAction.RESPOND
        else:
            next_action = NextAction.FOLLOW_UP

        # ------------------------------------------------------ follow-up days
        if rules.has_risk(flags) or is_junk:
            follow_up_days = 0
        elif explicit_future_timing:
            follow_up_days = 30
        elif next_action == NextAction.NURTURE:
            follow_up_days = 14
        else:
            follow_up_days = rules.default_follow_up_days(priority, next_action)

        # ---------------------------------------------------------- rationale
        rationale = self._rationale(priority, flags, high_signal, low_signal, explicit_future_timing, asks_price, asks_guarantee, is_junk)

        if is_junk or (not high_signal and not low_signal and priority == Priority.MEDIUM and len(text.split()) < 4):
            confidence = Confidence.LOW
        elif rules.has_risk(flags) or high_signal or low_signal:
            confidence = Confidence.HIGH
        else:
            confidence = Confidence.MEDIUM

        subject, body = self._draft(lead, profile, flags, next_action, asks_price, asks_guarantee, is_junk, explicit_future_timing, priority)

        return AIAnalysisOutput(
            intent=self._intent(lead),
            summary=summary,
            priority=priority,
            priority_rationale=rationale,
            buying_signal=buying,
            sentiment=sentiment,
            risk_flags=flags,
            next_action=next_action,
            follow_up_days=follow_up_days,
            confidence=confidence,
            draft_subject=subject,
            draft_body=body,
        )

    @staticmethod
    def _summary(lead: Lead) -> str:
        company = f" at {lead.company}" if lead.company else ""
        return f"{lead.name}{company} submitted an inquiry via {lead.source or 'an unknown source'}: \"{lead.inquiry.strip()}\""

    @staticmethod
    def _intent(lead: Lead) -> str:
        text = lead.inquiry.lower()
        patterns = [
            (("refund",), "Refund request"),
            (("lawyer", "legal", "misleading"), "Legal complaint"),
            (("ssn", "bank details", "medical"), "Sensitive data handling"),
            (("price", "cost", "how much"), "Pricing question"),
            (("demo",), "Demo request"),
            (("redesign", "website"), "Website / digital project"),
            (("follow-up", "follow up", "followup"), "Lead follow-up automation"),
            (("case studies", "information about your services", "send information"), "Information request"),
            (("estimate",), "Estimate automation"),
            (("report",), "Reporting request"),
            (("test",), "Irrelevant inquiry"),
        ]
        for keywords, label in patterns:
            if any(keyword in text for keyword in keywords):
                return label
        return "General inquiry"

    @staticmethod
    def _rationale(priority, flags, high_signal, low_signal, future, asks_price, asks_guarantee, is_junk) -> str:
        if rules.has_risk(flags):
            names = ", ".join(str(f) for f in flags if f != RiskFlag.NONE)
            return f"Risk indicator detected ({names}); routed for human review regardless of intent."
        if is_junk:
            return "Inquiry appears irrelevant or invalid; no buying intent to pursue."
        reasons = []
        if future:
            reasons.append("explicit request to follow up later")
        if high_signal:
            reasons.append("explicit need and/or timing signal")
        if low_signal:
            reasons.append("weak or exploratory intent")
        if asks_price:
            reasons.append("asks about pricing (no pricing data available to quote)")
        if asks_guarantee:
            reasons.append("requests a results guarantee that cannot be made")
        if not reasons:
            reasons.append("relevant inquiry with moderate intent and unclear timing")
        return f"Priority {priority.value}: " + "; ".join(reasons) + "."

    @staticmethod
    def _draft(lead, profile, flags, next_action, asks_price, asks_guarantee, is_junk, future, priority):
        first = lead.name.split()[0] if lead.name else "there"
        business = profile.business_name
        subject_map = {
            NextAction.ESCALATE: f"Re: your message to {business}",
            NextAction.CLOSE_DISQUALIFY: f"Re: your message to {business}",
            NextAction.NURTURE: f"Checking in from {business}",
            NextAction.ASK_CLARIFYING_QUESTION: f"A quick question from {business}",
            NextAction.SCHEDULE_DISCOVERY: f"Finding time to talk — {business}",
            NextAction.RESPOND: f"Re: your inquiry — {business}",
            NextAction.FOLLOW_UP: f"Following up — {business}",
        }
        subject = subject_map.get(next_action, f"Re: your inquiry — {business}")

        if rules.has_risk(flags):
            body = (
                f"Hi {first}, thank you for reaching out, and I'm sorry for the frustration this has caused.\n\n"
                f"I want to make sure this is handled correctly, so I'm passing your message to a team member "
                f"who can review it personally. We will follow up with you directly.\n\n"
                f"If there is anything urgent you'd like included, simply reply to this message.\n\n"
                f"— {business}"
            )
            return subject, body

        if is_junk:
            body = (
                f"Hi {first}, thanks for the note. We couldn't identify a specific request from your message.\n\n"
                f"If you'd like help with lead response or follow-up, reply with a sentence about what you're "
                f"trying to solve and we'll point you in the right direction.\n\n— {business}"
            )
            return subject, body

        if future:
            body = (
                f"Hi {first}, thanks for letting us know about your timing — I'll follow up at the point you "
                f"mentioned rather than sooner.\n\n"
                f"In the meantime, if anything changes or you'd like to get a head start, just reply and we'll "
                f"pick it up from there.\n\n— {business}"
            )
            return subject, body

        if asks_price:
            body = (
                f"Hi {first}, thanks for asking about pricing. Because costs depend on the specifics of your "
                f"situation, I don't want to quote a number that could be wrong.\n\n"
                f"The best next step is a short call so we can understand what you need and confirm accurate "
                f"pricing with you. Would you like to set up a 15-minute conversation?\n\n— {business}"
            )
            return subject, body

        if asks_guarantee:
            body = (
                f"Hi {first}, thanks for your interest. I can't guarantee a specific sales outcome — no honest "
                f"partner can — but I can walk you through exactly how we approach lead response and follow-up.\n\n"
                f"Would it help to see how the process would apply to your business? Just reply and we'll arrange "
                f"a short call.\n\n— {business}"
            )
            return subject, body

        if next_action == NextAction.ASK_CLARIFYING_QUESTION:
            body = (
                f"Hi {first}, thanks for reaching out. To point you to the right thing, could you share a "
                f"sentence about what you're hoping to improve — for example, responding to new leads faster "
                f"or automating follow-up?\n\n"
                f"Reply and we'll take it from there.\n\n— {business}"
            )
            return subject, body

        if next_action == NextAction.SCHEDULE_DISCOVERY:
            body = (
                f"Hi {first}, thanks for reaching out — it sounds like there's a clear need here.\n\n"
                f"We help businesses like yours improve lead response and follow-up. To make sure we focus on "
                f"what matters most to you, would you be open to a short discovery call?\n\n"
                f"If you share a couple of times that work, we'll confirm one with you.\n\n— {business}"
            )
            return subject, body

        if next_action == NextAction.NURTURE:
            body = (
                f"Hi {first}, thanks for the note. It sounds like now isn't the moment to move forward.\n\n"
                f"I'll check back in with something useful rather than a generic nudge. If anything changes on "
                f"your side, just reply and we'll pick it up.\n\n— {business}"
            )
            return subject, body

        if next_action == NextAction.FOLLOW_UP:
            body = (
                f"Hi {first}, I wanted to follow up on your earlier message and make sure it didn't get lost.\n\n"
                f"If it's still relevant, the best next step is a short call to confirm what you need. If the "
                f"timing has changed, just let me know and I'll adjust.\n\n— {business}"
            )
            return subject, body

        body = (
            f"Hi {first}, thanks for reaching out to {business}. It sounds like you're looking to improve how "
            f"incoming leads are handled and followed up.\n\n"
            f"Would it help to have a quick conversation about your current process and where the gaps are? "
            f"If you reply with a good time, we'll set it up.\n\n— {business}"
        )
        return subject, body


class OpenAICompatibleAdapter(AIAdapter):
    """Minimal OpenAI-compatible chat completions adapter (stdlib only)."""

    name = "openai"

    def __init__(self, model: str, api_key: str, base_url: str):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def complete(self, system_prompt: str, user_prompt: str,
                 context: Optional[Dict[str, Any]] = None) -> str:
        if not self.api_key:
            raise AIUnavailable("AI_API_KEY is not configured")
        import urllib.error
        import urllib.request

        payload = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AIUnavailable(f"AI request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise AIUnavailable(f"AI returned non-JSON response: {exc}") from exc
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIUnavailable("AI response missing message content") from exc
        return _strip_code_fences(content)


def get_adapter(app_settings: Optional[Settings] = None) -> AIAdapter:
    app_settings = app_settings or settings
    provider = (app_settings.ai_provider or "mock").lower()
    if provider in ("mock", "offline", "none"):
        return MockAdapter()
    if provider in ("openai", "openai_compatible", "compatible"):
        return OpenAICompatibleAdapter(
            model=app_settings.ai_model,
            api_key=app_settings.ai_api_key,
            base_url=app_settings.ai_base_url,
        )
    raise AIUnavailable(f"Unknown AI_PROVIDER: {provider}")


# ------------------------------------------------------------- orchestration
def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def parse_and_validate(raw_text: str) -> AIAnalysisOutput:
    try:
        payload = json.loads(_strip_code_fences(raw_text))
    except json.JSONDecodeError as exc:
        raise AnalysisValidationError(f"Invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise AnalysisValidationError("Structured output must be a JSON object")
    try:
        return AIAnalysisOutput.model_validate(payload)
    except Exception as exc:  # pydantic ValidationError
        raise AnalysisValidationError(str(exc)) from exc


def run_analysis(
    adapter: AIAdapter,
    lead: Lead,
    profile: BusinessProfile,
    activities: List[Activity],
) -> AIAnalysisOutput:
    """Call the adapter, validate, and retry once with a repair prompt."""
    system_prompt = load_prompt("system.md")
    user_prompt = build_analyze_prompt(lead, profile, activities)
    context = {"lead": lead, "profile": profile, "activities": activities}
    try:
        raw = adapter.complete(system_prompt, user_prompt, context=context)
        return parse_and_validate(raw)
    except (AnalysisValidationError, json.JSONDecodeError):
        repair_prompt = (
            user_prompt
            + "\n\nIMPORTANT: Your previous reply was not valid JSON matching the schema. "
            "Reply again with ONLY a single valid JSON object using the exact keys and allowed values. "
            "No prose, no markdown fences."
        )
        raw = adapter.complete(system_prompt, repair_prompt, context=context)
        return parse_and_validate(raw)
