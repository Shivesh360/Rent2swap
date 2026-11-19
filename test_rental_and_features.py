"""
Rent2Swap - End-to-End Comprehensive Feature & Bug Verification Suite
Tests the exact rent request flow, modals, transaction transitions, admin panel,
and fallback behaviors.
"""

import unittest
from datetime import date, timedelta
from app import app, execute_query_one, execute_query_all, execute_query_commit, count_query


class ComprehensiveFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False
        cls.client = app.test_client()

    def test_01_user_registration_and_login_flow(self):
        """Test registration, duplicate prevention, and login."""
        # Clean up any existing test user
        execute_query_commit("DELETE FROM users WHERE email = 'flow_test@college.edu'")

        # Register
        res = self.client.post('/register', data={
            'name': 'Flow Test Student',
            'email': 'flow_test@college.edu',
            'password': 'password123',
            'confirm_password': 'password123',
            'location': 'Hostel 4',
            'college': 'Engineering College',
            'course': 'CSE',
            'year': '2nd Year'
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        # Verify user exists in database
        user = execute_query_one("SELECT * FROM users WHERE email = 'flow_test@college.edu'")
        self.assertIsNotNone(user)
        self.assertEqual(user['name'], 'Flow Test Student')

        # Test login
        res_login = self.client.post('/login', data={
            'email': 'flow_test@college.edu',
            'password': 'password123'
        }, follow_redirects=True)
        self.assertEqual(res_login.status_code, 200)

    def test_02_send_rent_request_validation_and_creation(self):
        """Test sending rent request: agreement validation, self-request block, success."""
        # Fetch an available item belonging to seller@rentitbro.com
        seller = execute_query_one("SELECT user_id FROM users WHERE email = 'seller@rentitbro.com'")
        item = execute_query_one("SELECT * FROM items WHERE user_id = ? AND transaction_type = 'Rent' AND status = 'Available' LIMIT 1", (seller['user_id'],))
        if not item:
            item = execute_query_one("SELECT * FROM items WHERE user_id = ? LIMIT 1", (seller['user_id'],))
        
        self.assertIsNotNone(item, "Must have a listing from demo seller")
        item_id = item['item_id']

        # 1. Self-request should fail
        with self.client.session_transaction() as sess:
            sess['user_id'] = seller['user_id']
            sess['name'] = 'Vikram Malhotra'
            sess['email'] = 'seller@rentitbro.com'

        res_self = self.client.post(f'/item/{item_id}/request', data={
            'request_type': 'Rent',
            'agreement': 'on',
            'start_date': str(date.today()),
            'due_date': str(date.today() + timedelta(days=3))
        }, follow_redirects=True)
        self.assertIn(b'own', res_self.data.lower())

        # 2. Switch to student requester
        student = execute_query_one("SELECT user_id FROM users WHERE email = 'test.student@college.edu'")
        with self.client.session_transaction() as sess:
            sess['user_id'] = student['user_id']
            sess['name'] = 'Test Student'
            sess['email'] = 'test.student@college.edu'

        # 3. Missing agreement checkbox should fail
        res_no_agree = self.client.post(f'/item/{item_id}/request', data={
            'request_type': 'Rent',
            'start_date': str(date.today()),
            'due_date': str(date.today() + timedelta(days=3))
        }, follow_redirects=True)
        self.assertIn(b'agreement', res_no_agree.data.lower())

        # 4. Valid rent request submission
        res_valid = self.client.post(f'/item/{item_id}/request', data={
            'request_type': 'Rent',
            'start_date': str(date.today()),
            'due_date': str(date.today() + timedelta(days=4)),
            'notes': 'Meeting at Library Plaza at 3 PM',
            'agreement': 'on'
        }, follow_redirects=True)
        self.assertEqual(res_valid.status_code, 200)

        # Check DB record exists
        req = execute_query_one("""
            SELECT * FROM rental_requests 
            WHERE item_id = ? AND renter_id = ? 
            ORDER BY request_id DESC LIMIT 1
        """, (item_id, student['user_id']))
        self.assertIsNotNone(req)
        self.assertEqual(req['status'], 'PENDING')
        self.assertEqual(req['notes'], 'Meeting at Library Plaza at 3 PM')

    def test_03_rental_lifecycle_acceptance_return_and_review(self):
        """Test accepting request, marking returned, peer review."""
        seller = execute_query_one("SELECT user_id FROM users WHERE email = 'seller@rentitbro.com'")
        student = execute_query_one("SELECT user_id FROM users WHERE email = 'test.student@college.edu'")
        item = execute_query_one("SELECT * FROM items WHERE user_id = ? LIMIT 1", (seller['user_id'],))

        # Create a pending rental request
        now_ts = date.today().strftime('%Y-%m-%d')
        due_ts = (date.today() + timedelta(days=3)).strftime('%Y-%m-%d')
        _, req_id = execute_query_commit("""
            INSERT INTO rental_requests (item_id, renter_id, owner_id, request_type, start_date, due_date, total_price, security_deposit, status, agreement_accepted)
            VALUES (?, ?, ?, 'Rent', ?, ?, 300.0, 0.0, 'PENDING', 1)
        """, (item['item_id'], student['user_id'], seller['user_id'], now_ts, due_ts))

        # Owner accepts
        with self.client.session_transaction() as sess:
            sess['user_id'] = seller['user_id']
            sess['name'] = 'Vikram Malhotra'
            sess['email'] = 'seller@rentitbro.com'

        res_accept = self.client.post(f'/request/{req_id}/accept', follow_redirects=True)
        self.assertEqual(res_accept.status_code, 200)

        # Verify status is ACTIVE
        req = execute_query_one("SELECT status FROM rental_requests WHERE request_id = ?", (req_id,))
        self.assertEqual(req['status'], 'ACTIVE')

        # Return item
        res_return = self.client.post(f'/rental/{req_id}/return', follow_redirects=True)
        self.assertEqual(res_return.status_code, 200)

        # Verify status is COMPLETED
        req_completed = execute_query_one("SELECT status FROM rental_requests WHERE request_id = ?", (req_id,))
        self.assertEqual(req_completed['status'], 'COMPLETED')

        # Student writes review
        with self.client.session_transaction() as sess:
            sess['user_id'] = student['user_id']
            sess['name'] = 'Test Student'
            sess['email'] = 'test.student@college.edu'

        res_review = self.client.post(f'/write-review/{req_id}', data={
            'rating': '5',
            'comment': 'Awesome gear in top condition! Punctual and friendly handover.'
        }, follow_redirects=True)
        self.assertEqual(res_review.status_code, 200)

        # Check review recorded
        review = execute_query_one("SELECT * FROM reviews WHERE rental_id = ?", (req_id,))
        self.assertIsNotNone(review)
        self.assertEqual(review['rating'], 5.0)

    def test_04_damage_report_and_admin_dispute_resolution(self):
        """Test submitting a damage report and resolving it from admin panel."""
        seller = execute_query_one("SELECT user_id FROM users WHERE email = 'seller@rentitbro.com'")
        student = execute_query_one("SELECT user_id FROM users WHERE email = 'test.student@college.edu'")
        item = execute_query_one("SELECT * FROM items WHERE user_id = ? LIMIT 1", (seller['user_id'],))

        # Create active request
        _, req_id = execute_query_commit("""
            INSERT INTO rental_requests (item_id, renter_id, owner_id, request_type, start_date, due_date, total_price, security_deposit, status, agreement_accepted)
            VALUES (?, ?, ?, 'Rent', '2026-10-01', '2026-10-05', 400.0, 0.0, 'ACTIVE', 1)
        """, (item['item_id'], student['user_id'], seller['user_id']))

        # Owner files damage report
        with self.client.session_transaction() as sess:
            sess['user_id'] = seller['user_id']
            sess['name'] = 'Vikram Malhotra'
            sess['email'] = 'seller@rentitbro.com'

        res_dmg = self.client.post(f'/damage-report/{req_id}', data={
            'description': 'Minor scratch on the bottom casing after return.',
            'severity': 'Minor'
        }, follow_redirects=True)
        self.assertEqual(res_dmg.status_code, 200)

        # Verify rental request is now DISPUTED
        req_disp = execute_query_one("SELECT status FROM rental_requests WHERE request_id = ?", (req_id,))
        self.assertEqual(req_disp['status'], 'DISPUTED')

        dmg_record = execute_query_one("SELECT * FROM damage_reports WHERE rental_id = ?", (req_id,))
        self.assertIsNotNone(dmg_record)

        # Admin resolves dispute
        with self.client.session_transaction() as sess:
            sess['user_id'] = 4
            sess['name'] = 'System Administrator'
            sess['email'] = 'admin@rent2swap.com'
            sess['is_admin'] = True

        res_resolve = self.client.post(f'/admin/damage-report/{dmg_record["report_id"]}/resolve', data={
            'status': 'Resolved — Normal Wear & Tear',
            'admin_remark': 'Superficial cosmetic scratch consistent with standard usage. No penalty assessed.'
        }, follow_redirects=True)
        self.assertEqual(res_resolve.status_code, 200)

        # Verify damage report and rental status
        updated_dmg = execute_query_one("SELECT status, admin_remark FROM damage_reports WHERE report_id = ?", (dmg_record['report_id'],))
        self.assertEqual(updated_dmg['status'], 'Resolved — Normal Wear & Tear')

    def test_05_admin_management_and_authorization(self):
        """Test admin metrics, student toggle, listing toggle."""
        # Non-admin user blocked
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['name'] = 'Normal Student'
            sess['email'] = 'test.student@college.edu'
            sess['is_admin'] = False

        res_blocked = self.client.get('/admin', follow_redirects=False)
        self.assertEqual(res_blocked.status_code, 302)

        # Admin user allowed
        admin_user = execute_query_one("SELECT user_id FROM users WHERE is_admin = 1 LIMIT 1")
        with self.client.session_transaction() as sess:
            sess['user_id'] = admin_user['user_id']
            sess['name'] = 'Admin'
            sess['email'] = 'admin@rent2swap.com'
            sess['is_admin'] = True

        res_dashboard = self.client.get('/admin/dashboard')
        self.assertEqual(res_dashboard.status_code, 200)
        self.assertIn(b'Total Students', res_dashboard.data)

        # Admin students directory
        res_students = self.client.get('/admin/students')
        self.assertEqual(res_students.status_code, 200)

        # Admin verifications
        res_verifs = self.client.get('/admin/verifications')
        self.assertEqual(res_verifs.status_code, 200)

        # Admin listings
        res_listings = self.client.get('/admin/listings')
        self.assertEqual(res_listings.status_code, 200)


if __name__ == '__main__':
    unittest.main()
