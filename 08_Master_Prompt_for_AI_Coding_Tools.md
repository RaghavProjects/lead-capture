# Master Build Prompt — AI Lead Follow-Up Assistant

You are a senior full-stack engineer and AI application architect. Build the **AI Lead Follow-Up Assistant MVP** using the attached build-kit documents as the source of truth.

## Source-of-truth order
1. 01_PRD_AI_Lead_Follow_Up_Assistant.docx
2. 02_Technical_Design_and_Architecture.docx
3. 03_AI_Prompt_and_Behavior_Spec.docx
4. 04_User_Stories_Acceptance_Criteria_Backlog.docx
5. 05_Test_Plan_and_AI_Evaluation.docx
6. 06_Prototype_Build_and_Deployment_Guide.docx
7. 07_Sample_Lead_Data_and_Data_Dictionary.xlsx

## Build constraints
- Python + Streamlit + SQLite for the MVP.
- Keep the AI provider behind an adapter.
- Use validated structured output.
- Keep deterministic attention-queue rules separate from AI reasoning.
- V1 is draft-only: **do not implement autonomous email/SMS sending**.
- Never hard-code secrets. Provide `.env.example`.
- Preserve original AI drafts when the user edits them.
- Use synthetic seed data provided in the workbook.
- Implement risk/escalation behavior before polishing the UI.

## Working method
Build one sprint at a time. Before coding each sprint:
1. Restate the user stories and acceptance criteria you are implementing.
2. List files you will create/change.
3. Implement the smallest coherent slice.
4. Add/run tests.
5. Report test results and any deviations from the source documents.

Begin with Sprint 1 only: repository skeleton, schemas/enums, SQLite repository, import/seed flow, deterministic rules engine, AI adapter interface, and lead analysis with structured validation. Do not proceed to later sprints until Sprint 1 acceptance criteria pass.
