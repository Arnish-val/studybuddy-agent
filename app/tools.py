import os
import json
import uuid
import datetime
from cryptography.fernet import Fernet

REVIEWS_PATH = os.path.join(os.path.dirname(__file__), "reviews.json")
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

# Simple symmetric encryption setup
# Attempts to read key from env; generates and appends one to .env if missing.
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    ENCRYPTION_KEY = Fernet.generate_key().decode()
    dotenv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.exists(dotenv_path):
        try:
            with open(dotenv_path, "a") as f:
                f.write(f"\nENCRYPTION_KEY={ENCRYPTION_KEY}\n")
        except Exception:
            pass
    os.environ["ENCRYPTION_KEY"] = ENCRYPTION_KEY

fernet = Fernet(ENCRYPTION_KEY.encode())


def _read_reviews() -> list:
    """Helper to decrypt and load reviews list from disk."""
    if not os.path.exists(REVIEWS_PATH):
        return []
    try:
        with open(REVIEWS_PATH, "rb") as f:
            encrypted_data = f.read()
        if not encrypted_data:
            return []
        decrypted = fernet.decrypt(encrypted_data)
        return json.loads(decrypted.decode())
    except Exception:
        # Fallback to plain JSON read in case of unencrypted migration leftovers
        try:
            with open(REVIEWS_PATH) as f:
                return json.load(f)
        except Exception:
            return []


def _write_reviews(reviews: list) -> None:
    """Helper to encrypt and write reviews list to disk."""
    data = json.dumps(reviews, indent=2).encode()
    encrypted = fernet.encrypt(data)
    with open(REVIEWS_PATH, "wb") as f:
        f.write(encrypted)


def flag_message_for_review(student_id: str, message: str, reason: str, timestamp: str) -> dict:
    """Creates a pending human-review record for a message identified as high-risk.

    Args:
        student_id: Unique identifier for the student.
        message: The flagged high-risk message text.
        reason: The reason why this message was flagged (e.g. self-harm, distress, policy bypass).
        timestamp: The original timestamp of the student message.

    Returns:
        A dictionary containing the review record including its unique review_id and status.
    """
    review_id = f"rev_{uuid.uuid4()}"
    reviews = _read_reviews()
    
    # Create new record
    record = {
        "review_id": review_id,
        "student_id": student_id,
        "message": message,
        "reason": reason,
        "timestamp": timestamp,
        "status": "pending",
        "decision": None,
        "decision_timestamp": None,
        "authenticated_user_id": None
    }
    
    reviews.append(record)
    _write_reviews(reviews)
    
    return {"status": "success", "review_id": review_id, "record": record}


def record_parent_decision(review_id: str, decision: str, caller_role: str, authenticated_user_id: str) -> dict:
    """Updates a pending human-review record with a decision from a parent or teacher.

    Args:
        review_id: The unique identifier of the flagged review.
        decision: The decision made. Must be either 'approve' or 'reject'.
        caller_role: The role of the caller. Must be 'parent_teacher'.
        authenticated_user_id: The authenticated user ID of the parent or teacher making the decision.

    Returns:
        A dictionary with the updated review status and decision details.
    """
    if caller_role != "parent_teacher":
        return {"status": "error", "message": "Access Denied: Only parent_teacher can perform this action."}
        
    if not authenticated_user_id:
        return {"status": "error", "message": "Access Denied: Authenticated User ID is required."}
        
    if decision not in ["approve", "reject"]:
        return {"status": "error", "message": "Decision must be either 'approve' or 'reject'."}
        
    reviews = _read_reviews()
    if not reviews:
        return {"status": "error", "message": "No review records found."}
        
    updated = False
    updated_record = {}
    for r in reviews:
        if r.get("review_id") == review_id:
            if r.get("status") == "completed":
                return {"status": "error", "message": "Review decision has already been finalized."}
            r["status"] = "completed"
            r["decision"] = decision
            r["decision_timestamp"] = datetime.datetime.utcnow().isoformat()
            r["authenticated_user_id"] = authenticated_user_id
            updated = True
            updated_record = r
            break
            
    if not updated:
        return {"status": "error", "message": f"Review ID '{review_id}' not found."}
        
    _write_reviews(reviews)
    return {"status": "success", "review_id": review_id, "record": updated_record}


def update_flag_patterns(action: str, pattern: str, caller_role: str) -> dict:
    """Allows administrators to dynamically add or remove keyword risk patterns from config.json.

    Args:
        action: The modification action. Must be either 'add' or 'remove'.
        pattern: The keyword pattern string. Cannot contain regex control characters.
        caller_role: The role of the caller. Must be 'administrator'.

    Returns:
        A dictionary containing the status of the operation and the updated list of patterns.
    """
    if caller_role != "administrator":
        return {"status": "error", "message": "Access Denied: Only administrators can update flag patterns."}
        
    if action not in ["add", "remove"]:
        return {"status": "error", "message": "Action must be either 'add' or 'remove'."}
        
    # Security sanitization check: reject regex operators or patterns longer than 50 chars
    if not pattern or len(pattern) > 50:
        return {"status": "error", "message": "Pattern must be non-empty and less than 50 characters."}
        
    forbidden_chars = ["*", "+", "?", "^", "$", "(", ")", "[", "]", "{", "}", "|", "\\"]
    if any(char in pattern for char in forbidden_chars):
        return {"status": "error", "message": "Regex control characters are not allowed in plain keyword patterns."}
        
    if not os.path.exists(CONFIG_PATH):
        return {"status": "error", "message": "Configuration file not found."}
        
    try:
        with open(CONFIG_PATH) as f:
            config_data = json.load(f)
    except Exception as e:
        return {"status": "error", "message": f"Failed to load config: {str(e)}"}
        
    patterns = config_data.get("flagged_patterns", [])
    
    if action == "add":
        if pattern not in patterns:
            patterns.append(pattern)
            config_data["flagged_patterns"] = patterns
            updated = True
        else:
            updated = False
    elif action == "remove":
        if pattern in patterns:
            patterns.remove(pattern)
            config_data["flagged_patterns"] = patterns
            updated = True
        else:
            updated = False
            
    if updated:
        with open(CONFIG_PATH, "w") as f:
            json.dump(config_data, f, indent=2)
            
    return {"status": "success", "action": action, "pattern": pattern, "flagged_patterns": patterns}
