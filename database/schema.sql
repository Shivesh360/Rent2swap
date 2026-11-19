-- =======================================================
-- Project: Rent It Bro - An AI-Powered Rental, Selling & Exchange Marketplace
-- Phase: 2 (Marketplace & Resource Management)
-- Database: SQLite (database/student_exchange.db)
-- =======================================================

-- Users table (Preserved from Phase 1, extended for general commercial marketplace)
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password TEXT NOT NULL,
    college TEXT NOT NULL DEFAULT 'General User',
    course TEXT NOT NULL DEFAULT 'General',
    year TEXT NOT NULL DEFAULT 'N/A',
    location TEXT NOT NULL,
    profile_image TEXT DEFAULT NULL,
    rating REAL DEFAULT 0.00,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'active'
);

-- Items table (Phase 2 core marketplace listings)
CREATE TABLE IF NOT EXISTS items (
    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    transaction_type TEXT NOT NULL, -- 'Sell', 'Rent', 'Exchange'
    description TEXT NOT NULL,
    condition TEXT NOT NULL,        -- 'New', 'Like New', 'Good', 'Fair', 'Used'
    brand TEXT DEFAULT '',
    model TEXT DEFAULT '',
    age TEXT DEFAULT '',
    price REAL DEFAULT 0.0,
    rent_amount REAL DEFAULT 0.0,
    rent_period TEXT DEFAULT '',    -- 'Hour', 'Day', 'Week', 'Month'
    exchange_preference TEXT DEFAULT '',
    city TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT DEFAULT 'Available', -- 'Available', 'Inactive', 'Rented', 'Sold', 'Exchanged'
    views_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

-- Item Images table (Supports multiple images per listing)
CREATE TABLE IF NOT EXISTS item_images (
    image_id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    image_path TEXT NOT NULL,
    is_primary INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (item_id) REFERENCES items(item_id) ON DELETE CASCADE
);

-- Indices for performance
CREATE INDEX IF NOT EXISTS idx_items_category ON items(category);
CREATE INDEX IF NOT EXISTS idx_items_transaction_type ON items(transaction_type);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_user_id ON items(user_id);
CREATE INDEX IF NOT EXISTS idx_item_images_item_id ON item_images(item_id);

-- Private Messaging Tables
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL,
    owner_id INTEGER NOT NULL,
    interested_user_id INTEGER NOT NULL,
    request_id INTEGER DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_message_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CHECK (owner_id <> interested_user_id),
    UNIQUE (listing_id, owner_id, interested_user_id),
    FOREIGN KEY (listing_id) REFERENCES items(item_id) ON DELETE CASCADE,
    FOREIGN KEY (owner_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (interested_user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS messages (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    sender_id INTEGER NOT NULL,
    message_text TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0,
    client_msg_id TEXT DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    FOREIGN KEY (sender_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_conversations_owner_last ON conversations(owner_id, last_message_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversations_interested_last ON conversations(interested_user_id, last_message_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversations_listing ON conversations(listing_id);
CREATE INDEX IF NOT EXISTS idx_messages_conv_id ON messages(conversation_id, message_id ASC);
CREATE INDEX IF NOT EXISTS idx_messages_conv_unread ON messages(conversation_id, sender_id, is_read);
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_client_idemp ON messages(conversation_id, sender_id, client_msg_id) WHERE client_msg_id IS NOT NULL;

