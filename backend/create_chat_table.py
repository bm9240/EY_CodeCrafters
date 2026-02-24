#!/usr/bin/env python3
"""
Create chat_messages table in Supabase for virtual circles chat persistence.
Run: python create_chat_table.py
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import requests

# Load environment
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().strip('"')
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "").strip().strip('"')
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip().strip('"')

def get_headers():
    """Get headers for Supabase API requests"""
    return {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal"
    }

def create_chat_messages_table():
    """Create the chat_messages table in Supabase"""

    # SQL to create the table
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS chat_messages (
        id SERIAL PRIMARY KEY,
        circle_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        alias TEXT,
        text TEXT NOT NULL,
        timestamp TIMESTAMPTZ DEFAULT NOW(),
        type TEXT DEFAULT 'user',
        message_id TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );

    -- Create index for faster queries
    CREATE INDEX IF NOT EXISTS idx_chat_messages_circle_timestamp
    ON chat_messages(circle_id, timestamp DESC);

    -- Create index for user queries
    CREATE INDEX IF NOT EXISTS idx_chat_messages_user_timestamp
    ON chat_messages(user_id, timestamp DESC);

    -- Enable RLS
    ALTER TABLE chat_messages ENABLE ROW LEVEL SECURITY;

    -- Allow all operations for authenticated users (adjust as needed)
    CREATE POLICY "Allow all operations for authenticated users" ON chat_messages
    FOR ALL USING (auth.role() = 'authenticated');
    """

    # For Supabase, we need to use the REST API or SQL editor
    # Since we can't run raw SQL via REST API easily, we'll try to insert a test record
    # which will fail if the table doesn't exist, then we can provide the SQL

    print("📋 Chat Messages Table Creation SQL:")
    print("=" * 50)
    print(create_table_sql)
    print("=" * 50)
    print()
    print("⚠️  Please run this SQL in your Supabase SQL Editor to create the table.")
    print("   Go to: https://supabase.com/dashboard/project/YOUR_PROJECT/sql")
    print("   Then paste and run the SQL above.")
    print()
    print("✅ After creating the table, chat messages will persist across user sessions!")

if __name__ == "__main__":
    create_chat_messages_table()