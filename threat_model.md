# STRIDE Threat Modeling Assessment

This document provides a systematic STRIDE threat modeling assessment for the **Study Buddy AI Tutor** agent system.

---

## 1. System Boundaries & Data Flow
- **Entry Points**: 
  - Student messages received by `parse_input_node` in the workflow graph.
  - Parent/teacher decisions received via `record_parent_decision` tool and workspace resumption events.
  - Administrator operations via `update_flag_patterns` tool.
- **Data Storage Layers**:
  - `app/reviews.json`: Stores parent/teacher audit decision logs, encrypted using Fernet at rest.
  - `app/data/progress_log.csv`: Stores PII-scrubbed progress reports (student_id, date, summary).
  - In-memory cache structures: `RATE_LIMIT_CACHE`, `SESSION_HISTORIES`, and `SESSION_ACTIVITY` mapped by student ID.

---

## 2. STRIDE Evaluation

### 👤 Spoofing (Student/Parent Identity Verification)
- **Threat**: A student or external caller attempts to resolve a pending review by calling `record_parent_decision` or triggering resumption events.
- **Control**: 
  - `record_parent_decision` enforces a strict check: `caller_role == "parent_teacher"` and rejects missing or empty `authenticated_user_id` values.
  - The workflow `approval_node` enforces the same role checks on resumption payloads, rejecting any spoofed or non-authenticated payload.
- **Impact Rating**: Low (mitigated).

### ✏️ Tampering (Data Manipulation / Code Bypass)
- **Threat**: A student manipulates variables (like `subject`) to read files outside the tutoring scope (e.g. Directory Traversal via `../../etc/passwd`).
- **Control**: 
  - `tutor_node` validates the `subject` parameter against an explicit allowlist: `["fractions", "decimals"]`.
  - Non-conforming subjects safely fallback to `"fractions"`, preventing path traversal.
  - Re-evaluation / Replay of already resolved safety reviews is blocked inside `record_parent_decision`.
- **Impact Rating**: Low (mitigated).

### 📝 Repudiation (Traceability & Logging)
- **Threat**: A parent or teacher claims they never approved/rejected a flagged student message, or a bad actor modifies review decisions.
- **Control**:
  - Every action recorded in `reviews.json` logs `authenticated_user_id` alongside the UTC timestamp.
  - The file is encrypted at rest using a symmetric key, protecting it against unauthorized tampering and repudiation.
- **Impact Rating**: Low (mitigated).

### 🔍 Information Disclosure (PII Leakage)
- **Threat**: Student PII (name, email, phone, home address) is leaked to LLM calls or saved as plaintext in public summaries/CSV logs.
- **Control**:
  - `scrub_pii` utilizes regular expression filters to redact email patterns, phone patterns, standard US street addresses, and common introductory phrases like *"my name is [Name]"*, *"I'm [Name]"*, or *"I live at [Address]"*.
  - This redactor is executed at both input (`parse_input_node`) and logging (`progress_node` / CSV writing) stages.
- **Honest Limitation**: Best-effort regex pass only. Deep NER is required for absolute name-disambiguation coverage.
- **Impact Rating**: Medium (mitigated with best-effort boundaries).

### 🚫 Denial of Service (LLM Exhaustion / Rate Limits)
- **Threat**: A student spams messages to exhaust API credits and block normal server operations.
- **Control**:
  - `check_risk_patterns` maintains an in-memory sliding window cache checking student turn frequency.
  - If a student exceeds 9 messages/minute, the 10th message is immediately routed to `output_sender` with a friendly "slow down" warning, completely bypassing GenAI model calls.
- **Impact Rating**: Low (mitigated).

### 👑 Elevation of Privilege (Access Control)
- **Threat**: A regular student modifies safety filters, reads flagged reviews, or accesses parent dashboards.
- **Control**:
  - `update_flag_patterns` enforces a strict check: `caller_role == "administrator"`.
  - Streamlit dashboard separates Student Chat from the Parent / Teacher Audit Portal.
- **Impact Rating**: Low (mitigated).
