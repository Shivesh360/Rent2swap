import io
import json
import unittest
from app import app, execute_query_one, execute_query_all, execute_query_commit
from werkzeug.datastructures import MultiDict

class TestPublishToCampusWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False
        cls.client = app.test_client()

    def _login(self, user_id=1, role='student'):
        with self.client.session_transaction() as sess:
            sess['user_id'] = user_id
            sess['role'] = role
            sess['full_name'] = 'Campus Tester'

    def _logout(self):
        with self.client.session_transaction() as sess:
            sess.clear()

    def _create_mock_image(self, name="item.jpg", content=b"\xFF\xD8\xFF\xE0MockPhotoData"):
        return (io.BytesIO(content), name)

    def test_01_publish_rent_listing_ajax(self):
        """Test publishing a Rent listing via AJAX with loading state support"""
        self._login()
        img1 = self._create_mock_image("cycle1.jpg")
        img2 = self._create_mock_image("cycle2.png")

        data = {
            'title': 'Hercules 26T Mountain Gear Cycle',
            'category': 'Cycles',
            'transaction_type': 'Rent',
            'rent_amount': '120',
            'rent_period': 'Day',
            'condition': 'Good',
            'city': 'Bengaluru',
            'location': 'Hostel Block 4 Entrance',
            'description': 'Reliable gear cycle with front suspension, mudguards, and cable lock.',
            'agreement': 'on',
            'images': [img1, img2]
        }

        res = self.client.post('/add-listing', data=data, content_type='multipart/form-data',
                               headers={'X-Requested-With': 'XMLHttpRequest'})
        self.assertEqual(res.status_code, 201)
        resp_data = json.loads(res.data.decode())
        self.assertTrue(resp_data.get('success'))
        self.assertIn('published successfully', resp_data.get('message', '').lower())
        self.assertIn('/item/', resp_data.get('redirect_url', ''))

        # Verify listing in DB
        item_id = resp_data.get('item_id')
        item = execute_query_one("SELECT * FROM items WHERE item_id = %s", (item_id,))
        self.assertIsNotNone(item)
        self.assertEqual(item['transaction_type'], 'Rent')
        self.assertEqual(float(item['rent_amount']), 120.0)
        self.assertEqual(item['rent_period'], 'Day')
        self.assertEqual(item['status'], 'Available')

        # Verify images
        images = execute_query_all("SELECT * FROM item_images WHERE item_id = %s", (item_id,))
        self.assertEqual(len(images), 2)

    def test_02_publish_sell_listing_ajax_with_price_resolution(self):
        """Test publishing a Sell listing where pricing fields exist from both containers"""
        self._login()
        img = self._create_mock_image("calci.jpg")

        # Simulate form payload where empty estimated_value precedes the selling price
        data = MultiDict([
            ('title', 'Casio FX-991CW ClassWiz Calculator'),
            ('category', 'Electronics'),
            ('transaction_type', 'Sell'),
            ('condition', 'Like New'),
            ('city', 'Bengaluru'),
            ('location', 'Engineering Quad Lab 3'),
            ('description', 'Brand new condition scientific calculator approved for university exams.'),
            ('agreement', 'on'),
            ('estimated_value', ''),
            ('price', '950'),
            ('images', img)
        ])

        res = self.client.post('/add-listing', data=data, content_type='multipart/form-data',
                               headers={'X-Requested-With': 'XMLHttpRequest'})
        self.assertEqual(res.status_code, 201)
        resp_data = json.loads(res.data.decode())
        self.assertTrue(resp_data.get('success'))

        item_id = resp_data.get('item_id')
        item = execute_query_one("SELECT * FROM items WHERE item_id = %s", (item_id,))
        self.assertIsNotNone(item)
        self.assertEqual(item['transaction_type'], 'Sell')
        self.assertEqual(float(item['price']), 950.0)

    def test_03_publish_swap_listing_with_swap_keyword(self):
        """Test publishing an exchange listing submitting transaction_type='Swap'"""
        self._login()
        img = self._create_mock_image("drafter.jpg")

        data = {
            'title': 'Omega Mini Drafter & Sheet Tube',
            'category': 'Tools',
            'transaction_type': 'Swap', # User selected Swap/Trade
            'exchange_preference': 'Looking for Casio FX-991EX or Digital Multimeter',
            'estimated_value': '600',
            'condition': 'Good',
            'city': 'Bengaluru',
            'location': 'Architecture Studio Ground Floor',
            'description': 'Sturdy drafting arm with protractor head, clips, and protective carrying tube.',
            'agreement': 'on',
            'images': [img]
        }

        res = self.client.post('/add-listing', data=data, content_type='multipart/form-data',
                               headers={'X-Requested-With': 'XMLHttpRequest'})
        self.assertEqual(res.status_code, 201)
        resp_data = json.loads(res.data.decode())
        self.assertTrue(resp_data.get('success'))

        item_id = resp_data.get('item_id')
        item = execute_query_one("SELECT * FROM items WHERE item_id = %s", (item_id,))
        self.assertIsNotNone(item)
        # Normalized to Exchange
        self.assertEqual(item['transaction_type'], 'Exchange')
        self.assertIn('Multimeter', item['exchange_preference'])

    def test_04_validation_failures_return_clear_error_json(self):
        """Test that missing required fields return informative JSON errors rather than 500 crashes"""
        self._login()

        # Missing title (< 3 chars)
        res = self.client.post('/add-listing', data={
            'title': 'A',
            'category': 'Electronics',
            'transaction_type': 'Sell',
            'price': '50',
            'description': 'Valid description at least 10 chars',
            'condition': 'Good',
            'city': 'Bengaluru',
            'location': 'Hostel'
        }, headers={'X-Requested-With': 'XMLHttpRequest'})
        self.assertEqual(res.status_code, 400)
        data = json.loads(res.data.decode())
        self.assertFalse(data.get('success'))
        self.assertIn('at least 3 characters', data.get('message', ''))

        # Missing / invalid sell price
        res2 = self.client.post('/add-listing', data={
            'title': 'Valid Title Here',
            'category': 'Electronics',
            'transaction_type': 'Sell',
            'price': '0',
            'description': 'Valid description at least 10 chars',
            'condition': 'Good',
            'city': 'Bengaluru',
            'location': 'Hostel'
        }, headers={'X-Requested-With': 'XMLHttpRequest'})
        self.assertEqual(res2.status_code, 400)
        data2 = json.loads(res2.data.decode())
        self.assertIn('positive amount', data2.get('message', ''))

        # Missing swap preference
        res3 = self.client.post('/add-listing', data={
            'title': 'Valid Title Here',
            'category': 'Electronics',
            'transaction_type': 'Exchange',
            'exchange_preference': '',
            'description': 'Valid description at least 10 chars',
            'condition': 'Good',
            'city': 'Bengaluru',
            'location': 'Hostel'
        }, headers={'X-Requested-With': 'XMLHttpRequest'})
        self.assertEqual(res3.status_code, 400)
        data3 = json.loads(res3.data.decode())
        self.assertIn('specify what item', data3.get('message', ''))

    def test_05_unauthenticated_request_returns_401(self):
        """Test publishing when session is expired returns 401 with sign-in guidance"""
        self._logout()
        res = self.client.post('/add-listing', data={
            'title': 'Attempt Without Auth',
            'category': 'Books',
            'transaction_type': 'Sell',
            'price': '100'
        }, headers={'X-Requested-With': 'XMLHttpRequest'})
        self.assertEqual(res.status_code, 401)
        data = json.loads(res.data.decode())
        self.assertFalse(data.get('success'))
        self.assertIn('sign in again', data.get('message', '').lower())

    def test_06_verify_published_item_appears_in_marketplace_and_dashboard(self):
        """Test that newly published listing renders on the Marketplace and Dashboard"""
        self._login(user_id=1)
        # Create a unique item
        res = self.client.post('/add-listing', data={
            'title': 'Unique Arduino Starter Kit Uno R3',
            'category': 'Electronics',
            'transaction_type': 'Sell',
            'price': '1450',
            'condition': 'New',
            'city': 'Bengaluru',
            'location': 'ECE Department Lab 2',
            'description': 'Complete starter kit with sensors, LCD screen, breadboard and jumper wires.',
            'agreement': 'on'
        }, headers={'X-Requested-With': 'XMLHttpRequest'})
        self.assertEqual(res.status_code, 201)
        item_id = json.loads(res.data.decode())['item_id']

        # Check marketplace
        mkt_res = self.client.get('/marketplace')
        self.assertEqual(mkt_res.status_code, 200)
        self.assertIn(b'Unique Arduino Starter Kit Uno R3', mkt_res.data)

        # Check dashboard
        dash_res = self.client.get('/dashboard')
        self.assertEqual(dash_res.status_code, 200)
        self.assertIn(b'Unique Arduino Starter Kit Uno R3', dash_res.data)

if __name__ == '__main__':
    unittest.main()
