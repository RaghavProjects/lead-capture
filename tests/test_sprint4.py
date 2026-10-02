from datetime import datetime, timedelta, timezone

from models.schemas import (
    AIAnalysis,
    BusinessProfile,
    BuyingSignal,
    Confidence,
    Lead,
    NextAction,
    Priority,
    ResponseStatus,
    RiskFlag,
    Sentiment,
    Stage,
)
from services.ai import build_analyze_prompt

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def seed_lead(repo, lead_id, *, stage=Stage.NEW, priority=Priority.MEDIUM, source="Website",
              needs_review=False):
    lead = Lead(
        lead_id=lead_id,
        name=f"Person {lead_id}",
        inquiry="Interested in automating lead follow-up.",
        stage=stage,
        source=source,
        received_at=NOW - timedelta(hours=5),
        response_status=ResponseStatus.NOT_CONTACTED,
    )
    repo.upsert_lead(lead)
    analysis = AIAnalysis(
        lead_id=lead_id,
        prompt_version="test",
        intent="test",
        summary="test",
        priority=priority,
        priority_rationale="test",
        buying_signal=BuyingSignal.MODERATE,
        sentiment=Sentiment.NEUTRAL,
        risk_flags=[RiskFlag.NONE],
        next_action=NextAction.RESPOND,
        follow_up_at=NOW + timedelta(days=2),
        confidence=Confidence.HIGH,
    )
    repo.save_analysis(analysis)
    repo.set_next_follow_up(lead_id, analysis.follow_up_at)
    if needs_review:
        repo.set_review_state(lead_id, True, "test")
    return lead


def test_counts_reconcile_to_lead_list(service, repo):
    seed_lead(repo, "L-1", stage=Stage.NEW, priority=Priority.HIGH, source="Website")
    seed_lead(repo, "L-2", stage=Stage.CONTACTED, priority=Priority.MEDIUM, source="Referral")
    seed_lead(repo, "L-3", stage=Stage.NEW, priority=Priority.LOW, source="Website")
    seed_lead(repo, "L-4", stage=Stage.WON, priority=Priority.LOW, source="Event", needs_review=True)

    leads = service.filtered_leads()
    summary = service.pipeline_summary()

    assert summary["total"] == len(leads) == 4
    assert sum(summary["by_stage"].values()) == len(leads)
    assert sum(summary["by_priority"].values()) == len(leads)
    assert summary["needs_review"] == sum(1 for lead in leads if lead.needs_review)


def test_counts_reconcile_under_filters(service, repo):
    seed_lead(repo, "L-1", stage=Stage.NEW, priority=Priority.HIGH, source="Website")
    seed_lead(repo, "L-2", stage=Stage.NEW, priority=Priority.LOW, source="Website")
    seed_lead(repo, "L-3", stage=Stage.CONTACTED, priority=Priority.HIGH, source="Referral")

    filters = dict(stage=str(Stage.NEW), priority="High")
    leads = service.filtered_leads(**filters)
    summary = service.pipeline_summary(**filters)

    assert [lead.lead_id for lead in leads] == ["L-1"]
    assert summary["total"] == len(leads) == 1


def test_filters_cover_stage_priority_source_review_search(service, repo):
    seed_lead(repo, "L-1", stage=Stage.NEW, priority=Priority.HIGH, source="Website", needs_review=True)
    seed_lead(repo, "L-2", stage=Stage.CONTACTED, priority=Priority.LOW, source="Referral")

    assert [l.lead_id for l in service.filtered_leads(stage=str(Stage.CONTACTED))] == ["L-2"]
    assert [l.lead_id for l in service.filtered_leads(priority="High")] == ["L-1"]
    assert [l.lead_id for l in service.filtered_leads(source="Referral")] == ["L-2"]
    assert [l.lead_id for l in service.filtered_leads(needs_review=True)] == ["L-1"]
    assert [l.lead_id for l in service.filtered_leads(search="Person L-2")] == ["L-2"]
    assert set(service.available_sources()) == {"Referral", "Website"}


def test_business_profile_persists_and_warnings(service):
    assert service.profile_warnings() == []  # default profile is configured

    blank = BusinessProfile(business_name="", description="", services="", tone="", allowed_claims="", restricted_topics="", follow_up_rules="")
    service.save_business_profile(blank)

    warnings = service.profile_warnings()
    assert "business name" in warnings
    assert "description" in warnings
    assert "services" in warnings

    configured = BusinessProfile(
        business_name="Acme Studio",
        description="We automate lead follow-up.",
        services="Assessment; implementation",
        tone="Friendly",
        allowed_claims="We offer assessments.",
        restricted_topics="No pricing commitments.",
        follow_up_rules="Follow up within 2 days.",
    )
    service.save_business_profile(configured)
    assert service.profile_warnings() == []
    assert service.get_business_profile().business_name == "Acme Studio"


def test_prompt_uses_saved_business_context(service, repo):
    profile = BusinessProfile(
        business_name="Northstar Growth Studio",
        description="We help local service businesses with lead response automation.",
        services="Lead workflow assessment; implementation support",
        tone="Warm and practical",
        allowed_claims="We offer workflow assessments.",
        restricted_topics="Do not quote prices unless supplied.",
        follow_up_rules="High intent: next business day.",
    )
    service.save_business_profile(profile)
    lead = seed_lead(repo, "L-CTX")

    prompt = build_analyze_prompt(lead, service.get_business_profile(), [])

    assert "Northstar Growth Studio" in prompt
    assert "Warm and practical" in prompt
    assert "We offer workflow assessments." in prompt
    assert "Do not quote prices unless supplied." in prompt
