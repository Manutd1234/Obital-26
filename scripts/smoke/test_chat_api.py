"""
Test the chat API locally without the UI.

Usage:
    1. Start the backend:  python -m uvicorn backend.app.main:app --port 8000
    2. Run this script:    python scripts/smoke/test_chat_api.py
"""

import json
import sys
import urllib.request
import urllib.error

BASE = "http://localhost:8000"
ACCOUNT = "admin"

DSML = chr(0xFF5C) * 2 + "DSML" + chr(0xFF5C) * 2


def get(path):
    req = urllib.request.Request(f"{BASE}{path}")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE}{path}", data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def post_stream(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE}{path}", data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    events = []
    with urllib.request.urlopen(req) as resp:
        buf = ""
        while True:
            chunk = resp.read(4096)
            if not chunk:
                break
            buf += chunk.decode()
            while "\n\n" in buf:
                raw, buf = buf.split("\n\n", 1)
                for line in raw.strip().split("\n"):
                    line = line.strip()
                    if line.startswith("data: "):
                        events.append(json.loads(line[6:]))
    return events


def has_dsml(text):
    return DSML in text


passed = 0
failed = 0


def check(name, condition):
    global passed, failed
    if condition:
        print(f"  PASS: {name}")
        passed += 1
    else:
        print(f"  FAIL: {name}")
        failed += 1


# --- Tests ---

print("1. Health check")
try:
    r = get("/health")
    check("GET /health returns ok", r.get("status") == "ok")
except Exception as e:
    check("GET /health reachable", False)
    print(f"         Is the backend running? ({e})")
    sys.exit(1)

print("\n2. DB health check")
try:
    r = get("/health/db")
    check("GET /health/db returns enabled", r.get("enabled") is not None)
except Exception as e:
    check("GET /health/db reachable", False)

print("\n3. Register + login")
try:
    r = post("/auth/register", {"username": ACCOUNT, "pin": "1234"})
    check("POST /auth/register", r.get("username") == ACCOUNT or "already" in str(r.get("detail", "")).lower())
except urllib.error.HTTPError as e:
    detail = json.loads(e.read()).get("detail", "")
    check("POST /auth/register (already exists)", "already" in detail.lower() or e.code == 200)

try:
    r = post("/auth/login", {"username": ACCOUNT, "pin": "1234"})
    check("POST /auth/login", r.get("username") == ACCOUNT)
except Exception as e:
    check("POST /auth/login", False)

print("\n4. Create chat session")
try:
    r = post(f"/chat/sessions?account_id={ACCOUNT}", {"lesson_id": None})
    session_id = r.get("session_id")
    check("POST /chat/sessions returns session_id", session_id is not None)
except Exception as e:
    check("POST /chat/sessions", False)
    session_id = None

print("\n5. Non-streaming chat")
if session_id:
    try:
        r = post(
            f"/chat/sessions/{session_id}/messages?account_id={ACCOUNT}",
            {"message": "What is 2+2?", "lesson_id": None, "doc_chunk_ids": []},
        )
        reply = r.get("reply", "")
        check("POST /messages returns reply", len(reply) > 0)
        check("Reply has no DSML tags", not has_dsml(reply))
    except Exception as e:
        check("POST /messages", False)
        print(f"         {e}")

print("\n6. Streaming chat (SSE)")
if session_id:
    try:
        events = post_stream(
            f"/chat/sessions/{session_id}/messages/stream?account_id={ACCOUNT}",
            {"message": "Hello, how are you?", "lesson_id": None, "doc_chunk_ids": []},
        )
        check("SSE stream returns events", len(events) > 0)
        types = [e.get("event") for e in events]
        check("SSE has done event", "done" in types)

        token_events = [e for e in events if e.get("event") == "token"]
        all_text = "".join(e.get("data", "") for e in token_events)
        check("Token text has no DSML tags", not has_dsml(all_text))
        if has_dsml(all_text):
            print(f"         DSML leaked: {all_text[:200]}")

        error_events = [e for e in events if e.get("event") == "error"]
        check("No error events", len(error_events) == 0)
        if error_events:
            for e in error_events:
                print(f"         Error: {e.get('data')}")
    except Exception as e:
        check("SSE stream", False)
        print(f"         {e}")

print("\n7. Server-side text search")
try:
    r = post(
        f"/docs/search/text?account_id={ACCOUNT}",
        {"query": "test", "top_k": 3, "threshold": 0.0},
    )
    check("POST /docs/search/text returns list", isinstance(r, list))
except urllib.error.HTTPError as e:
    check("POST /docs/search/text", False)
    print(f"         HTTP {e.code}: {json.loads(e.read()).get('detail', '')}")
except Exception as e:
    check("POST /docs/search/text", False)
    print(f"         {e}")

print("\n8. Quiz grading endpoint")
try:
    fake_quiz = {
        "quiz_id": "test_quiz",
        "questions": [
            {
                "question_id": "q1",
                "topic_id": "math",
                "topic_title": "Basic Math",
                "question": "What is 2+2?",
                "expected_answer": "4",
                "difficulty": "easy",
                "question_mode": "mcq",
                "options": ["3", "4", "5", "6"],
                "correct_option_index": 1,
            },
            {
                "question_id": "q2",
                "topic_id": "physics",
                "topic_title": "Gravity",
                "question": "Explain gravity",
                "expected_answer": "Gravity is a force that attracts objects toward each other.",
                "difficulty": "medium",
                "question_mode": "open",
                "options": [],
                "correct_option_index": None,
            },
        ],
    }
    answers = [
        {"question_id": "q1", "answer": "4"},
        {"question_id": "q2", "answer": "Gravity pulls things down."},
    ]
    r = post(f"/quiz/grade?account_id={ACCOUNT}", {"quiz": fake_quiz, "answers": answers})
    check("POST /quiz/grade returns result", r.get("quiz_id") == "test_quiz")
    check("Grading found correct answer", r.get("correct_answers", 0) >= 1)
    check("Score is reasonable", 0 <= r.get("score_percent", -1) <= 100)
except urllib.error.HTTPError as e:
    check("POST /quiz/grade", False)
    print(f"         HTTP {e.code}: {json.loads(e.read()).get('detail', '')}")
except Exception as e:
    check("POST /quiz/grade", False)
    print(f"         {e}")

print("\n" + "=" * 50)
print(f"Results: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
