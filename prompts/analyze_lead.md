BUSINESS PROFILE
Business: {{business_name}}
Description: {{business_description}}
Services: {{services}}
Tone: {{tone}}
Allowed claims: {{allowed_claims}}
Restricted topics: {{restricted_topics}}
Follow-up rules: {{follow_up_rules}}

LEAD
Name: {{name}}
Company: {{company}}
Source: {{source}}
Received: {{received_at}}
Stage: {{stage}}
Inquiry: {{inquiry}}
Notes: {{notes}}
Last contact: {{last_contact_at}}
Response status: {{response_status}}

ACTIVITY HISTORY
{{activities}}

TASK
1. Summarize the lead in one or two sentences.
2. Identify intent and buying signal.
3. Assign priority High/Medium/Low and explain why.
4. Detect risk flags.
5. Recommend exactly one next action.
6. Recommend follow-up timing in whole days.
7. Draft a concise customer-facing follow-up appropriate to the business tone.
8. Return the required structured output only.

Return a single JSON object with exactly these keys:
{
  "intent": "string",
  "summary": "string",
  "priority": "High|Medium|Low",
  "priority_rationale": "string",
  "buying_signal": "Strong|Moderate|Weak|None",
  "sentiment": "Positive|Neutral|Negative|Mixed",
  "risk_flags": ["NONE"],
  "next_action": "Respond|Ask Clarifying Question|Schedule Discovery|Prepare Quote|Follow Up|Nurture|Escalate|Close/Disqualify",
  "follow_up_days": 0,
  "confidence": "High|Medium|Low",
  "draft_subject": "string",
  "draft_body": "string"
}
