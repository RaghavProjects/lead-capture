"""Vercel-compatible Flask UI for the AI Lead Follow-Up Assistant.

This reuses the framework-agnostic domain layer (models, services, data,
prompts) and replaces only the Streamlit presentation layer so the app can run
as a Vercel Python serverless function.

Storage note: Vercel's filesystem is read-only except /tmp, and function
instances are ephemeral. On cold start we seed the bundled synthetic data into
/tmp so the demo works with zero external services; data resets per instance.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Optional

# Configure the environment before importing config/settings.
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/lead_assistant.db")
os.environ.setdefault("AI_PROVIDER", os.environ.get("AI_PROVIDER", "mock"))

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from flask import Flask, Response, flash, redirect, request, url_for  # noqa: E402

from config import settings  # noqa: E402
from data.repository import SQLiteRepository  # noqa: E402
from models.schemas import BusinessProfile, Lead, ResponseStatus, Stage  # noqa: E402
from services.ai import AIUnavailable, AnalysisValidationError  # noqa: E402
from services.importer import import_leads  # noqa: E402
from services.leads import LeadService, LeadValidationError  # noqa: E402

SAMPLE_CSV = REPO_ROOT / "sample_data" / "sample_leads.csv"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "prototype-draft-only-not-a-secret")

_service: Optional[LeadService] = None


# --------------------------------------------------------------------- helpers
def get_service() -> LeadService:
    global _service
    if _service is None:
        repo = SQLiteRepository(settings.db_path)
        service = LeadService(repo, app_settings=settings)
        if repo.count_leads() == 0:
            repo.save_business_profile(BusinessProfile())
            if SAMPLE_CSV.exists():
                import_leads(str(SAMPLE_CSV), repo, filename="sample_leads.csv")
                for lead in repo.list_leads():
                    try:
                        service.analyze_lead(lead.lead_id)
                    except Exception:  # analysis failure must never break the demo
                        pass
        _service = service
    return _service


def e(value) -> str:
    return escape(str(value)) if value is not None else ""


def fmt_date(value) -> str:
    return value.date().isoformat() if isinstance(value, datetime) else (e(value) or "")


BASE_CSS = """
:root { --bg:#0f172a; --card:#ffffff; --muted:#64748b; --line:#e2e8f0; --accent:#2563eb; }
* { box-sizing: border-box; }
body { margin:0; font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; background:#f1f5f9; color:#0f172a; }
header { background:var(--bg); color:#fff; padding:14px 20px; }
header h1 { margin:0 0 6px; font-size:18px; }
nav a { color:#cbd5e1; margin-right:14px; text-decoration:none; font-size:14px; }
nav a:hover { color:#fff; }
main { max-width:1100px; margin:20px auto; padding:0 16px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:16px; margin-bottom:16px; }
table { border-collapse:collapse; width:100%; font-size:13px; }
th, td { border-bottom:1px solid var(--line); text-align:left; padding:6px 8px; }
th { background:#f8fafc; }
.metrics { display:flex; gap:12px; flex-wrap:wrap; }
.metric { background:#fff; border:1px solid var(--line); border-radius:8px; padding:12px 16px; min-width:120px; }
.metric b { display:block; font-size:22px; }
.muted { color:var(--muted); font-size:12px; }
.flash { padding:10px 12px; border-radius:6px; margin-bottom:10px; }
.flash.success { background:#dcfce7; color:#166534; }
.flash.error { background:#fee2e2; color:#991b1b; }
.flash.info { background:#dbeafe; color:#1e40af; }
.btn { display:inline-block; background:var(--accent); color:#fff; border:0; border-radius:6px; padding:8px 12px; cursor:pointer; text-decoration:none; font-size:13px; }
.btn.secondary { background:#475569; }
.btn.danger { background:#b91c1c; }
textarea, input, select { width:100%; padding:8px; border:1px solid var(--line); border-radius:6px; font:inherit; margin:4px 0 10px; }
label { font-size:13px; font-weight:600; }
pre.draft { white-space:pre-wrap; background:#f8fafc; border:1px solid var(--line); border-radius:6px; padding:12px; font-size:13px; }
.row { display:flex; gap:16px; flex-wrap:wrap; }
.row > div { flex:1; min-width:220px; }
.badge { display:inline-block; padding:2px 8px; border-radius:999px; background:#e2e8f0; font-size:11px; }
.risk { background:#fee2e2; color:#991b1b; }
footer { text-align:center; color:var(--muted); font-size:12px; padding:24px; }
"""


def layout(title: str, body: str) -> str:
    messages = ""
    from flask import get_flashed_messages

    for category, message in get_flashed_messages(with_categories=True):
        messages += f'<div class="flash {e(category)}">{e(message)}</div>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)} · AI Lead Follow-Up Assistant</title>
<style>{BASE_CSS}</style></head>
<body>
<header>
  <h1>AI Lead Follow-Up Assistant</h1>
  <nav>
    <a href="{url_for('today')}">Today</a>
    <a href="{url_for('dashboard')}">Dashboard</a>
    <a href="{url_for('leads')}">Leads</a>
    <a href="{url_for('import_view')}">Import</a>
    <a href="{url_for('new_lead')}">New lead</a>
    <a href="{url_for('settings_view')}">Settings</a>
  </nav>
</header>
<main>{messages}{body}</main>
<footer>Human-in-the-loop · draft-only prototype · nothing is sent automatically</footer>
</body></html>"""


def risk_badge(analysis) -> str:
    if analysis is None:
        return ""
    flags = [str(f) for f in analysis.risk_flags if str(f) != "NONE"]
    if not flags:
        return ""
    return f'<span class="badge risk">{e(", ".join(flags))}</span>'


# ---------------------------------------------------------------------- routes
@app.get("/")
def today():
    service = get_service()
    queue = service.list_attention_queue()
    metrics = "".join(
        f'<div class="metric"><span class="muted">{e(name)}</span><b>{count}</b></div>'
        for name, count in queue.counts.items()
    )
    sections = ""
    for name in ["Overdue", "Due Today", "New Hot Lead", "Stale", "Needs Review"]:
        entries = queue.buckets.get(name, [])
        if not entries:
            continue
        rows = ""
        for entry in entries:
            analysis = entry.analysis
            rows += (
                f"<tr><td><a href='{url_for('lead_detail', lead_id=entry.lead.lead_id)}'>{e(entry.lead.lead_id)}</a></td>"
                f"<td>{e(entry.lead.name)}</td><td>{e(entry.lead.company or '')}</td>"
                f"<td>{e(entry.lead.stage)}</td>"
                f"<td>{e(analysis.priority) if analysis else ''}</td>"
                f"<td>{e(analysis.next_action) if analysis else ''}</td>"
                f"<td>{fmt_date(entry.follow_up_at)}</td>"
                f"<td>{e(', '.join(entry.queues))}</td></tr>"
            )
        sections += (
            f"<div class='card'><h3>{e(name)} ({len(entries)})</h3><table>"
            "<tr><th>Lead</th><th>Name</th><th>Company</th><th>Stage</th><th>Priority</th>"
            "<th>Next action</th><th>Follow-up</th><th>Reason</th></tr>"
            f"{rows}</table></div>"
        )
    body = (
        "<div class='card'><h2>Today — who needs attention</h2>"
        f"<div class='metrics'>{metrics}</div>"
        "<p class='muted'>Won / Lost / Not a Lead leads are excluded from all buckets.</p></div>"
        + sections
    )
    return Response(layout("Today", body), mimetype="text/html")


@app.get("/dashboard")
def dashboard():
    service = get_service()
    stage_f = request.args.get("stage", "All")
    priority_f = request.args.get("priority", "All")
    source_f = request.args.get("source", "All")
    review_f = request.args.get("review", "All")
    search_f = request.args.get("search", "")

    filters = dict(
        search=search_f or None,
        stage=None if stage_f == "All" else stage_f,
        priority=priority_f,
        source=source_f,
        needs_review={"All": None, "Needs review": True, "OK": False}[review_f],
    )
    leads = service.filtered_leads(**filters)
    summary = service.pipeline_summary(**filters)

    def options(name, values, selected):
        opts = "".join(
            f"<option value='{e(v)}' {'selected' if v == selected else ''}>{e(v)}</option>"
            for v in values
        )
        return f"<label>{e(name)}</label><select name='{name.lower()}'>{opts}</select>"

    form = (
        "<form method='get' class='card'><div class='row'>"
        f"<div>{options('Stage', ['All'] + [str(s) for s in Stage], stage_f)}</div>"
        f"<div>{options('Priority', ['All', 'High', 'Medium', 'Low'], priority_f)}</div>"
        f"<div>{options('Source', ['All'] + service.available_sources(), source_f)}</div>"
        f"<div>{options('Review', ['All', 'Needs review', 'OK'], review_f)}</div>"
        f"<div><label>Search</label><input name='search' value='{e(search_f)}'></div>"
        "</div><button class='btn' type='submit'>Apply filters</button></form>"
    )

    rows = "".join(
        f"<tr><td><a href='{url_for('lead_detail', lead_id=lead.lead_id)}'>{e(lead.lead_id)}</a></td>"
        f"<td>{e(lead.name)}</td><td>{e(lead.company or '')}</td><td>{e(lead.stage)}</td>"
        f"<td>{e(service._priority_of(lead) or '')}</td><td>{e(lead.source or '')}</td>"
        f"<td>{'Yes' if lead.needs_review else ''}</td></tr>"
        for lead in leads
    )

    body = (
        "<h2>Pipeline dashboard</h2>"
        + form
        + "<div class='metrics'>"
        f"<div class='metric'><span class='muted'>Filtered leads</span><b>{summary['total']}</b></div>"
        f"<div class='metric'><span class='muted'>Needs review</span><b>{summary['needs_review']}</b></div>"
        f"<div class='metric'><span class='muted'>Unanalyzed</span><b>{summary['by_priority'].get('Unanalyzed', 0)}</b></div>"
        "</div><div class='card'><h3>Filtered leads</h3><table>"
        "<tr><th>Lead</th><th>Name</th><th>Company</th><th>Stage</th><th>Priority</th><th>Source</th><th>Needs review</th></tr>"
        f"{rows}</table><p class='muted'>Counts and this table come from the same filtered set, so they always reconcile.</p></div>"
    )
    return Response(layout("Dashboard", body), mimetype="text/html")


@app.get("/leads")
def leads():
    service = get_service()
    search = request.args.get("search", "")
    stage_f = request.args.get("stage", "All")
    rows_data = service.filtered_leads(
        search=search or None, stage=None if stage_f == "All" else stage_f
    )
    stage_opts = "".join(
        f"<option value='{e(s)}' {'selected' if s == stage_f else ''}>{e(s)}</option>"
        for s in ["All"] + [str(x) for x in Stage]
    )
    form = (
        "<form method='get' class='card'><div class='row'>"
        f"<div><label>Search</label><input name='search' value='{e(search)}'></div>"
        f"<div><label>Stage</label><select name='stage'>{stage_opts}</select></div>"
        "</div><button class='btn' type='submit'>Filter</button></form>"
    )
    rows = "".join(
        f"<tr><td><a href='{url_for('lead_detail', lead_id=lead.lead_id)}'>{e(lead.lead_id)}</a></td>"
        f"<td>{e(lead.name)}</td><td>{e(lead.company or '')}</td><td>{e(lead.stage)}</td>"
        f"<td>{'Yes' if lead.needs_review else ''}</td></tr>"
        for lead in rows_data
    )
    body = (
        f"<h2>Leads ({len(rows_data)})</h2>" + form
        + "<div class='card'><table><tr><th>Lead</th><th>Name</th><th>Company</th><th>Stage</th><th>Needs review</th></tr>"
        f"{rows}</table></div>"
    )
    return Response(layout("Leads", body), mimetype="text/html")


@app.get("/lead/<lead_id>")
def lead_detail(lead_id: str):
    service = get_service()
    try:
        lead = service.get_lead(lead_id)
    except Exception:
        flash("Lead not found.", "error")
        return redirect(url_for("leads"))

    analysis = service.repo.get_latest_analysis(lead_id)
    draft = service.repo.get_draft_for_analysis(analysis.analysis_id) if analysis else None
    activities = service.repo.list_activities(lead_id)

    info = (
        "<div class='card'><h2>%s · %s</h2><table>"
        "<tr><th>Lead ID</th><td>%s</td><th>Email</th><td>%s</td></tr>"
        "<tr><th>Phone</th><td>%s</td><th>Source</th><td>%s</td></tr>"
        "<tr><th>Stage</th><td>%s</td><th>Response status</th><td>%s</td></tr>"
        "<tr><th>Received</th><td>%s</td><th>Last contact</th><td>%s</td></tr>"
        "<tr><th>Next follow-up</th><td>%s</td><th>Notes</th><td>%s</td></tr>"
        "</table><p><b>Inquiry.</b> %s</p>%s</div>"
    ) % (
        e(lead.name), e(lead.company or "—"), e(lead.lead_id), e(lead.email or ""),
        e(lead.phone or ""), e(lead.source or ""), e(lead.stage), e(lead.response_status),
        fmt_date(lead.received_at), fmt_date(lead.last_contact_at),
        fmt_date(lead.next_follow_up_at), e(lead.notes or ""), e(lead.inquiry),
        f"<p class='flash error'>Needs review: {e(lead.review_reason or 'manual review required')}</p>" if lead.needs_review else "",
    )

    warnings = service.profile_warnings()
    warning_html = (
        f"<div class='flash info'>Business profile incomplete; drafts may be generic: {e('; '.join(warnings))}</div>"
        if warnings else ""
    )

    analyze_form = (
        f"<form method='post' action='{url_for('analyze', lead_id=lead_id)}'>"
        "<button class='btn' type='submit'>Analyze lead</button></form>"
    )

    analysis_html = "<div class='card'><h3>AI analysis</h3><p class='muted'>No analysis yet.</p></div>"
    if analysis:
        flags = risk_badge(analysis)
        draft_html = ""
        if draft:
            draft_html = (
                "<div class='card'><h3>Draft follow-up (not sent)</h3>"
                f"<p class='muted'>Status: {e(draft.status)}"
                + (f" · copied {e(draft.copied_at.isoformat())}" if draft.copied_at else "")
                + "</p>"
                f"<label>Subject</label><input value='{e(draft.subject)}' readonly>"
                f"<label>Your draft (editable)</label>"
                f"<form method='post' action='{url_for('edit_draft', draft_id=draft.draft_id, lead_id=lead_id)}'>"
                f"<textarea name='body' rows='10'>{e(draft.effective_body)}</textarea>"
                "<button class='btn' type='submit'>Save edit</button></form>"
                f"<form method='post' action='{url_for('copy_draft', draft_id=draft.draft_id, lead_id=lead_id)}' style='margin-top:8px'>"
                "<button class='btn secondary' type='submit'>Mark as copied</button>"
                "</form>"
                "<p class='muted'>Use the textarea above to copy into your own email tool. "
                "The original AI draft is shown below and is always preserved.</p>"
                f"<details><summary>Original AI draft</summary><pre class='draft'>{e(draft.body)}</pre></details>"
                "</div>"
            )
        analysis_html = (
            "<div class='card'><h3>AI analysis " + flags + "</h3>"
            "<div class='metrics'>"
            f"<div class='metric'><span class='muted'>Priority</span><b>{e(analysis.priority)}</b></div>"
            f"<div class='metric'><span class='muted'>Buying signal</span><b>{e(analysis.buying_signal)}</b></div>"
            f"<div class='metric'><span class='muted'>Confidence</span><b>{e(analysis.confidence)}</b></div>"
            f"<div class='metric'><span class='muted'>Next action</span><b>{e(analysis.next_action)}</b></div></div>"
            f"<p><b>Summary.</b> {e(analysis.summary)}</p>"
            f"<p><b>Why this priority.</b> {e(analysis.priority_rationale)}</p>"
            f"<p class='muted'>Intent: {e(analysis.intent)} · Sentiment: {e(analysis.sentiment)} · "
            f"Follow-up: {fmt_date(analysis.follow_up_at)} · Prompt: {e(analysis.prompt_version)}</p></div>"
            + draft_html
        )

    outcome_form = (
        "<div class='card'><h3>Record outcome</h3>"
        f"<form method='post' action='{url_for('outcome', lead_id=lead_id)}'>"
        "<label>Activity</label><select name='activity'>"
        + "".join(
            f"<option>{e(x)}</option>"
            for x in ["Email sent", "Call", "Meeting", "Note", "Proposal sent", "No response"]
        )
        + "</select>"
        "<label>Outcome</label><input name='outcome'>"
        "<label>Notes</label><textarea name='notes' rows='3'></textarea>"
        "<label>Update stage</label><select name='stage'><option>(unchanged)</option>"
        + "".join(f"<option>{e(s)}</option>" for s in Stage)
        + "</select>"
        "<label>Update response status</label><select name='status'><option>(unchanged)</option>"
        + "".join(f"<option>{e(s)}</option>" for s in ResponseStatus)
        + "</select><button class='btn' type='submit'>Save activity</button></form></div>"
    )

    activity_rows = "".join(
        f"<tr><td>{e(a.occurred_at.isoformat())}</td><td>{e(a.activity_type)}</td>"
        f"<td>{e(a.outcome or '')}</td><td>{e(a.notes or '')}</td></tr>"
        for a in activities
    )
    activity_html = (
        "<div class='card'><h3>Activity history</h3><table>"
        "<tr><th>When</th><th>Type</th><th>Outcome</th><th>Notes</th></tr>"
        f"{activity_rows}</table></div>"
    )

    body = (
        info
        + warning_html
        + analyze_form
        + analysis_html
        + outcome_form
        + activity_html
    )
    return Response(layout(lead.name, body), mimetype="text/html")


@app.post("/lead/<lead_id>/analyze")
def analyze(lead_id: str):
    service = get_service()
    try:
        result = service.analyze_lead(lead_id)
        flash("Analysis complete" + (" — flagged for human review." if result.needs_review else "."),
              "info" if result.needs_review else "success")
    except LeadValidationError as exc:
        flash("Cannot analyze: " + "; ".join(exc.errors), "error")
    except AIUnavailable as exc:
        flash(f"Analysis unavailable; lead preserved and marked Needs Review. ({exc})", "error")
    except AnalysisValidationError as exc:
        flash(f"AI output failed validation; lead preserved and marked Needs Review. ({exc})", "error")
    return redirect(url_for("lead_detail", lead_id=lead_id))


@app.post("/draft/<draft_id>/edit")
def edit_draft(draft_id: str):
    service = get_service()
    lead_id = request.form.get("lead_id", "")
    try:
        service.save_draft_edit(draft_id, request.form.get("body", ""))
        flash("Edit saved. The original AI draft is preserved.", "success")
    except LeadValidationError as exc:
        flash("; ".join(exc.errors), "error")
    return redirect(url_for("lead_detail", lead_id=lead_id or service.get_draft(draft_id).lead_id))


@app.post("/draft/<draft_id>/copy")
def copy_draft(draft_id: str):
    service = get_service()
    lead_id = request.form.get("lead_id", "")
    text = service.copy_draft(draft_id)
    flash("Recorded as copied. Nothing was sent — paste it into your own email tool.", "info")
    target = lead_id or service.get_draft(draft_id).lead_id
    return redirect(url_for("lead_detail", lead_id=target))


@app.post("/lead/<lead_id>/outcome")
def outcome(lead_id: str):
    service = get_service()
    stage_raw = request.form.get("stage", "(unchanged)")
    status_raw = request.form.get("status", "(unchanged)")
    activity_type = request.form.get("activity", "Note")
    try:
        _, next_follow_up = service.record_outcome(
            lead_id,
            activity_type,
            outcome=request.form.get("outcome") or None,
            notes=request.form.get("notes") or None,
            stage=Stage(stage_raw) if stage_raw != "(unchanged)" else None,
            response_status=ResponseStatus(status_raw) if status_raw != "(unchanged)" else None,
            update_contact=activity_type != "Note",
        )
        if next_follow_up:
            flash(f"Activity recorded. Next follow-up recalculated: {next_follow_up.date().isoformat()}.", "success")
        else:
            flash("Activity recorded. Lead is closed; no follow-up scheduled.", "success")
    except (LeadValidationError, ValueError) as exc:
        flash(f"Could not record outcome: {exc}", "error")
    return redirect(url_for("lead_detail", lead_id=lead_id))


@app.route("/import", methods=["GET", "POST"])
def import_view():
    service = get_service()
    if request.method == "POST":
        uploaded = request.files.get("file")
        if uploaded is None or not uploaded.filename:
            flash("Choose a CSV or XLSX file first.", "error")
        else:
            try:
                report = import_leads(uploaded, service.repo, filename=uploaded.filename)
                if report.missing_columns:
                    flash("Missing required columns: " + ", ".join(report.missing_columns), "error")
                else:
                    flash(report.summary(), "success" if report.failed == 0 else "info")
                    for error in report.errors[:10]:
                        flash(f"Row {error.row}: {error.message}", "error")
            except (ValueError, RuntimeError) as exc:
                flash(str(exc), "error")
        return redirect(url_for("import_view"))
    body = (
        "<div class='card'><h2>Import leads</h2>"
        "<p class='muted'>Upload the provided sample CSV/XLSX, or your own with matching columns. "
        "Invalid rows are reported without losing valid rows.</p>"
        "<form method='post' enctype='multipart/form-data'>"
        "<input type='file' name='file' accept='.csv,.xlsx,.xlsm'>"
        "<button class='btn' type='submit'>Import file</button></form></div>"
    )
    return Response(layout("Import", body), mimetype="text/html")


@app.route("/new", methods=["GET", "POST"])
def new_lead():
    service = get_service()
    if request.method == "POST":
        try:
            lead = Lead(
                name=request.form.get("name", ""),
                company=request.form.get("company") or None,
                email=request.form.get("email") or None,
                phone=request.form.get("phone") or None,
                source=request.form.get("source") or None,
                inquiry=request.form.get("inquiry", ""),
                stage=Stage(request.form.get("stage", "New")),
                response_status=ResponseStatus(request.form.get("status", "Not Contacted")),
                notes=request.form.get("notes") or None,
            )
            service.create_lead(lead)
            flash(f"Lead saved: {lead.lead_id}", "success")
            return redirect(url_for("lead_detail", lead_id=lead.lead_id))
        except (LeadValidationError, ValueError) as exc:
            flash(f"Please fix: {exc}", "error")
    stage_opts = "".join(f"<option>{e(s)}</option>" for s in Stage)
    status_opts = "".join(f"<option>{e(s)}</option>" for s in ResponseStatus)
    body = (
        "<div class='card'><h2>Add a lead manually</h2><form method='post'>"
        "<label>Name *</label><input name='name'>"
        "<label>Company</label><input name='company'>"
        "<div class='row'><div><label>Email</label><input name='email'></div>"
        "<div><label>Phone</label><input name='phone'></div></div>"
        "<label>Lead source</label><input name='source' value='Manual'>"
        "<label>Inquiry *</label><textarea name='inquiry' rows='4'></textarea>"
        f"<label>Stage</label><select name='stage'>{stage_opts}</select>"
        f"<label>Response status</label><select name='status'>{status_opts}</select>"
        "<label>Notes</label><textarea name='notes' rows='3'></textarea>"
        "<button class='btn' type='submit'>Create lead</button></form></div>"
    )
    return Response(layout("New lead", body), mimetype="text/html")


@app.route("/settings", methods=["GET", "POST"])
def settings_view():
    service = get_service()
    if request.method == "POST":
        service.save_business_profile(
            BusinessProfile(
                business_name=request.form.get("business_name", ""),
                description=request.form.get("description", ""),
                services=request.form.get("services", ""),
                tone=request.form.get("tone", ""),
                allowed_claims=request.form.get("allowed_claims", ""),
                restricted_topics=request.form.get("restricted_topics", ""),
                follow_up_rules=request.form.get("follow_up_rules", ""),
            )
        )
        flash("Business profile saved. Drafting will use this context.", "success")
        return redirect(url_for("settings_view"))
    profile = service.get_business_profile()
    warning = (
        f"<div class='flash info'>Profile incomplete; drafts may be generic: {e('; '.join(service.profile_warnings()))}</div>"
        if service.profile_warnings() else ""
    )

    def area(label, name, value, rows=3):
        return f"<label>{e(label)}</label><textarea name='{name}' rows='{rows}'>{e(value)}</textarea>"

    body = (
        "<div class='card'><h2>Business profile</h2>"
        + warning
        + "<form method='post'>"
        f"<label>Business name</label><input name='business_name' value='{e(profile.business_name)}'>"
        + area("Description", "description", profile.description)
        + area("Services", "services", profile.services, 2)
        + f"<label>Tone</label><input name='tone' value='{e(profile.tone)}'>"
        + area("Allowed claims", "allowed_claims", profile.allowed_claims)
        + area("Restricted topics", "restricted_topics", profile.restricted_topics)
        + area("Follow-up rules", "follow_up_rules", profile.follow_up_rules)
        + "<button class='btn' type='submit'>Save profile</button></form></div>"
    )
    return Response(layout("Settings", body), mimetype="text/html")


@app.get("/health")
def health():
    return {"status": "ok", "provider": settings.ai_provider, "db": settings.db_path}


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
