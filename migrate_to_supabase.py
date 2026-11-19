"""
Data Migration Utility: SQLite to Supabase PostgreSQL
Project: Rent It Bro - Commercial Marketplace

Transfers users, items, and item_images from local SQLite
(database/student_exchange.db) to Supabase PostgreSQL.
"""

import os
import sqlite3
import psycopg2
from config import Config


def migrate():
    print("=" * 60)
    print("Starting Migration: SQLite -> Supabase PostgreSQL")
    print("=" * 60)

    sqlite_path = Config.DATABASE_PATH
    if not os.path.exists(sqlite_path):
        print(f"[Notice] Local SQLite file '{sqlite_path}' does not exist. Nothing to migrate.")
        return

    # 1. Connect to SQLite
    s_conn = sqlite3.connect(sqlite_path)
    s_conn.row_factory = sqlite3.Row
    s_cur = s_conn.cursor()

    # 2. Connect to Supabase PostgreSQL
    print(f"Connecting to Supabase PostgreSQL at {Config.DB_HOST}:{Config.DB_PORT}...")
    pg_conn = psycopg2.connect(
        host=Config.DB_HOST,
        port=Config.DB_PORT,
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        dbname=Config.DB_NAME,
        connect_timeout=15
    )
    pg_cur = pg_conn.cursor()

    # 3. Create tables if not present
    pg_cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            email VARCHAR(255) NOT NULL UNIQUE,
            password VARCHAR(255) NOT NULL,
            college VARCHAR(255) NOT NULL DEFAULT 'General User',
            course VARCHAR(255) NOT NULL DEFAULT 'General',
            year VARCHAR(50) NOT NULL DEFAULT 'N/A',
            location VARCHAR(255) NOT NULL,
            profile_image TEXT DEFAULT NULL,
            rating DOUBLE PRECISION DEFAULT 0.00,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status VARCHAR(50) DEFAULT 'active'
        );
    """)

    pg_cur.execute("""
        CREATE TABLE IF NOT EXISTS items (
            item_id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            title VARCHAR(255) NOT NULL,
            category VARCHAR(100) NOT NULL,
            transaction_type VARCHAR(50) NOT NULL,
            description TEXT NOT NULL,
            condition VARCHAR(50) NOT NULL,
            brand VARCHAR(100) DEFAULT '',
            model VARCHAR(100) DEFAULT '',
            age VARCHAR(100) DEFAULT '',
            price DOUBLE PRECISION DEFAULT 0.0,
            rent_amount DOUBLE PRECISION DEFAULT 0.0,
            rent_period VARCHAR(50) DEFAULT '',
            exchange_preference TEXT DEFAULT '',
            city VARCHAR(100) NOT NULL,
            location VARCHAR(255) NOT NULL,
            status VARCHAR(50) NOT NULL DEFAULT 'Available',
            views_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    pg_cur.execute("""
        CREATE TABLE IF NOT EXISTS item_images (
            image_id SERIAL PRIMARY KEY,
            item_id INTEGER NOT NULL REFERENCES items(item_id) ON DELETE CASCADE,
            image_path TEXT NOT NULL,
            is_primary INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # 4. Migrate users
    s_cur.execute("SELECT * FROM users;")
    sqlite_users = s_cur.fetchall()
    print(f"Migrating {len(sqlite_users)} users...")
    migrated_users = 0
    for u in sqlite_users:
        d = dict(u)
        pg_cur.execute("""
            INSERT INTO users (user_id, name, email, password, college, course, year, location, profile_image, rating, created_at, status)
            VALUES (%(user_id)s, %(name)s, %(email)s, %(password)s, %(college)s, %(course)s, %(year)s, %(location)s, %(profile_image)s, %(rating)s, %(created_at)s, %(status)s)
            ON CONFLICT (email) DO NOTHING;
        """, d)
        migrated_users += 1

    pg_cur.execute("SELECT setval(pg_get_serial_sequence('users', 'user_id'), coalesce((SELECT MAX(user_id) FROM users), 1));")

    # 5. Migrate items
    s_cur.execute("SELECT * FROM items;")
    sqlite_items = s_cur.fetchall()
    print(f"Migrating {len(sqlite_items)} items...")
    migrated_items = 0
    for it in sqlite_items:
        d = dict(it)
        pg_cur.execute("SELECT item_id FROM items WHERE item_id = %s;", (d['item_id'],))
        if not pg_cur.fetchone():
            pg_cur.execute("""
                INSERT INTO items (
                    item_id, user_id, title, category, transaction_type, description,
                    condition, brand, model, age, price, rent_amount, rent_period,
                    exchange_preference, city, location, status, views_count, created_at, updated_at
                ) VALUES (
                    %(item_id)s, %(user_id)s, %(title)s, %(category)s, %(transaction_type)s, %(description)s,
                    %(condition)s, %(brand)s, %(model)s, %(age)s, %(price)s, %(rent_amount)s, %(rent_period)s,
                    %(exchange_preference)s, %(city)s, %(location)s, %(status)s, %(views_count)s, %(created_at)s, %(updated_at)s
                );
            """, d)
            migrated_items += 1

    pg_cur.execute("SELECT setval(pg_get_serial_sequence('items', 'item_id'), coalesce((SELECT MAX(item_id) FROM items), 1));")

    # 6. Migrate item_images
    s_cur.execute("SELECT * FROM item_images;")
    sqlite_images = s_cur.fetchall()
    print(f"Migrating {len(sqlite_images)} images...")
    migrated_images = 0
    for img in sqlite_images:
        d = dict(img)
        pg_cur.execute("SELECT image_id FROM item_images WHERE image_id = %s;", (d['image_id'],))
        if not pg_cur.fetchone():
            pg_cur.execute("""
                INSERT INTO item_images (image_id, item_id, image_path, is_primary, created_at)
                VALUES (%(image_id)s, %(item_id)s, %(image_path)s, %(is_primary)s, %(created_at)s);
            """, d)
            migrated_images += 1

    pg_cur.execute("SELECT setval(pg_get_serial_sequence('item_images', 'image_id'), coalesce((SELECT MAX(image_id) FROM item_images), 1));")

    pg_conn.commit()

    pg_cur.execute("SELECT COUNT(*) FROM users;")
    total_users = pg_cur.fetchone()[0]
    pg_cur.execute("SELECT COUNT(*) FROM items;")
    total_items = pg_cur.fetchone()[0]
    pg_cur.execute("SELECT COUNT(*) FROM item_images;")
    total_images = pg_cur.fetchone()[0]

    print("\n--- Migration Complete ---")
    print(f"Supabase PostgreSQL Current Totals:")
    print(f"  Users:       {total_users}")
    print(f"  Items:       {total_items}")
    print(f"  Item Images: {total_images}")

    s_conn.close()
    pg_conn.close()


if __name__ == "__main__":
    migrate()
