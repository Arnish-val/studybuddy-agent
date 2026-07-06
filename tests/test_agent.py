import os
import json
import pytest
import time
from cryptography.fernet import Fernet
from app.tools import flag_message_for_review, record_parent_decision, update_flag_patterns
from app.agent import check_risk_patterns, StudentTurn, RATE_LIMIT_CACHE, tutor_node, parse_input_node
from google.adk.agents.context import Context

# Cleanup reviews.json before and after each test
REVIEWS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app", "reviews.json")

@pytest.fixture(autouse=True)
def cleanup_reviews():
    if os.path.exists(REVIEWS_PATH):
        try:
            os.remove(REVIEWS_PATH)
        except Exception:
            pass
    # Reset the rate limit cache
    RATE_LIMIT_CACHE.clear()
    yield
    if os.path.exists(REVIEWS_PATH):
        try:
            os.remove(REVIEWS_PATH)
        except Exception:
            pass
    RATE_LIMIT_CACHE.clear()

def test_record_parent_decision_rejects_invalid_role():
    # 1. Flag a message first
    res = flag_message_for_review(
        student_id="student_1",
        message="Help me",
        reason="distress",
        timestamp="2026-07-06T09:00:00"
    )
    review_id = res["review_id"]
    
    # 2. Call with invalid role
    res_err = record_parent_decision(
        review_id=review_id,
        decision="approve",
        caller_role="student",
        authenticated_user_id="parent_1"
    )
    assert res_err["status"] == "error"
    assert "Access Denied" in res_err["message"]

def test_record_parent_decision_rejects_missing_auth_id():
    res = flag_message_for_review(
        student_id="student_1",
        message="Help me",
        reason="distress",
        timestamp="2026-07-06T09:00:00"
    )
    review_id = res["review_id"]
    
    # Empty string should be rejected
    res_err1 = record_parent_decision(
        review_id=review_id,
        decision="approve",
        caller_role="parent_teacher",
        authenticated_user_id=""
    )
    assert res_err1["status"] == "error"
    assert "Access Denied" in res_err1["message"]

    # None should be rejected
    res_err2 = record_parent_decision(
        review_id=review_id,
        decision="approve",
        caller_role="parent_teacher",
        authenticated_user_id=None
    )
    assert res_err2["status"] == "error"
    assert "Access Denied" in res_err2["message"]

def test_record_parent_decision_prevents_replay():
    res = flag_message_for_review(
        student_id="student_1",
        message="Help me",
        reason="distress",
        timestamp="2026-07-06T09:00:00"
    )
    review_id = res["review_id"]
    
    # First decision should succeed
    res_ok = record_parent_decision(
        review_id=review_id,
        decision="approve",
        caller_role="parent_teacher",
        authenticated_user_id="parent_1"
    )
    assert res_ok["status"] == "success"
    
    # Second decision should fail
    res_err = record_parent_decision(
        review_id=review_id,
        decision="reject",
        caller_role="parent_teacher",
        authenticated_user_id="parent_1"
    )
    assert res_err["status"] == "error"
    assert "already been finalized" in res_err["message"]

def test_student_rate_limiting():
    # Setup turn data
    turn = StudentTurn(
        student_id="spammy_student",
        message="Hello",
        subject="Math",
        timestamp="2026-07-06T09:00:00"
    )
    
    # Call check_risk_patterns 9 times (all should allow through)
    for i in range(9):
        event = check_risk_patterns._func(None, turn)
        assert event.actions.route != "rate_limited"
        
    # The 10th call should be blocked and routed to rate_limited route
    event_blocked = check_risk_patterns._func(None, turn)
    assert event_blocked.actions.route == "rate_limited"
    assert "Slow down" in event_blocked.output["response"]

def test_update_flag_patterns_rejects_non_admin():
    res = update_flag_patterns(
        action="add",
        pattern="test-pattern",
        caller_role="student"
    )
    assert res["status"] == "error"
    assert "Access Denied" in res["message"]

def test_update_flag_patterns_rejects_regex_characters():
    res = update_flag_patterns(
        action="add",
        pattern="suicide*",
        caller_role="administrator"
    )
    assert res["status"] == "error"
    assert "Regex control characters are not allowed" in res["message"]

def test_reviews_log_encrypted_at_rest():
    # 1. Create a review record
    flag_message_for_review(
        student_id="student_secret",
        message="Sensitive distress content",
        reason="distress",
        timestamp="2026-07-06T09:00:00"
    )
    
    # 2. Check the raw file contents
    assert os.path.exists(REVIEWS_PATH)
    with open(REVIEWS_PATH, "rb") as f:
        raw_data = f.read()
        
    # Check that it is not readable as plain JSON
    try:
        json.loads(raw_data.decode())
        assert False, "File was read as plain JSON but should be encrypted!"
    except Exception:
        # Expected: decryption is required
        pass
        
    # 3. Assert decrypting it using Fernet is successful
    key = os.getenv("ENCRYPTION_KEY")
    assert key is not None
    fernet = Fernet(key.encode())
    decrypted = fernet.decrypt(raw_data)
    json_data = json.loads(decrypted.decode())
    assert json_data[0]["student_id"] == "student_secret"


from unittest.mock import AsyncMock

@pytest.mark.asyncio
async def test_tutor_node_loads_skill_dynamically():
    class MockContext:
        def __init__(self, state):
            self.state = state
            self.run_node = AsyncMock(return_value={
                "explanation": "Test fractions explanation",
                "practice_problem": "Calculate 1/2 + 1/4",
                "is_correct": True
            })

    # 1. Test fractions subject
    state_fractions = {
        "turn": {
            "student_id": "student_1",
            "message": "Start fractions topic",
            "subject": "fractions",
            "timestamp": "2026-07-06T09:00:00"
        },
        "history": []
    }
    
    ctx = MockContext(state_fractions)
    events = []
    async for event in tutor_node._func(ctx, {}):
        events.append(event)
        
    assert len(events) == 1
    # Verify tutor_agent was called with prompt containing fractions instructions
    args, kwargs = ctx.run_node.call_args
    assert "=== ACTIVE TUTORING SKILL: FRACTIONS ===" in kwargs["node_input"]["message"]
    assert "Treating numerator and denominator as independent whole numbers" in kwargs["node_input"]["message"]

    # 2. Test decimals subject
    state_decimals = {
        "turn": {
            "student_id": "student_1",
            "message": "Start decimals topic",
            "subject": "decimals",
            "timestamp": "2026-07-06T09:00:00"
        },
        "history": []
    }
    
    ctx2 = MockContext(state_decimals)
    events2 = []
    async for event in tutor_node._func(ctx2, {}):
        events2.append(event)
        
    assert len(events2) == 1
    # Verify tutor_agent was called with prompt containing decimals instructions
    args2, kwargs2 = ctx2.run_node.call_args
    assert "=== ACTIVE TUTORING SKILL: DECIMALS ===" in kwargs2["node_input"]["message"]
    assert "TODO: Add decimal place value models" in kwargs2["node_input"]["message"]


@pytest.mark.asyncio
async def test_tutor_node_rejects_directory_traversal():
    class MockContext:
        def __init__(self, state):
            self.state = state
            self.run_node = AsyncMock(return_value={
                "explanation": "Fallback explanation",
                "practice_problem": "Calculate problem",
                "is_correct": True
            })

    # Test directory traversal attempt
    state_traversal = {
        "turn": {
            "student_id": "student_1",
            "message": "Start traversal topic",
            "subject": "../../etc/passwd",
            "timestamp": "2026-07-06T09:00:00"
        },
        "history": []
    }
    
    ctx = MockContext(state_traversal)
    events = []
    async for event in tutor_node._func(ctx, {}):
        events.append(event)
        
    assert len(events) == 1
    args, kwargs = ctx.run_node.call_args
    # Verify that the subject was resolved to 'fractions' fallback, preventing file read outside skills_dir
    assert "=== ACTIVE TUTORING SKILL: FRACTIONS ===" in kwargs["node_input"]["message"]
    assert "Treating numerator and denominator as independent whole numbers" in kwargs["node_input"]["message"]


from unittest.mock import patch, MagicMock

# Cleanup CSV before and after tests
PROGRESS_CSV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app", "data", "progress_log.csv")

@pytest.fixture
def cleanup_csv():
    if os.path.exists(PROGRESS_CSV_PATH):
        try:
            os.remove(PROGRESS_CSV_PATH)
        except Exception:
            pass
    yield
    if os.path.exists(PROGRESS_CSV_PATH):
        try:
            os.remove(PROGRESS_CSV_PATH)
        except Exception:
            pass

def test_pii_redactor_scrubs_email_and_phone():
    from app.agent import scrub_pii
    raw_text = "My email is student@google.com and phone is 123-456-7890."
    scrubbed = scrub_pii(raw_text)
    assert "[EMAIL_REDACTED]" in scrubbed
    assert "[PHONE_REDACTED]" in scrubbed
    assert "student@google.com" not in scrubbed
    assert "123-456-7890" not in scrubbed

def test_parse_input_node_scrubs_pii():
    # Mock context
    ctx = MockContext({"history": []})
    
    node_input = {
        "student_id": "student_pii",
        "message": "Send mail to teacher@gmail.com or call 555-555-5555",
        "subject": "fractions",
        "timestamp": "2026-07-06T09:00:00"
    }
    
    event = parse_input_node._func(ctx, node_input)
    # Check turn model properties
    assert "[EMAIL_REDACTED]" in event.output.message
    assert "[PHONE_REDACTED]" in event.output.message
    assert "teacher@gmail.com" not in event.output.message

class MockContext:
    def __init__(self, state):
        self.state = state

@patch("app.agent.Client")
def test_background_ambient_idle_trigger(mock_client_class, cleanup_csv):
    from app.agent import SESSION_ACTIVITY, SESSION_HISTORIES
    import app.agent
    
    # Configure mock Client behavior
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client
    
    mock_response = MagicMock()
    mock_response.text = '{"summary": "Practiced fractions. Showing improvement in addition. Suggested focus: division."}'
    mock_client.models.generate_content.return_value = mock_response
    
    # 1. Manually add history and set last activity
    student_id = "idle_student"
    SESSION_HISTORIES[student_id] = [
        {"message": "Hello, I am student_123"},
        {"message": "Need help with fractions"}
    ]
    SESSION_ACTIVITY[student_id] = time.time() - 10
    
    # Temporarily set timeout to 0.1 second for rapid trigger testing
    original_timeout = app.agent.IDLE_TIMEOUT_SECONDS
    app.agent.IDLE_TIMEOUT_SECONDS = 0.1
    
    try:
        # Wait for checker thread to run and trigger finalization
        time.sleep(1.5)
        
        # Verify activity has been popped (indicating background trigger fired)
        assert student_id not in SESSION_ACTIVITY
        
        # Verify CSV log exists and contains the summary
        assert os.path.exists(PROGRESS_CSV_PATH)
        with open(PROGRESS_CSV_PATH, "r", encoding="utf-8") as f:
            content = f.read()
            assert "idle_student" in content
            assert "Practiced fractions" in content
    finally:
        app.agent.IDLE_TIMEOUT_SECONDS = original_timeout


def test_pii_redactor_scrubs_name_and_address():
    from app.agent import scrub_pii
    raw_text = "my name is Sarah, I live at 42 Elm Street"
    scrubbed = scrub_pii(raw_text)
    assert "Sarah" not in scrubbed
    assert "42 Elm Street" not in scrubbed
    assert "[NAME_REDACTED]" in scrubbed
    assert "[ADDRESS_REDACTED]" in scrubbed




