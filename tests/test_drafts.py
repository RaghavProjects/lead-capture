import re

import pytest

from models.schemas import DraftMessage, DraftStatus, Lead, Stage
from services.leads import LeadValidationError


def _analyzed_draft(service):
    lead = service.create_lead(
        Lead(lead_id="lead_draft", name="Dana Ray", inquiry="Interested in automating our lead follow-up.", stage=Stage.NEW)
    )
    result = service.analyze_lead(lead.lead_id)
    return lead, result.draft


def test_edit_preserves_original_and_stores_separately(service):
    _, draft = _analyzed_draft(service)
    original = draft.body

    updated = service.save_draft_edit(draft.draft_id, "Hi Dana, thanks — are you free Thursday at 2pm?")

    assert updated.body == original, "original AI draft must never be overwritten"
    assert updated.user_edited_body == "Hi Dana, thanks — are you free Thursday at 2pm?"
    assert updated.status == DraftStatus.EDITED
    assert updated.effective_body == updated.user_edited_body
    assert updated.is_edited is True

    reloaded = service.get_draft(draft.draft_id)
    assert reloaded.body == original


def test_copy_without_edit_returns_original_draft(service):
    _, draft = _analyzed_draft(service)
    copied = service.copy_draft(draft.draft_id)
    assert copied == draft.body
    assert service.get_draft(draft.draft_id).status == DraftStatus.COPIED


def test_copy_uses_edited_body_when_present(service):
    _, draft = _analyzed_draft(service)
    service.save_draft_edit(draft.draft_id, "Edited body to copy.")
    copied = service.copy_draft(draft.draft_id)
    assert copied == "Edited body to copy."
    assert service.get_draft(draft.draft_id).copied_at is not None


def test_copy_does_not_imply_message_was_sent(service, repo):
    lead, draft = _analyzed_draft(service)
    before = service.get_lead(lead.lead_id)
    assert before.last_contact_at is None

    service.copy_draft(draft.draft_id)

    after = service.get_lead(lead.lead_id)
    assert after.last_contact_at == before.last_contact_at, "copying must not log contact"
    assert after.response_status == before.response_status

    stored = service.get_draft(draft.draft_id)
    assert stored.status != "sent"
    assert not hasattr(DraftStatus, "SENT")
    assert all(d.status != "sent" for d in service.list_lead_drafts(lead.lead_id))


def test_empty_edit_is_rejected(service):
    _, draft = _analyzed_draft(service)
    with pytest.raises(LeadValidationError):
        service.save_draft_edit(draft.draft_id, "   ")


def test_edit_and_copy_are_recorded_in_activity_history(service):
    lead, draft = _analyzed_draft(service)
    service.save_draft_edit(draft.draft_id, "Edited text.")
    service.copy_draft(draft.draft_id)

    types = [a.activity_type for a in service.repo.list_activities(lead.lead_id)]
    assert "draft_edited" in types
    assert "draft_copied" in types


def test_draft_is_grounded_and_has_one_call_to_action(service):
    _, draft = _analyzed_draft(service)
    assert draft.body.strip()
    assert not re.search(r"\$\s?\d", draft.body), "must not fabricate a price"
    assert draft.body.count("?") <= 1, "at most one clear call to action"
    cta_markers = ["reply", "call", "conversation", "times", "set it up", "let me know"]
    assert any(marker in draft.body.lower() for marker in cta_markers)


def test_effective_body_prefers_edit():
    draft = DraftMessage(lead_id="l", analysis_id="a", subject="s", body="original")
    assert draft.effective_body == "original"
    draft.user_edited_body = "edited"
    assert draft.effective_body == "edited"
