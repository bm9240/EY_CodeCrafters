"""
Redis utilities for Virtual Circles Agent - Real Customer Chat Storage
Stores:
- Circle messages (persistent chat history)
- User timestamps (rate limiting)
- AI insight timers (cooldown tracking)
"""

import redis
import json
import os
from typing import List, Dict, Optional
from datetime import datetime
from urllib.parse import urlparse

# Redis connection - parse from URL like other agents
REDIS_URL = os.getenv("REDIS_URL", "rediss://default:AcROAAIncDFkZGY3NTVkMmM4Mzk0MTZlOTJmZDhmMjAyY2UzYmNmM3AxNTAyNTQ@light-dolphin-50254.upstash.io:6379")

# Remove quotes if present
REDIS_URL = REDIS_URL.strip('"').strip("'")

# Parse the URL properly
parsed = urlparse(REDIS_URL)

redis_client = redis.Redis(
    host=parsed.hostname,
    port=parsed.port or 6379,
    password=parsed.password,
    username=parsed.username or 'default',
    ssl=True,
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=5
)

# Supabase fallback for persistent storage
supabase_enabled = False
try:
    import sys
    from pathlib import Path
    from dotenv import load_dotenv
    
    # Load .env from backend directory
    backend_path = Path(__file__).resolve().parent.parent.parent.parent
    env_path = backend_path / ".env"
    load_dotenv(env_path)
    
    if str(backend_path) not in sys.path:
        sys.path.insert(0, str(backend_path))
    from db import supabase_client
    supabase_enabled = supabase_client.is_enabled()
    if supabase_enabled:
        print("✅ Supabase fallback enabled for Virtual Circles chat persistence")
    else:
        print("⚠️ Supabase fallback disabled - check FEATURE_SUPABASE_READ/WRITE and credentials")
except ImportError as e:
    print(f"⚠️ Supabase import failed: {e}")
except Exception as e:
    print(f"⚠️ Supabase initialization failed: {e}")

# Key patterns
CIRCLE_CHAT_KEY = "vc:circle:{circle_id}:messages"  # List of message JSON
USER_TIMESTAMPS_KEY = "vc:user:{user_id}:timestamps"  # List of timestamps
AI_INSIGHT_TIMER_KEY = "vc:circle:{circle_id}:ai_last_insight"  # Timestamp string
USER_ALIAS_KEY = "vc:alias:{user_id}:{circle_id}"  # String alias


# Supabase fallback functions for persistent chat storage
def _store_message_supabase(circle_id: str, message: Dict) -> bool:
    """Store a chat message in Supabase as fallback"""
    if not supabase_enabled:
        return False
    
    try:
        # Prepare message for Supabase
        message_data = {
            "circle_id": circle_id,
            "user_id": message.get("user_id", ""),
            "alias": message.get("alias", ""),
            "text": message.get("text", ""),
            "timestamp": message.get("timestamp", datetime.now().isoformat()),
            "type": message.get("type", "user"),
            "message_id": message.get("message_id", "")
        }
        
        # Insert into chat_messages table
        result = supabase_client.insert("chat_messages", [message_data])
        if result:
            print(f"✅ Message stored in Supabase fallback for circle {circle_id}")
            return True
        return False
    except Exception as e:
        print(f"❌ Supabase store_message error: {e}")
        return False


def _get_messages_supabase(circle_id: str, limit: int = 50) -> List[Dict]:
    """Get recent messages from Supabase as fallback"""
    if not supabase_enabled:
        return []
    
    try:
        # Query chat_messages table
        messages = supabase_client.select(
            "chat_messages", 
            params=f"circle_id=eq.{circle_id}",
            columns="circle_id,user_id,alias,text,timestamp,type,message_id",
            timeout=10
        )
        
        if not messages:
            return []
        
        # Sort by timestamp in chronological order (oldest first, newest last)
        sorted_messages = sorted(messages, key=lambda x: x.get('timestamp', ''))
        # Return the most recent messages (last 'limit' messages)
        return sorted_messages[-limit:] if len(sorted_messages) > limit else sorted_messages
    except Exception as e:
        print(f"❌ Supabase get_messages error: {e}")
        return []


def _get_message_count_supabase(circle_id: str) -> int:
    """Get total message count from Supabase"""
    if not supabase_enabled:
        return 0
    
    try:
        # This is a simplified count - in production you'd want a more efficient query
        messages = supabase_client.select("chat_messages", params=f"circle_id=eq.{circle_id}", columns="id")
        return len(messages) if messages else 0
    except Exception as e:
        print(f"❌ Supabase get_message_count error: {e}")
        return 0


def store_message(circle_id: str, message: Dict) -> bool:
    """Store a chat message in Redis with Supabase fallback"""
    try:
        key = CIRCLE_CHAT_KEY.format(circle_id=circle_id)
        redis_client.rpush(key, json.dumps(message))
        
        # Keep only last 100 messages
        redis_client.ltrim(key, -100, -1)
        
        return True
    except Exception as e:
        print(f"❌ Redis store_message error: {e}")
        # Fallback to Supabase
        print("🔄 Falling back to Supabase for message storage...")
        return _store_message_supabase(circle_id, message)


def get_messages(circle_id: str, limit: int = 50) -> List[Dict]:
    """Get recent messages from Redis with Supabase fallback"""
    try:
        key = CIRCLE_CHAT_KEY.format(circle_id=circle_id)
        messages_json = redis_client.lrange(key, -limit, -1)
        
        messages = []
        for msg_json in messages_json:
            try:
                messages.append(json.loads(msg_json))
            except:
                continue
        
        return messages
    except Exception as e:
        print(f"❌ Redis get_messages error: {e}")
        # Fallback to Supabase
        print("🔄 Falling back to Supabase for message retrieval...")
        return _get_messages_supabase(circle_id, limit)


def get_message_count(circle_id: str) -> int:
    """Get total message count for a circle with Supabase fallback"""
    try:
        key = CIRCLE_CHAT_KEY.format(circle_id=circle_id)
        return redis_client.llen(key)
    except Exception as e:
        print(f"❌ Redis get_message_count error: {e}")
        # Fallback to Supabase
        print("🔄 Falling back to Supabase for message count...")
        return _get_message_count_supabase(circle_id)


def add_user_timestamp(user_id: str, timestamp: str):
    """Add a message timestamp for rate limiting"""
    try:
        key = USER_TIMESTAMPS_KEY.format(user_id=user_id)
        redis_client.rpush(key, timestamp)
        
        # Keep only last 10 timestamps
        redis_client.ltrim(key, -10, -1)
        
        # Expire after 5 minutes
        redis_client.expire(key, 300)
    except Exception as e:
        print(f"❌ Redis add_user_timestamp error: {e}")


def get_user_timestamps(user_id: str) -> List[str]:
    """Get recent message timestamps for rate limiting"""
    try:
        key = USER_TIMESTAMPS_KEY.format(user_id=user_id)
        return redis_client.lrange(key, 0, -1)
    except Exception as e:
        print(f"❌ Redis get_user_timestamps error: {e}")
        return []


def set_ai_insight_timer(circle_id: str, timestamp: str):
    """Set the last AI insight time (for cooldown)"""
    try:
        key = AI_INSIGHT_TIMER_KEY.format(circle_id=circle_id)
        redis_client.set(key, timestamp, ex=1800)  # Expire after 30 minutes
    except Exception as e:
        print(f"❌ Redis set_ai_insight_timer error: {e}")


def get_ai_insight_timer(circle_id: str) -> Optional[str]:
    """Get the last AI insight time"""
    try:
        key = AI_INSIGHT_TIMER_KEY.format(circle_id=circle_id)
        return redis_client.get(key)
    except Exception as e:
        print(f"❌ Redis get_ai_insight_timer error: {e}")
        return None


def store_alias(user_id: str, circle_id: str, alias: str):
    """Store user alias for a circle"""
    try:
        key = USER_ALIAS_KEY.format(user_id=user_id, circle_id=circle_id)
        redis_client.set(key, alias)
    except Exception as e:
        print(f"❌ Redis store_alias error: {e}")


def get_alias(user_id: str, circle_id: str) -> Optional[str]:
    """Get user alias for a circle"""
    try:
        key = USER_ALIAS_KEY.format(user_id=user_id, circle_id=circle_id)
        return redis_client.get(key)
    except Exception as e:
        print(f"❌ Redis get_alias error: {e}")
        return None


def test_connection() -> bool:
    """Test Redis connection"""
    try:
        redis_client.ping()
        print("✅ Redis connected for Virtual Circles")
        return True
    except Exception as e:
        print(f"❌ Redis connection failed: {e}")
        return False
