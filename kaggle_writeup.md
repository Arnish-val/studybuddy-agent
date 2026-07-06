# Kaggle Submission Writeup: Study Buddy AI Tutor

Here are the complete details to copy and paste into your Kaggle submission portal.

---

## 1. Basic Details

* **Title**:
  ```text
  Study Buddy: An Adaptive, Safety-Gated AI Math Tutor for Middle Schoolers
  ```
  *(78 / 80 characters)*

* **Subtitle (One-sentence explanation)**:
  ```text
  An AI tutor built on Google GenAI ADK featuring safety-guardrails, PII scrubbing, parent approvals, and cost-control rate limits.
  ```
  *(126 / 140 characters)*

* **Submission Tracks**:
  Select **Education / Social Impact** or **Safety & Alignment / Responsible AI** (based on your preference).

* **Media Gallery**:
  Upload the following screenshot files from your project root:
  1. `second_question_check.png` (Tutor main interface)
  2. `greeting_response.png` (Conversational greeting behavior)
  3. `out_of_scope_response.png` (Out-of-scope redirection behavior)

* **Project Links**:
  - **Live Working Demo**: `https://studybuddy-tutor-112080613832.us-central1.run.app`
  - **Code Repository (GitHub)**: `https://github.com/Arnish-val/studybuddy-agent`

---

## 2. Project Description (Markdown)

Copy and paste the following content into the **Project Description** text editor:

```markdown
### Overview
Study Buddy is an intelligent, safety-gated tutoring agent designed for students in grades 6-8. Middle school students often struggle with foundational mathematics, particularly fractions and decimals. Study Buddy addresses this challenge by providing an adaptive, step-by-step learning environment that adjusts to a student's weak skills while enforcing strict safety guardrails and privacy boundaries.

The system is built on the Google GenAI Agent Development Kit (ADK) and wraps a multi-agent stateful workflow inside an interactive Streamlit portal deployed on Google Cloud Run.

---

### Core Architecture & Flow
The application processes inputs through a pipeline of validation, safety, intent routing, and tutoring nodes:

1. **Input Parse & Scrub**: The input node normalizes incoming messages and scrubs any personally identifiable information (PII) before any data is stored in the session history or sent to database logs.
2. **API Abuse Rate Limiting**: The system implements an in-memory rate limiter. If a student sends more than 9 messages per minute, the gate intercepts the turn and routes it directly to output with a friendly warning. This completely bypasses all downstream LLM calls, protecting the system from runaway API bills or spam bots.
3. **Safety Evaluation**: Evaluates incoming text for severe distress, self-harm signals, or hostile prompt-injection attempts. High-risk inputs trigger a workflow suspension.
4. **Human-in-the-loop Verification**: Suspended workflows are held in a pending state. A dedicated supervisor dashboard (Parent / Teacher Portal) allows adults to view flagged inputs and either Approve or Reject them to resume or terminate the turn.
5. **Intent Routing**: Classifies messages into greetings, out-of-scope queries, progress checks, learning questions, or new topic requests.
6. **Adaptive Tutoring**: Routes learning questions to a specialized tutoring node that dynamically loads mathematical instruction sheets (Fractions/Decimals). A guardrail node ensures the tutor gives hints and scaffolding rather than raw answers.

---

### Key Capabilities

#### 1. Friendly Conversational Routing & Boundaries
Rather than forcing students directly into diagnostic math quizzes upon first greeting, the system detects conversational introductions (like "hello" or "how are you") and welcomes them with a friendly explanation of the subjects. If a student inputs out-of-scope requests (such as "is 1+1=3" or general history/coding queries), the tutor politely informs them of its focus and redirects them to fractions or decimals study.

#### 2. Student Privacy & PII Redaction
We enforce a strict privacy boundary. The input parsing node runs a robust PII scrubber that redacts email addresses, phone numbers, name disclosures ("my name is..."), and standard US street addresses, ensuring all recorded conversation logs and databases remain redacted.

#### 3. Ambient Inactivity Finalization
To provide session progress reports without manual triggers, a background daemon thread monitors session inactivity. After 5 minutes of idle time, the thread invokes an asynchronous summary call and appends the progress logs directly to a local secure CSV database (`app/data/progress_log.csv`).

#### 4. Secure Parent Supervision Logs
All historical audit determinations are stored encrypted on disk using Fernet symmetric encryption. The Parent Portal decrypts these audits on-the-fly for supervisor review, preventing unauthorized exposure of safety incidents.

---

### Verification and Test Suite
The workflow and tools are verified by a comprehensive automated test suite consisting of 15 pytest assertions. The tests cover rate limiting, PII redactors, encryption bounds, parent approval mechanics, and intent routing fallbacks. All 15 unit and integration tests compile and pass successfully.
```
