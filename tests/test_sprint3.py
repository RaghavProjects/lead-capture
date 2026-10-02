from datetime import datetime, timedelta, timezone

from models.schemas import (
    AIAnalysis,
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

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def add_lead(
    repo,
    lead_id,
    *,
    stage=Stage.CONTACTED,
    priority=Priority.MEDIUM,
    follow_up_offset_days=0,
    received_offset_hours=72,
    last_contact_offset_days=None,
    confidence=Confidence.HIGH,
    needs_review=False,
):
    received = NOW - timedelta(hours=received_offset_hours)
    last_contact = (
        NOW - timedelta(days=last_contact_offset_days)
        if last_contact_offset_days is not None
        else None
    )
    lead = Lead(
        lead_id=lead_id,
        name=f"Person {lead_id}",
        inquiry="Looking for help with lead follow-up.",
        stage=stage,
        received_at=received,
        last_contact_at=last_contact,
        response_status=ResponseStatus.AWAITING_RESPONSE,
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
        next_action=NextAction.FOLLOW_UP,
        follow_up_at=NOW + timedelta(days=follow_up_offset_days),
        confidence=confidence,
    )
    repo.save_analysis(analysis)
    repo.set_next_follow_up(lead_id, analysis.follow_up_at)
    if needs_review:
        repo.set_review_state(lead_id, True, "test")
    return lead


def test_overdue_and_due_today_are_surfaced(service, repo):
    add_lead(repo, "L-OVER", follow_up_offset_days=-2)
    add_lead(repo, "L-DUE", follow_up_offset_days=0)
    add_lead(repo, "L-FUTURE", follow_up_offset_days=5, received_offset_hours=1, priority=Priority.LOW)

    queue = service.list_attention_queue(today=NOW)
    overdue_ids = [e.lead.lead_id for e in queue.buckets["Overdue"]]
    due_ids = [e.lead.lead_id for e in queue.buckets["Due Today"]]

    assert "L-OVER" in overdue_ids
    assert "L-DUE" in due_ids
    assert "L-FUTURE" not in overdue_ids


def test_new_high_priority_lead_surfaced(service, repo):
    add_lead(repo, "L-HOT", priority=Priority.HIGH, received_offset_hours=2, follow_up_offset_days=1)
    add_lead(repo, "L-OLD", priority=Priority.HIGH, received_offset_hours=200, follow_up_offset_days=1)

    queue = service.list_attention_queue(today=NOW)
    hot_ids = [e.lead.lead_id for e in queue.buckets["New Hot Lead"]]

    assert "L-HOT" in hot_ids
    assert "L-OLD" not in hot_ids


def test_closed_stages_excluded_from_all_buckets(service, repo):
    for stage in (Stage.WON, Stage.LOST, Stage.NOT_A_LEAD):
        add_lead(repo, f"L-{stage.value}", stage=stage, follow_up_offset_days=-5)

    queue = service.list_attention_queue(today=NOW)
    all_ids = {e.lead.lead_id for entries in queue.buckets.values() for e in entries}
    assert "L-Won" not in all_ids
    assert "L-Lost" not in all_ids
    assert "L-Not a Lead" not in all_ids


def test_counts_reconcile_to_buckets(service, repo):
    add_lead(repo, "L-OVER", follow_up_offset_days=-1)
    add_lead(repo, "L-STALE", follow_up_offset_days=10, received_offset_hours=1, last_contact_offset_days=30)

    queue = service.list_attention_queue(today=NOW)
    for name, count in queue.counts.items():
        assert count == len(queue.buckets[name])


def test_queue_is_deterministic(service, repo):
    add_lead(repo, "L-B", follow_up_offset_days=-1)
    add_lead(repo, "L-A", follow_up_offset_days=-3)

    first = service.list_attention_queue(today=NOW)
    second = service.list_attention_queue(today=NOW)
    assert [e.lead.lead_id for e in first.buckets["Overdue"]] == [
        e.lead.lead_id for e in second.buckets["Overdue"]
    ]
    # earliest follow-up first
    assert first.buckets["Overdue"][0].lead.lead_id == "L-A"


def test_record_outcome_updates_state_and_recalculates(service, repo):
    add_lead(repo, "L-OUT", follow_up_offset_days=-1)

    activity, next_follow_up = service.record_outcome(
        "L-OUT", "Call", outcome="No response", notes="Left voicemail", stage=Stage.CONTACTED
    )

    assert activity.activity_type == "Call"
    assert activity.occurred_at is not None
    stored_activity = service.repo.list_activities("L-OUT")[0]
    assert stored_activity.outcome == "No response"

    lead = service.get_lead("L-OUT")
    assert lead.last_contact_at is not None
    assert lead.next_follow_up_at is not None
    # "no response" routes to a 7-day nurture-style window
    assert (next_follow_up - NOW).days == 7


def test_closed_outcome_schedules_no_follow_up(service, repo):
    add_lead(repo, "L-CLOSE", follow_up_offset_days=-1)
    _, next_follow_up = service.record_outcome("L-CLOSE", "Note", outcome="Won", stage=Stage.WON)
    assert next_follow_up is None
    assert service.get_lead("L-CLOSE").next_follow_up_at is None


def test_queue_uses_recalculated_follow_up(service, repo):
    add_lead(repo, "L-MOVE", follow_up_offset_days=-1)
    # recalculating pushes the follow-up into the future, clearing overdue.
    service.recalculate_follow_up("L-MOVE", last_outcome="No response")

    queue = service.list_attention_queue(today=NOW)
    overdue_ids = [e.lead.lead_id for e in queue.buckets["Overdue"]]
    assert "L-MOVE" not in overdue_ids


def test_seeded_overdue_leads_surface_before_analysis(repo, settings):
    from services.ai import MockAdapter
    from services.importer import import_leads
    from services.leads import LeadService
    from tests.conftest import SAMPLE_CSV

    import_leads(str(SAMPLE_CSV), repo, filename="sample_leads.csv")
    service = LeadService(repo, adapter=MockAdapter(), app_settings=settings)

    queue = service.list_attention_queue(today=NOW)
    overdue_ids = [e.lead.lead_id for e in queue.buckets["Overdue"]]

    # L-1008 is an open (Contacted) lead whose seeded follow-up date has passed.
    assert "L-1008" in overdue_ids
    # Closed leads from the seed must never appear.
    all_ids = {e.lead.lead_id for entries in queue.buckets.values() for e in entries}
    assert "L-1014" not in all_ids  # Lost
    assert "L-1027" not in all_ids  # Won

