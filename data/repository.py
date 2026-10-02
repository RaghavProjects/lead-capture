"""SQLite persistence and history for the lead assistant."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

from models.schemas import (
    Activity,
    AIAnalysis,
    BusinessProfile,
    DraftMessage,
    Lead,
    ResponseStatus,
    RiskFlag,
    Stage,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    lead_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    company TEXT,
    email TEXT,
    phone TEXT,
    source TEXT,
    inquiry TEXT NOT NULL,
    stage TEXT NOT NULL,
    received_at TEXT NOT NULL,
    last_contact_at TEXT,
    response_status TEXT NOT NULL,
    notes TEXT,
    needs_review INTEGER NOT NULL DEFAULT 0,
    review_reason TEXT
);

CREATE TABLE IF NOT EXISTS analyses (
    analysis_id TEXT PRIMARY KEY,
    lead_id TEXT NOT NULL REFERENCES leads(lead_id) ON DELETE CASCADE,
    prompt_version TEXT NOT NULL,
    intent TEXT NOT NULL,
    summary TEXT NOT NULL,
    priority TEXT NOT NULL,
    priority_rationale TEXT NOT NULL,
    buying_signal TEXT NOT NULL,
    sentiment TEXT NOT NULL,
    risk_flags TEXT NOT NULL,
    next_action TEXT NOT NULL,
    follow_up_at TEXT NOT NULL,
    confidence TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drafts (
    draft_id TEXT PRIMARY KEY,
    lead_id TEXT NOT NULL REFERENCES leads(lead_id) ON DELETE CASCADE,
    analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL,
    user_edited_body TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activities (
    activity_id TEXT PRIMARY KEY,
    lead_id TEXT NOT NULL REFERENCES leads(lead_id) ON DELETE CASCADE,
    activity_type TEXT NOT NULL,
    outcome TEXT,
    notes TEXT,
    occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS business_profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    business_name TEXT NOT NULL,
    description TEXT NOT NULL,
    services TEXT NOT NULL,
    differentiators TEXT,
    tone TEXT NOT NULL,
    service_area TEXT,
    allowed_claims TEXT NOT NULL,
    restricted_topics TEXT NOT NULL,
    business_hours TEXT,
    follow_up_rules TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_analyses_lead ON analyses(lead_id);
CREATE INDEX IF NOT EXISTS idx_drafts_lead ON drafts(lead_id);
CREATE INDEX IF NOT EXISTS idx_activities_lead ON activities(lead_id);
"""


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat()


def _dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value)


class SQLiteRepository:
    def __init__(self, db_path: str):
        self.db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    # ------------------------------------------------------------------ leads
    def upsert_lead(self, lead: Lead) -> Lead:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO leads (lead_id, name, company, email, phone, source, inquiry,
                    stage, received_at, last_contact_at, response_status, notes,
                    needs_review, review_reason)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(lead_id) DO UPDATE SET
                    name=excluded.name, company=excluded.company, email=excluded.email,
                    phone=excluded.phone, source=excluded.source, inquiry=excluded.inquiry,
                    stage=excluded.stage, received_at=excluded.received_at,
                    last_contact_at=excluded.last_contact_at,
                    response_status=excluded.response_status, notes=excluded.notes,
                    needs_review=excluded.needs_review, review_reason=excluded.review_reason
                """,
                (
                    lead.lead_id, lead.name, lead.company, lead.email, lead.phone,
                    lead.source, lead.inquiry, str(lead.stage), _iso(lead.received_at),
                    _iso(lead.last_contact_at), str(lead.response_status), lead.notes,
                    int(lead.needs_review), lead.review_reason,
                ),
            )
        return lead

    def lead_exists(self, lead_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM leads WHERE lead_id = ?", (lead_id,)
            ).fetchone()
        return row is not None

    def _row_to_lead(self, row: sqlite3.Row) -> Lead:
        return Lead(
            lead_id=row["lead_id"],
            name=row["name"],
            company=row["company"],
            email=row["email"],
            phone=row["phone"],
            source=row["source"],
            inquiry=row["inquiry"],
            stage=Stage(row["stage"]),
            received_at=_dt(row["received_at"]),
            last_contact_at=_dt(row["last_contact_at"]),
            response_status=ResponseStatus(row["response_status"]),
            notes=row["notes"],
            needs_review=bool(row["needs_review"]),
            review_reason=row["review_reason"],
        )

    def get_lead(self, lead_id: str) -> Optional[Lead]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM leads WHERE lead_id = ?", (lead_id,)
            ).fetchone()
        return self._row_to_lead(row) if row else None

    def list_leads(
        self,
        search: Optional[str] = None,
        stage: Optional[str] = None,
        priority: Optional[str] = None,
    ) -> List[Lead]:
        query = "SELECT * FROM leads"
        clauses: List[str] = []
        params: List[object] = []
        if search:
            clauses.append("(name LIKE ? OR company LIKE ? OR inquiry LIKE ? OR lead_id LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like, like, like])
        if stage:
            clauses.append("stage = ?")
            params.append(stage)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY received_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        leads = [self._row_to_lead(r) for r in rows]
        if priority:
            leads = [lead for lead in leads if self.get_latest_analysis(lead.lead_id) and
                     str(self.get_latest_analysis(lead.lead_id).priority) == priority]
        return leads

    def set_review_state(self, lead_id: str, needs_review: bool, reason: Optional[str]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE leads SET needs_review = ?, review_reason = ? WHERE lead_id = ?",
                (int(needs_review), reason, lead_id),
            )

    def update_lead_stage(self, lead_id: str, stage: Stage, response_status: Optional[ResponseStatus] = None,
                          last_contact_at: Optional[datetime] = None) -> None:
        with self._connect() as conn:
            fields = ["stage = ?"]
            params: List[object] = [str(stage)]
            if response_status is not None:
                fields.append("response_status = ?")
                params.append(str(response_status))
            if last_contact_at is not None:
                fields.append("last_contact_at = ?")
                params.append(_iso(last_contact_at))
            params.append(lead_id)
            conn.execute(f"UPDATE leads SET {', '.join(fields)} WHERE lead_id = ?", params)

    # --------------------------------------------------------------- analyses
    def save_analysis(self, analysis: AIAnalysis) -> AIAnalysis:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO analyses (analysis_id, lead_id, prompt_version, intent, summary,
                    priority, priority_rationale, buying_signal, sentiment, risk_flags,
                    next_action, follow_up_at, confidence, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    analysis.analysis_id, analysis.lead_id, analysis.prompt_version,
                    analysis.intent, analysis.summary, str(analysis.priority),
                    analysis.priority_rationale, str(analysis.buying_signal),
                    str(analysis.sentiment),
                    json.dumps([str(f) for f in analysis.risk_flags]),
                    str(analysis.next_action), _iso(analysis.follow_up_at),
                    str(analysis.confidence), _iso(analysis.created_at),
                ),
            )
        return analysis

    def _row_to_analysis(self, row: sqlite3.Row) -> AIAnalysis:
        return AIAnalysis(
            analysis_id=row["analysis_id"],
            lead_id=row["lead_id"],
            prompt_version=row["prompt_version"],
            intent=row["intent"],
            summary=row["summary"],
            priority=row["priority"],
            priority_rationale=row["priority_rationale"],
            buying_signal=row["buying_signal"],
            sentiment=row["sentiment"],
            risk_flags=[RiskFlag(f) for f in json.loads(row["risk_flags"])],
            next_action=row["next_action"],
            follow_up_at=_dt(row["follow_up_at"]),
            confidence=row["confidence"],
            created_at=_dt(row["created_at"]),
        )

    def get_latest_analysis(self, lead_id: str) -> Optional[AIAnalysis]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM analyses WHERE lead_id = ? ORDER BY created_at DESC LIMIT 1",
                (lead_id,),
            ).fetchone()
        return self._row_to_analysis(row) if row else None

    def list_analyses(self, lead_id: str) -> List[AIAnalysis]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM analyses WHERE lead_id = ? ORDER BY created_at DESC",
                (lead_id,),
            ).fetchall()
        return [self._row_to_analysis(r) for r in rows]

    # ----------------------------------------------------------------- drafts
    def save_draft(self, draft: DraftMessage) -> DraftMessage:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO drafts (draft_id, lead_id, analysis_id, channel, subject, body,
                    status, user_edited_body, created_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    draft.draft_id, draft.lead_id, draft.analysis_id, draft.channel,
                    draft.subject, draft.body, draft.status, draft.user_edited_body,
                    _iso(draft.created_at),
                ),
            )
        return draft

    def _row_to_draft(self, row: sqlite3.Row) -> DraftMessage:
        return DraftMessage(
            draft_id=row["draft_id"],
            lead_id=row["lead_id"],
            analysis_id=row["analysis_id"],
            channel=row["channel"],
            subject=row["subject"],
            body=row["body"],
            status=row["status"],
            user_edited_body=row["user_edited_body"],
            created_at=_dt(row["created_at"]),
        )

    def get_draft_for_analysis(self, analysis_id: str) -> Optional[DraftMessage]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM drafts WHERE analysis_id = ? ORDER BY created_at DESC LIMIT 1",
                (analysis_id,),
            ).fetchone()
        return self._row_to_draft(row) if row else None

    def update_draft_user_body(self, draft_id: str, user_edited_body: str) -> None:
        """Store the user edit separately; the original AI body is never overwritten."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE drafts SET user_edited_body = ?, status = 'edited' WHERE draft_id = ?",
                (user_edited_body, draft_id),
            )

    # ------------------------------------------------------------- activities
    def add_activity(self, activity: Activity) -> Activity:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO activities (activity_id, lead_id, activity_type, outcome, notes, occurred_at)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    activity.activity_id, activity.lead_id, activity.activity_type,
                    activity.outcome, activity.notes, _iso(activity.occurred_at),
                ),
            )
        return activity

    def list_activities(self, lead_id: str) -> List[Activity]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM activities WHERE lead_id = ? ORDER BY occurred_at DESC",
                (lead_id,),
            ).fetchall()
        return [
            Activity(
                activity_id=r["activity_id"],
                lead_id=r["lead_id"],
                activity_type=r["activity_type"],
                outcome=r["outcome"],
                notes=r["notes"],
                occurred_at=_dt(r["occurred_at"]),
            )
            for r in rows
        ]

    # --------------------------------------------------------- business profile
    def get_business_profile(self) -> Optional[BusinessProfile]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM business_profile WHERE id = 1").fetchone()
        if not row:
            return None
        return BusinessProfile(
            business_name=row["business_name"],
            description=row["description"],
            services=row["services"],
            differentiators=row["differentiators"],
            tone=row["tone"],
            service_area=row["service_area"],
            allowed_claims=row["allowed_claims"],
            restricted_topics=row["restricted_topics"],
            business_hours=row["business_hours"],
            follow_up_rules=row["follow_up_rules"],
        )

    def save_business_profile(self, profile: BusinessProfile) -> BusinessProfile:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO business_profile (id, business_name, description, services,
                    differentiators, tone, service_area, allowed_claims, restricted_topics,
                    business_hours, follow_up_rules)
                VALUES (1,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    business_name=excluded.business_name, description=excluded.description,
                    services=excluded.services, differentiators=excluded.differentiators,
                    tone=excluded.tone, service_area=excluded.service_area,
                    allowed_claims=excluded.allowed_claims,
                    restricted_topics=excluded.restricted_topics,
                    business_hours=excluded.business_hours,
                    follow_up_rules=excluded.follow_up_rules
                """,
                (
                    profile.business_name, profile.description, profile.services,
                    profile.differentiators, profile.tone, profile.service_area,
                    profile.allowed_claims, profile.restricted_topics,
                    profile.business_hours, profile.follow_up_rules,
                ),
            )
        return profile

    # ------------------------------------------------------------------ admin
    def count_leads(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) AS c FROM leads").fetchone()["c"])

    def delete_all(self) -> None:
        with self._connect() as conn:
            for table in ("drafts", "analyses", "activities", "leads"):
                conn.execute(f"DELETE FROM {table}")
