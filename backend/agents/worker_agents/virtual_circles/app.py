"""
Virtual Style Circles Agent - FastAPI Microservice
Port: 8009
Integrates with EY CodeCrafters multi-agent system

REAL CUSTOMER COMMUNITY CHAT:
- Each customer_id = Real User from customers.csv
- No fake users, no AI-generated user messages
- Messages only appear when real customers send them
- AI only provides insights/summaries, never pretends to be a user
- Circles formed by matching customers based on purchase history
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Optional, Set
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from contextlib import asynccontextmanager
import pandas as pd
import json
import math
import os
from pathlib import Path
import google.generativeai as genai

# Import redis_utils (handle both direct and module execution)
try:
    from . import redis_utils
except ImportError:
    import redis_utils

# In-memory storage (defined before app initialization)
customers_df = None
products_df = None
orders_df = None
user_profiles = {}
circles = {}  # circle_id -> [user_ids] (REAL customers only)
user_to_circle = {}  # user_id -> circle_id
circle_trends = {}  # circle_id -> trend data
interaction_log = []  # Real interactions only (no simulated data)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler - replaces deprecated on_event"""
    # Startup
    redis_utils.test_connection()
    await load_data()
    yield
    # Shutdown (if needed)
    pass


app = FastAPI(title="Virtual Style Circles Agent", version="1.0.0", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# ==================== BEHAVIOR CIRCLE CONFIG ====================

MAX_CIRCLE_SIZE = 30  # Max users per circle
NEW_USER_THRESHOLD = 2  # Purchases needed to join behavior circles
MIGRATION_THRESHOLD = 2  # Auto-migrate after this many purchases

# Behavior-based circle definitions
BEHAVIOR_CIRCLES = {
    "gym_enthusiasts": {
        "name": "Gym Enthusiasts 💪",
        "categories": ["Sports Sandal", "Sports Shoes", "Tracksuits", "Track Pants", "Tshirts"],
        "brands": ["Nike", "Puma", "Adidas", "Reebok", "NIKE", "PUMA", "ADIDAS"],
        "min_category_count": 2,
        "price_range": (1000, 3000)
    },
    "casual_comfort": {
        "name": "Casual Comfort 👕",
        "categories": ["Casual Shoes", "Flip Flops", "Sandals", "Shorts", "Tshirts"],
        "brands": ["Roadster", "United Colors of Benetton", "Nike", "Puma", "HRX"],
        "min_category_count": 2,
        "price_range": (500, 2000)
    },
    "office_professionals": {
        "name": "Office Professionals 👔",
        "categories": ["Formal Shoes", "Shirts", "Trousers", "Ties"],
        "brands": ["Peter England", "Van Heusen", "Allen Solly", "Louis Philippe"],
        "min_category_count": 2,
        "price_range": (1200, 4000)
    },
    "ethnic_wear": {
        "name": "Ethnic Wear 🎨",
        "categories": ["Kurtas", "Kurta Sets", "Ethnic Footwear"],
        "brands": ["Fabindia", "Manyavar"],
        "min_category_count": 1,
        "price_range": (800, 3000)
    },
    "premium_shoppers": {
        "name": "Premium Shoppers 💎",
        "categories": [],  # Any category
        "brands": [],  # Any brand
        "min_category_count": 0,
        "price_range": (3000, 10000)  # High spenders
    },
    "value_seekers": {
        "name": "Value Seekers 🛍️",
        "categories": [],  # Any category
        "brands": [],  # Any brand
        "min_category_count": 0,
        "price_range": (0, 1000)  # Budget conscious
    }
}

# Intent-based circles for NEW USERS (< 2 purchases)
INTENT_CIRCLES = {
    "new_arrivals": "New Arrivals 🆕",
    "style_explorers": "Style Explorers 🔍",
    "first_timers": "First Timers 👋"
}

INTERACTION_WEIGHTS = {
    "purchase": 10,
    "cart": 5,
    "like": 2,
    "view": 1
}

# Chat Configuration
MAX_MESSAGES_PER_CIRCLE = 100
MAX_MESSAGES_PER_MINUTE = 5
MIN_MESSAGE_LENGTH = 1
MAX_MESSAGE_LENGTH = 500
AI_INSIGHT_COOLDOWN_MINUTES = 2  # AI posts insights every 2 min (for testing, use 15 in prod)
CHAT_MENTION_WEIGHT = 3  # Weight for trend scoring

# Basic profanity filter (extend as needed)
PROFANITY_LIST = {
    "spam", "scam", "fake", "stupid", "idiot", "hate",
    # Add more as needed, kept minimal for demo
}

# Soft Taxonomy: Brand Groups
BRAND_GROUPS = {
    "sportswear": {"Nike", "Adidas", "Puma", "Reebok", "ADIDAS", "NIKE", "PUMA"},
    "formal": {"Van Heusen", "Allen Solly", "Louis Philippe", "Peter England", "Arrow"},
    "casual": {"Roadster", "HRX", "Levis", "United Colors of Benetton", "Wrangler", "Lee"},
    "ethnic": {"Fabindia", "Biba", "W", "Aurelia", "Global Desi"},
    "premium": {"Tommy Hilfiger", "Calvin Klein", "Hugo Boss", "Polo", "Lacoste"},
    "budget": {"Wildcraft", "Flying Machine", "SPYKAR", "Newport", "Basics"}
}

# Soft Taxonomy: Category Groups  
CATEGORY_GROUPS = {
    "footwear": {"Shoes", "Casual Shoes", "Sports Shoes", "Formal Shoes", "Sandals", "Flip Flops", "Heels"},
    "topwear": {"Shirts", "T-Shirts", "Tops", "Tshirts", "Sweaters", "Sweatshirts", "Jackets"},
    "bottomwear": {"Jeans", "Trousers", "Shorts", "Track Pants", "Leggings", "Capris"},
    "accessories": {"Watches", "Sunglasses", "Bags", "Belts", "Wallets", "Ties"},
    "innerwear": {"Innerwear", "Socks", "Briefs", "Trunks"},
    "activewear": {"Sports", "Track Suits", "Swimwear", "Sports Sandals"}
}

# Soft Taxonomy: Color Groups
COLOR_GROUPS = {
    "dark": {"black", "navy", "brown", "grey", "gray"},
    "light": {"white", "beige", "khaki", "cream"},
    "bright": {"red", "blue", "green", "yellow", "pink", "purple", "orange", "olive"}
}

# Fallback Circles for Cold-Start Users
FALLBACK_CIRCLES = {
    "casual_explorers": {
        "name": "Casual Explorers",
        "description": "Users exploring casual fashion",
        "criteria": lambda p: "casual" in [get_brand_group(b) for b in p.get('top_brands', [])] or 
                              "topwear" in [get_category_group(c) for c in p.get('top_categories', [])]
    },
    "value_seekers": {
        "name": "Value Seekers",
        "description": "Budget-conscious shoppers",
        "criteria": lambda p: p.get('avg_price', 0) < 1500
    },
    "sports_lovers": {
        "name": "Sportswear Lovers",
        "description": "Active lifestyle enthusiasts",
        "criteria": lambda p: "sportswear" in [get_brand_group(b) for b in p.get('top_brands', [])] or
                              "activewear" in [get_category_group(c) for c in p.get('top_categories', [])]
    },
    "premium_shoppers": {
        "name": "Premium Shoppers",
        "description": "High-value customers",
        "criteria": lambda p: p.get('avg_price', 0) > 3000
    },
    "new_arrivals": {
        "name": "New Arrivals",
        "description": "New customers still finding their style",
        "criteria": lambda p: True  # Default fallback
    }
}

# Pydantic Models
class InteractionEvent(BaseModel):
    user_id: str
    sku: str
    event_type: str  # view, like, cart, purchase
    timestamp: Optional[str] = None

class StyleProfile(BaseModel):
    user_id: str
    top_brands: List[str]
    top_categories: List[str]
    preferred_colors: List[str]
    avg_price: float
    price_range: tuple

class CircleInfo(BaseModel):
    circle_id: str
    user_count: int
    avg_order_value: float
    top_brands: List[str]
    top_categories: List[str]

class TrendItem(BaseModel):
    sku: str
    product_name: str
    brand: str
    score: float
    trend_label: str  # Rising, Peaking, Emerging
    interaction_count: int
    velocity: float

class Recommendation(BaseModel):
    sku: str
    product_name: str
    brand: str
    price: float
    image_url: str
    score: float
    explanation: str

class ChatMessage(BaseModel):
    user_id: str
    text: str

class ChatMessageResponse(BaseModel):
    message_id: str
    circle_id: str
    alias: str
    text: str
    timestamp: str
    type: str  # "user" or "ai_insight"


# ==================== DATA LOADING ====================

async def load_data():
    """Load CSV data on startup"""
    global customers_df, products_df, orders_df
    
    # Path from: backend/agents/worker_agents/virtual_circles/app.py
    # To: backend/data/
    data_path = Path(__file__).parent.parent.parent.parent / "data"
    
    print(f"📁 Looking for data at: {data_path.absolute()}")
    
    try:
        customers_df = pd.read_csv(data_path / "customers.csv")
        products_df = pd.read_csv(data_path / "products.csv")
        
        # Normalize column names for new CSV schema
        if "product_display_name" in products_df.columns and "ProductDisplayName" not in products_df.columns:
            products_df = products_df.rename(columns={"product_display_name": "ProductDisplayName"})
        if "sub_category" in products_df.columns and "subcategory" not in products_df.columns:
            products_df = products_df.rename(columns={"sub_category": "subcategory"})
        
        print(f"✅ Loaded {len(customers_df)} customers")
        print(f"✅ Loaded {len(products_df)} products")
        
        # Parse purchase_history from customers.csv (NOT orders.csv)
        _parse_purchase_history()
        
        # Initialize style profiles and circles (REAL customers only)
        _initialize_profiles()
        _form_circles()
        # NO simulated interactions - only real customer messages allowed
        
    except Exception as e:
        import traceback
        print(f"❌ Error loading data: {e}")
        traceback.print_exc()


def _parse_purchase_history():
    """Parse purchase_history from customers.csv to extract SKU and pricing"""
    global orders_df
    
    expanded_orders = []
    
    for _, customer_row in customers_df.iterrows():
        customer_id = str(customer_row['customer_id'])
        
        try:
            # Parse purchase_history JSON string
            import ast
            purchase_history = ast.literal_eval(customer_row['purchase_history'])
            
            for order in purchase_history:
                expanded_orders.append({
                    'order_id': order.get('order_id', 'N/A'),
                    'customer_id': customer_id,
                    'sku': order.get('sku', 'N/A'),
                    'qty': order.get('qty', 1),
                    'unit_price': order.get('amount', 0),
                    'line_total': order.get('amount', 0),
                    'created_at': order.get('date', '')
                })
        except Exception as e:
            print(f"⚠️ Could not parse purchase history for customer {customer_id}: {e}")
            continue
    
    # Create orders_df from purchase history
    orders_df = pd.DataFrame(expanded_orders)
    print(f"✅ Parsed {len(orders_df)} purchases from customer purchase_history")


def _initialize_profiles():
    """Create style profiles for all users from customers.csv"""
    global user_profiles
    
    for _, customer_row in customers_df.iterrows():
        customer_id = str(customer_row['customer_id'])
        profile = _build_style_profile(customer_id)
        if profile:
            user_profiles[customer_id] = profile
    
    print(f"✅ Created {len(user_profiles)} style profiles from {len(customers_df)} customers")


def _build_style_profile(user_id: str) -> Optional[Dict]:
    """Build style profile from order history"""
    user_orders = orders_df[orders_df['customer_id'] == user_id]
    
    if len(user_orders) == 0:
        print(f"     ⚠️  User {user_id}: No orders found in orders_df")
        return None
    
    # Get product details
    skus = user_orders['sku'].tolist()
    print(f"     📦 User {user_id}: {len(skus)} SKUs found: {skus[:3]}...")
    
    user_products = products_df[products_df['sku'].isin(skus)]
    
    if len(user_products) == 0:
        print(f"     ❌ User {user_id}: No products found for SKUs {skus[:5]}... (SKUs not in products.csv)")
        return None
    
    # Extract attributes
    brands = user_products['brand'].value_counts().head(3).index.tolist()
    categories = user_products['category'].value_counts().head(3).index.tolist()
    
    # Extract colors from product names
    colors = _extract_colors(user_products['ProductDisplayName'].tolist())
    
    # Price analysis
    prices = user_products['price'].dropna()
    avg_price = prices.mean() if len(prices) > 0 else 0
    price_range = (prices.min(), prices.max()) if len(prices) > 0 else (0, 0)
    
    return {
        "user_id": user_id,
        "top_brands": brands,
        "top_categories": categories,
        "preferred_colors": colors[:3],
        "avg_price": float(avg_price),
        "price_range": (float(price_range[0]), float(price_range[1]))
    }


def _extract_colors(product_names: List[str]) -> List[str]:
    """Extract color mentions from product names"""
    color_keywords = [
        "black", "white", "blue", "red", "green", "yellow", "pink", "purple",
        "orange", "brown", "gray", "grey", "navy", "beige", "khaki", "olive"
    ]
    
    color_counts = Counter()
    for name in product_names:
        name_lower = name.lower()
        for color in color_keywords:
            if color in name_lower:
                color_counts[color] += 1
    
    return [color for color, _ in color_counts.most_common(5)]


# ==================== TAXONOMY HELPER FUNCTIONS ====================

def get_brand_group(brand: str) -> Optional[str]:
    """Get the group a brand belongs to"""
    if not brand:
        return None
    for group_name, brands in BRAND_GROUPS.items():
        if brand in brands:
            return group_name
    return "other"


def get_category_group(category: str) -> Optional[str]:
    """Get the group a category belongs to"""
    if not category:
        return None
    for group_name, categories in CATEGORY_GROUPS.items():
        if category in categories:
            return group_name
    return "other"


def get_color_group(color: str) -> Optional[str]:
    """Get the group a color belongs to"""
    if not color:
        return None
    for group_name, colors in COLOR_GROUPS.items():
        if color in colors:
            return group_name
    return "neutral"


# ==================== SIMILARITY & CIRCLES ====================

def _calculate_similarity(profile1: Dict, profile2: Dict) -> float:
    """Calculate similarity between two user profiles using soft scoring"""
    score = 0.0
    
    # Brand similarity with soft matching (40%)
    brands1 = profile1['top_brands']
    brands2 = profile2['top_brands']
    if brands1 and brands2:
        brand_score = _calculate_soft_brand_similarity(brands1, brands2)
        score += brand_score * 0.4
    
    # Category similarity with soft matching (30%)
    cats1 = profile1['top_categories']
    cats2 = profile2['top_categories']
    if cats1 and cats2:
        cat_score = _calculate_soft_category_similarity(cats1, cats2)
        score += cat_score * 0.3
    
    # Price range proximity (20%) - unchanged
    avg1 = profile1['avg_price']
    avg2 = profile2['avg_price']
    if avg1 > 0 and avg2 > 0:
        price_diff = abs(avg1 - avg2) / max(avg1, avg2)
        price_score = max(0, 1 - price_diff)
        score += price_score * 0.2
    
    # Color similarity with soft matching (10%)
    colors1 = profile1['preferred_colors']
    colors2 = profile2['preferred_colors']
    if colors1 and colors2:
        color_score = _calculate_soft_color_similarity(colors1, colors2)
        score += color_score * 0.1
    
    return score


def _calculate_soft_brand_similarity(brands1: List[str], brands2: List[str]) -> float:
    """Calculate soft brand similarity: exact match = 1.0, same group = 0.6, no match = 0"""
    if not brands1 or not brands2:
        return 0.0
    
    exact_matches = 0
    group_matches = 0
    
    brands1_set = set(brands1)
    brands2_set = set(brands2)
    
    # Count exact matches
    exact_matches = len(brands1_set & brands2_set)
    
    # Count group matches (excluding exact matches)
    brands1_groups = {get_brand_group(b) for b in brands1_set}
    brands2_groups = {get_brand_group(b) for b in brands2_set}
    
    for b1 in brands1_set:
        if b1 in brands2_set:
            continue  # Already counted as exact
        group1 = get_brand_group(b1)
        for b2 in brands2_set:
            if b2 in brands1_set:
                continue
            group2 = get_brand_group(b2)
            if group1 and group2 and group1 == group2 and group1 != "other":
                group_matches += 0.5  # Partial credit, averaged across pairs
                break
    
    total_brands = max(len(brands1_set), len(brands2_set))
    weighted_score = (exact_matches * 1.0 + group_matches * 0.6) / total_brands
    
    return min(weighted_score, 1.0)


def _calculate_soft_category_similarity(cats1: List[str], cats2: List[str]) -> float:
    """Calculate soft category similarity: exact match = 1.0, same group = 0.6, no match = 0"""
    if not cats1 or not cats2:
        return 0.0
    
    exact_matches = 0
    group_matches = 0
    
    cats1_set = set(cats1)
    cats2_set = set(cats2)
    
    # Count exact matches
    exact_matches = len(cats1_set & cats2_set)
    
    # Count group matches (excluding exact matches)
    for c1 in cats1_set:
        if c1 in cats2_set:
            continue  # Already counted as exact
        group1 = get_category_group(c1)
        for c2 in cats2_set:
            if c2 in cats1_set:
                continue
            group2 = get_category_group(c2)
            if group1 and group2 and group1 == group2 and group1 != "other":
                group_matches += 0.5  # Partial credit
                break
    
    total_cats = max(len(cats1_set), len(cats2_set))
    weighted_score = (exact_matches * 1.0 + group_matches * 0.6) / total_cats
    
    return min(weighted_score, 1.0)


def _calculate_soft_color_similarity(colors1: List[str], colors2: List[str]) -> float:
    """Calculate soft color similarity: exact match = 1.0, same group = 0.6, no match = 0"""
    if not colors1 or not colors2:
        return 0.0
    
    exact_matches = 0
    group_matches = 0
    
    colors1_set = set(colors1)
    colors2_set = set(colors2)
    
    # Count exact matches
    exact_matches = len(colors1_set & colors2_set)
    
    # Count group matches (excluding exact matches)
    for col1 in colors1_set:
        if col1 in colors2_set:
            continue  # Already counted as exact
        group1 = get_color_group(col1)
        for col2 in colors2_set:
            if col2 in colors1_set:
                continue
            group2 = get_color_group(col2)
            if group1 and group2 and group1 == group2 and group1 != "neutral":
                group_matches += 0.5  # Partial credit
                break
    
    total_colors = max(len(colors1_set), len(colors2_set))
    weighted_score = (exact_matches * 1.0 + group_matches * 0.6) / total_colors
    
    return min(weighted_score, 1.0)


def _assign_to_behavior_circle(user_id: str, user_profile: Dict) -> Optional[str]:
    """Assign user to behavior circle based on purchase history"""
    user_orders = orders_df[orders_df['customer_id'] == user_id]
    skus = user_orders['sku'].tolist()
    user_products = products_df[products_df['sku'].isin(skus)]
    
    if len(user_products) == 0:
        return None
    
    # Get user's actual purchases
    user_categories = user_products['category'].tolist()
    user_brands = user_products['brand'].tolist()
    user_avg_price = user_products['price'].mean()
    
    best_circle = None
    best_score = 0
    
    # Score each behavior circle
    for circle_id, circle_config in BEHAVIOR_CIRCLES.items():
        score = 0
        
        # Category matching
        if circle_config['categories']:
            category_matches = sum(1 for cat in user_categories if cat in circle_config['categories'])
            if category_matches >= circle_config['min_category_count']:
                score += category_matches * 10
        
        # Brand matching
        if circle_config['brands']:
            brand_matches = sum(1 for brand in user_brands if brand in circle_config['brands'])
            score += brand_matches * 5
        
        # Price range matching
        price_min, price_max = circle_config['price_range']
        if price_min <= user_avg_price <= price_max:
            score += 20
        elif abs(user_avg_price - price_min) < 500 or abs(user_avg_price - price_max) < 500:
            score += 10  # Close to range
        
        if score > best_score:
            best_score = score
            best_circle = circle_id
    
    return best_circle if best_score > 0 else "casual_comfort"  # Default


def _get_circle_name(circle_id: str) -> str:
    """Get display name for a circle"""
    if circle_id in BEHAVIOR_CIRCLES:
        return BEHAVIOR_CIRCLES[circle_id]['name']
    elif circle_id.startswith("intent_"):
        intent_id = circle_id.replace("intent_", "")
        return INTENT_CIRCLES.get(intent_id, "Style Circle")
    return "Style Circle"


def _should_migrate_user(user_id: str) -> bool:
    """Check if new user should migrate to behavior circle"""
    purchase_count = len(orders_df[orders_df['customer_id'] == user_id])
    current_circle = user_to_circle.get(user_id, "")
    
    # Migrate if user has enough purchases and is in intent circle
    return purchase_count >= MIGRATION_THRESHOLD and current_circle.startswith("intent_")


def _form_circles():
    """Form behavior-based circles (gym, casual, office, etc.) instead of similarity clustering"""
    global circles, user_to_circle
    
    # Initialize behavior circles
    for circle_id in BEHAVIOR_CIRCLES.keys():
        circles[circle_id] = []
    
    # Initialize intent circles for new users
    for intent_id in INTENT_CIRCLES.keys():
        circles[f"intent_{intent_id}"] = []
    
    user_ids = list(user_profiles.keys())
    new_users = []
    returning_users = []
    
    # Phase 1: Separate new vs returning users
    print("\n🔍 Phase 1: Identifying new vs returning users...")
    for user_id in user_ids:
        purchase_count = len(orders_df[orders_df['customer_id'] == user_id])
        if purchase_count < NEW_USER_THRESHOLD:
            new_users.append(user_id)
        else:
            returning_users.append(user_id)
    
    print(f"   📊 {len(returning_users)} returning users (≥{NEW_USER_THRESHOLD} purchases)")
    print(f"   🆕 {len(new_users)} new users (<{NEW_USER_THRESHOLD} purchases)")
    
    # Phase 2: Assign returning users to behavior circles
    print("\n🔍 Phase 2: Assigning returning users to behavior circles...")
    for user_id in returning_users:
        user_profile = user_profiles[user_id]
        circle_id = _assign_to_behavior_circle(user_id, user_profile)
        
        if circle_id and len(circles[circle_id]) < MAX_CIRCLE_SIZE:
            circles[circle_id].append(user_id)
            user_to_circle[user_id] = circle_id
    
    # Phase 3: Assign new users to intent circles (round-robin)
    print(f"\n🔍 Phase 3: Assigning {len(new_users)} new users to intent circles...")
    intent_circle_ids = list(INTENT_CIRCLES.keys())
    for idx, user_id in enumerate(new_users):
        intent_id = intent_circle_ids[idx % len(intent_circle_ids)]
        circle_id = f"intent_{intent_id}"
        circles[circle_id].append(user_id)
        user_to_circle[user_id] = circle_id
    
    # Clean up empty circles
    empty_circles = [cid for cid, members in circles.items() if not members]
    for cid in empty_circles:
        del circles[cid]
    
    # Summary
    behavior_circles = [cid for cid in circles.keys() if not cid.startswith("intent_")]
    intent_circles = [cid for cid in circles.keys() if cid.startswith("intent_")]
    
    print(f"\n✅ Final: {len(circles)} total circles")
    print(f"   🎯 {len(behavior_circles)} behavior circles with {len(returning_users)} users")
    print(f"   🆕 {len(intent_circles)} intent circles with {len(new_users)} users")
    
    # Show circle distribution
    for circle_id, members in sorted(circles.items()):
        circle_name = _get_circle_name(circle_id)
        print(f"      - {circle_name}: {len(members)} members")


def _simulate_interactions():
    """Simulate recent interaction events for demo"""
    global interaction_log
    
    now = datetime.now()
    
    # Generate interactions for last 14 days
    for user_id, circle_id in user_to_circle.items():
        user_orders = orders_df[orders_df['customer_id'] == user_id]
        
        if len(user_orders) > 0:
            # Simulate some recent interactions
            sample_skus = user_orders['sku'].sample(min(3, len(user_orders))).tolist()
            
            for sku in sample_skus:
                # Random events in past 14 days
                days_ago = hash(f"{user_id}{sku}") % 14
                timestamp = (now - timedelta(days=days_ago)).isoformat()
                
                # Purchase event
                interaction_log.append({
                    "user_id": user_id,
                    "sku": str(sku),
                    "event_type": "purchase",
                    "timestamp": timestamp
                })
                
                # Add view/like events before purchase
                for event_type in ["view", "like", "cart"]:
                    interaction_log.append({
                        "user_id": user_id,
                        "sku": str(sku),
                        "event_type": event_type,
                        "timestamp": (now - timedelta(days=days_ago+1)).isoformat()
                    })
    
    print(f"✅ Simulated {len(interaction_log)} interaction events")


# ==================== TREND DETECTION ====================

def _detect_circle_trends(circle_id: str, days: int = 14) -> List[Dict]:
    """Detect trending products in a circle (with chat amplification)"""
    circle_users = set(circles.get(circle_id, []))
    
    if not circle_users:
        return []
    
    # Filter interactions for this circle in time window
    cutoff = datetime.now() - timedelta(days=days)
    
    sku_scores = defaultdict(lambda: {
        "score": 0,
        "interactions": [],
        "users": set()
    })
    
    for event in interaction_log:
        if event['user_id'] not in circle_users:
            continue
        
        event_time = datetime.fromisoformat(event['timestamp'])
        if event_time < cutoff:
            continue
        
        sku = event['sku']
        weight = INTERACTION_WEIGHTS.get(event['event_type'], 1)
        
        # Recency boost
        days_old = (datetime.now() - event_time).days
        recency_factor = 1 / (1 + days_old * 0.1)
        
        sku_scores[sku]["score"] += weight * recency_factor
        sku_scores[sku]["interactions"].append(event)
        sku_scores[sku]["users"].add(event['user_id'])
    
    # CHAT AMPLIFICATION: Add chat mention signals
    chat_mentions = _extract_mentions_from_chat(circle_id, hours=24)
    for sku, mention_count in chat_mentions.items():
        if sku in sku_scores:
            sku_scores[sku]["score"] += mention_count * CHAT_MENTION_WEIGHT
    
    # Convert to list and enrich with product data
    trends = []
    for sku, data in sku_scores.items():
        product = products_df[products_df['sku'] == sku]
        
        if len(product) == 0:
            continue
        
        product = product.iloc[0]
        
        # Calculate velocity (interactions per day)
        interaction_days = set()
        for event in data['interactions']:
            event_time = datetime.fromisoformat(event['timestamp'])
            interaction_days.add(event_time.date())
        
        velocity = len(data['interactions']) / max(len(interaction_days), 1)
        
        # Determine trend label
        trend_label = _classify_trend(data['score'], velocity, len(data['users']))
        
        trends.append({
            "sku": sku,
            "product_name": product['ProductDisplayName'],
            "brand": product['brand'],
            "score": data['score'],
            "trend_label": trend_label,
            "interaction_count": len(data['interactions']),
            "velocity": velocity,
            "unique_users": len(data['users'])
        })
    
    # Sort by score
    trends.sort(key=lambda x: x['score'], reverse=True)
    
    return trends[:20]


def _classify_trend(score: float, velocity: float, user_count: int) -> str:
    """Classify trend momentum"""
    if velocity > 2 and user_count > 3:
        return "Rising"
    elif score > 30:
        return "Peaking"
    elif user_count >= 2 and velocity > 1:
        return "Emerging"
    else:
        return "Stable"


def _predict_trends(circle_id: str) -> List[Dict]:
    """Predict next 7-day trends"""
    current_trends = _detect_circle_trends(circle_id, days=7)
    
    predictions = []
    for trend in current_trends:
        # Simple momentum prediction
        if trend['velocity'] > 1.5:
            prediction_score = trend['score'] * 1.3
            prediction_label = "Rising"
        elif trend['trend_label'] == "Emerging":
            prediction_score = trend['score'] * 1.5
            prediction_label = "Breakthrough"
        else:
            prediction_score = trend['score'] * 0.9
            prediction_label = "Steady"
        
        predictions.append({
            **trend,
            "predicted_score": prediction_score,
            "prediction_label": prediction_label
        })
    
    predictions.sort(key=lambda x: x['predicted_score'], reverse=True)
    return predictions[:10]


# ==================== AI EXPLANATIONS ====================

def _generate_explanation(user_id: str, sku: str, trend_data: Dict) -> str:
    """Generate AI explanation using Gemini"""
    if not GEMINI_API_KEY:
        return _fallback_explanation(trend_data)
    
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        circle_id = user_to_circle.get(user_id, "")
        circle_users = circles.get(circle_id, [])
        user_profile = user_profiles.get(user_id, {})
        
        prompt = f"""Generate a brief, enthusiastic product recommendation explanation (2-3 sentences max).

Product: {trend_data.get('product_name', 'this item')}
Brand: {trend_data.get('brand', '')}
Trend: {trend_data.get('trend_label', 'popular')}

Context:
- {trend_data.get('unique_users', 0)} people in your style circle interacted with this
- User's preferred brands: {', '.join(user_profile.get('top_brands', [])[:2])}
- User's style: {', '.join(user_profile.get('top_categories', [])[:2])}

Write a compelling, personalized reason why this is perfect for them. Be specific about the social proof and style match. Sound excited but natural."""

        response = model.generate_content(prompt)
        return response.text.strip()
    
    except Exception as e:
        print(f"⚠️ Gemini error: {e}")
        return _fallback_explanation(trend_data)


def _fallback_explanation(trend_data: Dict) -> str:
    """Fallback template explanation"""
    users = trend_data.get('unique_users', 0)
    trend = trend_data.get('trend_label', 'popular').lower()
    product = trend_data.get('product_name', 'this item')
    
    templates = [
        f"{users} people in your style circle are loving this {trend} pick right now.",
        f"This {trend} item matches your usual style — {users} similar shoppers already grabbed it.",
        f"Trending with {users} people who share your taste. {product} is a hot pick this week!",
    ]
    
    return templates[hash(trend_data.get('sku', '')) % len(templates)]


# ==================== CHAT FUNCTIONS ====================

def _generate_alias(user_id: str, circle_id: str) -> str:
    """Generate consistent anonymous alias for user in circle (using Redis)"""
    # Check Redis first
    alias = redis_utils.get_alias(user_id, circle_id)
    if alias:
        return alias
    
    # Generate deterministic but anonymous alias
    hash_val = hash(f"{user_id}_{circle_id}") % 10000
    alias = f"StyleFan_{hash_val}"
    
    # Store in Redis
    redis_utils.store_alias(user_id, circle_id, alias)
    return alias


def _contains_profanity(text: str) -> bool:
    """Check if text contains profanity"""
    text_lower = text.lower()
    return any(word in text_lower for word in PROFANITY_LIST)


def _check_rate_limit(user_id: str) -> bool:
    """Check if user has exceeded message rate limit (using Redis)"""
    now = datetime.now()
    cutoff = now - timedelta(minutes=1)
    
    # Get timestamps from Redis
    timestamps = redis_utils.get_user_timestamps(user_id)
    
    # Parse and filter recent timestamps
    recent_timestamps = []
    for ts_str in timestamps:
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts > cutoff:
                recent_timestamps.append(ts)
        except:
            continue
    
    # Check limit
    if len(recent_timestamps) >= MAX_MESSAGES_PER_MINUTE:
        return False
    
    return True


def _validate_message(text: str, last_messages: List[Dict]) -> tuple[bool, str]:
    """Validate message content and check for spam"""
    # Length check
    if len(text) < MIN_MESSAGE_LENGTH:
        return False, "Message too short"
    if len(text) > MAX_MESSAGE_LENGTH:
        return False, "Message too long"
    
    # Profanity check
    if _contains_profanity(text):
        return False, "Message contains inappropriate content"
    
    # Spam check - detect repeated messages
    if last_messages:
        recent_texts = [msg['text'] for msg in last_messages[-5:]]
        if recent_texts.count(text) > 0:
            return False, "Duplicate message detected"
    
    return True, "OK"


def _extract_mentions_from_chat(circle_id: str, hours: int = 1) -> Dict[str, int]:
    """Extract product mentions from recent REAL chat messages (from Redis)"""
    # Get messages from Redis
    messages = redis_utils.get_messages(circle_id, limit=100)
    
    if not messages:
        return {}
    
    cutoff = datetime.now() - timedelta(hours=hours)
    mentions = defaultdict(int)
    
    for msg in messages:
        if msg['type'] != 'user':
            continue
        
        try:
            msg_time = datetime.fromisoformat(msg['timestamp'])
            if msg_time < cutoff:
                continue
        except:
            continue
        
        text_lower = msg['text'].lower()
        
        # Match against product names and brands
        for _, product in products_df.iterrows():
            product_name = product['ProductDisplayName'].lower()
            brand = product['brand'].lower()
            sku = product['sku']
            
            # Check for brand mentions
            if brand in text_lower:
                mentions[sku] += 1
            
            # Check for product keywords (simple matching)
            product_words = set(product_name.split()[:3])  # First 3 words
            text_words = set(text_lower.split())
            if len(product_words & text_words) >= 2:  # At least 2 word overlap
                mentions[sku] += 1
    
    return dict(mentions)


def _should_post_ai_insight(circle_id: str) -> bool:
    """Check if enough time has passed to post new AI insight (using Redis)"""
    now = datetime.now()
    
    last_post_str = redis_utils.get_ai_insight_timer(circle_id)
    
    if not last_post_str:
        return True
    
    try:
        last_post = datetime.fromisoformat(last_post_str)
        time_diff = (now - last_post).total_seconds() / 60
        return time_diff >= AI_INSIGHT_COOLDOWN_MINUTES
    except:
        return True


def _generate_ai_insight(circle_id: str) -> Optional[str]:
    """Generate AI insight based on circle trends and REAL chat activity using Gemini 2.5 Flash (NO fake messages)"""
    if not _should_post_ai_insight(circle_id):
        return None
    
    # Get circle info
    if circle_id not in circles:
        return None
    
    members = circles[circle_id]
    member_count = len(members)
    
    # Get circle trends
    trends = _detect_circle_trends(circle_id, days=7)
    
    # Get recent chat messages from REAL customers
    recent_messages = redis_utils.get_messages(circle_id, limit=10)
    
    # Get chat mentions from REAL messages
    chat_mentions = _extract_mentions_from_chat(circle_id, hours=2)
    
    # Get circle member profiles for personalization
    member_profiles = [user_profiles.get(m) for m in members if m in user_profiles]
    top_brands = []
    top_categories = []
    if member_profiles:
        all_brands = [b for p in member_profiles for b in p.get('top_brands', [])]
        all_categories = [c for p in member_profiles for c in p.get('top_categories', [])]
        from collections import Counter
        top_brands = [b for b, _ in Counter(all_brands).most_common(3)]
        top_categories = [c for c, _ in Counter(all_categories).most_common(3)]
    
    # Build detailed context for Gemini
    context_parts = []
    trend_details = []
    
    if trends:
        top_trends = trends[:3]
        for t in top_trends:
            trend_details.append({
                'product': t['product_name'],
                'brand': t['brand'],
                'users': t.get('unique_users', 0),
                'trend': t.get('trend_label', 'trending')
            })
        context_parts.append(f"Top trending: {', '.join([t['brand'] + ' ' + t['product_name'] for t in top_trends])}")
    
    if chat_mentions:
        mentioned_products = []
        for sku, count in list(chat_mentions.items())[:3]:
            prod = products_df[products_df['sku'] == sku]
            if not prod.empty:
                mentioned_products.append(f"{prod.iloc[0]['brand']} ({count}x)")
        if mentioned_products:
            context_parts.append(f"Chat buzz: {', '.join(mentioned_products)}")
    
    if recent_messages:
        user_messages = [m for m in recent_messages if m.get('type') == 'user']
        if user_messages:
            context_parts.append(f"Active chat: {len(user_messages)} messages")
    
    # If no activity, return personalized welcome
    if not context_parts:
        if top_brands:
            return f"👥 Welcome! You're among {member_count} {top_brands[0]} lovers with similar style."
        return f"👥 Welcome! You're in a community of {member_count} shoppers with similar taste."
    
    # Use Vertex AI Gemini to generate innovative recommendations with peer trends
    if GEMINI_API_KEY:
        try:
            print(f"\n🤖 USING VERTEX AI GEMINI-2.5-FLASH for AI recommendations...")
            print(f"   Circle: {circle_id} | Members: {member_count}")
            print(f"   Trends detected: {len(trends)} | Chat mentions: {len(chat_mentions)}")
            
            model = genai.GenerativeModel('gemini-2.5-flash')
            
            # Get peer purchase data for recommendations
            peer_products = []
            if trends:
                for t in trends[:5]:
                    peer_products.append({
                        'brand': t['brand'],
                        'product': t['product_name'],
                        'sku': t['sku'],
                        'users': t.get('unique_users', 0),
                        'trend': t.get('trend_label', 'trending'),
                        'price': products_df[products_df['sku'] == t['sku']]['price'].iloc[0] if t['sku'] in products_df['sku'].values else 0
                    })
            
            prompt = f"""You are a Style AI assistant providing data-driven product recommendations with peer trends for a fashion community.

**Circle Profile:**
- {member_count} members with shared style preferences
- Popular brands: {', '.join(top_brands[:3]) if top_brands else 'diverse'}
- Style focus: {', '.join(top_categories[:3]) if top_categories else 'varied'}

**Recent Activity:**
{chr(10).join([f"- {part}" for part in context_parts])}

**PEER TRENDS (What your circle is buying/viewing):**
{chr(10).join([f"- {p['brand']} {p['product']} - {p['users']} members interested ({p['trend']}) - ₹{p['price']}" for p in peer_products]) if peer_products else '- Building peer data...'}

**Task:** Generate ONE compelling recommendation (100-150 chars) that:
1. MUST include a specific product recommendation from peer trends
2. Show social proof ("X members bought/viewed...")
3. Explain WHY it's perfect based on peer behavior
4. Create urgency with peer activity
5. Use 1-2 emojis naturally

**Recommendation Style:** Data-driven, specific brand+product names, peer social proof, actionable

**Examples:**
✅ "🔥 Trending: Nike React Infinity Run! 8 members bought it this week. Your circle loves performance footwear - check it out!"
✅ "💎 Peer pick: Adidas Ultraboost seen by 12 members. Premium comfort is hot in your circle at ₹8,999"
✅ "👟 Your circle's favorite: Reebok Classic Leather - 5 purchases this week. Perfect casual upgrade!"
✅ "🎯 Recommended: Puma Future Rider - 9 members interested. Retro style trending in your group"
❌ "Check out products" (no specific recommendation)
❌ "People are shopping" (no peer trend data)

**Your recommendation with peer trends:**"""
            
            print(f"   📤 Sending prompt to Vertex AI Gemini...")
            
            response = model.generate_content(prompt, generation_config={
                'temperature': 0.8,  # Creative but accurate
                'top_p': 0.9,
                'top_k': 40,
                'max_output_tokens': 120
            })
            
            insight = response.text.strip()
            
            print(f"   ✅ VERTEX AI RESPONSE RECEIVED")
            print(f"   📝 Raw output: {insight[:100]}...")
            
            # Clean up
            insight = insight.replace('**', '').replace('*', '').strip()
            if insight.startswith('"') and insight.endswith('"'):
                insight = insight[1:-1]
            
            if len(insight) > 160:
                insight = insight[:157] + "..."
            
            print(f"   ✨ VERTEX AI RECOMMENDATION: {insight}")
            print(f"   🎯 Generated using Gemini 2.5 Flash\n")
            return insight
            
        except Exception as e:
            print(f"   ❌ VERTEX AI GEMINI FAILED: {e}")
            print(f"   🔄 Falling back to template recommendations...\n")
            # Fallback to enhanced template
    
    # Enhanced fallback template with recommendations and peer trends
    print(f"   ℹ️ Using template-based recommendations (Vertex AI not configured)")
    
    if trends:
        top_trend = trends[0]
        users = top_trend.get('unique_users', 0)
        product = top_trend['product_name']
        brand = top_trend['brand']
        sku = top_trend['sku']
        trend_label = top_trend.get('trend_label', 'trending').lower()
        
        # Get price for recommendation
        price_info = ""
        if sku in products_df['sku'].values:
            price = products_df[products_df['sku'] == sku]['price'].iloc[0]
            price_info = f" at ₹{int(price)}"
        
        # Peer trend recommendation
        return f"🔥 Peer Pick: {brand} {product} - {users} members {trend_label}{price_info}. Your circle loves it!"
    
    if chat_mentions:
        top_mentioned_sku = max(chat_mentions, key=chat_mentions.get)
        mention_count = chat_mentions[top_mentioned_sku]
        product = products_df[products_df['sku'] == top_mentioned_sku].iloc[0]
        price_info = f" (₹{int(product['price'])})" if 'price' in product else ""
        return f"💬 Trending in chat: {product['brand']} {product['ProductDisplayName']} - {mention_count} mentions{price_info}!"
    
    if top_brands:
        return f"👥 {member_count} members • Popular brands: {', '.join(top_brands[:2])}. Discover what they're buying!"
    
    return f"👥 Welcome to {member_count} members sharing similar style!"


# ==================== API ENDPOINTS ====================

@app.get("/")
def root():
    return {
        "service": "Virtual Style Circles Agent",
        "version": "1.0.0",
        "status": "operational",
        "circles": len(circles),
        "users": len(user_profiles)
    }


@app.post("/circles/assign-user")
def assign_user_to_circle(user_id: str):
    """Assign user to behavior or intent circle with auto-migration support"""
    print(f"\n🔍 Assigning user {user_id} to circle...")
    
    # Check if user needs migration from intent to behavior circle
    if user_id in user_to_circle and _should_migrate_user(user_id):
        old_circle = user_to_circle[user_id]
        print(f"  🔄 Migrating user from {old_circle} (reached {MIGRATION_THRESHOLD} purchases)")
        
        # Remove from old circle
        if old_circle in circles and user_id in circles[old_circle]:
            circles[old_circle].remove(user_id)
        
        # Re-assign to behavior circle
        del user_to_circle[user_id]
    
    # Check if already assigned
    if user_id in user_to_circle:
        existing_circle = user_to_circle[user_id]
        circle_name = _get_circle_name(existing_circle)
        print(f"  ℹ️  User already in {circle_name}")
        return {
            "user_id": user_id,
            "circle_id": existing_circle,
            "circle_name": circle_name,
            "status": "existing"
        }
    
    # Build profile if not exists
    if user_id not in user_profiles:
        print(f"  🔨 Building profile for user {user_id}...")
        profile = _build_style_profile(user_id)
        if profile:
            user_profiles[user_id] = profile
    
    # Check purchase count
    purchase_count = len(orders_df[orders_df['customer_id'] == user_id])
    print(f"  📊 User has {purchase_count} purchases")
    
    # NEW USER: Assign to intent circle
    if purchase_count < NEW_USER_THRESHOLD:
        print(f"  🆕 New user (<{NEW_USER_THRESHOLD} purchases) → Intent circle")
        intent_circle_ids = [f"intent_{i}" for i in INTENT_CIRCLES.keys()]
        # Find least populated intent circle
        best_intent = min(intent_circle_ids, key=lambda c: len(circles.get(c, [])))
        
        if best_intent not in circles:
            circles[best_intent] = []
        
        circles[best_intent].append(user_id)
        user_to_circle[user_id] = best_intent
        circle_name = _get_circle_name(best_intent)
        print(f"  ✅ Assigned to {circle_name}")
        print(f"  💡 Will auto-migrate to behavior circle after {MIGRATION_THRESHOLD} purchases")
        
        return {
            "user_id": user_id,
            "circle_id": best_intent,
            "circle_name": circle_name,
            "status": "assigned",
            "user_type": "new",
            "migration_threshold": MIGRATION_THRESHOLD
        }
    
    # RETURNING USER: Assign to behavior circle
    print(f"  🎯 Returning user (≥{NEW_USER_THRESHOLD} purchases) → Behavior circle")
    user_profile = user_profiles.get(user_id)
    
    if not user_profile:
        print(f"  ⚠️  No profile available, assigning to casual_comfort")
        circle_id = "casual_comfort"
    else:
        # Show customer preferences
        print(f"  📊 Customer preferences:")
        print(f"     - Top brands: {', '.join(user_profile['top_brands'][:3])}")
        print(f"     - Top categories: {', '.join(user_profile['top_categories'][:3])}")
        print(f"     - Avg price: ₹{user_profile['avg_price']:.0f}")
        
        circle_id = _assign_to_behavior_circle(user_id, user_profile)
        if not circle_id:
            circle_id = "casual_comfort"  # Default
    
    # Ensure circle exists
    if circle_id not in circles:
        circles[circle_id] = []
    
    # Check circle capacity
    if len(circles[circle_id]) >= MAX_CIRCLE_SIZE:
        print(f"  ⚠️  {_get_circle_name(circle_id)} is full, trying alternates...")
        # Find similar circle with space
        for alt_circle_id in BEHAVIOR_CIRCLES.keys():
            if alt_circle_id != circle_id and len(circles.get(alt_circle_id, [])) < MAX_CIRCLE_SIZE:
                circle_id = alt_circle_id
                break
    
    circles[circle_id].append(user_id)
    user_to_circle[user_id] = circle_id
    circle_name = _get_circle_name(circle_id)
    print(f"  ✅ Assigned to {circle_name} ({len(circles[circle_id])} members)")
    
    return {
        "user_id": user_id,
        "circle_id": circle_id,
        "circle_name": circle_name,
        "status": "assigned",
        "user_type": "returning"
    }


@app.get("/circles/{circle_id}")
def get_circle_info(circle_id: str):
    """Get circle information with behavior/intent labels"""
    if circle_id not in circles:
        raise HTTPException(status_code=404, detail="Circle not found")
    
    members = circles[circle_id]
    circle_name = _get_circle_name(circle_id)
    
    # Aggregate circle stats
    all_brands = []
    all_categories = []
    all_prices = []
    
    for user_id in members:
        if user_id in user_profiles:
            profile = user_profiles[user_id]
            all_brands.extend(profile['top_brands'])
            all_categories.extend(profile['top_categories'])
            all_prices.append(profile['avg_price'])
    
    brand_counts = Counter(all_brands)
    category_counts = Counter(all_categories)
    
    return {
        "circle_id": circle_id,
        "circle_name": circle_name,
        "circle_type": "intent" if circle_id.startswith("intent_") else "behavior",
        "user_count": len(members),
        "avg_order_value": sum(all_prices) / len(all_prices) if all_prices else 0,
        "top_brands": [b for b, _ in brand_counts.most_common(5)],
        "top_categories": [c for c, _ in category_counts.most_common(5)]
    }


@app.get("/circles/{circle_id}/trends")
def get_circle_trends(circle_id: str, days: int = 14):
    """Get trending products in a circle"""
    if circle_id not in circles:
        raise HTTPException(status_code=404, detail="Circle not found")
    
    trends = _detect_circle_trends(circle_id, days)
    
    return {
        "circle_id": circle_id,
        "time_window_days": days,
        "trends": trends
    }


@app.get("/circles/{circle_id}/predict")
def predict_circle_trends(circle_id: str):
    """Predict next 7-day trends"""
    if circle_id not in circles:
        raise HTTPException(status_code=404, detail="Circle not found")
    
    predictions = _predict_trends(circle_id)
    
    return {
        "circle_id": circle_id,
        "prediction_horizon": "7 days",
        "predictions": predictions
    }


@app.get("/user/{user_id}/recommendations")
def get_user_recommendations(user_id: str, limit: int = 10):
    """Get personalized recommendations using circle trends"""
    if user_id not in user_to_circle:
        raise HTTPException(status_code=404, detail="User not found in any circle")
    
    circle_id = user_to_circle[user_id]
    trends = _detect_circle_trends(circle_id)
    
    if not trends:
        return {"user_id": user_id, "circle_id": circle_id, "recommendations": []}
    
    user_profile = user_profiles[user_id]
    
    recommendations = []
    for trend in trends[:limit]:
        sku = trend['sku']
        product = products_df[products_df['sku'] == sku].iloc[0]
        
        # Filter by price range
        price = product['price']
        min_price, max_price = user_profile['price_range']
        
        if price < min_price * 0.7 or price > max_price * 1.5:
            continue
        
        # Generate explanation
        explanation = _generate_explanation(user_id, sku, trend)
        
        recommendations.append({
            "sku": sku,
            "product_name": product['ProductDisplayName'],
            "brand": product['brand'],
            "price": float(price),
            "image_url": product.get('image_url', ''),
            "score": trend['score'],
            "explanation": explanation
        })
    
    return {
        "user_id": user_id,
        "circle_id": circle_id,
        "recommendations": recommendations[:limit]
    }


@app.post("/interactions/log")
def log_interaction(event: InteractionEvent):
    """Log user interaction event"""
    if not event.timestamp:
        event.timestamp = datetime.now().isoformat()
    
    interaction_log.append(event.dict())
    
    return {"status": "logged", "event": event}


# ==================== CHAT ENDPOINTS ====================

@app.post("/circle/{circle_id}/ai/recommend")
def get_ai_recommendation(circle_id: str, user_id: str):
    """Generate AI product recommendation using Vertex AI when user clicks Recommend button"""
    print(f"\n🎯 USER REQUESTED AI RECOMMENDATION")
    print(f"   Circle: {circle_id} | User: {user_id}")
    
    if circle_id not in circles:
        raise HTTPException(status_code=404, detail="Circle not found")
    
    if user_id not in circles[circle_id]:
        raise HTTPException(status_code=403, detail="User not in this circle")
    
    # Get circle trends and member profiles
    members = circles[circle_id]
    trends = _detect_circle_trends(circle_id, days=7)
    member_profiles = [user_profiles.get(m) for m in members if m in user_profiles]
    
    top_brands = []
    top_categories = []
    if member_profiles:
        from collections import Counter
        all_brands = [b for p in member_profiles for b in p.get('top_brands', [])]
        all_categories = [c for p in member_profiles for c in p.get('top_categories', [])]
        top_brands = [b for b, _ in Counter(all_brands).most_common(3)]
        top_categories = [c for c, _ in Counter(all_categories).most_common(3)]
    
    # Get peer products for recommendations
    peer_products = []
    if trends:
        for t in trends[:5]:
            prod_data = products_df[products_df['sku'] == t['sku']]
            if not prod_data.empty:
                peer_products.append({
                    'brand': t['brand'],
                    'product': t['product_name'],
                    'sku': t['sku'],
                    'users': t.get('unique_users', 0),
                    'trend': t.get('trend_label', 'trending'),
                    'price': int(prod_data['price'].iloc[0])
                })
    
    # If no peer products from trends, get top products from circle members' purchase history
    if not peer_products:
        print(f"   ℹ️  No trends detected, analyzing circle members' purchase history...")
        member_skus = []
        for member_id in members[:10]:  # Analyze up to 10 members
            member_orders = orders_df[orders_df['customer_id'] == member_id]
            member_skus.extend(member_orders['sku'].tolist())
        
        # Get most common SKUs
        if member_skus:
            from collections import Counter
            common_skus = Counter(member_skus).most_common(5)
            for sku, count in common_skus:
                prod_data = products_df[products_df['sku'] == sku]
                if not prod_data.empty:
                    prod_row = prod_data.iloc[0]
                    peer_products.append({
                        'brand': str(prod_row['brand']),
                        'product': str(prod_row['ProductDisplayName']),
                        'sku': str(sku),
                        'users': count,
                        'trend': 'popular',
                        'price': int(prod_row['price'])
                    })
            print(f"   ✅ Found {len(peer_products)} products from member purchase history")
    
    recommendation = None
    detailed_explanation = None
    recommended_products = []
    
    if GEMINI_API_KEY:
        try:
            print(f"   🤖 USING VERTEX AI GEMINI-2.5-FLASH for detailed product recommendation...")
            model = genai.GenerativeModel('gemini-2.5-flash')
            
            # Build context
            peer_context = ""
            if peer_products:
                peer_context = "\n".join([f"{i+1}. {p['brand']} {p['product']} - {p['users']} peers interested - ₹{p['price']} ({p['trend']})" for i, p in enumerate(peer_products)])
            else:
                peer_context = "Building trend data from recent activity..."
            
            prompt = f"""You are a Style AI providing detailed, personalized product recommendations for a fashion shopper.

**Shopper's Circle Profile:**
- {len(members)} members with similar style preferences
- Circle's favorite brands: {', '.join(top_brands[:3]) if top_brands else 'Nike, Adidas, Reebok, Puma, Peter England'}
- Popular categories: {', '.join(top_categories[:3]) if top_categories else 'Footwear, Apparel, Accessories'}

**Peer Trending Products (What your circle is buying/viewing):**
{peer_context}

**Task:** Provide a DETAILED recommendation (300-400 chars) that includes:

1. **Top Pick**: Recommend a product that matches the circle's brand/category preferences {'from the list above' if peer_products else 'based on the circle profile'}
2. **Why It's Perfect**: Explain why it matches the circle's style (2-3 reasons)
3. **Peer Proof**: {'Mention how many peers love it' if peer_products else "Explain why it fits the circle's style preferences"}
4. **Style Tip**: Give a styling or usage suggestion
5. **Alternatives**: Briefly mention 1-2 other options that would also fit

**Format:**
🎯 **Top Pick:** [Brand] [Product] (₹[price])

✨ **Why It's Perfect:**
- [Reason 1 based on circle preferences]
- [Reason 2 with peer insight]
- [Styling tip or benefit]

👥 **Peer Proof:** [X] members in your circle are interested. [Why they love it]

💡 **Also Trending:** [Alt 1], [Alt 2]

**Example:**
🎯 **Top Pick:** Nike React Infinity Run (₹8,999)

✨ **Why It's Perfect:**
- Your circle loves performance footwear - this fits perfectly
- 8 peers bought it for the premium cushioning and durability
- Great for daily runs and gym sessions

👥 **Peer Proof:** 8 members already own it. They rave about all-day comfort.

💡 **Also Trending:** Adidas Ultraboost (₹9,999), Reebok Floatride (₹7,499)

**Your detailed recommendation:**"""
            
            print(f"   📤 Sending to Vertex AI...")
            response = model.generate_content(prompt, generation_config={
                'temperature': 0.7,
                'top_p': 0.9,
                'max_output_tokens': 400
            })
            
            recommendation = response.text.strip().replace('**', '').replace('*', '')
            detailed_explanation = recommendation
            recommended_products = peer_products[:3]  # Top 3 trending
            
            print(f"   ✅ VERTEX AI DETAILED RECOMMENDATION GENERATED")
            print(f"   📝 Length: {len(recommendation)} chars")
            print(f"   🎯 Generated using Gemini 2.5 Flash\n")
            
        except Exception as e:
            print(f"   ❌ Vertex AI failed: {e}\n")
    
    # Fallback with detailed info
    if not recommendation and peer_products:
        top = peer_products[0]
        recommendation = f"""🎯 Top Pick: {top['brand']} {top['product']} (₹{top['price']})

✨ Why It's Perfect:
- {top['users']} peers in your circle are interested
- {top['trend'].capitalize()} in your style community
- Matches your circle's taste for {', '.join(top_brands[:2]) if top_brands else 'quality products'}

👥 Peer Proof: Popular choice among members who share your style preferences.

💡 Also Trending: {peer_products[1]['brand'] + ' ' + peer_products[1]['product'] if len(peer_products) > 1 else 'Explore more in your circle'}"""
        detailed_explanation = recommendation
        recommended_products = peer_products[:3]
    elif not recommendation:
        recommendation = "🎯 Start exploring! Your circle members are discovering great products."
        detailed_explanation = "Chat with your circle to see what trending products match your style."
        recommended_products = []
    
    return {
        "recommendation": recommendation,
        "detailed_explanation": detailed_explanation,
        "products": recommended_products,
        "method": "vertex_ai" if GEMINI_API_KEY else "template"
    }


@app.post("/circle/{circle_id}/ai/summarize")
def get_chat_summary(circle_id: str, user_id: str):
    """Summarize recent chat activity using Vertex AI - analyze brands/products mentioned"""
    print(f"\n📊 USER REQUESTED CHAT SUMMARY")
    print(f"   Circle: {circle_id} | User: {user_id}")
    
    if circle_id not in circles:
        raise HTTPException(status_code=404, detail="Circle not found")
    
    if user_id not in circles[circle_id]:
        raise HTTPException(status_code=403, detail="User not in this circle")
    
    # Get recent messages
    messages = redis_utils.get_messages(circle_id, limit=50)
    user_messages = [m for m in messages if m.get('type') == 'user']
    
    print(f"   📊 Found {len(user_messages)} user messages to analyze")
    
    if len(user_messages) < 1:
        return {
            "summary": "💬 No chat activity yet. Start a conversation!",
            "detailed_analysis": "Start chatting with your circle to see AI-powered insights about trending brands and products.",
            "brands": [],
            "message_count": 0,
            "active_members": 0,
            "method": "template"
        }
    
    # Extract brands and products mentioned in chat
    chat_text = " ".join([m.get('text', '').lower() for m in user_messages])
    brands_mentioned = []
    products_mentioned = []
    
    print(f"   🔍 Analyzing chat text: {chat_text[:100]}...")
    
    # Detect brand mentions from products.csv
    unique_brands = products_df['brand'].unique()
    for brand in unique_brands:
        brand_lower = str(brand).lower()
        if brand_lower in chat_text and brand not in brands_mentioned:
            brands_mentioned.append(str(brand))
    
    # Get unique brands (top 5)
    brands_mentioned = list(set(brands_mentioned))[:5]
    print(f"   🏷️  Brands detected: {brands_mentioned}")
    
    summary = None
    detailed_analysis = None
    brands_list = []
    products_list = []
    
    # Get active member count
    active_members = len(set([m.get('alias', 'Unknown') for m in user_messages]))
    print(f"   👥 Active members: {active_members}")
    
    if GEMINI_API_KEY:
        try:
            print(f"   🤖 USING VERTEX AI GEMINI-2.5-FLASH for detailed chat analysis...")
            model = genai.GenerativeModel('gemini-2.5-flash')
            
            # Prepare detailed chat context
            chat_context = "\n".join([f"- {m.get('alias', 'User')}: {m.get('text', '')}" for m in user_messages[-20:]])
            brands_context = f"Brands detected: {', '.join(brands_mentioned)}" if brands_mentioned else "Analyze the chat to identify any brands or products mentioned"
            
            prompt = f"""You are a Style AI providing detailed analysis of fashion community chat activity.

**Chat Activity:**
- Total messages: {len(user_messages)}
- Active members: {active_members}
- {brands_context}

**Recent Conversation:**
{chat_context}

**Task:** Analyze the conversation CAREFULLY and provide a DETAILED chat summary (300-400 chars) that includes:

1. **Brands Mentioned**: Extract and list ALL brand names mentioned in the actual chat messages (e.g., Nike, Reebok, Peter England, Adidas, Puma, etc.). Read the conversation carefully.
2. **Products Discussed**: List specific product types found in messages (shoes, bags, tshirts, polo, sneakers, flip flops, etc.)
3. **What Members Specifically Said**: Quote or paraphrase actual messages (e.g., "loved the unisex bag", "shoes were amazing")
4. **Common Themes**: Main topics - quality, style, comfort, specific products mentioned
5. **Key Insights**: What products/brands are generating the most interest based on actual messages?

**Format:**
💬 **Chat Summary**

🏷️ **Brands Discussed:** [Brand 1], [Brand 2], [Brand 3]...

👕 **Products Mentioned:**
- [Product 1] - [how many times/context]
- [Product 2] - [how many times/context]
- [Product 3] - [how many times/context]

✨ **Member Highlights:**
- [What members loved or praised]
- [Comparison or questions raised]

🎯 **Themes:** [Quality/Style/Comfort focus], [Price discussions], [Recommendations shared]

**Example:**
💬 **Chat Summary**

🏷️ **Brands Discussed:** Reebok, Peter England, Nike

👕 **Products Mentioned:**
- Reebok shoes - mentioned 3x, praised for comfort
- Peter England polo tshirts - 2 mentions, loved for quality
- Unisex bags - discussed by 2 members

✨ **Member Highlights:**
- Members love Reebok's comfort and durability
- Peter England quality getting high praise
- Discussion about best casual style options

🎯 **Themes:** Quality-focused, practical style choices, peer recommendations valued

**Your detailed analysis:**"""
            
            print(f"   📤 Sending to Vertex AI...")
            response = model.generate_content(prompt, generation_config={
                'temperature': 0.5,
                'top_p': 0.9,
                'max_output_tokens': 450
            })
            
            summary = response.text.strip().replace('**', '').replace('*', '')
            detailed_analysis = summary
            
            # Extract brands from AI response if not detected earlier
            if not brands_mentioned:
                # Try to extract brand names from the summary
                summary_lower = summary.lower()
                for brand in unique_brands:
                    if str(brand).lower() in summary_lower:
                        brands_mentioned.append(str(brand))
                brands_mentioned = list(set(brands_mentioned))[:5]
            
            brands_list = brands_mentioned
            
            print(f"   ✅ VERTEX AI DETAILED SUMMARY GENERATED")
            print(f"   📝 Length: {len(summary)} chars")
            print(f"   🏷️  Final brands list: {brands_list}")
            print(f"   🎯 Generated using Gemini 2.5 Flash\n")
            
        except Exception as e:
            print(f"   ❌ Vertex AI failed: {e}\n")
    
    # Enhanced fallback
    if not summary:
        if brands_mentioned:
            summary = f"""💬 Chat Summary

🏷️ Brands Discussed: {', '.join(brands_mentioned[:5])}

👕 Products Mentioned:
- {len(user_messages)} messages analyzing products
- {len(set([m['alias'] for m in user_messages]))} active members sharing opinions

✨ Member Highlights:
- Community discussing {', '.join(brands_mentioned[:3])}
- Peer recommendations and style tips being shared

🎯 Themes: Quality products, peer insights, style discovery"""
            brands_list = brands_mentioned
        else:
            summary = f"""💬 Chat Summary

📊 Activity: {len(user_messages)} messages from {len(set([m['alias'] for m in user_messages]))} members

✨ Member Highlights:
- Community actively discussing style and products
- Peer recommendations being shared

🎯 Themes: Style exploration, community engagement"""
        detailed_analysis = summary
        brands_list = brands_mentioned
    
    return {
        "summary": summary,
        "detailed_analysis": detailed_analysis,
        "brands": brands_list,
        "message_count": len(user_messages),
        "active_members": len(set([m['alias'] for m in user_messages])),
        "method": "vertex_ai" if GEMINI_API_KEY else "template"
    }


@app.post("/circle/{circle_id}/chat/send")
def send_chat_message(circle_id: str, message: ChatMessage):
    """Send a REAL customer message to circle chat (no AI-generated user messages)"""
    user_id = message.user_id
    
    # Validate circle exists
    if circle_id not in circles:
        raise HTTPException(status_code=404, detail="Circle not found")
    
    # Validate user is in circle
    if user_id not in circles[circle_id]:
        raise HTTPException(status_code=403, detail="User not in this circle")
    
    # Check rate limit
    if not _check_rate_limit(user_id):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Please wait a moment.")
    
    # Get messages from Redis
    existing_messages = redis_utils.get_messages(circle_id, limit=20)
    
    # Validate message
    is_valid, error_msg = _validate_message(message.text, existing_messages)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)
    
    # Get or generate alias (stored in Redis)
    alias = redis_utils.get_alias(user_id, circle_id)
    if not alias:
        alias = _generate_alias(user_id, circle_id)
        redis_utils.store_alias(user_id, circle_id, alias)
    
    # Create message
    now = datetime.now()
    message_id = f"msg_{circle_id}_{int(now.timestamp() * 1000)}"
    
    chat_message = {
        "message_id": message_id,
        "circle_id": circle_id,
        "alias": alias,
        "text": message.text,
        "timestamp": now.isoformat(),
        "type": "user"
    }
    
    # Store message in Redis
    redis_utils.store_message(circle_id, chat_message)
    redis_utils.add_user_timestamp(user_id, now.isoformat())
    
    print(f"\n💬 User message posted to circle {circle_id}")
    print(f"   User: {alias} | Message: {message.text[:50]}...")
    print(f"   ℹ️  Automatic AI insights disabled - use AI buttons for recommendations\n")
    
    # NO automatic AI insights - only when user clicks AI buttons
    return {
        "status": "sent",
        "message": chat_message,
        "ai_insight": None  # AI insights only on demand
    }


@app.get("/circle/{circle_id}/chat/messages")
def get_chat_messages(circle_id: str, user_id: str, limit: int = 50):
    """Get recent REAL chat messages for a circle (no simulated messages)"""
    # Validate circle exists
    if circle_id not in circles:
        raise HTTPException(status_code=404, detail="Circle not found")
    
    # Validate user is in circle
    if user_id not in circles[circle_id]:
        raise HTTPException(status_code=403, detail="User not in this circle")
    
    # Get messages from Redis
    messages = redis_utils.get_messages(circle_id, limit=limit)
    total_count = redis_utils.get_message_count(circle_id)
    
    # Get or generate user alias
    alias = redis_utils.get_alias(user_id, circle_id)
    if not alias:
        alias = _generate_alias(user_id, circle_id)
        redis_utils.store_alias(user_id, circle_id, alias)
    
    return {
        "circle_id": circle_id,
        "user_alias": alias,
        "total_messages": total_count,
        "messages": messages
    }


@app.post("/circle/{circle_id}/generate-insight")
def force_generate_insight(circle_id: str):
    """Manually trigger AI insight generation (for testing)"""
    if circle_id not in circles:
        raise HTTPException(status_code=404, detail="Circle not found")
    
    # Generate insight regardless of cooldown
    insight_text = _generate_ai_insight(circle_id)
    
    if insight_text:
        # Store as AI message
        ai_message = {
            "message_id": f"ai_{circle_id}_{int(datetime.now().timestamp() * 1000)}",
            "circle_id": circle_id,
            "alias": "Style AI",
            "text": insight_text,
            "timestamp": datetime.now().isoformat(),
            "type": "ai_insight"
        }
        redis_utils.store_message(circle_id, ai_message)
        redis_utils.set_ai_insight_timer(circle_id, datetime.now().isoformat())
        
        return {
            "status": "generated",
            "insight": ai_message
        }
    
    return {
        "status": "no_insight",
        "message": "No insights available yet"
    }


@app.get("/health")
def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "gemini_configured": bool(GEMINI_API_KEY),
        "redis_connected": redis_utils.test_connection(),
        "circles": len(circles),
        "users": len(user_profiles)
    }


@app.get("/stats")
def get_stats():
    """Get system statistics"""
    circle_sizes = [len(members) for members in circles.values()]
    
    return {
        "total_circles": len(circles),
        "total_users": len(user_profiles),
        "total_interactions": len(interaction_log),
        "avg_circle_size": sum(circle_sizes) / len(circle_sizes) if circle_sizes else 0,
        "largest_circle": max(circle_sizes) if circle_sizes else 0,
        "smallest_circle": min(circle_sizes) if circle_sizes else 0
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8009)