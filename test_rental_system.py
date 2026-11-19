"""
Rent2Swap — Comprehensive Automated End-to-End Rental System & Tracker Test Suite
Tests every lifecycle stage, edge case, concurrency safety rule, and audit log.

Test Matrix:
 1. Listing creation with 'Rent' transaction type and marketplace presence
 2. Self-rental prevention (Owner cannot rent own listing)
 3. Date validation (Past start date rejected, due date < start date rejected)
 4. Rental request submission with agreement acceptance -> status PENDING
 5. Duplicate request prevention (Same student cannot submit multiple pending/active requests)
 6. Owner rejects request -> status REJECTED, item remains Available
 7. Invalid transition prevention (Cannot cancel an already rejected or completed request)
 8. Competing rental requests by multiple students
 9. Owner accepts request -> status ACTIVE, item status Rented, competing requests auto-rejected
10. Unavailable item protection (Cannot rent already Rented item)
11. Overdue synchronization (Past-due active rentals automatically marked OVERDUE in DB)
12. Two-step return workflow Step A (Renter requests return -> status RETURN_REQUESTED)
13. Authorization enforcement (Only owner can confirm return)
14. Two-step return workflow Step B (Owner confirms return -> status COMPLETED, item Available)
15. Rental status log audit trail verification (/request/<id>/history API)
"""

import os
import sys
from datetime import date, timedelta, datetime
from werkzeug.security import generate_password_hash

from app import (
    app,
    execute_query_one,
    execute_query_all,
    execute_query_commit,
    sync_overdue_rentals,
    log_rental_status_transition
)

def run_e2e_rental_tests():
    print("=" * 75)
    print("RENT2SWAP — COMPREHENSIVE RENTAL TRACKER & LIFECYCLE TEST SUITE")
    print("=" * 75)

    client = app.test_client()

    owner_email = "test.owner.rental@college.edu"
    renter1_email = "test.renter1.rental@college.edu"
    renter2_email = "test.renter2.rental@college.edu"

    # Clean up any leftover test data
    existing_users = execute_query_all(
        "SELECT user_id FROM users WHERE email IN (?, ?, ?)",
        (owner_email, renter1_email, renter2_email)
    )
    for u in existing_users:
        u_id = u["user_id"]
        # Delete items and rental requests
        execute_query_commit("DELETE FROM items WHERE user_id = ?", (u_id,))
        execute_query_commit("DELETE FROM rental_requests WHERE renter_id = ? OR owner_id = ?", (u_id, u_id))
        execute_query_commit("DELETE FROM users WHERE user_id = ?", (u_id,))

    # Create 3 test users
    pw_hash = generate_password_hash("password123")
    today = date.today()

    _, owner_id = execute_query_commit("""
        INSERT INTO users (name, email, password, college, location, verification_status)
        VALUES ('Prof Owner', ?, ?, 'Campus Tech', 'Hostel A-101', 'Verified')
    """, (owner_email, pw_hash))

    _, renter1_id = execute_query_commit("""
        INSERT INTO users (name, email, password, college, location, verification_status)
        VALUES ('Student Renter One', ?, ?, 'Campus Tech', 'Hostel B-202', 'Verified')
    """, (renter1_email, pw_hash))

    _, renter2_id = execute_query_commit("""
        INSERT INTO users (name, email, password, college, location, verification_status)
        VALUES ('Student Renter Two', ?, ?, 'Campus Tech', 'Hostel C-303', 'Verified')
    """, (renter2_email, pw_hash))

    print(f"-> Created test accounts: Owner={owner_id}, Renter1={renter1_id}, Renter2={renter2_id}")

    # Helper for logging in
    def login(email):
        client.get("/logout")
        return client.post("/login", data={"email": email, "password": "password123"}, follow_redirects=True)

    passed_tests = 0
    total_tests = 15

    # -------------------------------------------------------------------------
    # TEST 1: Owner creates a listing with Rent transaction type
    # -------------------------------------------------------------------------
    print("\n[TEST 1] Owner posts rental item listing...")
    login(owner_email)
    res = client.post("/add-listing", data={
        "title": "Scientific Calculator FX-991CW",
        "description": "Advanced scientific calculator for engineering math semesters.",
        "category": "Electronics",
        "transaction_type": "Rent",
        "rent_amount": "50",
        "rent_period": "Day",
        "condition": "Like New",
        "city": "Bengaluru",
        "location": "Engineering Block, Room 204"
    }, follow_redirects=True)

    item = execute_query_one("SELECT * FROM items WHERE user_id = ? ORDER BY item_id DESC LIMIT 1", (owner_id,))
    assert item is not None, "Listing was not created in database"
    assert item["transaction_type"] == "Rent", f"Expected Rent, got {item['transaction_type']}"
    assert item["status"] == "Available", f"Expected Available, got {item['status']}"
    item_id = item["item_id"]
    print(f"✓ TEST 1 PASSED: Rental listing #{item_id} created with status 'Available'.")
    passed_tests += 1

    # -------------------------------------------------------------------------
    # TEST 2: Self-rental prevention
    # -------------------------------------------------------------------------
    print("\n[TEST 2] Preventing owner from requesting their own listing...")
    res = client.post(f"/item/{item_id}/request", data={
        "request_type": "Rent",
        "start_date": today.strftime('%Y-%m-%d'),
        "due_date": (today + timedelta(days=5)).strftime('%Y-%m-%d'),
        "agreement": "on"
    }, follow_redirects=True)
    assert b"cannot submit a rental or swap request on your own" in res.data
    req_count = execute_query_one("SELECT COUNT(*) as c FROM rental_requests WHERE item_id = ?", (item_id,))["c"]
    assert req_count == 0, "Self-rental request was improperly inserted into DB"
    print("✓ TEST 2 PASSED: Self-rental correctly blocked by backend validation.")
    passed_tests += 1

    # -------------------------------------------------------------------------
    # TEST 3: Date validation (past start date & inverted due date)
    # -------------------------------------------------------------------------
    print("\n[TEST 3] Date validations (past start date & inverted due date)...")
    login(renter1_email)

    # 3a. Past start date
    past_date = today - timedelta(days=3)
    res = client.post(f"/item/{item_id}/request", data={
        "request_type": "Rent",
        "start_date": past_date.strftime('%Y-%m-%d'),
        "due_date": (today + timedelta(days=3)).strftime('%Y-%m-%d'),
        "agreement": "on"
    }, follow_redirects=True)
    assert b"start date cannot be in the past" in res.data

    # 3b. Inverted due date (due_date < start_date)
    res = client.post(f"/item/{item_id}/request", data={
        "request_type": "Rent",
        "start_date": (today + timedelta(days=5)).strftime('%Y-%m-%d'),
        "due_date": (today + timedelta(days=2)).strftime('%Y-%m-%d'),
        "agreement": "on"
    }, follow_redirects=True)
    assert b"cannot be earlier than" in res.data
    print("✓ TEST 3 PASSED: Past start date and inverted due dates strictly blocked.")
    passed_tests += 1

    # -------------------------------------------------------------------------
    # TEST 4: Valid rental request submission -> status PENDING & log
    # -------------------------------------------------------------------------
    print("\n[TEST 4] Submitting valid rental request with mandatory agreement...")
    start_dt = today
    due_dt = today + timedelta(days=7)
    res = client.post(f"/item/{item_id}/request", data={
        "request_type": "Rent",
        "start_date": start_dt.strftime('%Y-%m-%d'),
        "due_date": due_dt.strftime('%Y-%m-%d'),
        "notes": "Need for Engineering calculus midterms",
        "agreement": "on"
    }, follow_redirects=True)
    assert b"has been sent to the owner" in res.data

    req1 = execute_query_one("SELECT * FROM rental_requests WHERE item_id = ? AND renter_id = ?", (item_id, renter1_id))
    assert req1 is not None, "Rental request record was not created"
    assert req1["status"] == "PENDING", f"Expected PENDING, got {req1['status']}"
    assert req1["agreement_accepted"] in (True, 1), "Agreement acceptance was not recorded"
    req1_id = req1["request_id"]

    # Verify log entry
    log1 = execute_query_one("SELECT * FROM rental_status_log WHERE request_id = ? ORDER BY log_id ASC LIMIT 1", (req1_id,))
    assert log1 is not None, "Rental status log entry was not created"
    assert log1["new_status"] == "PENDING", f"Expected new_status PENDING in log, got {log1['new_status']}"
    assert log1["changed_by"] == renter1_id, "Log changed_by should match renter"
    print(f"✓ TEST 4 PASSED: Request #{req1_id} created in PENDING status with audit log.")
    passed_tests += 1

    # -------------------------------------------------------------------------
    # TEST 5: Duplicate request prevention
    # -------------------------------------------------------------------------
    print("\n[TEST 5] Preventing duplicate rental request by same student...")
    res = client.post(f"/item/{item_id}/request", data={
        "request_type": "Rent",
        "start_date": start_dt.strftime('%Y-%m-%d'),
        "due_date": due_dt.strftime('%Y-%m-%d'),
        "agreement": "on"
    }, follow_redirects=True)
    assert b"already have an active or pending request" in res.data
    count = execute_query_one("SELECT COUNT(*) as c FROM rental_requests WHERE item_id = ? AND renter_id = ?", (item_id, renter1_id))["c"]
    assert count == 1, "Duplicate request was incorrectly allowed"
    print("✓ TEST 5 PASSED: Duplicate request blocked successfully.")
    passed_tests += 1

    # -------------------------------------------------------------------------
    # TEST 6: Owner rejects request -> status REJECTED, item remains Available
    # -------------------------------------------------------------------------
    print("\n[TEST 6] Owner declines rental request...")
    login(owner_email)
    res = client.post(f"/request/{req1_id}/reject", follow_redirects=True)
    assert b"Request declined" in res.data

    req1_after = execute_query_one("SELECT * FROM rental_requests WHERE request_id = ?", (req1_id,))
    assert req1_after["status"] == "REJECTED", f"Expected REJECTED, got {req1_after['status']}"
    item_after = execute_query_one("SELECT * FROM items WHERE item_id = ?", (item_id,))
    assert item_after["status"] == "Available", "Item should remain Available after decline"

    # Verify log
    log_reject = execute_query_one("SELECT * FROM rental_status_log WHERE request_id = ? AND new_status = 'REJECTED'", (req1_id,))
    assert log_reject is not None, "Reject action was not logged"
    assert log_reject["changed_by"] == owner_id, "Reject changed_by should match owner"
    print(f"✓ TEST 6 PASSED: Request #{req1_id} declined, item remains Available, audit log recorded.")
    passed_tests += 1

    # -------------------------------------------------------------------------
    # TEST 7: Invalid transition prevention
    # -------------------------------------------------------------------------
    print("\n[TEST 7] Invalid transition: Renter attempts to cancel an already REJECTED request...")
    login(renter1_email)
    res = client.post(f"/request/{req1_id}/cancel", follow_redirects=True)
    assert b"You can only cancel pending requests" in res.data
    print("✓ TEST 7 PASSED: Invalid state transition rejected by backend.")
    passed_tests += 1

    # -------------------------------------------------------------------------
    # TEST 8: Renter 1 and Renter 2 submit competing requests
    # -------------------------------------------------------------------------
    print("\n[TEST 8] Renter 1 and Renter 2 submit competing requests...")
    # Renter 1 submits new request
    res = client.post(f"/item/{item_id}/request", data={
        "request_type": "Rent",
        "start_date": today.strftime('%Y-%m-%d'),
        "due_date": (today + timedelta(days=5)).strftime('%Y-%m-%d'),
        "notes": "Attempt 2 for midterms",
        "agreement": "on"
    }, follow_redirects=True)
    req2 = execute_query_one("SELECT * FROM rental_requests WHERE item_id = ? AND renter_id = ? AND status = 'PENDING'", (item_id, renter1_id))
    assert req2 is not None
    req2_id = req2["request_id"]

    # Renter 2 submits competing request
    login(renter2_email)
    res = client.post(f"/item/{item_id}/request", data={
        "request_type": "Rent",
        "start_date": today.strftime('%Y-%m-%d'),
        "due_date": (today + timedelta(days=6)).strftime('%Y-%m-%d'),
        "notes": "Competing request for lab exam",
        "agreement": "on"
    }, follow_redirects=True)
    req3 = execute_query_one("SELECT * FROM rental_requests WHERE item_id = ? AND renter_id = ? AND status = 'PENDING'", (item_id, renter2_id))
    assert req3 is not None
    req3_id = req3["request_id"]
    print(f"✓ TEST 8 PASSED: Competing requests created: Req #{req2_id} (Renter 1) & Req #{req3_id} (Renter 2).")
    passed_tests += 1

    # -------------------------------------------------------------------------
    # TEST 9: Owner accepts Req 2 -> item becomes 'Rented', Req 3 auto-declined
    # -------------------------------------------------------------------------
    print("\n[TEST 9] Owner accepts Renter 1's request -> item Rented, competing request auto-declined...")
    login(owner_email)
    res = client.post(f"/request/{req2_id}/accept", follow_redirects=True)
    assert b"Rental request accepted" in res.data

    req2_status = execute_query_one("SELECT status FROM rental_requests WHERE request_id = ?", (req2_id,))["status"]
    assert req2_status == "ACTIVE", f"Expected ACTIVE, got {req2_status}"

    item_rented = execute_query_one("SELECT status FROM items WHERE item_id = ?", (item_id,))["status"]
    assert item_rented == "Rented", f"Expected Rented, got {item_rented}"

    # Competing request 3 MUST be automatically rejected
    req3_status = execute_query_one("SELECT status FROM rental_requests WHERE request_id = ?", (req3_id,))["status"]
    assert req3_status == "REJECTED", f"Expected competing request to be auto-declined (REJECTED), got {req3_status}"

    # Check auto-decline log
    log_auto = execute_query_one("SELECT * FROM rental_status_log WHERE request_id = ? AND new_status = 'REJECTED'", (req3_id,))
    assert log_auto is not None, "Auto-decline was not logged in rental_status_log"
    print(f"✓ TEST 9 PASSED: Req #{req2_id} ACTIVE, item 'Rented', competing Req #{req3_id} auto-declined.")
    passed_tests += 1

    # -------------------------------------------------------------------------
    # TEST 10: Unavailable item protection (Cannot rent already Rented item)
    # -------------------------------------------------------------------------
    print("\n[TEST 10] Attempting to rent an already 'Rented' item...")
    login(renter2_email)
    res = client.post(f"/item/{item_id}/request", data={
        "request_type": "Rent",
        "start_date": today.strftime('%Y-%m-%d'),
        "due_date": (today + timedelta(days=4)).strftime('%Y-%m-%d'),
        "agreement": "on"
    }, follow_redirects=True)
    assert b"not accepting new requests" in res.data or b"currently rented" in res.data
    print("✓ TEST 10 PASSED: Rented items safely reject new rental requests.")
    passed_tests += 1

    # -------------------------------------------------------------------------
    # TEST 11: Backend Overdue synchronization
    # -------------------------------------------------------------------------
    print("\n[TEST 11] Automatic Overdue synchronization...")
    # Simulate past due date on active rental request 2
    yesterday = today - timedelta(days=1)
    execute_query_commit("UPDATE rental_requests SET due_date = ? WHERE request_id = ?", (yesterday.strftime('%Y-%m-%d'), req2_id))
    
    # Run sync_overdue_rentals()
    sync_overdue_rentals()

    req2_overdue = execute_query_one("SELECT status FROM rental_requests WHERE request_id = ?", (req2_id,))["status"]
    assert req2_overdue == "OVERDUE", f"Expected OVERDUE in database, got {req2_overdue}"

    log_overdue = execute_query_one("SELECT * FROM rental_status_log WHERE request_id = ? AND new_status = 'OVERDUE'", (req2_id,))
    assert log_overdue is not None, "Overdue transition was not logged in rental_status_log"
    print(f"✓ TEST 11 PASSED: Rental #{req2_id} automatically transitioned to OVERDUE in database.")
    passed_tests += 1

    # -------------------------------------------------------------------------
    # TEST 12: Two-Step Return Step A: Renter requests return -> RETURN_REQUESTED
    # -------------------------------------------------------------------------
    print("\n[TEST 12] Step A: Renter initiates return handover on campus...")
    login(renter1_email)
    res = client.post(f"/request/{req2_id}/return", follow_redirects=True)
    assert b"return initiated" in res.data.lower() or b"hand over the item" in res.data.lower()

    req2_ret_req = execute_query_one("SELECT status FROM rental_requests WHERE request_id = ?", (req2_id,))["status"]
    assert req2_ret_req == "RETURN_REQUESTED", f"Expected RETURN_REQUESTED, got {req2_ret_req}"

    # Item should still be Rented until owner confirms!
    item_still_rented = execute_query_one("SELECT status FROM items WHERE item_id = ?", (item_id,))["status"]
    assert item_still_rented == "Rented", "Item must remain Rented until owner confirms receipt"

    log_ret_req = execute_query_one("SELECT * FROM rental_status_log WHERE request_id = ? AND new_status = 'RETURN_REQUESTED'", (req2_id,))
    assert log_ret_req is not None, "Return requested status was not logged"
    print(f"✓ TEST 12 PASSED: Renter initiated return. Status is RETURN_REQUESTED; item securely held as Rented.")
    passed_tests += 1

    # -------------------------------------------------------------------------
    # TEST 13: Unauthorized user cannot confirm return
    # -------------------------------------------------------------------------
    print("\n[TEST 13] Unauthorized user attempts to confirm return...")
    login(renter2_email)
    res = client.post(f"/request/{req2_id}/confirm-return", follow_redirects=True)
    assert b"Only the item owner can confirm" in res.data
    req2_still_req = execute_query_one("SELECT status FROM rental_requests WHERE request_id = ?", (req2_id,))["status"]
    assert req2_still_req == "RETURN_REQUESTED", "Unauthorized user was able to alter rental state"
    print("✓ TEST 13 PASSED: Unauthorized return confirmation blocked.")
    passed_tests += 1

    # -------------------------------------------------------------------------
    # TEST 14: Step B: Owner confirms safe return -> status COMPLETED, item Available
    # -------------------------------------------------------------------------
    print("\n[TEST 14] Step B: Owner inspects and confirms item return...")
    login(owner_email)
    res = client.post(f"/request/{req2_id}/confirm-return", follow_redirects=True)
    assert b"safely confirmed" in res.data.lower() or b"review" in res.data.lower()

    req2_completed = execute_query_one("SELECT * FROM rental_requests WHERE request_id = ?", (req2_id,))
    assert req2_completed["status"] == "COMPLETED", f"Expected COMPLETED, got {req2_completed['status']}"
    assert req2_completed["returned_date"] is not None, "Returned date was not set"

    item_available = execute_query_one("SELECT status FROM items WHERE item_id = ?", (item_id,))["status"]
    assert item_available == "Available", f"Expected item status Available, got {item_available}"

    log_completed = execute_query_one("SELECT * FROM rental_status_log WHERE request_id = ? AND new_status = 'COMPLETED'", (req2_id,))
    assert log_completed is not None, "COMPLETED transition was not logged"
    print(f"✓ TEST 14 PASSED: Rental #{req2_id} marked COMPLETED; item #{item_id} restored to 'Available'.")
    passed_tests += 1

    # -------------------------------------------------------------------------
    # TEST 15: Rental tracker timeline API (/request/<id>/history)
    # -------------------------------------------------------------------------
    print("\n[TEST 15] Audit trail history API verification...")
    res = client.get(f"/request/{req2_id}/history")
    data = res.get_json()
    assert data["success"] is True, "History API returned failure"
    assert data["status"] == "COMPLETED"
    history = data["history"]
    status_flow = [h["new_status"] for h in history]
    print(f"  Recorded status transitions for Req #{req2_id}: {' -> '.join(status_flow)}")
    assert "PENDING" in status_flow, "PENDING missing from history"
    assert "ACTIVE" in status_flow, "ACTIVE missing from history"
    assert "RETURN_REQUESTED" in status_flow, "RETURN_REQUESTED missing from history"
    assert "COMPLETED" in status_flow, "COMPLETED missing from history"
    print("✓ TEST 15 PASSED: Full lifecycle audit trail verified through history API.")
    passed_tests += 1

    # Clean up test records
    print("\nCleaning up test records...")
    execute_query_commit("DELETE FROM rental_status_log WHERE request_id IN (?, ?, ?)", (req1_id, req2_id, req3_id))
    execute_query_commit("DELETE FROM rental_requests WHERE request_id IN (?, ?, ?)", (req1_id, req2_id, req3_id))
    execute_query_commit("DELETE FROM items WHERE item_id = ?", (item_id,))
    execute_query_commit("DELETE FROM users WHERE user_id IN (?, ?, ?)", (owner_id, renter1_id, renter2_id))
    print("✓ Test cleanup completed.")

    print("\n" + "=" * 75)
    print(f"ALL {passed_tests}/{total_tests} RENTAL SYSTEM & TRACKER TESTS PASSED SUCCESSFULLY!")
    print("=" * 75)
    return True

if __name__ == "__main__":
    success = run_e2e_rental_tests()
    sys.exit(0 if success else 1)
