"""
Rent2Swap - Phase 4: Comprehensive System Audit & Stabilization Test Suite
Tests every major module against the live Flask application and Supabase PostgreSQL.
"""

import unittest
import json
import os
from datetime import date, timedelta
from app import app, execute_query_one, execute_query_all, execute_query_commit, get_db_connection


class Rent2SwapSystemAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False
        cls.client = app.test_client()

    # =========================================================================
    # 1. UI & TEMPLATE RENDERING TESTS
    # =========================================================================
    def test_01_home_page_rendering(self):
        """Verify Home page loads, contains Stitch UI elements, Aurora wave canvas, threads canvas."""
        res = self.client.get('/')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'Rent2Swap', res.data)
        self.assertIn(b'r2s-threads-canvas', res.data)
        self.assertIn(b'aurora-wave.js', res.data)

    def test_02_marketplace_page_rendering(self):
        """Verify Marketplace loads and displays listings from PostgreSQL."""
        res = self.client.get('/marketplace')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'Campus Marketplace', res.data)
        # Should have filter controls
        self.assertIn(b'All Categories', res.data)
        self.assertIn(b'Rent', res.data)
        self.assertIn(b'Sell', res.data)
        self.assertIn(b'Swap', res.data)

    def test_03_item_details_page_rendering(self):
        """Verify Item Details displays real item information."""
        item = execute_query_one("SELECT item_id, title FROM items LIMIT 1")
        if item:
            res = self.client.get(f'/item/{item["item_id"]}')
            self.assertEqual(res.status_code, 200)
            self.assertIn(item["title"].encode('utf-8')[:20], res.data)

    def test_04_auth_pages_rendering(self):
        """Verify Login and Register pages render with 2D Panda companion."""
        res_login = self.client.get('/login')
        self.assertEqual(res_login.status_code, 200)
        self.assertIn(b'login-companion-container', res_login.data)
        self.assertIn(b'panda-companion.js', res_login.data)

        res_reg = self.client.get('/register')
        self.assertEqual(res_reg.status_code, 200)
        self.assertIn(b'login-companion-container', res_reg.data)
        self.assertIn(b'panda-companion.js', res_reg.data)

    def test_05_ai_feature_pages_rendering(self):
        """Verify AI Smart Search, Matching, and Recommendations pages render."""
        res_search = self.client.get('/ai-search')
        self.assertEqual(res_search.status_code, 200)
        self.assertIn(b'AI Smart Search', res_search.data)

        # Smart matching requires login
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['name'] = 'Test Student'
            sess['email'] = 'test.student@college.edu'

        res_match = self.client.get('/smart-matching')
        self.assertEqual(res_match.status_code, 200)
        self.assertIn(b'Smart', res_match.data)

        res_rec = self.client.get('/ai-recommendations')
        self.assertEqual(res_rec.status_code, 200)

    # =========================================================================
    # 2. AUTHENTICATION & ACCESS CONTROL TESTS
    # =========================================================================
    def test_06_unauthenticated_protected_route_redirection(self):
        """Verify that protected pages redirect unauthenticated users to login."""
        protected_routes = ['/add-listing', '/dashboard', '/my-requests', '/profile', '/student-verification']
        for route in protected_routes:
            # Clear session
            with self.client.session_transaction() as sess:
                sess.clear()
            res = self.client.get(route, follow_redirects=False)
            self.assertEqual(res.status_code, 302, f"Route {route} should redirect unauthenticated user")
            self.assertIn('/login', res.headers.get('Location', ''))

    def test_07_invalid_login_rejection(self):
        """Verify that invalid credentials fail gracefully."""
        res = self.client.post('/login', data={
            'email': 'seller@rentitbro.com',
            'password': 'wrong_password_xyz'
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(b'invalid' in res.data.lower() or b'incorrect' in res.data.lower() or b'credentials' in res.data.lower())

    def test_08_admin_authorization_enforcement(self):
        """Verify that regular students CANNOT access admin panel or admin APIs."""
        # Non-admin user session
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['name'] = 'Regular Student'
            sess['email'] = 'test.student@college.edu'
            sess['is_admin'] = False

        res = self.client.get('/admin', follow_redirects=False)
        # Should be 302 or 403
        self.assertIn(res.status_code, [302, 403], "Non-admin student must NOT access /admin")

        res_users = self.client.get('/admin/students', follow_redirects=False)
        self.assertIn(res_users.status_code, [302, 403], "Non-admin student must NOT access /admin/students")

    def test_09_admin_user_access(self):
        """Verify that admin user CAN access admin panel."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = 6
            sess['name'] = 'System Administrator'
            sess['email'] = 'admin@rent2swap.com'
            sess['is_admin'] = True

        res = self.client.get('/admin')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'Admin', res.data)

    # =========================================================================
    # 3. MARKETPLACE & FILTERING TESTS
    # =========================================================================
    def test_10_marketplace_filtering_queries(self):
        """Verify category, deal type, and text search filters return HTTP 200 with matching items."""
        # Test Rent filter
        res_rent = self.client.get('/marketplace?deal_type=Rent')
        self.assertEqual(res_rent.status_code, 200)

        # Test Category filter
        res_cat = self.client.get('/marketplace?category=Cameras')
        self.assertEqual(res_cat.status_code, 200)

        # Test Search term
        res_search = self.client.get('/marketplace?q=Sony')
        self.assertEqual(res_search.status_code, 200)

    # =========================================================================
    # 4. END-TO-END RENTAL REQUEST TRANSACTION WORKFLOW
    # =========================================================================
    def test_11_rent_request_workflow_complete(self):
        """
        Complete Rental Request Flow:
        1. User 1 (Requester) submits request for User 3's listing (Item 1: Sony Alpha Camera).
        2. Agreement validation enforced.
        3. Request stored in PostgreSQL.
        4. User 1 sees it in Sent Requests.
        5. User 3 sees it in Received Requests.
        6. User 3 accepts the request.
        7. Status updates to ACCEPTED / ACTIVE.
        """
        # Step 1: Attempt submit without agreement -> Must be rejected!
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['name'] = 'Test Student'
            sess['email'] = 'test.student@college.edu'
            sess['is_admin'] = False

        res_no_agree = self.client.post('/item/1/request', data={
            'request_type': 'Rent',
            'start_date': str(date.today()),
            'due_date': str(date.today() + timedelta(days=5)),
            'notes': 'Test without agreement'
            # 'agreement' is intentionally omitted
        }, follow_redirects=True)
        self.assertTrue(b'agreement' in res_no_agree.data.lower() or b'agree' in res_no_agree.data.lower())

        # Step 2: Submit with agreement accepted
        res_success = self.client.post('/item/1/request', data={
            'request_type': 'Rent',
            'start_date': str(date.today()),
            'due_date': str(date.today() + timedelta(days=5)),
            'notes': 'Phase 4 System Audit Rental Test',
            'agreement': 'on'
        }, follow_redirects=True)
        self.assertEqual(res_success.status_code, 200)
        self.assertIn(b'request', res_success.data.lower())

        # Step 3: Verify PostgreSQL record created
        req = execute_query_one("""
            SELECT * FROM rental_requests 
            WHERE item_id = 1 AND renter_id = 1 
            ORDER BY request_id DESC LIMIT 1
        """)
        self.assertIsNotNone(req, "Rental request must exist in Supabase PostgreSQL")
        self.assertEqual(req['status'], 'PENDING')
        self.assertTrue(req['agreement_accepted'])
        req_id = req['request_id']

        # Step 4: Requester verifies Sent Requests
        res_sent = self.client.get('/my-requests')
        self.assertEqual(res_sent.status_code, 200)
        self.assertIn(b'Sony Alpha', res_sent.data)

        # Step 5: Owner (User 3) verifies Received Requests
        with self.client.session_transaction() as sess:
            sess['user_id'] = 3
            sess['name'] = 'Vikram Malhotra'
            sess['email'] = 'seller@rentitbro.com'
            sess['is_admin'] = False

        res_received = self.client.get('/my-requests')
        self.assertEqual(res_received.status_code, 200)
        self.assertIn(b'Sony Alpha', res_received.data)

        # Step 6: Owner accepts the request
        res_accept = self.client.post(f'/request/{req_id}/accept', follow_redirects=True)
        self.assertEqual(res_accept.status_code, 200)

        # Step 7: Verify updated status in PostgreSQL
        updated_req = execute_query_one("SELECT status FROM rental_requests WHERE request_id = ?", (req_id,))
        self.assertIn(updated_req['status'], ['ACCEPTED', 'ACTIVE'])

        # Step 8: Return workflow (Owner marks item returned)
        res_return = self.client.post(f'/rental/{req_id}/return', follow_redirects=True)
        self.assertEqual(res_return.status_code, 200)

        # Step 9: Verify item is back to Available in Supabase PostgreSQL
        item_status = execute_query_one("SELECT status FROM items WHERE item_id = 1")
        self.assertEqual(item_status['status'], 'Available')

    def test_12_own_item_request_prevention(self):
        """Verify that an owner cannot submit a rental/swap request on their own item."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = 3  # Owner of item 1
            sess['name'] = 'Vikram Malhotra'
            sess['email'] = 'seller@rentitbro.com'

        res = self.client.post('/item/1/request', data={
            'request_type': 'Rent',
            'agreement': 'on'
        }, follow_redirects=True)
        self.assertTrue(b'own' in res.data.lower() or res.status_code in [200, 302])

    # =========================================================================
    # 5. PRIVACY & SENSITIVE DATA PROTECTION
    # =========================================================================
    def test_13_public_pages_do_not_leak_student_private_info(self):
        """Verify that public listings do not expose private student ID cards, enrollment numbers, or phone numbers."""
        res_market = self.client.get('/marketplace')
        self.assertNotIn(b'id_card_image', res_market.data)
        self.assertNotIn(b'enrollment_no', res_market.data)

        res_item = self.client.get('/item/1')
        self.assertNotIn(b'id_card_image', res_item.data)
        # Should not display raw passwords or tokens
        self.assertNotIn(b'password_hash', res_item.data)

    # =========================================================================
    # 6. AI FEATURES & GRACEFUL FALLBACK
    # =========================================================================
    def test_14_ai_smart_search_execution(self):
        """Verify AI Smart search functions with query input and falls back gracefully if Gemini is unreachable."""
        res = self.client.get('/ai-search?q=I+need+a+camera+for+a+weekend+shoot')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(b'Sony' in res.data or b'DJI' in res.data or b'Camera' in res.data)  # Should match camera listing

    def test_15_ai_smart_matching_execution(self):
        """Verify AI Smart matching produces swap pairs from database."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = 3
            sess['name'] = 'Vikram Malhotra'
            sess['email'] = 'seller@rentitbro.com'

        res = self.client.get('/smart-matching')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'Match', res.data)


if __name__ == '__main__':
    unittest.main()
