"""Application service coordinating leads, rules, AI analysis and persistence."""
from __future__ import annotations

from datetime import timedelta
from typing import List, Optional

from config import Settings, settings
from data.repository import SQLiteRepository
from models.schemas import (
    Activity,
    AIAnalysis,
    AnalysisResult,
    BusinessProfile,
    Confidence,
    DraftMessage,
    DraftStatus,
    Lead,
    ResponseStatus,
    Stage,
    utc_now,
)
from services import rules
from services.ai import (
    AIAdapter,
    AnalysisValidationError,
    AIUnavailable,
    PROMPT_VERSION,
    get_adapter,
    run_analysis,
)


class LeadNotFound(ValueError):
    pass


class DraftNotFound(ValueError):
    pass


class LeadValidationError(ValueError):
    def __init__(self, errors: List[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


class LeadService:
    def __init__(
        self,
        repository: SQLiteRepository,
        adapter: Optional[AIAdapter] = None,
        app_settings: Optional[Settings] = None,
    ):
        self.repo = repository
        self.app_settings = app_settings or settings
        self._adapter = adapter

    @property
    def adapter(self) -> AIAdapter:
        if self._adapter is None:
            self._adapter = get_adapter(self.app_settings)
        return self._adapter

    # ------------------------------------------------------------------ leads
    def create_lead(self, lead: Lead) -> Lead:
        errors = rules.validate_lead_input(lead)
        if errors:
            raise LeadValidationError(errors)
        self.repo.upsert_lead(lead)
        self.repo.add_activity(
            Activity(lead_id=lead.lead_id, activity_type="lead_created", notes="Lead created")
        )
        return lead

    def get_lead(self, lead_id: str) -> Lead:
        lead = self.repo.get_lead(lead_id)
        if lead is None:
            raise LeadNotFound(f"No lead with id {lead_id}")
        return lead

    def list_leads(self, search: Optional[str] = None, stage: Optional[str] = None,
                   priority: Optional[str] = None) -> List[Lead]:
        return self.repo.list_leads(search=search, stage=stage, priority=priority)

    # --------------------------------------------------------------- analysis
    def analyze_lead(self, lead_id: str) -> AnalysisResult:
        lead = self.get_lead(lead_id)

        errors = rules.validate_lead_input(lead)
        if errors:
            # Never call the model on incomplete critical data.
            raise LeadValidationError(errors)

        profile = self.repo.get_business_profile() or BusinessProfile()
        activities = self.repo.list_activities(lead_id)

        try:
            output = run_analysis(self.adapter, lead, profile, activities)
        except (AIUnavailable, AnalysisValidationError) as exc:
            reason = f"Analysis unavailable: {exc}"
            self.repo.set_review_state(lead_id, True, reason)
            self.repo.add_activity(
                Activity(
                    lead_id=lead_id,
                    activity_type="analysis_failed",
                    outcome="Needs Review",
                    notes=reason,
                )
            )
            raise

        deterministic_flags = rules.detect_risk_flags(lead)
        merged_flags = rules.merge_risk_flags(output.risk_flags, deterministic_flags)
        next_action = rules.enforce_risk_action(merged_flags, output.next_action)

        created_at = utc_now()
        follow_up_at = created_at + timedelta(days=int(output.follow_up_days))

        analysis = AIAnalysis(
            lead_id=lead_id,
            prompt_version=PROMPT_VERSION,
            intent=output.intent,
            summary=output.summary,
            priority=output.priority,
            priority_rationale=output.priority_rationale,
            buying_signal=output.buying_signal,
            sentiment=output.sentiment,
            risk_flags=merged_flags,
            next_action=next_action,
            follow_up_at=follow_up_at,
            confidence=output.confidence,
            created_at=created_at,
        )
        self.repo.save_analysis(analysis)

        draft = DraftMessage(
            lead_id=lead_id,
            analysis_id=analysis.analysis_id,
            channel="email",
            subject=output.draft_subject,
            body=output.draft_body,
            status="draft",
            created_at=created_at,
        )
        self.repo.save_draft(draft)

        needs_review = rules.has_risk(merged_flags) or output.confidence == Confidence.LOW
        reason = None
        if rules.has_risk(merged_flags):
            flags = ", ".join(str(f) for f in merged_flags if str(f) != "NONE")
            reason = f"Risk flags detected: {flags}"
        elif output.confidence == Confidence.LOW:
            reason = "Low AI confidence; please review"
        self.repo.set_review_state(lead_id, needs_review, reason)
        self.repo.add_activity(
            Activity(
                lead_id=lead_id,
                activity_type="analysis_completed",
                outcome=str(analysis.priority),
                notes=f"Next action: {analysis.next_action}",
            )
        )

        return AnalysisResult(
            analysis=analysis,
            draft=draft,
            needs_review=needs_review,
            risk_flags=merged_flags,
        )

    # ----------------------------------------------------------------- drafts
    def get_draft(self, draft_id: str) -> DraftMessage:
        draft = self.repo.get_draft(draft_id)
        if draft is None:
            raise DraftNotFound(f"No draft with id {draft_id}")
        return draft

    def list_lead_drafts(self, lead_id: str) -> List[DraftMessage]:
        return self.repo.list_drafts(lead_id)

    def save_draft_edit(self, draft_id: str, user_edited_body: str) -> DraftMessage:
        """Store the user's edit separately; the original AI draft is preserved."""
        draft = self.get_draft(draft_id)
        text = (user_edited_body or "").strip()
        if not text:
            raise LeadValidationError(["draft body must not be empty"])
        self.repo.update_draft_user_body(draft_id, text)
        self.repo.add_activity(
            Activity(
                lead_id=draft.lead_id,
                activity_type="draft_edited",
                notes="User edited the AI draft; original AI draft preserved",
            )
        )
        return self.get_draft(draft_id)

    def copy_draft(self, draft_id: str) -> str:
        """Return the effective draft text and record a copy.

        This only marks the draft as copied; it never sends anything and never
        marks the draft as sent.
        """
        draft = self.get_draft(draft_id)
        text = draft.effective_body
        self.repo.mark_draft_copied(draft_id, utc_now())
        self.repo.add_activity(
            Activity(
                lead_id=draft.lead_id,
                activity_type="draft_copied",
                outcome="Copied",
                notes="Copied for the user's own channel; not sent by this app",
            )
        )
        return text

    # ------------------------------------------------------------- activities
    def record_activity(
        self,
        lead_id: str,
        activity_type: str,
        outcome: Optional[str] = None,
        notes: Optional[str] = None,
        stage: Optional[Stage] = None,
        response_status: Optional[ResponseStatus] = None,
        update_contact: bool = True,
    ) -> Activity:
        lead = self.get_lead(lead_id)
        activity = Activity(
            lead_id=lead_id,
            activity_type=activity_type,
            outcome=outcome,
            notes=notes,
        )
        self.repo.add_activity(activity)
        if stage is not None or response_status is not None or update_contact:
            self.repo.update_lead_stage(
                lead_id,
                stage or lead.stage,
                response_status or lead.response_status,
                last_contact_at=utc_now() if update_contact else None,
            )
        return activity

    # --------------------------------------------------------- business profile
    def get_business_profile(self) -> BusinessProfile:
        return self.repo.get_business_profile() or BusinessProfile()

    def save_business_profile(self, profile: BusinessProfile) -> BusinessProfile:
        return self.repo.save_business_profile(profile)

    # ------------------------------------------------------------------- demo
    def reset_demo_data(self, *, include_profile: bool = False) -> None:
        self.repo.delete_all()
        if include_profile:
            self.repo.save_business_profile(BusinessProfile())
