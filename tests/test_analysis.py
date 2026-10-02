import json

import pytest

from models.schemas import AIAnalysisOutput, Lead, NextAction, Priority, RiskFlag, Stage
from services.ai import AIAdapter, AIUnavailable, AnalysisValidationError
from services.leads import LeadValidationError

VALID_PAYLOAD = {
    "intent": "General inquiry",
    "summary": "A lead asked a question.",
    "priority": "Medium",
    "priority_rationale": "Relevant inquiry with moderate intent.",
    "buying_signal": "Moderate",
    "sentiment": "Neutral",
    "risk_flags": ["NONE"],
    "next_action": "Respond",
    "follow_up_days": 2,
    "confidence": "Medium",
    "draft_subject": "Re: your inquiry",
    "draft_body": "Hello, thanks for reaching out. Would you like to set up a short call?",
}


class UnavailableAdapter(AIAdapter):
    def complete(self, system_prompt, user_prompt, context=None):
        raise AIUnavailable("simulated outage")


class RepairAdapter(AIAdapter):
    def __init__(self):
        self.calls = 0

    def complete(self, system_prompt, user_prompt, context=None):
        self.calls += 1
        if self.calls == 1:
            return "Sure! Here is the analysis: not valid json"
        return json.dumps(VALID_PAYLOAD)


def _new_lead(inquiry, stage=Stage.NEW, name="Test Lead") -> Lead:
    return Lead(lead_id="lead_test", name=name, inquiry=inquiry, stage=stage, source="Website")


def test_manual_create_validation_blocks_empty_fields(service):
    # Bypass the pydantic field validator to exercise the service-level guard.
    invalid = Lead.model_construct(lead_id="lead_x", name="No Inquiry", inquiry="", stage=Stage.NEW)
    with pytest.raises(LeadValidationError):
        service.create_lead(invalid)


def test_manual_create_persists_and_appears_in_list(service):
    lead = service.create_lead(_new_lead("We need help with lead follow-up."))
    assert service.repo.count_leads() == 1
    assert any(item.lead_id == lead.lead_id for item in service.list_leads())


def test_analyze_normal_lead_stores_validated_output_and_draft(service):
    lead = service.create_lead(_new_lead("Interested in automating estimates. Please call me."))
    result = service.analyze_lead(lead.lead_id)

    assert isinstance(result.analysis.analysis_id, str)
    assert result.analysis.prompt_version
    assert result.draft.body
    assert result.draft.body == service.repo.get_draft_for_analysis(result.analysis.analysis_id).body
    stored = service.repo.get_latest_analysis(lead.lead_id)
    assert stored is not None
    assert str(stored.next_action) in {a.value for a in NextAction}


def test_risk_lead_escalates_and_is_flagged_for_review(service):
    lead = service.create_lead(_new_lead("I want a refund immediately. This did not meet expectations."))
    result = service.analyze_lead(lead.lead_id)

    assert RiskFlag.REFUND in result.risk_flags
    assert result.analysis.next_action == NextAction.ESCALATE
    assert result.needs_review is True
    assert service.get_lead(lead.lead_id).needs_review is True


def test_ai_unavailable_preserves_lead_and_marks_needs_review(repo, settings):
    from services.leads import LeadService

    service = LeadService(repo, adapter=UnavailableAdapter(), app_settings=settings)
    lead = service.create_lead(_new_lead("Need a faster response process."))

    with pytest.raises(AIUnavailable):
        service.analyze_lead(lead.lead_id)

    preserved = service.get_lead(lead.lead_id)
    assert preserved is not None
    assert preserved.needs_review is True
    assert "unavailable" in (preserved.review_reason or "").lower()
    assert service.repo.get_latest_analysis(lead.lead_id) is None


def test_invalid_json_triggers_single_repair_retry(repo, settings):
    from services.leads import LeadService

    adapter = RepairAdapter()
    service = LeadService(repo, adapter=adapter, app_settings=settings)
    lead = service.create_lead(_new_lead("Please send information about your services."))
    result = service.analyze_lead(lead.lead_id)

    assert adapter.calls == 2
    assert result.analysis is not None


def test_invalid_json_after_repair_marks_needs_review(repo, settings):
    from services.leads import LeadService

    class AlwaysInvalid(AIAdapter):
        def complete(self, system_prompt, user_prompt, context=None):
            return "still not json"

    service = LeadService(repo, adapter=AlwaysInvalid(), app_settings=settings)
    lead = service.create_lead(_new_lead("Need help with leads."))
    with pytest.raises(AnalysisValidationError):
        service.analyze_lead(lead.lead_id)
    assert service.get_lead(lead.lead_id).needs_review is True


def test_golden_general_inquiry_is_not_over_prioritized(service):
    lead = service.create_lead(_new_lead("Please send information about your services."))
    result = service.analyze_lead(lead.lead_id)
    assert result.analysis.priority in (Priority.MEDIUM, Priority.LOW)
    assert result.analysis.next_action in (
        NextAction.ASK_CLARIFYING_QUESTION,
        NextAction.RESPOND,
    )


def test_golden_future_timing_is_honored(service):
    lead = service.create_lead(
        _new_lead("Could you follow up with me in a month? Budget is frozen right now.")
    )
    result = service.analyze_lead(lead.lead_id)
    assert result.analysis.next_action == NextAction.NURTURE
    assert result.analysis.priority == Priority.LOW


def test_golden_junk_is_closed(service):
    lead = service.create_lead(_new_lead("test test test"))
    result = service.analyze_lead(lead.lead_id)
    assert result.analysis.next_action == NextAction.CLOSE_DISQUALIFY


def test_golden_guarantee_request_is_not_guaranteed(service):
    lead = service.create_lead(_new_lead("Can you guarantee this will double my sales in 30 days?"))
    result = service.analyze_lead(lead.lead_id)
    assert "guarantee" not in result.draft.body.lower() or "can't guarantee" in result.draft.body.lower()
    assert result.analysis.next_action != NextAction.ESCALATE


def test_parse_and_validate_accepts_valid_and_rejects_invalid():
    from services.ai import parse_and_validate

    output = parse_and_validate(json.dumps(VALID_PAYLOAD))
    assert isinstance(output, AIAnalysisOutput)
    with pytest.raises(AnalysisValidationError):
        parse_and_validate("not json")
    with pytest.raises(AnalysisValidationError):
        parse_and_validate(json.dumps({"intent": "missing everything else"}))
