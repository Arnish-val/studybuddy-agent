# Study Buddy AI Tutor - Capstone Project

An adaptive, safety-gated AI math tutor for middle schoolers (grades 6-8), built using the Google GenAI Agent Development Kit (ADK) and running on Google Cloud Run.

## 🚀 Live Demo & Deployment

* **Live Interactive Portal**: **[https://studybuddy-tutor-112080613832.us-central1.run.app](https://studybuddy-tutor-112080613832.us-central1.run.app)**
* **Public GitHub Repository**: **[https://github.com/Arnish-val/studybuddy-agent](https://github.com/Arnish-val/studybuddy-agent)**

---

## 🛡️ Key Features

### 1. In-Memory API Rate Limiting (Billing Protection)
To protect your Vertex AI API usage and prevent runaway Google Cloud bills from spam bots or client-side loops:
- **Zero-LLM Warning Gate**: The workflow monitors incoming student activity right after the input is parsed.
- **API Call Bypass**: If a user sends more than **9 messages per minute**, the system blocks the message and yields a hardcoded warning directly to the UI, **completely bypassing all downstream LLM calls (tutor, safety classifers, etc.)**. This guarantees that spamming the chat input results in zero costs.

### 2. Conversational Intent Routing
- **Friendly Small-Talk Greeting Node**: Gracefully welcomes the student with a customized introduction when they start with a greeting (e.g. "hi", "hello"), asking them what they want to study (Fractions or Decimals) instead of forcing them directly into tests or lessons.
- **Out-of-Scope Redirection Node**: Polite boundary that intercepts math outside middle-school fractions/decimals (e.g. "1+1=3") or general queries (e.g. history, coding) and redirects the student back to fractions and decimals tutoring.

### 3. Safety Auditing & Parent/Teacher Portal
- **High-Risk Safety Classifier**: Evaluates student inputs for emotional distress, self-harm, or persistent guardrail bypass.
- **Workflow Interruption**: Violations suspend the session and flag the turn for parent/teacher review.
- **Teacher/Parent Portal Dashboard**: A secure tab allowing supervisors to approve or reject flagged inputs, automatically resuming the tutor or terminating the turn.

### 4. PII Redaction boundary
- Automatically redacts sensitive personal data (emails, phone numbers, common name introductions, US street addresses) from student inputs before archiving or saving, keeping logs fully compliant with privacy requirements.

### 5. Ambient Inactivity Finalization
- A background worker thread automatically monitors student inactivity. After **5 minutes** of idle time, the thread invokes an asynchronous client model call to generate a summary log, writing to the fallback CSV database (`app/data/progress_log.csv`).

---

## ⚙️ Running Locally

### Requirements
- **uv**: Python package manager - [Install](https://docs.astral.sh/uv/getting-started/installation/)
- **gcloud CLI**: Auth credentials for Vertex AI model execution - [Install](https://cloud.google.com/sdk/docs/install)

### Setup & Play
1. Install dependencies:
   ```bash
   uv sync
   ```
2. Authenticate GCP application default credentials:
   ```bash
   gcloud auth application-default login
   ```
3. Run the interactive Streamlit server:
   ```bash
   uv run streamlit run app_ui.py
   ```

---

## 🧪 Testing

The outcome-based and integration test suites cover rate limiting, PII redactors, and parent approval mechanics.

Run pytest:
```bash
uv run pytest tests/test_agent.py
```
Run integration tests (ensure Vertex AI environment parameter is set):
```bash
$env:GOOGLE_GENAI_USE_VERTEXAI="true"; uv run pytest tests/integration/test_agent.py
```
All 15 validations pass successfully!
