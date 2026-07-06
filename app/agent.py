# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import Any
import os
import json
import base64
import time
import re
import csv
import datetime
import threading
from collections import defaultdict
from pydantic import BaseModel, Field
from google.adk.workflow import Workflow, START, node
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.agents.context import Context
from google.adk.agents import LlmAgent
from google.adk.apps import App, ResumabilityConfig
from google.adk.models import Gemini
from google.genai import types, Client

# Load Configuration
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
with open(CONFIG_PATH) as f:
    config = json.load(f)

MODEL_NAME = config["model_name"]
FLAGGED_PATTERNS = config["flagged_patterns"]
DISALLOWED_PHRASES = config["safety_guardrails"]["disallowed_phrases"]

# In-memory rate limiting configuration
RATE_LIMIT_CACHE = defaultdict(list)
MAX_MESSAGES_PER_MINUTE = 9


# Pydantic Schemas

class StudentTurn(BaseModel):
    student_id: str
    message: str
    subject: str
    timestamp: str


class RiskVerdict(BaseModel):
    is_high_risk: bool = Field(description="True if the message represents serious self-harm, distress, or repeated attempts to bypass safety.")
    reason: str = Field(description="Short rationale for the decision.")


class IntentVerdict(BaseModel):
    intent: str = Field(description="Intent class: 'new_topic', 'question', 'progress_check', 'greeting', or 'out_of_scope'.")


class DiagnosticQuestion(BaseModel):
    question: str = Field(description="The multiple choice question to ask.")
    options: list[str] = Field(description="Exactly 4 multiple choice options.")
    correct_answer: str = Field(description="The correct answer to the question.")
    explanation: str = Field(description="Explanation of the correct answer.")
    difficulty: str = Field(description="Difficulty level of the question.")


class DiagnosticQuiz(BaseModel):
    topic: str = Field(description="The selected topic.")
    questions: list[DiagnosticQuestion] = Field(description="List of 3 diagnostic questions.")


class SkillDiagnosis(BaseModel):
    weak_skill: str = Field(description="The primary weak skill or misconception identified.")
    explanation: str = Field(description="Diagnostic summary.")


class TutorOutput(BaseModel):
    explanation: str = Field(description="Explanation of the concept or feedback on the previous attempt.")
    practice_problem: str = Field(description="A single practice problem to solve next. Do NOT give away the answer to this problem.")
    is_correct: bool = Field(description="True if the student's answer to the previous problem was correct.")


class GuardrailVerdict(BaseModel):
    is_safe: bool = Field(description="True if the draft does NOT give away the answer, is inappropriate, or discouraging.")
    feedback: str = Field(description="Feedback/revisions needed if unsafe.")


class SummaryOutput(BaseModel):
    summary: str = Field(description="Parent/teacher summary of accomplishments.")


def scrub_pii(text: str) -> str:
    """Redacts common PII from student messages and summaries.
    
    NOTE: This is a best-effort regex pass and is not an exhaustive NLP-based PII detection.
    Some non-standard name or address patterns may not be fully covered.
    """
    if not text:
        return ""
    # Email pattern
    text = re.sub(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", "[EMAIL_REDACTED]", text)
    # Phone number pattern
    text = re.sub(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b", "[PHONE_REDACTED]", text)
    
    # Name disclosure phrase patterns (e.g. "my name is X", "I'm X", "I am X")
    text = re.sub(r"(?i)\bmy\s+name\s+is\s+([A-Za-z]+)\b", "my name is [NAME_REDACTED]", text)
    text = re.sub(r"(?i)\bi'm\s+([A-Za-z]+)\b", "I'm [NAME_REDACTED]", text)
    text = re.sub(r"(?i)\bi\s+am\s+([A-Za-z]+)\b", "I am [NAME_REDACTED]", text)
    
    # Address disclosure phrase patterns (e.g. "I live at/on X")
    text = re.sub(r"(?i)\bi\s+live\s+(?:at|on)\s+([^.,?!]+)", "I live at [ADDRESS_REDACTED]", text)
    
    # Basic standalone US street address patterns (number + street name + suffix)
    address_pattern = r"(?i)\b\d+\s+[A-Za-z0-9\s'\-]+?\s+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Way|Plaza|Pl)\b"
    text = re.sub(address_pattern, "[ADDRESS_REDACTED]", text)
    
    return text


def log_progress(student_id: str, summary: str) -> None:
    """Logs the PII-scrubbed session summary directly to local CSV database."""
    log_dir = os.path.join(os.path.dirname(__file__), "data")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "progress_log.csv")
    
    clean_summary = scrub_pii(summary)
    file_exists = os.path.exists(log_file)
    date_str = datetime.datetime.utcnow().isoformat()
    try:
        with open(log_file, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["student_id", "date", "summary"])
            writer.writerow([student_id, date_str, clean_summary])
        print(f"Logged progress to {log_file} successfully.")
    except Exception as e:
        print(f"Error logging progress to CSV: {e}")


# In-memory session histories and background activity monitor
SESSION_HISTORIES = defaultdict(list)
SESSION_ACTIVITY = {}
IDLE_TIMEOUT_SECONDS = float(os.getenv("STUDYBUDDY_IDLE_TIMEOUT", "300"))


def trigger_background_progress(student_id: str):
    """Generates progress summary asynchronously in background for idle sessions."""
    history = SESSION_HISTORIES.get(student_id, [])
    if not history:
        return
        
    history_str = "\n".join(f"Student: {h.get('message')}" for h in history)
    # Perform input side PII scrubbing
    history_str = scrub_pii(history_str)
    
    client = Client()
    prompt = (
        f"You are a study progress reporter. Summarize the student's recent session history and accomplishments. "
        f"Provide an encouraging overview for parents/teachers.\n\n"
        f"Session History:\n{history_str}\n\n"
        f"Provide a structured summary containing what was practiced, what is improving, and one suggested focus."
    )
    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=SummaryOutput,
            ),
        )
        summary_json = json.loads(response.text)
        summary_text = summary_json.get("summary", "")
        
        # Log to the local CSV fallback
        log_progress(student_id, summary_text)
        print(f"[BACKGROUND SUMMARY] Background progress summary successfully written for student '{student_id}'.")
    except Exception as e:
        print(f"Error generating background progress report: {e}")


def session_idle_checker():
    """Background loop that polls for idle sessions to finalize them."""
    while True:
        time.sleep(1) # poll every 1 second
        now = time.time()
        for student_id in list(SESSION_ACTIVITY.keys()):
            last_active = SESSION_ACTIVITY[student_id]
            if now - last_active >= IDLE_TIMEOUT_SECONDS:
                # Remove first to prevent double trigger
                SESSION_ACTIVITY.pop(student_id, None)
                print(f"[SESSION IDLE CHECKER] Session ended for student '{student_id}' due to inactivity. Triggering background progress check...")
                threading.Thread(target=trigger_background_progress, args=(student_id,), daemon=True).start()


# Spawn checker thread
checker_thread = threading.Thread(target=session_idle_checker, daemon=True)
checker_thread.start()


# Workflow Nodes Implementation

@node
def parse_input_node(ctx: Context, node_input: Any) -> Event:
    # Handle google.genai.types.Content object or dict-wrapped Content
    parts = getattr(node_input, "parts", None)
    if not parts and isinstance(node_input, dict):
        parts = node_input.get("parts")
        
    if parts:
        part = parts[0]
        text_data = part.get("text") if isinstance(part, dict) else getattr(part, "text", "")
        try:
            parsed = json.loads(text_data)
        except Exception:
            parsed = {"message": text_data}
    else:
        # Unpack base64 real Pub/Sub event or plain JSON payload
        data = None
        if isinstance(node_input, dict):
            data = node_input.get("data")
            if not data:
                message = node_input.get("message", {})
                if isinstance(message, dict):
                    data = message.get("data")
                else:
                    data = message
        
        if not data:
            data = node_input

        parsed = {}
        if isinstance(data, str):
            try:
                decoded = base64.b64decode(data).decode('utf-8')
                parsed = json.loads(decoded)
            except Exception:
                try:
                    parsed = json.loads(data)
                except Exception:
                    parsed = {"message": data}
        elif isinstance(data, dict):
            parsed = data

    student_id = parsed.get("student_id", "default_student")
    msg = parsed.get("message", "")
    if not msg and isinstance(node_input, dict):
        msg = node_input.get("message", "")
        if isinstance(msg, dict):
            msg = msg.get("data", "")
    subject = parsed.get("subject", "math")
    timestamp = parsed.get("timestamp", "")

    # Scrub PII from student input message
    clean_msg = scrub_pii(msg)

    # Track activity and session histories for background finalize trigger
    SESSION_ACTIVITY[student_id] = time.time()
    SESSION_HISTORIES[student_id].append({
        "message": clean_msg,
        "timestamp": timestamp or datetime.datetime.utcnow().isoformat()
    })

    turn = StudentTurn(
        student_id=student_id,
        message=clean_msg,
        subject=subject,
        timestamp=timestamp
    )

    history = ctx.state.get("history", [])
    history.append(turn.model_dump())

    return Event(
        output=turn,
        state={"turn": turn.model_dump(), "history": history}
    )


@node
def check_risk_patterns(ctx: Context, node_input: StudentTurn) -> Event:
    student_id = node_input.student_id
    now = time.time()
    
    # Prune timestamps older than 60 seconds
    RATE_LIMIT_CACHE[student_id] = [t for t in RATE_LIMIT_CACHE[student_id] if now - t < 60]
    
    # Check rate limit
    if len(RATE_LIMIT_CACHE[student_id]) >= MAX_MESSAGES_PER_MINUTE:
        print(f"[RATE LIMIT HIT] Student '{student_id}' has sent {len(RATE_LIMIT_CACHE[student_id])} messages in the last 60 seconds.")
        # Log hit by emitting Event to output_sender directly
        return Event(
            output={"response": "Slow down! You are sending too many messages. Please wait a minute and try again."},
            route="rate_limited"
        )
        
    # Record current timestamp
    RATE_LIMIT_CACHE[student_id].append(now)
    
    msg_lower = node_input.message.lower()
    flagged = False
    for pattern in FLAGGED_PATTERNS:
        if pattern in msg_lower:
            flagged = True
            break

    if flagged:
        return Event(output=node_input, route="run_llm_risk_eval")
    return Event(output=node_input, route="no_risk")


llm_risk_evaluator = LlmAgent(
    name="llm_risk_evaluator",
    model=Gemini(
        model=MODEL_NAME,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=(
        "You are a safety officer. Review the student's message. Assess if it indicates real risk of self-harm, "
        "severe emotional distress, or a persistent/hostile attempt to bypass safety constraints (e.g. demanding solutions)."
    ),
    output_schema=RiskVerdict,
)


@node
def check_llm_risk_verdict(ctx: Context, node_input: dict) -> Event:
    verdict = RiskVerdict(**node_input)
    if verdict.is_high_risk:
        return Event(output=ctx.state["turn"], route="high_risk", state={"risk_evaluation": verdict.reason})
    return Event(output=ctx.state["turn"], route="low_risk")


@node(rerun_on_resume=True)
async def approval_node(ctx: Context, node_input: dict) -> Event:
    if not ctx.resume_inputs or "teacher_approval" not in ctx.resume_inputs:
        turn_data = ctx.state["turn"]
        message_text = turn_data.get("message", "")
        reason = ctx.state.get("risk_evaluation", "Flagged content detected")
        student_id = turn_data.get("student_id", "default_student")
        timestamp = turn_data.get("timestamp", "")
        
        # Log pending review record using our new tool
        from app.tools import flag_message_for_review
        res = flag_message_for_review(
            student_id=student_id,
            message=message_text,
            reason=reason,
            timestamp=timestamp
        )
        review_id = res.get("review_id")
        ctx.state["current_review_id"] = review_id
        
        prompt_msg = (
            f"⚠️ ALERT: A student message has triggered the safety threshold.\n"
            f"Review ID: {review_id}\n"
            f"Reason: {reason}\n"
            f"Message: \"{message_text}\"\n\n"
            f"Please approve or reject this message to continue."
        )
        yield RequestInput(
            interrupt_id="teacher_approval",
            message=prompt_msg
        )
        return

    decision = ctx.resume_inputs["teacher_approval"]
    if isinstance(decision, dict):
        decision = decision.get("decision") or decision.get("value")
    caller_role = ctx.resume_inputs.get("caller_role")
    if isinstance(caller_role, dict):
        caller_role = caller_role.get("caller_role") or caller_role.get("value")
    auth_user_id = ctx.resume_inputs.get("authenticated_user_id")
    if isinstance(auth_user_id, dict):
        auth_user_id = auth_user_id.get("authenticated_user_id") or auth_user_id.get("value")
    review_id = ctx.state.get("current_review_id")
    
    # Enforce caller_role check
    if caller_role != "parent_teacher":
        yield Event(
            output={"response": "Access Denied: Only users with parent_teacher role can resolve safety audits.", "status": "blocked"},
            route="rejected"
        )
        return
        
    # Enforce authenticated_user_id presence check
    if not auth_user_id:
        yield Event(
            output={"response": "Access Denied: Authenticated User ID is missing from authorization payload.", "status": "blocked"},
            route="rejected"
        )
        return
        
    # Record decision using our new tool
    from app.tools import record_parent_decision
    if review_id:
        record_parent_decision(
            review_id=review_id,
            decision=decision,
            caller_role=caller_role,
            authenticated_user_id=auth_user_id
        )
        
    if decision == "approve":
        yield Event(output=ctx.state["turn"], route="approved", state={"teacher_decision": "approved"})
    else:
        yield Event(output=ctx.state["turn"], route="rejected", state={"teacher_decision": "rejected"})


@node
def rejection_node(ctx: Context, node_input: dict) -> Event:
    response_msg = (
        "I am here to help you study, but I cannot process this message. "
        "If you are feeling overwhelmed or distressed, please talk to a parent, teacher, or trusted adult."
    )
    return Event(output={"response": response_msg, "status": "blocked"})


# LLM intent classifier fallback
intent_classifier = LlmAgent(
    name="intent_classifier",
    model=Gemini(
        model=MODEL_NAME,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=(
        "You are an intent router for a middle school math study agent specializing ONLY in fractions and decimals.\n"
        "Classify the user's message into one of these intents:\n"
        "- 'greeting': General greetings, small talk, or introductions (e.g., 'hi', 'hello', 'how are you', 'hey').\n"
        "- 'out_of_scope': General math outside middle-school fractions/decimals (e.g., '1+1=3', '2+2'), or general knowledge queries unrelated to fractions/decimals (e.g. science, history, coding).\n"
        "- 'new_topic': The student specifically asks to start learning, study, or take a quiz on fractions or decimals.\n"
        "- 'question': The student asks a specific math question about fractions or decimals, or responds to a practice problem.\n"
        "- 'progress_check': The student or parent asks for progress, score, session summary, or how things are going.\n"
    ),
    output_schema=IntentVerdict,
)


@node(rerun_on_resume=True)
async def router_node(ctx: Context, node_input: StudentTurn) -> Event:
    msg = node_input.message.lower().strip()
    intent = None

    # Keyword check
    if msg in ["hi", "hello", "hey", "how are you", "greetings", "yo"]:
        intent = "greeting"
    elif any(k in msg for k in ["how am i doing", "progress", "report", "grade", "score", "summary"]):
        intent = "progress_check"
    elif any(k in msg for k in ["quiz", "test me", "diagnostic", "new topic", "study", "learn", "start"]):
        intent = "new_topic"
    elif any(k in msg for k in ["what is", "why", "how do", "explain", "question", "help me with"]):
        intent = "question"

    # LLM fallback
    if not intent:
        res = await ctx.run_node(intent_classifier, node_input=node_input)
        verdict = IntentVerdict(**res)
        intent = verdict.intent

    if intent not in ["new_topic", "question", "progress_check", "greeting", "out_of_scope"]:
        intent = "question"

    return Event(output=node_input, route=intent, state={"intent": intent})


@node
def greeting_node(ctx: Context, node_input: StudentTurn) -> Event:
    response_msg = (
        "Hello! 👋 I am your Study Buddy AI Tutor. I specialize in helping you learn and practice "
        "**Fractions** and **Decimals**! \n\n"
        "Which of these subjects would you like to focus on today? (Just let me know when you're ready to start!)"
    )
    return Event(output={"response": response_msg})


@node
def out_of_scope_node(ctx: Context, node_input: StudentTurn) -> Event:
    response_msg = (
        "I can only help you with middle school **Fractions** and **Decimals** concepts. \n\n"
        "Let's focus on those! Would you like to study fractions or decimals today?"
    )
    return Event(output={"response": response_msg})


diagnostic_generator = LlmAgent(
    name="diagnostic_generator",
    model=Gemini(
        model=MODEL_NAME,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=(
        "Generate exactly 3 multiple-choice diagnostic quiz questions (one easy, one medium, one hard) "
        "for the given topic to assess the student's current skill level."
    ),
    output_schema=DiagnosticQuiz,
    output_key="diagnostic_quiz_data"
)


eval_diagnosis = LlmAgent(
    name="eval_diagnosis",
    model=Gemini(
        model=MODEL_NAME,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=(
        "Analyze the student's diagnostic quiz answers. Identify their primary weak skill or misconception."
    ),
    output_schema=SkillDiagnosis,
)


@node(rerun_on_resume=True)
async def diagnostic_node(ctx: Context, node_input: dict) -> Event:
    quiz_data = ctx.state.get("diagnostic_quiz_data")
    if not quiz_data:
        yield Event(output={"weak_skill": "General understanding"}, route="diagnosis_complete")
        return

    questions = quiz_data.get("questions", [])
    quiz_index = ctx.state.get("quiz_index", 0)
    quiz_answers = ctx.state.get("quiz_answers", [])

    if ctx.resume_inputs:
        current_interrupt_id = f"quiz_q_{quiz_index}"
        if current_interrupt_id in ctx.resume_inputs:
            ans = ctx.resume_inputs[current_interrupt_id]
            if isinstance(ans, dict):
                ans = ans.get("response") or ans.get("value") or ""
            quiz_answers.append(ans)
            ctx.state["quiz_answers"] = quiz_answers
            quiz_index += 1
            ctx.state["quiz_index"] = quiz_index

    if quiz_index < len(questions):
        q = questions[quiz_index]
        q_text = q.get("question", "")
        options = q.get("options", [])

        prompt_msg = f"📝 Diagnostic Quiz (Question {quiz_index + 1}/{len(questions)}):\n\n{q_text}\n\n"
        if options:
            prompt_msg += "Options:\n" + "\n".join(f"- {opt}" for opt in options) + "\n\n"
        prompt_msg += "Please reply with your answer."

        yield RequestInput(
            interrupt_id=f"quiz_q_{quiz_index}",
            message=prompt_msg
        )
        return

    # Analyze completion
    diag_prompt = f"Topic: {quiz_data.get('topic')}\n"
    for i, q in enumerate(questions):
        diag_prompt += f"Q{i+1}: {q.get('question')}\n"
        diag_prompt += f"Correct Answer: {q.get('correct_answer')}\n"
        student_ans = quiz_answers[i] if i < len(quiz_answers) else "None"
        diag_prompt += f"Student Answer: {student_ans}\n\n"

    res = await ctx.run_node(eval_diagnosis, node_input={"message": diag_prompt})
    diagnosis = SkillDiagnosis(**res)

    yield Event(
        output={"weak_skill": diagnosis.weak_skill},
        route="diagnosis_complete",
        state={"weak_skill": diagnosis.weak_skill, "quiz_index": 0, "quiz_answers": []}
    )


tutor_agent = LlmAgent(
    name="tutor_agent",
    model=Gemini(
        model=MODEL_NAME,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=(
        "You are an encouraging and skilled study tutor. Explain concepts clearly. "
        "When evaluating a student's answer, do NOT give away the final answer even if they ask directly. "
        "Give them hints, point out their errors, and encourage them to try again. "
        "Always provide one practice problem at a time for them to solve."
    ),
    output_schema=TutorOutput,
)


@node(rerun_on_resume=True)
async def tutor_node(ctx: Context, node_input: Any) -> Event:
    history_str = ""
    for h in ctx.state.get("history", [])[-5:]:
        history_str += f"Student: {h.get('message')}\n"

    # Load skill instructions dynamically based on the current subject
    turn_data = ctx.state.get("turn", {})
    subject = turn_data.get("subject", "fractions")
    if not subject:
        subject = "fractions"
    subject = subject.lower().strip()
    
    # Restrict subject values to explicit allowlist to prevent directory traversal
    ALLOWED_SUBJECTS = ["fractions", "decimals"]
    if subject not in ALLOWED_SUBJECTS:
        subject = "fractions"
    
    skills_dir = os.path.join(os.path.dirname(__file__), "skills")
    skill_file = os.path.join(skills_dir, subject, "SKILL.md")
    
    skill_instructions = ""
    if os.path.exists(skill_file):
        try:
            with open(skill_file, encoding="utf-8") as f:
                skill_instructions = f.read()
        except Exception as e:
            print(f"Error loading skill file {skill_file}: {e}")
            
    tutor_prompt = ""
    if skill_instructions:
        tutor_prompt += f"=== ACTIVE TUTORING SKILL: {subject.upper()} ===\n{skill_instructions}\n============================================\n\n"

    weak_skill = ctx.state.get("weak_skill", "")
    tutor_prompt += f"Session History:\n{history_str}\n"

    if weak_skill:
        tutor_prompt += f"Identified Weak Skill to focus on: {weak_skill}\n"

    current_prob = ctx.state.get("current_practice_problem")
    if current_prob:
        tutor_prompt += f"Current Practice Problem that was active: {current_prob}\n"

    safety_feedback = ctx.state.get("safety_feedback", "")
    if safety_feedback:
        tutor_prompt += f"\nATTENTION: Your previous draft was rejected by safety guardrails for the following reason:\n{safety_feedback}\nPlease correct your response and do not repeat this error.\n"
        ctx.state["safety_feedback"] = ""

    res = await ctx.run_node(tutor_agent, node_input={"message": tutor_prompt})
    tutor_out = TutorOutput(**res)

    yield Event(
        output=tutor_out.model_dump(),
        state={
            "current_practice_problem": tutor_out.practice_problem,
            "last_draft": tutor_out.model_dump()
        }
    )


guardrail_agent = LlmAgent(
    name="guardrail_agent",
    model=Gemini(
        model=MODEL_NAME,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=(
        "You are a safety guardrail. Review the tutor's draft explanation and practice problem. "
        "Verify that it does NOT give away the correct answer to the practice problem, "
        "is encouraging and supportive in tone, and is safe and appropriate for kids."
    ),
    output_schema=GuardrailVerdict,
)


@node(rerun_on_resume=True)
async def safety_guardrail_node(ctx: Context, node_input: dict) -> Event:
    draft = ctx.state.get("last_draft", {})
    explanation = draft.get("explanation", "").lower()
    prob = draft.get("practice_problem", "").lower()

    violation = False
    feedback_reason = ""

    # Python keyword check
    for phrase in DISALLOWED_PHRASES:
        if phrase in explanation or phrase in prob:
            violation = True
            feedback_reason = f"Contains disallowed phrase: '{phrase}'"
            break

    # LLM check
    if not violation:
        res = await ctx.run_node(guardrail_agent, node_input={"message": json.dumps(draft)})
        verdict = GuardrailVerdict(**res)
        if not verdict.is_safe:
            violation = True
            feedback_reason = verdict.feedback

    if violation:
        violations_count = ctx.state.get("guardrail_violations", 0)
        if violations_count < 1:
            ctx.state["guardrail_violations"] = violations_count + 1
            ctx.state["safety_feedback"] = feedback_reason
            return Event(output=node_input, route="regenerate")

    return Event(output=draft, route="approved")


progress_agent = LlmAgent(
    name="progress_agent",
    model=Gemini(
        model=MODEL_NAME,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=(
        "You are a study progress reporter. Summarize the student's recent session history and accomplishments. "
        "Provide an encouraging overview for parents/teachers."
    ),
    output_schema=SummaryOutput,
)


@node(rerun_on_resume=True)
async def progress_node(ctx: Context, node_input: StudentTurn) -> Event:
    history_str = "\n".join(f"Student: {h.get('message')}" for h in ctx.state.get("history", []))
    history_str = scrub_pii(history_str)
    res = await ctx.run_node(progress_agent, node_input={"message": history_str})
    
    # Log progress summary to the local CSV database fallback
    student_id = ctx.state["turn"].get("student_id", "default_student")
    log_progress(student_id, res.get("summary", ""))
    
    return Event(output=res)


@node
def output_sender(ctx: Context, node_input: dict) -> Event:
    if "response" in node_input:
        text_out = node_input["response"]
    elif "summary" in node_input:
        text_out = f"📊 Session Summary:\n\n{node_input['summary']}"
    else:
        text_out = f"{node_input.get('explanation', '')}\n\n📝 Practice Problem:\n{node_input.get('practice_problem', '')}"

    yield Event(content=types.Content(role='model', parts=[types.Part.from_text(text=text_out)]))
    yield Event(output=node_input)


# Graph Topology Wiring

root_agent = Workflow(
    name="study_buddy_workflow",
    edges=[
        (START, parse_input_node),
        (parse_input_node, check_risk_patterns),
        
        # Risk check routing
        (check_risk_patterns, {
            "no_risk": router_node,
            "run_llm_risk_eval": llm_risk_evaluator,
            "rate_limited": output_sender
        }),
        
        # LLM risk eval routing
        (llm_risk_evaluator, check_llm_risk_verdict),
        
        # LLM risk verdict routing
        (check_llm_risk_verdict, {
            "low_risk": router_node,
            "high_risk": approval_node
        }),
        
        # Approval node routing
        (approval_node, {
            "approved": router_node,
            "rejected": rejection_node
        }),
        
        # Rejection node goes to output
        (rejection_node, output_sender),
        
        # Intent routing from router_node
        (router_node, {
            "new_topic": diagnostic_generator,
            "question": tutor_node,
            "progress_check": progress_node,
            "greeting": greeting_node,
            "out_of_scope": out_of_scope_node
        }),
        
        # Greeting and out-of-scope nodes go to output
        (greeting_node, output_sender),
        (out_of_scope_node, output_sender),
        
        # Diagnostic routing
        (diagnostic_generator, diagnostic_node),
        
        (diagnostic_node, {
            "diagnosis_complete": tutor_node
        }),
        
        # Tutoring and guardrail loop
        (tutor_node, safety_guardrail_node),
        
        (safety_guardrail_node, {
            "regenerate": tutor_node,
            "approved": output_sender
        }),
        
        # Progress report to output
        (progress_node, output_sender)
    ]
)

app = App(
    root_agent=root_agent,
    name="app",
    resumability_config=ResumabilityConfig(is_resumable=True)
)
