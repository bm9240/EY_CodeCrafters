"""
Recommendation Flow Manager
============================
Progressive clarification engine for the Sales Agent's recommendation intent.

Responsibilities
-----------------
* Detect the correct /recommend mode from the user's message
  (normal | gifting_genius | trendseer)
* Maintain a session-level ``recommendation_context`` scaffold that tracks
  every field collected across multiple conversational turns
* Decide what (single) natural question to ask next when required fields
  are still missing
* Signal when enough context has been gathered so the orchestrator can
  stop asking and fire the /recommend API call
* Build the final RecommendationRequest payload from the collected context

Design principles
-----------------
* Stateless module – all state lives in the ``recommendation_context`` dict
  which is stored inside session metadata between turns.
* Does NOT call the /recommend endpoint itself – that remains the
  responsibility of ``call_recommendation_worker`` in sales_graph.py.
* Does NOT modify recommendation/app.py, API schema, or any other flows.
* One question per turn – never a bullet interrogation.
* After 2 clarification attempts (or 2 vague answers) → proceed with
  whatever has been collected; never loop forever.
"""

import re
import logging
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# KEYWORD BANKS
# ---------------------------------------------------------------------------

GIFTING_KEYWORDS = {
    # Explicit gifting words
    "gift", "gifting", "present", "surprise",
    # Occasions
    "birthday", "anniversary", "wedding", "festive",
    "diwali", "christmas", "eid", "holi",
    # Relations (common short forms included)
    "mom", "mum", "mummy", "mama", "mother",
    "dad", "papa", "daddy", "father",
    "sister", "sis", "brother", "bro",
    "wife", "husband", "girlfriend", "boyfriend", "gf", "bf",
    "aunt", "aunty", "uncle",
    "grandma", "grandmother", "grandpa", "grandfather",
    "daughter", "son", "niece", "nephew", "cousin",
    # Phrases (checked via substring)
    "for my", "for her", "for him", "for them",
}

TREND_KEYWORDS = {
    "trending", "trend", "trendseer",
    "what's popular", "whats popular", "what is popular",
    "what's in", "whats in", "new arrivals",
    "hot right now", "fresh picks", "popular right now",
    "what should i buy", "what to buy next", "what's new",
}

# Canonical relation name → gender
RELATION_NORMALISE: Dict[str, str] = {
    "mom": "mother", "mum": "mother", "mummy": "mother",
    "mama": "mother", "mommy": "mother",
    "dad": "father", "papa": "father", "daddy": "father", "pops": "father",
    "sis": "sister",  "bro": "brother",
    "gf": "girlfriend", "bf": "boyfriend",
    "granny": "grandmother", "grandma": "grandmother",
    "grandpa": "grandfather", "granddad": "grandfather",
}

GENDER_FROM_RELATION: Dict[str, str] = {
    "mother": "female", "grandmother": "female", "sister": "female",
    "wife": "female", "girlfriend": "female", "daughter": "female",
    "aunt": "female", "niece": "female",
    "father": "male", "grandfather": "male", "brother": "male",
    "husband": "male", "boyfriend": "male", "son": "male",
    "uncle": "male", "nephew": "male",
}

CATEGORY_KEYWORDS: List[str] = [
    # Footwear
    "footwear", "shoes", "sneakers", "boots", "sandals", "heels", "flats",
    # Clothing
    "clothing", "apparel", "shirt", "t-shirt", "tshirt", "tops", "jeans",
    "trousers", "pants", "dress", "kurta", "saree", "lehenga",
    "jacket", "coat", "sweater", "hoodie", "sportswear", "activewear", "gym wear",
    # Accessories & lifestyle
    "accessories", "bags", "handbag", "watches", "jewellery", "jewelry",
    "sunglasses", "belts", "wallets",
    # Personal care
    "personal care", "beauty", "skincare", "perfume", "fragrance", "makeup",
]

INTEREST_MAP: Dict[str, str] = {
    "jewellery": "jewelry",   "jewelry": "jewelry",
    "accessories": "accessories",
    "watches": "watches",
    "sports": "sports",       "fitness": "fitness",
    "fashion": "fashion",
    # "clothing" maps to "fashion" so the recommendation engine's interest_category_map
    # correctly scores Apparel products (it knows "fashion" -> Apparel, not "clothing")
    "clothing": "fashion",    "clothes": "fashion",    "apparel": "fashion",
    "shoes": "footwear",      "footwear": "footwear",  "sneakers": "footwear",
    "bags": "bags",           "handbag": "bags",
    "perfume": "fragrances",  "fragrance": "fragrances",
    "skincare": "skincare",   "makeup": "makeup",
    "gaming": "gaming",       "books": "books",
    "electronics": "electronics",
}

# Maps user-spoken category words to the actual values stored in the DB `category` column
# (Supabase uses 'Apparel', 'Accessories', 'Footwear', 'Personal Care', etc.)
CATEGORY_TO_DB: Dict[str, str] = {
    "clothing":     "Apparel",
    "clothes":      "Apparel",
    "apparel":      "Apparel",
    "shirt":        "Apparel",
    "tops":         "Apparel",
    "dress":        "Apparel",
    "kurta":        "Apparel",
    "sportswear":   "Apparel",
    "activewear":   "Apparel",
    "footwear":     "Footwear",
    "shoes":        "Footwear",
    "shoe":         "Footwear",
    "sneakers":     "Footwear",
    "boots":        "Footwear",
    "sandals":      "Footwear",
    "heels":        "Footwear",
    "flats":        "Footwear",
    "accessories":  "Accessories",
    "accessory":    "Accessories",
    "jewellery":    "Accessories",
    "jewelry":      "Accessories",
    "bags":         "Accessories",
    "handbag":      "Accessories",
    "watches":      "Accessories",
    "sunglasses":   "Accessories",
    "belts":        "Accessories",
    "wallets":      "Accessories",
    "personal care": "Personal Care",
    "beauty":       "Personal Care",
    "skincare":     "Personal Care",
    "makeup":       "Personal Care",
}

# (pattern, kind) – kind = "max" | "range" | "around" | "exact"
BUDGET_PATTERNS: List[Tuple[str, str]] = [
    (r"under\s*₹?\s*(\d[\d,]*)",                               "max"),
    (r"below\s*₹?\s*(\d[\d,]*)",                               "max"),
    (r"less\s+than\s*₹?\s*(\d[\d,]*)",                         "max"),
    (r"₹?\s*(\d[\d,]*)\s*(?:to|-)\s*₹?\s*(\d[\d,]*)",          "range"),
    (r"around\s*₹?\s*(\d[\d,]*)",                               "around"),
    (r"₹\s*(\d[\d,]*)",                                         "exact"),
    (r"\b(\d[\d,]*)\s*(?:rupees|rs\.?)\b",                      "exact"),
]

GENDER_KEYWORDS: Dict[str, List[str]] = {
    "female": ["women", "woman", "female", "her", "she", "girl", "girls", "ladies"],
    "male":   ["men",   "man",   "male",   "him", "he",  "boy",  "boys",  "gents"],
}

OCCASION_KEYWORDS: Dict[str, List[str]] = {
    "birthday":    ["birthday", "bday"],
    "anniversary": ["anniversary"],
    "wedding":     ["wedding", "marriage", "engagement"],
    "festive":     ["diwali", "christmas", "eid", "holi", "festival", "festive"],
    # NOTE: "casual" and "everyday" are intentionally excluded — they match
    # style answers and cause the occasion question to be skipped silently.
}

# style label → list of words that signal it
STYLE_KEYWORDS: Dict[str, List[str]] = {
    "sporty":   ["sporty", "sport", "athletic", "active", "gym", "running", "workout", "fitness"],
    "elegant":  ["elegant", "elegant", "classy", "sophisticated", "formal", "classic", "graceful"],
    "casual":   ["casual", "everyday", "relaxed", "chill", "laid back", "laid-back", "comfy", "comfortable"],
    "trendy":   ["trendy", "fashionable", "stylish", "modern", "contemporary", "chic", "street", "streetwear"],
    "bohemian": ["boho", "bohemian", "ethnic", "traditional", "festive", "indo-western"],
    "minimal":  ["minimal", "minimalist", "simple", "clean", "understated", "subtle"],
    "bold":     ["bold", "vibrant", "bright", "colorful", "statement", "loud"],
}

# Map style label → interest tags understood by the recommendation engine
STYLE_TO_INTERESTS: Dict[str, List[str]] = {
    "sporty":   ["sports", "fitness"],
    "elegant":  ["fashion"],
    "casual":   ["fashion"],
    "trendy":   ["fashion"],
    "bohemian": ["fashion"],
    "minimal":  ["fashion"],
    "bold":     ["fashion"],
}

KNOWN_BRANDS: List[str] = [
    "nike", "adidas", "puma", "reebok", "levis", "levi's",
    "h&m", "zara", "mango", "fossil", "titan", "casio",
    "woodland", "bata", "campus", "hm",
]

VAGUE_PHRASES = {
    "anything", "anything is fine", "anything's fine",
    "you decide", "you suggest", "i don't know", "i dont know",
    "no preference", "any", "whatever", "not sure",
    "don't know", "dont know", "surprise me", "up to you",
    "your choice", "no idea", "you choose", "just suggest",
    "doesn't matter", "does not matter", "doesn't matter to me",
}


# ---------------------------------------------------------------------------
# MODE DETECTION
# ---------------------------------------------------------------------------

def detect_mode(message: str, entities: Dict[str, Any]) -> str:
    """
    Determine which /recommend mode best fits the user's request.

    Returns
    -------
    "trendseer"     – trend / what's-popular request
    "gifting_genius" – gifting / relation-based request
    "normal"        – default shopping
    """
    msg_lower = message.lower()

    # Trend mode takes precedence
    if any(kw in msg_lower for kw in TREND_KEYWORDS):
        return "trendseer"

    # Gifting mode – keyword substring scan
    if any(kw in msg_lower for kw in GIFTING_KEYWORDS):
        return "gifting_genius"

    # Gifting mode – from Vertex entities
    if entities.get("recipient_relation"):
        return "gifting_genius"
    if entities.get("occasion") in (
        "birthday", "anniversary", "wedding", "festive", "gift"
    ):
        return "gifting_genius"

    return "normal"


# ---------------------------------------------------------------------------
# FIELD EXTRACTION HELPERS
# ---------------------------------------------------------------------------

def _is_vague(text: str) -> bool:
    """Return True if user gave a non-committal / vague answer."""
    text_lower = text.lower().strip()
    return text_lower in VAGUE_PHRASES or any(v in text_lower for v in VAGUE_PHRASES)


def _extract_relation(text: str) -> Optional[str]:
    """Extract recipient relation from text, returning canonical form."""
    text_lower = text.lower()

    # Check short-form nicknames via word-boundary match
    for nick, canonical in RELATION_NORMALISE.items():
        if re.search(rf"\b{re.escape(nick)}\b", text_lower):
            return canonical

    # Check canonical relation names directly
    for relation in GENDER_FROM_RELATION:
        if re.search(rf"\b{re.escape(relation)}\b", text_lower):
            return relation

    # Phrase mining: "for my X" / "to my X" / "gifting my X"
    m = re.search(
        r"(?:for|to|gifting|gift\s+for|present\s+for|buying\s+for)\s+(?:my\s+)?(\w+)",
        text_lower,
    )
    if m:
        candidate = m.group(1)
        canonical = RELATION_NORMALISE.get(candidate)
        if canonical:
            return canonical
        if candidate in GENDER_FROM_RELATION:
            return candidate

    return None


def _extract_category(text: str) -> Optional[str]:
    """Return the first matching category keyword found in text."""
    text_lower = text.lower()
    for kw in CATEGORY_KEYWORDS:
        if kw in text_lower:
            return kw
    return None


def _extract_interests(text: str) -> List[str]:
    """Return a list of normalised interest tags from text."""
    text_lower = text.lower()
    found: List[str] = []
    for kw, norm in INTEREST_MAP.items():
        if kw in text_lower and norm not in found:
            found.append(norm)
    return found


def _extract_budget(text: str) -> Tuple[Optional[int], Optional[int]]:
    """
    Parse budget information from text.
    Returns (budget_min, budget_max); either can be None.
    """
    text_clean = text.lower().replace(",", "")
    for pattern, kind in BUDGET_PATTERNS:
        m = re.search(pattern, text_clean)
        if m:
            if kind == "max":
                return None, int(m.group(1))
            elif kind == "range":
                return int(m.group(1)), int(m.group(2))
            elif kind in ("around", "exact"):
                v = int(m.group(1))
                return int(v * 0.8), int(v * 1.2)
    return None, None


def _extract_gender(text: str) -> Optional[str]:
    text_lower = text.lower()
    for gender, keywords in GENDER_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return gender
    return None


def _extract_occasion(text: str) -> Optional[str]:
    text_lower = text.lower()
    for occasion, keywords in OCCASION_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return occasion
    return None


def _extract_brands(text: str) -> List[str]:
    text_lower = text.lower()
    return [b.title() for b in KNOWN_BRANDS if re.search(rf"\b{re.escape(b)}\b", text_lower)]


def _extract_style(text: str) -> Optional[str]:
    """Return the dominant style preference found in text, or None."""
    text_lower = text.lower()
    for style, keywords in STYLE_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return style
    return None


# ---------------------------------------------------------------------------
# CONTEXT MANAGEMENT
# ---------------------------------------------------------------------------

def init_context() -> Dict[str, Any]:
    """Return a fresh, empty recommendation_context scaffold."""
    return {
        "mode": "",
        "recipient_relation": "",
        "recipient_gender": "",
        "category": "",
        "style": "",          # sporty / elegant / casual / trendy / bohemian / minimal / bold
        "interests": [],
        "budget_min": None,
        "budget_max": None,
        "preferred_brands": [],
        "occasion": "",
        "clarification_stage": "collecting",
        "clarification_attempts": 0,   # How many questions we've asked
        "vague_count": 0,              # How many times user was vague
    }


def absorb_message(text: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse *text* and merge any discovered fields into a copy of *ctx*.

    Always returns a new dict – does not mutate the input.
    """
    ctx = dict(ctx)  # shallow copy

    if _is_vague(text):
        ctx["vague_count"] = ctx.get("vague_count", 0) + 1
        return ctx

    # ── Recipient relation (gifting) ──────────────────────────────────────
    if not ctx.get("recipient_relation"):
        rel = _extract_relation(text)
        if rel:
            ctx["recipient_relation"] = rel

    # ── Auto-infer gender from relation (always, regardless of who set it) ─
    if ctx.get("recipient_relation") and not ctx.get("recipient_gender"):
        ctx["recipient_gender"] = GENDER_FROM_RELATION.get(ctx["recipient_relation"], "")

    # ── Category ─────────────────────────────────────────────────────────
    if not ctx.get("category"):
        cat = _extract_category(text)
        if cat:
            ctx["category"] = cat

    # ── Interests ────────────────────────────────────────────────────────
    interests = _extract_interests(text)
    if interests:
        existing: List[str] = list(ctx.get("interests") or [])
        for i in interests:
            if i not in existing:
                existing.append(i)
        ctx["interests"] = existing

    # ── Budget ───────────────────────────────────────────────────────────
    bmin, bmax = _extract_budget(text)
    if bmin is not None and ctx.get("budget_min") is None:
        ctx["budget_min"] = bmin
    if bmax is not None and ctx.get("budget_max") is None:
        ctx["budget_max"] = bmax

    # ── Explicit gender (may override relation inference) ─────────────────
    if not ctx.get("recipient_gender"):
        g = _extract_gender(text)
        if g:
            ctx["recipient_gender"] = g

    # ── Style preference ──────────────────────────────────────────────────
    if not ctx.get("style"):
        sty = _extract_style(text)
        if sty:
            ctx["style"] = sty
            # Seed interests from style immediately
            style_interests = STYLE_TO_INTERESTS.get(sty, [])
            existing: List[str] = list(ctx.get("interests") or [])
            for si in style_interests:
                if si not in existing:
                    existing.append(si)
            ctx["interests"] = existing

    # ── Occasion ──────────────────────────────────────────────────────────
    if not ctx.get("occasion"):
        occ = _extract_occasion(text)
        if occ:
            ctx["occasion"] = occ

    # ── Brands ───────────────────────────────────────────────────────────
    brands = _extract_brands(text)
    if brands:
        existing_brands: List[str] = list(ctx.get("preferred_brands") or [])
        for b in brands:
            if b not in existing_brands:
                existing_brands.append(b)
        ctx["preferred_brands"] = existing_brands

    return ctx


# ---------------------------------------------------------------------------
# READINESS CHECK
# ---------------------------------------------------------------------------

def _missing_normal(ctx: Dict[str, Any]) -> List[str]:
    """Ordered list of fields still needed for a 'normal' recommendation."""
    missing = []
    if not ctx.get("category"):
        missing.append("category")
        return missing  # Ask category first before anything else
    if not ctx.get("style"):
        missing.append("style")
    if not ctx.get("occasion"):
        missing.append("occasion")
    if ctx.get("budget_min") is None and ctx.get("budget_max") is None:
        missing.append("budget")
    return missing


def _missing_gifting(ctx: Dict[str, Any]) -> List[str]:
    """Ordered list of fields still needed for a 'gifting_genius' recommendation."""
    missing = []
    if not ctx.get("recipient_relation"):
        missing.append("relation")
        return missing  # Must know who before asking anything else
    if not ctx.get("category") and not ctx.get("interests"):
        missing.append("category_or_interests")
    if not ctx.get("style"):
        missing.append("style")
    if not ctx.get("occasion"):
        missing.append("occasion")
    if ctx.get("budget_min") is None and ctx.get("budget_max") is None:
        missing.append("budget")
    return missing


def get_missing_fields(ctx: Dict[str, Any]) -> List[str]:
    """Return a list of field names still needed for the current mode."""
    mode = ctx.get("mode", "normal")
    if mode == "trendseer":
        return []                      # TrendSeer needs nothing – call immediately
    if mode == "gifting_genius":
        return _missing_gifting(ctx)
    return _missing_normal(ctx)


def is_ready(ctx: Dict[str, Any]) -> bool:
    """
    Return True when the agent should stop asking and call /recommend.

    Stops early after 2 vague answers or 4 clarification rounds,
    so the flow never loops forever.
    """
    if ctx.get("vague_count", 0) >= 2:
        return True
    if ctx.get("clarification_attempts", 0) >= 5:
        return True
    return not get_missing_fields(ctx)


# ---------------------------------------------------------------------------
# QUESTION GENERATION
# ---------------------------------------------------------------------------

def next_question(ctx: Dict[str, Any]) -> str:
    """
    Return the single next natural clarification question to ask.
    Returns an empty string when no question is needed (i.e. is_ready()).

    One question per call – never a bullet list of multiple questions.
    The wording adapts to how many times we've already asked (attempts).
    """
    if is_ready(ctx):
        return ""

    mode = ctx.get("mode", "normal")
    missing = get_missing_fields(ctx)
    if not missing:
        return ""

    field = missing[0]
    relation = ctx.get("recipient_relation") or "them"
    attempts = ctx.get("clarification_attempts", 0)

    if mode == "gifting_genius":
        if field == "relation":
            if attempts == 0:
                return (
                    "That's so thoughtful! 😊 Who are you shopping for? "
                    "(e.g. mom, sister, girlfriend, friend)"
                )
            return "Who would you like to gift this to?"

        if field == "category_or_interests":
            if attempts == 0:
                return (
                    f"What does {relation} usually enjoy? "
                    f"For example — clothing, accessories, jewellery, footwear, "
                    f"or something else?"
                )
            return f"What kind of products would work best for {relation}?"

        if field == "style":
            return (
                f"What's {relation}'s style like? "
                f"For example — sporty, elegant, casual, trendy, or bohemian?"
            )

        if field == "occasion":
            return (
                f"Is this for a special occasion? "
                f"(e.g. birthday, anniversary, just because 😊)"
            )

        if field == "budget":
            return (
                f"What's your budget for this gift? "
                f"(e.g. under ₹2000, ₹1000–₹3000, or let me know a range)"
            )

    # normal mode
    if field == "category":
        if attempts == 0:
            return (
                "I'd love to help! 😊 What kind of products are you looking for? "
                "(e.g. shoes, clothing, accessories, sportswear)"
            )
        return (
            "What category are you shopping in — footwear, "
            "clothing, or accessories?"
        )

    if field == "style":
        return (
            "What style are you going for? "
            "(e.g. sporty, elegant, casual, trendy, minimalist)"
        )

    if field == "occasion":
        return (
            "Is this for any particular occasion, or just everyday use? "
            "(e.g. birthday, gym, office, casual)"
        )

    if field == "budget":
        return (
            "Do you have a budget in mind? "
            "(e.g. under ₹3000, anywhere between ₹1000–₹5000)"
        )

    return ""


# ---------------------------------------------------------------------------
# PAYLOAD BUILDER
# ---------------------------------------------------------------------------

def build_payload(
    ctx: Dict[str, Any],
    customer_id: str,
    cart_skus: Optional[List[str]] = None,
    entities: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Construct the complete RecommendationRequest payload from collected context.

    Falls back to *entities* (from Vertex AI) for any field not yet in context,
    ensuring the payload is always fully populated.
    """
    cart_skus = cart_skus or []
    entities = entities or {}
    mode = ctx.get("mode", "normal")

    # ── Build intent sub-object ───────────────────────────────────────────
    intent: Dict[str, Any] = {}

    # Normalize category to the actual DB value (e.g. 'clothing' → 'Apparel')
    # and detect multiple categories so we can broaden or narrow the filter.
    raw_cat = (
        ctx.get("category")
        or entities.get("category")
        or entities.get("product_type", "")
    )
    single_db_cat = CATEGORY_TO_DB.get(raw_cat.lower(), raw_cat) if raw_cat else ""

    # ── Merge style preference into interests list ────────────────────────
    # Must be built BEFORE the category-detection loop that iterates raw_interests.
    raw_interests: List[str] = list(ctx.get("interests") or [])
    style = ctx.get("style", "")
    if style:
        for si in STYLE_TO_INTERESTS.get(style, []):
            if si not in raw_interests:
                raw_interests.append(si)
    # Also include the style label itself so LLM reasoning can reference it
    if style and style not in raw_interests:
        raw_interests.append(style)

    # Find which DB categories the collected interests imply
    interest_db_cats: List[str] = []
    for interest in raw_interests:
        db_c = CATEGORY_TO_DB.get(interest.lower(), "")
        if db_c and db_c not in interest_db_cats:
            interest_db_cats.append(db_c)

    if len(interest_db_cats) > 1:
        # Multiple different DB categories → don't restrict; let interests rank
        category = ""
    elif interest_db_cats:
        category = interest_db_cats[0]
    else:
        category = single_db_cat

    if category:
        intent["category"] = category

    budget_min = ctx.get("budget_min") if ctx.get("budget_min") is not None else entities.get("price_min")
    budget_max = ctx.get("budget_max") if ctx.get("budget_max") is not None else entities.get("price_max")
    if budget_min is not None:
        intent["budget_min"] = budget_min
    if budget_max is not None:
        intent["budget_max"] = budget_max

    # ── Base payload ─────────────────────────────────────────────────────
    base: Dict[str, Any] = {
        "customer_id": str(customer_id),
        "mode": mode,
        "limit": 5,
    }

    if mode == "gifting_genius":
        # Primary: explicitly collected gender
        # Secondary: infer from the relation name
        # Tertiary: Vertex AI entity
        # Last resort: unisex
        recipient_gender = (
            ctx.get("recipient_gender")
            or GENDER_FROM_RELATION.get(ctx.get("recipient_relation", ""), "")
            or entities.get("gender")
            or "unisex"
        )
        if recipient_gender:
            intent["gender"] = recipient_gender
        occasion = ctx.get("occasion") or entities.get("occasion") or "gift"
        if occasion:
            intent["occasion"] = occasion

        base.update({
            "intent": intent,
            "recipient_relation": (
                ctx.get("recipient_relation")
                or entities.get("recipient_relation")
                or "friend"
            ),
            "recipient_gender": recipient_gender,
            "interests": raw_interests,
            "preferred_brands": ctx.get("preferred_brands") or [],
            "occasion": occasion,
            "safe_sizes_only": False,
        })

    elif mode == "trendseer":
        base.update({
            "current_cart_skus": cart_skus,
        })

    else:  # normal
        gender = ctx.get("recipient_gender") or entities.get("gender")
        if gender:
            intent["gender"] = gender
        base.update({
            "intent": intent,
            "current_cart_skus": cart_skus,
            "interests": raw_interests,
        })

    return base
