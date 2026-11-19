"""
Comprehensive Testing Script for Rent2Swap Security Question Recovery System
Verifies all 12 points from the user prompt:
1. New user registration with a security question.
2. Registration without selecting a question.
3. Registration without entering an answer.
4. Password recovery with the correct answer.
5. Password recovery with an incorrect answer.
6. Repeated incorrect attempts and temporary lockout.
7. Successful password reset.
8. Mismatched new password and confirmation.
9. Login using the newly created password.
10. Existing users configuring security questions.
11. Updating security questions through profile settings.
12. Security answer hashes never being exposed to the frontend.
"""

import time
import re
from werkzeug.security import check_password_hash
from app import (
    app,
    execute_query_one,
    execute_query_commit,
    SECURITY_QUESTIONS,
    normalize_security_answer,
    check_recovery_rate_limit,
    clear_recovery_rate_limit,
    _recovery_rate_limits,
    _reset_tokens
)

def run_tests():
    print("=" * 60)
    print("STARTING RENT2SWAP SECURITY QUESTION TEST SUITE")
    print("=" * 60)

    client = app.test_client()

    # Clean up rate limits and reset tokens before testing
    _recovery_rate_limits.clear()
    _reset_tokens.clear()

    test_email_1 = "test.secuser1@college.edu"
    test_email_2 = "test.secuser2@college.edu"
    test_email_existing = "test.existing@college.edu"

    # Clean existing test users if any
    execute_query_commit("DELETE FROM users WHERE email IN (?, ?, ?)", (test_email_1, test_email_2, test_email_existing))

    # ---------------------------------------------------------
    # TEST 2: Registration without selecting a question
    # ---------------------------------------------------------
    print("\n[TEST 2] Registration without selecting a question...")
    res = client.post("/register", data={
        "name": "No Question User",
        "email": test_email_1,
        "password": "password123",
        "confirm_password": "password123",
        "location": "Main Campus",
        "college": "BCA College",
        "security_question": "",
        "security_answer": "Mumbai",
        "confirm_security_answer": "Mumbai"
    }, follow_redirects=True)
    assert b"Please select a valid security question" in res.data, "Failed: Should reject registration without question"
    print("✓ PASSED: Registration correctly rejected when no security question is selected.")

    # ---------------------------------------------------------
    # TEST 3: Registration without entering an answer
    # ---------------------------------------------------------
    print("\n[TEST 3] Registration without entering an answer...")
    res = client.post("/register", data={
        "name": "No Answer User",
        "email": test_email_1,
        "password": "password123",
        "confirm_password": "password123",
        "location": "Main Campus",
        "college": "BCA College",
        "security_question": SECURITY_QUESTIONS[0],
        "security_answer": "",
        "confirm_security_answer": ""
    }, follow_redirects=True)
    assert b"Security question answer and confirmation are both required" in res.data, "Failed: Should reject registration without answer"
    print("✓ PASSED: Registration correctly rejected when answer is empty.")

    # ---------------------------------------------------------
    # TEST 1: New user registration with a security question
    # ---------------------------------------------------------
    print("\n[TEST 1] New user registration with security question...")
    chosen_question = SECURITY_QUESTIONS[0]  # "What is your birthplace?"
    chosen_answer = "  New Delhi  "
    expected_normalized = "new delhi"

    res = client.post("/register", data={
        "name": "Security Student",
        "email": test_email_1,
        "password": "initialPassword123",
        "confirm_password": "initialPassword123",
        "location": "South Campus",
        "college": "BCA Tech",
        "security_question": chosen_question,
        "security_answer": chosen_answer,
        "confirm_security_answer": chosen_answer
    }, follow_redirects=True)
    assert res.status_code == 200, "Registration response failed"

    user = execute_query_one("SELECT * FROM users WHERE email = ?", (test_email_1,))
    assert user is not None, "User was not created in database"
    assert user["security_question"] == chosen_question, f"Expected question {chosen_question}, got {user['security_question']}"
    assert user["security_answer_hash"] != "", "Security answer hash is missing"
    assert "new delhi" not in user["security_answer_hash"].lower(), "Plaintext answer must not be stored"
    assert check_password_hash(user["security_answer_hash"], expected_normalized), "Normalized answer failed hash verification"
    print(f"✓ PASSED: User successfully registered. Question saved: '{user['security_question']}', Answer securely hashed.")

    # ---------------------------------------------------------
    # TEST 12: Security answer hashes never exposed to frontend
    # ---------------------------------------------------------
    print("\n[TEST 12] Security answer hashes never exposed to frontend...")
    res_identify = client.post("/api/forgot-password/identify", json={"email": test_email_1})
    ident_data = res_identify.get_json()
    assert ident_data["success"] is True, "Identify API failed"
    assert "question" in ident_data, "Question missing"
    assert "security_answer_hash" not in ident_data, "Security answer hash exposed in API!"
    assert "answer" not in ident_data, "Answer exposed in API!"
    print("✓ PASSED: API returns question only; security answer and hash are never exposed.")

    # ---------------------------------------------------------
    # TEST 5: Password recovery with an incorrect answer
    # ---------------------------------------------------------
    print("\n[TEST 5] Password recovery with an incorrect answer...")
    res_bad = client.post("/api/forgot-password/verify-answer", json={
        "email": test_email_1,
        "answer": "Kolkata"
    })
    bad_data = res_bad.get_json()
    assert bad_data["success"] is False, "Should fail on wrong answer"
    assert "Incorrect security answer" in bad_data["message"], "Wrong answer message missing"
    print("✓ PASSED: Incorrect security answer rejected with attempt counter.")

    # ---------------------------------------------------------
    # TEST 6: Repeated incorrect attempts and temporary lockout
    # ---------------------------------------------------------
    print("\n[TEST 6] Repeated incorrect attempts and temporary lockout...")
    # Send 4 more incorrect attempts (total 5)
    for i in range(4):
        res_lock = client.post("/api/forgot-password/verify-answer", json={
            "email": test_email_1,
            "answer": f"WrongAttempt{i}"
        })
    lock_data = res_lock.get_json()
    assert lock_data.get("locked") is True, "User should be locked out after 5 failed attempts"
    assert "locked for 10 minutes" in lock_data["message"], "Lockout message missing"

    # Even a correct answer is rejected while locked
    res_try_locked = client.post("/api/forgot-password/verify-answer", json={
        "email": test_email_1,
        "answer": "New Delhi"
    })
    assert res_try_locked.status_code == 429, "Locked request should return 429"
    print("✓ PASSED: Rate limiting triggered after 5 failed attempts with 10-minute temporary lockout.")

    # Clear rate limit for subsequent tests
    clear_recovery_rate_limit(test_email_1)

    # ---------------------------------------------------------
    # TEST 4: Password recovery with the correct answer
    # ---------------------------------------------------------
    print("\n[TEST 4] Password recovery with the correct answer (case-insensitive & whitespace normalized)...")
    res_good = client.post("/api/forgot-password/verify-answer", json={
        "email": test_email_1,
        "answer": "  nEW   DeLhI  "
    })
    good_data = res_good.get_json()
    assert good_data["success"] is True, f"Failed: {good_data}"
    assert "reset_token" in good_data, "Reset token missing from response"
    valid_token = good_data["reset_token"]
    print(f"✓ PASSED: Correct answer verified successfully! Short-lived token issued: {valid_token[:12]}...")

    # ---------------------------------------------------------
    # TEST 8: Mismatched new password and confirmation
    # ---------------------------------------------------------
    print("\n[TEST 8] Mismatched new password and confirmation...")
    res_mismatch = client.post("/api/forgot-password/reset", json={
        "reset_token": valid_token,
        "new_password": "NewSecretPassword1",
        "confirm_new_password": "DifferentPassword2"
    })
    mismatch_data = res_mismatch.get_json()
    assert mismatch_data["success"] is False, "Should reject mismatched passwords"
    assert "do not match" in mismatch_data["message"], "Mismatch error message missing"
    print("✓ PASSED: Password reset correctly rejected when new passwords do not match.")

    # ---------------------------------------------------------
    # TEST 7: Successful password reset
    # ---------------------------------------------------------
    print("\n[TEST 7] Successful password reset...")
    # Re-verify to get a fresh token (since previous test consumed or kept it)
    res_verify_again = client.post("/api/forgot-password/verify-answer", json={
        "email": test_email_1,
        "answer": "New Delhi"
    })
    fresh_token = res_verify_again.get_json()["reset_token"]

    res_reset = client.post("/api/forgot-password/reset", json={
        "reset_token": fresh_token,
        "new_password": "BrandNewPassword2026",
        "confirm_new_password": "BrandNewPassword2026"
    })
    reset_data = res_reset.get_json()
    assert reset_data["success"] is True, f"Reset failed: {reset_data}"
    assert "Your password has been reset successfully. You can now log in using your new password." in reset_data["message"]
    print("✓ PASSED: Password reset succeeded with exact prompt success message.")

    # Verify single-use: Using same token again must fail
    res_reuse = client.post("/api/forgot-password/reset", json={
        "reset_token": fresh_token,
        "new_password": "AnotherPassword3",
        "confirm_new_password": "AnotherPassword3"
    })
    assert res_reuse.get_json()["success"] is False, "Single-use token re-use should fail"
    print("✓ PASSED: Single-use token immediately consumed and cannot be re-used.")

    # ---------------------------------------------------------
    # TEST 9: Login using the newly created password
    # ---------------------------------------------------------
    print("\n[TEST 9] Login using the newly created password...")
    client.get("/logout")  # Ensure logged out
    # Old password fails
    res_old_login = client.post("/login", data={
        "email": test_email_1,
        "password": "initialPassword123"
    }, follow_redirects=True)
    assert b"Incorrect password" in res_old_login.data, "Old password should not work"

    # New password succeeds
    res_new_login = client.post("/login", data={
        "email": test_email_1,
        "password": "BrandNewPassword2026"
    }, follow_redirects=True)
    assert b"Welcome back" in res_new_login.data, "New password login failed"
    print("✓ PASSED: Successfully logged in using newly reset password; old password invalid.")

    # ---------------------------------------------------------
    # TEST 10: Existing users configuring security questions
    # ---------------------------------------------------------
    print("\n[TEST 10] Existing users configuring security questions...")
    # Create an existing user with NO security question
    from werkzeug.security import generate_password_hash
    execute_query_commit("""
        INSERT INTO users (name, email, password, college, course, year, location, rating, status, security_question, security_answer_hash)
        VALUES ('Old User', ?, ?, 'Old College', 'BCA', '3', 'North Campus', 5.0, 'active', '', '')
    """, (test_email_existing, generate_password_hash("oldUserPass123")))

    existing_user = execute_query_one("SELECT * FROM users WHERE email = ?", (test_email_existing,))
    assert existing_user["security_question"] == "" or existing_user["security_question"] is None, "Should have no question"

    # Log in as existing user
    with client.session_transaction() as sess:
        sess["user_id"] = existing_user["user_id"]
        sess["user_name"] = existing_user["name"]
        sess["user_email"] = existing_user["email"]

    # Dashboard should show security prompt banner
    res_dash = client.get("/dashboard")
    assert b"Security Action Recommended" in res_dash.data, "Security notice banner missing on dashboard for user without question"
    print("✓ PASSED: Existing user without security question receives friendly prompt banner without account lockout.")

    # ---------------------------------------------------------
    # TEST 11: Updating security questions through profile settings
    # ---------------------------------------------------------
    print("\n[TEST 11] Updating security questions through profile settings...")
    # Wrong password fails
    res_bad_pw = client.post("/profile/security-question", data={
        "security_question": SECURITY_QUESTIONS[2],
        "security_answer": "Goa",
        "confirm_security_answer": "Goa",
        "current_password": "WrongPassword999"
    }, follow_redirects=True)
    assert b"Incorrect current password" in res_bad_pw.data, "Should require correct password"

    # Correct password succeeds
    res_update_ok = client.post("/profile/security-question", data={
        "security_question": SECURITY_QUESTIONS[2],
        "security_answer": "Goa",
        "confirm_security_answer": "Goa",
        "current_password": "oldUserPass123"
    }, follow_redirects=True)
    assert b"updated successfully" in res_update_ok.data, "Failed to update security question"

    updated_user = execute_query_one("SELECT * FROM users WHERE email = ?", (test_email_existing,))
    assert updated_user["security_question"] == SECURITY_QUESTIONS[2], "Question was not updated"
    assert check_password_hash(updated_user["security_answer_hash"], "goa"), "Answer hash verification failed"
    print(f"✓ PASSED: Existing user successfully configured security question '{SECURITY_QUESTIONS[2]}' via profile settings.")

    print("\n" + "=" * 60)
    print("ALL 12 TESTS COMPLETED AND PASSED PERFECTLY!")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()
