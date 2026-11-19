import io
import os
import unittest
from app import app, execute_query_one, execute_query_all, execute_query_commit
from config import Config

class TestMultipleImageUpload(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False
        self.client = app.test_client()

        # Ensure upload folder exists
        os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)

        # Login a student user
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['role'] = 'student'
            sess['full_name'] = 'Test Student'

    def _create_mock_image(self, name="test.jpg", content=b"fake image data"):
        return (io.BytesIO(content), name)

    def test_01_create_rent_listing_multiple_images(self):
        """Test creating a Rent listing with 3 photos and verify all 3 are in DB and disk"""
        img1 = self._create_mock_image("camera_front.jpg", b"\xFF\xD8\xFF\xE0" + b"front_view_content")
        img2 = self._create_mock_image("camera_back.png", b"\x89PNG\r\n\x1a\n" + b"back_view_content")
        img3 = self._create_mock_image("camera_lens.webp", b"RIFF....WEBP" + b"lens_view_content")

        data = {
            'title': 'Canon DSLR Camera for Project',
            'description': 'High quality DSLR for photography assignments with tripod and lens.',
            'category': 'Electronics',
            'transaction_type': 'Rent',
            'rent_amount': '15.00',
            'rent_period': 'Day',
            'condition': 'Like New',
            'city': 'Campus City',
            'location': 'Design Block A',
            'images': [img1, img2, img3]
        }

        response = self.client.post('/add-listing', data=data, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(response.status_code, 200)

        # Find the inserted item
        item = execute_query_one("SELECT item_id, title, transaction_type, rent_amount FROM items WHERE title = 'Canon DSLR Camera for Project' ORDER BY item_id DESC LIMIT 1")
        self.assertIsNotNone(item, "Listing was not inserted into database")
        self.assertEqual(item['transaction_type'], 'Rent')

        # Check item_images table
        images = execute_query_all("SELECT * FROM item_images WHERE item_id = %s ORDER BY image_id ASC", (item['item_id'],))
        self.assertEqual(len(images), 3, f"Expected 3 images in DB, found {len(images)}")

        # Verify first image is primary
        self.assertEqual(images[0]['is_primary'], 1)
        self.assertEqual(images[1]['is_primary'], 0)
        self.assertEqual(images[2]['is_primary'], 0)

        # Verify physical disk files exist in static/
        for img in images:
            disk_path = os.path.join(Config.BASE_DIR, "static", img['image_path'])
            self.assertTrue(os.path.exists(disk_path), f"Disk file does not exist: {disk_path}")

        # Check Item Details page renders multiple images
        details_resp = self.client.get(f"/item/{item['item_id']}")
        self.assertEqual(details_resp.status_code, 200)
        self.assertIn(b"Photos", details_resp.data)
        for img in images:
            self.assertIn(img['image_path'].encode(), details_resp.data)

    def test_02_create_sell_listing_multiple_images(self):
        """Test creating a Sell listing with 2 photos"""
        img1 = self._create_mock_image("microbiology_book_cover.jpg", b"\xFF\xD8\xFF" + b"cover")
        img2 = self._create_mock_image("microbiology_book_pages.jpg", b"\xFF\xD8\xFF" + b"pages")

        data = {
            'title': 'Brock Biology of Microorganisms',
            'description': 'Latest edition textbook with handwritten notes and summaries included.',
            'category': 'Books',
            'transaction_type': 'Sell',
            'price': '45.00',
            'condition': 'Good',
            'city': 'Campus City',
            'location': 'Library 2nd Floor',
            'images': [img1, img2]
        }

        response = self.client.post('/add-listing', data=data, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(response.status_code, 200)

        item = execute_query_one("SELECT item_id, title, transaction_type, price FROM items WHERE title = 'Brock Biology of Microorganisms' ORDER BY item_id DESC LIMIT 1")
        self.assertIsNotNone(item)
        self.assertEqual(item['transaction_type'], 'Sell')

        images = execute_query_all("SELECT * FROM item_images WHERE item_id = %s ORDER BY image_id ASC", (item['item_id'],))
        self.assertEqual(len(images), 2)
        self.assertEqual(images[0]['is_primary'], 1)

    def test_03_create_swap_listing_multiple_images(self):
        """Test creating an Exchange/Swap listing with multiple photos"""
        img1 = self._create_mock_image("keyboard_top.jpg", b"\xFF\xD8\xFF" + b"kb_top")
        img2 = self._create_mock_image("keyboard_switches.png", b"\x89PNG" + b"kb_switches")

        data = {
            'title': 'Mechanical Gaming Keyboard',
            'description': 'Blue clicky switches, looking to swap for quiet red switch board.',
            'category': 'Electronics',
            'transaction_type': 'Exchange',
            'exchange_preference': 'Brown or Red switch mechanical keyboard',
            'condition': 'Like New',
            'city': 'Campus City',
            'location': 'Hostel 3 Common Room',
            'images': [img1, img2]
        }

        response = self.client.post('/add-listing', data=data, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(response.status_code, 200)

        item = execute_query_one("SELECT item_id, title, transaction_type, exchange_preference FROM items WHERE title = 'Mechanical Gaming Keyboard' ORDER BY item_id DESC LIMIT 1")
        self.assertIsNotNone(item)
        self.assertEqual(item['transaction_type'], 'Exchange')

        images = execute_query_all("SELECT * FROM item_images WHERE item_id = %s ORDER BY image_id ASC", (item['item_id'],))
        self.assertEqual(len(images), 2)

    def test_04_edit_listing_delete_photo_and_add_new(self):
        """Test editing a listing: removing one old photo and adding a new photo"""
        # First create a listing with 2 images
        img1 = self._create_mock_image("orig_img1.jpg", b"\xFF\xD8\xFF" + b"orig1")
        img2 = self._create_mock_image("orig_img2.jpg", b"\xFF\xD8\xFF" + b"orig2")

        data = {
            'title': 'Lab Stool & Chair For Studio',
            'description': 'Adjustable height stool with footrest ring, solid metal base.',
            'category': 'Furniture',
            'transaction_type': 'Rent',
            'rent_amount': '8.00',
            'rent_period': 'Week',
            'condition': 'Good',
            'city': 'Campus City',
            'location': 'Studio B',
            'images': [img1, img2]
        }
        self.client.post('/add-listing', data=data, content_type='multipart/form-data')

        item = execute_query_one("SELECT item_id FROM items WHERE title = 'Lab Stool & Chair For Studio' ORDER BY item_id DESC LIMIT 1")
        self.assertIsNotNone(item)
        orig_images = execute_query_all("SELECT * FROM item_images WHERE item_id = %s ORDER BY image_id ASC", (item['item_id'],))
        self.assertEqual(len(orig_images), 2)

        del_id = orig_images[0]['image_id'] # Delete the primary image
        del_path = os.path.join(Config.BASE_DIR, "static", orig_images[0]['image_path'])

        # Now edit: delete first image, add one new image
        new_img = self._create_mock_image("new_cushion_view.jpg", b"\xFF\xD8\xFF" + b"new_cushion")

        edit_data = {
            'title': 'Lab Stool & Chair - Updated',
            'description': 'Updated description with new cushion photo and warranty.',
            'category': 'Furniture',
            'transaction_type': 'Rent',
            'rent_amount': '9.00',
            'rent_period': 'Week',
            'condition': 'Like New',
            'city': 'Campus City',
            'location': 'Studio B',
            'delete_image_ids': [str(del_id)],
            'images': [new_img]
        }

        edit_resp = self.client.post(f"/edit-listing/{item['item_id']}", data=edit_data, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(edit_resp.status_code, 200)

        # Check database: deleted image must be gone, new one added
        updated_images = execute_query_all("SELECT * FROM item_images WHERE item_id = %s ORDER BY image_id ASC", (item['item_id'],))
        self.assertEqual(len(updated_images), 2)
        remaining_ids = [img['image_id'] for img in updated_images]
        self.assertNotIn(del_id, remaining_ids)

        # Verify deleted file on disk was removed
        self.assertFalse(os.path.exists(del_path), "Deleted image file should be purged from disk")

        # Verify that exactly one primary image exists
        primary_count = sum(1 for img in updated_images if img['is_primary'] == 1)
        self.assertEqual(primary_count, 1, "Exactly one image should be marked primary")

if __name__ == '__main__':
    unittest.main()
