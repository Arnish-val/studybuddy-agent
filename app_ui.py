import streamlit as st
import os
import json
import datetime
import csv
from dotenv import load_dotenv
load_dotenv()
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.genai import types

from app.agent import root_agent
from app.tools import _read_reviews, record_parent_decision

# Streamlit Page Configuration
st.set_page_config(
    page_title="Study Buddy - Fractions & Decimals AI Tutor",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Premium Styling
st.markdown("""
<style>
    .reportview-container {
        background: linear-gradient(135deg, #1e1e2f 0%, #11111b 100%);
    }
    h1 {
        font-family: 'Outfit', 'Inter', sans-serif;
        font-weight: 800;
        background: linear-gradient(90deg, #6c5ce7 0%, #a8a5e6 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    h3 {
        font-family: 'Outfit', 'Inter', sans-serif;
        color: #a8a5e6;
    }
    .stButton>button {
        background: linear-gradient(90deg, #6c5ce7 0%, #5848c2 100%);
        color: white;
        border: none;
        padding: 0.5rem 1.5rem;
        border-radius: 8px;
        font-weight: bold;
        transition: all 0.3s ease;
    }
    .stButton>button:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(108, 92, 231, 0.4);
    }
    .alert-card {
        background: rgba(225, 112, 85, 0.1);
        border-left: 5px solid #e17055;
        padding: 1rem;
        border-radius: 8px;
        margin-bottom: 1rem;
    }
    .success-card {
        background: rgba(85, 239, 196, 0.1);
        border-left: 5px solid #00b894;
        padding: 1rem;
        border-radius: 8px;
        margin-bottom: 1rem;
    }
</style>
""", unsafe_allow_html=True)

# Initialize Session State Variables
if "session_service" not in st.session_state:
    st.session_state.session_service = InMemorySessionService()
    session = st.session_state.session_service.create_session_sync(user_id="demo_user", app_name="studybuddy")
    st.session_state.session_id = session.id
    st.session_state.runner = Runner(agent=root_agent, session_service=st.session_state.session_service, app_name="studybuddy")
    st.session_state.chat_history = []
    st.session_state.student_id = "demo_student_1"
    st.session_state.pending_approval = False
    st.session_state.active_interrupt = None

# Sidebar Configuration
st.sidebar.title("🛠️ Config & Setup")
st.session_state.student_id = st.sidebar.text_input("Student ID", value=st.session_state.student_id)
subject = st.sidebar.selectbox("Subject Focus", options=["fractions", "decimals"])
st.sidebar.markdown("---")
st.sidebar.info(
    "🎓 **Study Buddy Tutor**\n\n"
    "This AI tutor guides students in grades 6-8 on math concepts using dynamic lessons, scaffolding, and guardrails."
)

# Header Title
st.title("🎓 Study Buddy AI Tutor Portal")

# Main Navigation Tabs
tab1, tab2 = st.tabs(["🎓 Student Chat", "🛡️ Parent / Teacher Portal"])

# Helper to read progress log
def read_progress_log():
    csv_path = os.path.join("app", "data", "progress_log.csv")
    if not os.path.exists(csv_path):
        return []
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return list(reader)
    except Exception:
        return []

# ----------------- Tab 1: Student Chat -----------------
with tab1:
    st.subheader("Interactive Math Lesson")
    
    # Display Chat Messages
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.write(msg["text"])

    # Handle Input from Chat Box
    user_input = st.chat_input("Type your response or question here...")
    
    if user_input:
        # Append Student turn to history
        st.session_state.chat_history.append({"role": "user", "text": user_input})
        with st.chat_message("user"):
            st.write(user_input)

        # Check if there is an active interrupt we are responding to
        new_message = None
        if st.session_state.get("active_interrupt"):
            interrupt_id = st.session_state.active_interrupt
            st.session_state.active_interrupt = None
            new_message = types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=interrupt_id,
                            id=interrupt_id,
                            response={"value": user_input}
                        )
                    )
                ]
            )
        else:
            # Prepare Payload
            payload = {
                "student_id": st.session_state.student_id,
                "message": user_input,
                "subject": subject,
                "timestamp": datetime.datetime.utcnow().isoformat()
            }
            # Build new GenAI Content payload for workflow entry
            new_message = types.Content(
                role="user",
                parts=[types.Part.from_text(text=json.dumps(payload))]
            )

        # Invoke Runner
        with st.spinner("Tutor is thinking..."):
            try:
                events = list(st.session_state.runner.run(
                    new_message=new_message,
                    user_id="demo_user",
                    session_id=st.session_state.session_id,
                    run_config=RunConfig(streaming_mode=StreamingMode.SSE)
                ))
                
                # Check for interrupts or outputs
                tutor_response = ""
                interrupted = False
                
                for event in events:
                    # Detect intermediate / final model texts ONLY from output_sender
                    if event.node_name == "output_sender":
                        if hasattr(event, "content") and event.content and event.content.parts:
                            text_part = "".join(part.text for part in event.content.parts if part.text)
                            if text_part:
                                tutor_response += text_part
                            
                    # Detect RequestInput interruption
                    if hasattr(event, "interrupt_id"):
                        if event.interrupt_id == "teacher_approval":
                            interrupted = True
                        elif event.interrupt_id.startswith("quiz_q_"):
                            st.session_state.active_interrupt = event.interrupt_id
                            tutor_response = event.message
                
                if interrupted:
                    st.session_state.pending_approval = True
                    warning_msg = (
                        "⚠️ **Safety Alert**: Your message has been flagged for safety review. "
                        "A parent or teacher needs to approve this message before you can proceed."
                    )
                    st.session_state.chat_history.append({"role": "assistant", "text": warning_msg})
                    with st.chat_message("assistant"):
                        st.warning(warning_msg)
                elif tutor_response:
                    st.session_state.chat_history.append({"role": "assistant", "text": tutor_response})
                    with st.chat_message("assistant"):
                        st.write(tutor_response)
                else:
                    st.write("Tutor processed input successfully.")
                    
            except Exception as e:
                st.error(f"Error calling workflow agent: {e}")

# ----------------- Tab 2: Parent / Teacher Portal -----------------
with tab2:
    col1, col2 = st.columns([2, 1])
    
    with col1:
        st.subheader("🛡️ Safety Auditing Dashboard")
        
        # Fetch pending/history review logs
        all_reviews = _read_reviews()
        pending_reviews = [r for r in all_reviews if r.get("status") == "pending"]
        
        if not pending_reviews:
            st.success("✅ No pending reviews. All student turns are safe!")
        else:
            st.warning(f"⚠️ There are {len(pending_reviews)} pending reviews requiring action.")
            for r in pending_reviews:
                review_id = r.get("review_id")
                with st.container():
                    st.markdown(
                        f"<div class='alert-card'>"
                        f"<strong>Review ID:</strong> {review_id}<br/>"
                        f"<strong>Student ID:</strong> {r.get('student_id')}<br/>"
                        f"<strong>Reason:</strong> {r.get('reason')}<br/>"
                        f"<strong>Flagged Message:</strong> \"{r.get('message')}\"<br/>"
                        f"</div>",
                        unsafe_allow_html=True
                    )
                    
                    # Buttons to Approve or Reject
                    btn_col1, btn_col2 = st.columns(2)
                    with btn_col1:
                        if st.button("Approve Turn", key=f"app_{review_id}"):
                            # 1. Record parent decision
                            record_parent_decision(
                                review_id=review_id,
                                decision="approve",
                                caller_role="parent_teacher",
                                authenticated_user_id="parent_1"
                            )
                            # 2. Resume session
                            message = types.Content(
                                role="user",
                                parts=[
                                    types.Part(
                                        function_response=types.FunctionResponse(
                                            name="teacher_approval",
                                            id="teacher_approval",
                                            response={"value": "approve"}
                                        )
                                    ),
                                    types.Part(
                                        function_response=types.FunctionResponse(
                                            name="caller_role",
                                            id="caller_role",
                                            response={"value": "parent_teacher"}
                                        )
                                    ),
                                    types.Part(
                                        function_response=types.FunctionResponse(
                                            name="authenticated_user_id",
                                            id="authenticated_user_id",
                                            response={"value": "parent_1"}
                                        )
                                    )
                                ]
                            )
                            with st.spinner("Resuming student session..."):
                                events = list(st.session_state.runner.run(
                                    new_message=message,
                                    user_id="demo_user",
                                    session_id=st.session_state.session_id,
                                    run_config=RunConfig(streaming_mode=StreamingMode.SSE)
                                ))
                                st.session_state.pending_approval = False
                                # Get output from resumption
                                res_text = ""
                                for ev in events:
                                    if ev.node_name == "output_sender":
                                        if ev.content and ev.content.parts:
                                            res_text += "".join(p.text for p in ev.content.parts if p.text)
                                if res_text:
                                    st.session_state.chat_history.append({"role": "assistant", "text": res_text})
                            st.rerun()
                            
                    with btn_col2:
                        if st.button("Reject Turn", key=f"rej_{review_id}"):
                            # 1. Record parent decision
                            record_parent_decision(
                                review_id=review_id,
                                decision="reject",
                                caller_role="parent_teacher",
                                authenticated_user_id="parent_1"
                            )
                            # 2. Resume session
                            message = types.Content(
                                role="user",
                                parts=[
                                    types.Part(
                                        function_response=types.FunctionResponse(
                                            name="teacher_approval",
                                            id="teacher_approval",
                                            response={"value": "reject"}
                                        )
                                    ),
                                    types.Part(
                                        function_response=types.FunctionResponse(
                                            name="caller_role",
                                            id="caller_role",
                                            response={"value": "parent_teacher"}
                                        )
                                    ),
                                    types.Part(
                                        function_response=types.FunctionResponse(
                                            name="authenticated_user_id",
                                            id="authenticated_user_id",
                                            response={"value": "parent_1"}
                                        )
                                    )
                                ]
                            )
                            with st.spinner("Blocking student turn..."):
                                events = list(st.session_state.runner.run(
                                    new_message=message,
                                    user_id="demo_user",
                                    session_id=st.session_state.session_id,
                                    run_config=RunConfig(streaming_mode=StreamingMode.SSE)
                                ))
                                st.session_state.pending_approval = False
                                # Get blocked output
                                res_text = ""
                                for ev in events:
                                    if ev.node_name == "output_sender":
                                        if ev.content and ev.content.parts:
                                            res_text += "".join(p.text for p in ev.content.parts if p.text)
                                if res_text:
                                    st.session_state.chat_history.append({"role": "assistant", "text": res_text})
                            st.rerun()

        # Decrypted historical safety log table
        st.markdown("### Decrypted Historical Audits")
        historical_reviews = [r for r in all_reviews if r.get("status") != "pending"]
        if historical_reviews:
            st.table(historical_reviews)
        else:
            st.info("No resolved safety review history recorded yet.")

    with col2:
        st.subheader("📊 Session Progress Logs")
        
        # Read local CSV progress log
        log_data = read_progress_log()
        if not log_data:
            st.info("No student session summaries recorded in progress_log.csv yet.")
        else:
            st.dataframe(log_data)
            
        # Add a refresh button for log database
        if st.button("Refresh Logs"):
            st.rerun()
