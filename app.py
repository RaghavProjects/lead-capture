"""Streamlit entry point for the AI Lead Follow-Up Assistant (Sprint 1)."""
from __future__ import annotations

import streamlit as st

from config import settings
from data.repository import SQLiteRepository
from models.schemas import Lead, ResponseStatus, Stage
from services.ai import AIUnavailable, AnalysisValidationError
from services.importer import import_leads
from services.leads import LeadService, LeadValidationError
from scripts.seed import seed

st.set_page_config(page_title="AI Lead Follow-Up Assistant", page_icon="📋", layout="wide")


@st.cache_resource
def get_service() -> LeadService:
    repo = SQLiteRepository(settings.db_path)
    return LeadService(repo)


service = get_service()


def lead_table_rows() -> list[dict]:
    rows = []
    for lead in service.list_leads():
        analysis = service.repo.get_latest_analysis(lead.lead_id)
        rows.append(
            {
                "Lead ID": lead.lead_id,
                "Name": lead.name,
                "Company": lead.company or "",
                "Stage": str(lead.stage),
                "Source": lead.source or "",
                "Received": lead.received_at.date().isoformat() if lead.received_at else "",
                "Priority": str(analysis.priority) if analysis else "",
                "Next action": str(analysis.next_action) if analysis else "",
                "Follow-up": analysis.follow_up_at.date().isoformat() if analysis else "",
                "Needs review": "Yes" if lead.needs_review else "",
            }
        )
    return rows


def render_analysis(lead_id: str, key_prefix: str = "leads") -> None:
    analysis = service.repo.get_latest_analysis(lead_id)
    if analysis is None:
        st.info("No AI analysis yet. Click **Analyze lead** to generate one.")
        return
    draft = service.repo.get_draft_for_analysis(analysis.analysis_id)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Priority", str(analysis.priority))
    col2.metric("Buying signal", str(analysis.buying_signal))
    col3.metric("Confidence", str(analysis.confidence))
    col4.metric("Next action", str(analysis.next_action))

    if any(str(f) != "NONE" for f in analysis.risk_flags):
        st.error("Risk flags: " + ", ".join(str(f) for f in analysis.risk_flags) + " — route to human review.")

    st.markdown(f"**Summary.** {analysis.summary}")
    st.markdown(f"**Why this priority.** {analysis.priority_rationale}")
    st.caption(
        f"Intent: {analysis.intent} · Sentiment: {analysis.sentiment} · "
        f"Follow-up: {analysis.follow_up_at.date().isoformat()} · Prompt: {analysis.prompt_version}"
    )

    if draft:
        st.subheader("Draft follow-up (not sent)")
        status_line = f"Status: {draft.status}"
        if draft.copied_at:
            status_line += f" · copied {draft.copied_at.isoformat()}"
        st.caption(status_line)

        st.text_input("Subject", value=draft.subject, key=f"subject_{key_prefix}_{draft.draft_id}", disabled=True)
        current_body = draft.effective_body
        edited = st.text_area("Your draft (editable)", value=current_body, height=220, key=f"body_{key_prefix}_{draft.draft_id}")

        c1, c2 = st.columns([1, 1])
        if c1.button("Save edit", key=f"save_{key_prefix}_{draft.draft_id}"):
            try:
                service.save_draft_edit(draft.draft_id, edited)
                st.success("Edit saved. The original AI draft is preserved.")
                st.rerun()
            except LeadValidationError as exc:
                st.error("; ".join(exc.errors))
        if c2.button("Mark as copied", key=f"copy_{key_prefix}_{draft.draft_id}"):
            service.copy_draft(draft.draft_id)
            st.info("Recorded as copied. Nothing was sent — paste it into your own email tool.")
            st.rerun()

        with st.expander("Draft to copy (use the copy icon)"):
            st.code(f"Subject: {draft.subject}\n\n{current_body}", language=None)
        with st.expander("Original AI draft (read-only, always preserved)"):
            st.text(draft.body)
        if draft.is_edited:
            st.caption("Showing your saved edit. The original AI draft is retained above.")
        st.caption("This app never sends messages. Copy the draft into your own channel.")

    with st.expander("Activity history"):
        activities = service.repo.list_activities(lead_id)
        if not activities:
            st.write("No activity recorded yet.")
        for activity in activities:
            stamp = activity.occurred_at.isoformat() if activity.occurred_at else ""
            st.write(f"{stamp} — **{activity.activity_type}** {activity.outcome or ''} {activity.notes or ''}")


def render_lead_detail(lead_id: str, key_prefix: str) -> None:
    lead = service.get_lead(lead_id)
    st.subheader(f"{lead.name} · {lead.company or '—'}")
    st.write(
        {
            "Lead ID": lead.lead_id,
            "Email": lead.email,
            "Phone": lead.phone,
            "Source": lead.source,
            "Stage": str(lead.stage),
            "Response status": str(lead.response_status),
            "Received": lead.received_at.isoformat() if lead.received_at else "",
            "Last contact": lead.last_contact_at.isoformat() if lead.last_contact_at else "",
            "Next follow-up": lead.next_follow_up_at.isoformat() if lead.next_follow_up_at else "",
            "Notes": lead.notes,
        }
    )
    st.markdown(f"**Inquiry.** {lead.inquiry}")

    if lead.needs_review:
        st.warning(f"Needs review: {lead.review_reason or 'manual review required'}")

    profile_warnings = service.profile_warnings()
    if profile_warnings:
        st.warning(
            "Business profile is incomplete; drafts may be generic. "
            "Update it in Settings: " + "; ".join(profile_warnings)
        )

    if st.button("Analyze lead", type="primary", key=f"analyze_{key_prefix}_{lead_id}"):
        try:
            result = service.analyze_lead(lead.lead_id)
            if result.needs_review:
                st.warning("Analysis complete — flagged for human review.")
            else:
                st.success("Analysis complete.")
            st.rerun()
        except LeadValidationError as exc:
            st.error("Cannot analyze: " + "; ".join(exc.errors))
        except AIUnavailable as exc:
            st.error(f"Analysis unavailable. The lead was preserved and marked Needs Review. ({exc})")
        except AnalysisValidationError as exc:
            st.error(f"AI output failed validation. The lead was preserved and marked Needs Review. ({exc})")

    render_analysis(lead_id, key_prefix)

    with st.expander("Record outcome", expanded=False):
        activity_type = st.selectbox(
            "Activity",
            ["Email sent", "Call", "Meeting", "Note", "Proposal sent", "No response"],
            key=f"activity_{key_prefix}_{lead_id}",
        )
        outcome = st.text_input("Outcome", key=f"outcome_{key_prefix}_{lead_id}")
        notes = st.text_area("Notes", height=80, key=f"notes_{key_prefix}_{lead_id}")
        new_stage = st.selectbox(
            "Update stage", ["(unchanged)"] + [str(s) for s in Stage], key=f"stage_{key_prefix}_{lead_id}"
        )
        new_status = st.selectbox(
            "Update response status",
            ["(unchanged)"] + [str(s) for s in ResponseStatus],
            key=f"status_{key_prefix}_{lead_id}",
        )
        if st.button("Save activity", key=f"save_{key_prefix}_{lead_id}"):
            _, next_follow_up = service.record_outcome(
                lead.lead_id,
                activity_type,
                outcome=outcome or None,
                notes=notes or None,
                stage=Stage(new_stage) if new_stage != "(unchanged)" else None,
                response_status=ResponseStatus(new_status) if new_status != "(unchanged)" else None,
                update_contact=activity_type != "Note",
            )
            if next_follow_up:
                st.success(f"Activity recorded. Next follow-up recalculated: {next_follow_up.date().isoformat()}.")
            else:
                st.success("Activity recorded. Lead is closed; no follow-up scheduled.")
            st.rerun()


st.title("AI Lead Follow-Up Assistant")
st.caption("Human-in-the-loop, draft-only prototype. Nothing is sent automatically.")

with st.sidebar:
    st.header("Session")
    st.write(f"Provider: **{settings.ai_provider}**")
    st.write(f"Database: `{settings.db_path}`")
    st.write(f"Leads stored: **{service.repo.count_leads()}**")
    if st.button("Seed / reset sample data"):
        result = seed(reset=True)
        st.cache_resource.clear()
        st.success(f"Imported {result['imported']} leads.")
        st.rerun()
    if st.button("Reset all demo data"):
        service.reset_demo_data(include_profile=True)
        st.cache_resource.clear()
        st.warning("Demo data cleared.")
        st.rerun()

tab_today, tab_dashboard, tab_leads, tab_import, tab_new, tab_settings = st.tabs(
    ["Today", "Dashboard", "Leads", "Import", "New lead", "Settings"]
)

with tab_today:
    st.subheader("Today — who needs attention")
    queue = service.list_attention_queue()
    metric_cols = st.columns(len(queue.counts))
    for col, (name, count) in zip(metric_cols, queue.counts.items()):
        col.metric(name, count)

    for name in ["Overdue", "Due Today", "New Hot Lead", "Stale", "Needs Review"]:
        entries = queue.buckets.get(name, [])
        if not entries:
            continue
        st.markdown(f"#### {name} ({len(entries)})")
        st.dataframe(
            [
                {
                    "Lead ID": e.lead.lead_id,
                    "Name": e.lead.name,
                    "Company": e.lead.company or "",
                    "Stage": str(e.lead.stage),
                    "Priority": str(e.analysis.priority) if e.analysis else "",
                    "Next action": str(e.analysis.next_action) if e.analysis else "",
                    "Follow-up": e.follow_up_at.date().isoformat() if e.follow_up_at else "",
                    "Reason": ", ".join(e.queues),
                }
                for e in entries
            ],
            use_container_width=True,
            hide_index=True,
        )
    st.caption("Won / Lost / Not a Lead leads are excluded from all attention buckets.")

with tab_dashboard:
    st.subheader("Pipeline dashboard")
    f1, f2, f3, f4 = st.columns(4)
    stage_f = f1.selectbox("Stage", ["All"] + [str(s) for s in Stage], key="dash_stage")
    priority_f = f2.selectbox("Priority", ["All", "High", "Medium", "Low"], key="dash_priority")
    source_f = f3.selectbox("Source", ["All"] + service.available_sources(), key="dash_source")
    review_f = f4.selectbox("Review", ["All", "Needs review", "OK"], key="dash_review")
    search_f = st.text_input("Search", key="dash_search")

    filters = dict(
        search=search_f or None,
        stage=None if stage_f == "All" else stage_f,
        priority=priority_f,
        source=source_f,
        needs_review={"All": None, "Needs review": True, "OK": False}[review_f],
    )
    leads = service.filtered_leads(**filters)
    summary = service.pipeline_summary(**filters)

    m1, m2, m3 = st.columns(3)
    m1.metric("Filtered leads", summary["total"])
    m2.metric("Needs review", summary["needs_review"])
    m3.metric("Unanalyzed", summary["by_priority"].get("Unanalyzed", 0))

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**By priority**")
        st.dataframe(
            [{"Priority": k, "Count": v} for k, v in sorted(summary["by_priority"].items())],
            use_container_width=True, hide_index=True,
        )
    with c2:
        st.markdown("**By stage**")
        st.dataframe(
            [{"Stage": k, "Count": v} for k, v in sorted(summary["by_stage"].items())],
            use_container_width=True, hide_index=True,
        )

    st.markdown(f"**Filtered leads ({len(leads)})**")
    st.dataframe(
        [
            {
                "Lead ID": lead.lead_id,
                "Name": lead.name,
                "Company": lead.company or "",
                "Stage": str(lead.stage),
                "Priority": str(service._priority_of(lead) or ""),
                "Source": lead.source or "",
                "Needs review": "Yes" if lead.needs_review else "",
            }
            for lead in leads
        ],
        use_container_width=True,
        hide_index=True,
    )
    st.caption("Counts and the table are derived from the same filtered set, so they always reconcile.")

    if leads:
        options = {f"{lead.lead_id} — {lead.name}": lead.lead_id for lead in leads}
        selected = st.selectbox("Open lead detail", list(options.keys()), key="dash_open")
        render_lead_detail(options[selected], key_prefix="dash")
    else:
        st.info("No leads match the current filters.")

with tab_leads:
    search = st.text_input("Search leads", placeholder="Name, company or inquiry")
    stage_filter = st.selectbox("Stage", ["All"] + [str(s) for s in Stage])
    rows = lead_table_rows()
    if search:
        needle = search.lower()
        rows = [
            row for row in rows
            if needle in row["Name"].lower() or needle in row["Company"].lower() or needle in row["Lead ID"].lower()
        ]
    if stage_filter != "All":
        rows = [row for row in rows if row["Stage"] == stage_filter]
    st.dataframe(rows, use_container_width=True, hide_index=True)

    if rows:
        options = {f"{r['Lead ID']} — {r['Name']}": r["Lead ID"] for r in rows}
        selected_label = st.selectbox("Open a lead", list(options.keys()))
        selected_id = options[selected_label]
        lead = service.get_lead(selected_id)
        render_lead_detail(lead.lead_id, key_prefix="leads")

with tab_import:
    st.subheader("Import leads from CSV or XLSX")
    uploaded = st.file_uploader("Choose a file", type=["csv", "xlsx", "xlsm"])
    if uploaded is not None and st.button("Import file"):
        try:
            report = import_leads(uploaded, service.repo, filename=uploaded.name)
            if report.missing_columns:
                st.error("Missing required columns: " + ", ".join(report.missing_columns))
            st.success(report.summary())
            if report.errors:
                st.write("Rejected rows")
                st.dataframe(
                    [{"Row": e.row, "Error": e.message} for e in report.errors],
                    use_container_width=True,
                    hide_index=True,
                )
        except (ValueError, RuntimeError) as exc:
            st.error(str(exc))

with tab_new:
    st.subheader("Add a lead manually")
    with st.form("new_lead"):
        name = st.text_input("Name *")
        company = st.text_input("Company")
        email = st.text_input("Email")
        phone = st.text_input("Phone")
        source = st.text_input("Lead source", value="Manual")
        inquiry = st.text_area("Inquiry *", height=120)
        stage = st.selectbox("Stage", [str(s) for s in Stage], index=0)
        response_status = st.selectbox("Response status", [str(s) for s in ResponseStatus], index=0)
        notes = st.text_area("Notes", height=80)
        submitted = st.form_submit_button("Create lead")

    if submitted:
        try:
            lead = service.create_lead(
                Lead(
                    name=name,
                    company=company or None,
                    email=email or None,
                    phone=phone or None,
                    source=source or None,
                    inquiry=inquiry,
                    stage=Stage(stage),
                    response_status=ResponseStatus(response_status),
                    notes=notes or None,
                )
            )
            st.success(f"Lead saved: {lead.lead_id}. It is now available in the Leads tab.")
        except LeadValidationError as exc:
            st.error("Please fix: " + "; ".join(exc.errors))

with tab_settings:
    st.subheader("Business profile")
    if service.profile_warnings():
        st.warning(
            "Profile incomplete; drafts may be generic. Missing: "
            + "; ".join(service.profile_warnings())
        )
    profile = service.get_business_profile()
    with st.form("profile"):
        business_name = st.text_input("Business name", value=profile.business_name)
        description = st.text_area("Description", value=profile.description, height=90)
        services = st.text_input("Services", value=profile.services)
        tone = st.text_input("Tone", value=profile.tone)
        allowed_claims = st.text_area("Allowed claims", value=profile.allowed_claims, height=70)
        restricted_topics = st.text_area("Restricted topics", value=profile.restricted_topics, height=70)
        follow_up_rules = st.text_area("Follow-up rules", value=profile.follow_up_rules, height=70)
        saved = st.form_submit_button("Save profile")
    if saved:
        service.save_business_profile(
            type(profile)(
                business_name=business_name,
                description=description,
                services=services,
                tone=tone,
                allowed_claims=allowed_claims,
                restricted_topics=restricted_topics,
                follow_up_rules=follow_up_rules,
            )
        )
        st.success("Business profile saved. Drafting will use this context.")
