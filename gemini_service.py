"""
Rent2Swap - AI Intelligence Service
Server-side integration with Gemini API (gemini-3.8-flash).

CRITICAL ARCHITECTURAL RULES:
1. GEMINI_API_KEY is server-side ONLY. Never exposed to browser / JavaScript.
2. Gemini NEVER invents fake listings. All displayed items come directly from Supabase PostgreSQL.
3. Every call has robust timeout, error catching, and graceful fallbacks.
4. Normal marketplace operations continue seamlessly even if Gemini is unreachable.
"""

import os
import re
import json
import urllib.request
import urllib.error
from config import Config

# Standard Gemini model
GEMINI_MODEL = "gemini-3.8-flash"
API_ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

MARKETPLACE_CATEGORIES = [
    "Books", "Electronics", "Mobile Phones", "Laptops", "Tablets",
    "Cameras", "Headphones", "Speakers", "Gaming Devices", "Cycles",
    "Sports Equipment", "Musical Instruments", "Furniture", "Home Appliances",
    "Tools", "Bags", "Accessories", "Project/Work Equipment", "Vehicles", "Other"
]


def get_gemini_api_key():
    """Retrieve Gemini API key safely from environment."""
    return os.getenv("GEMINI_API_KEY", "").strip()


def call_gemini(prompt: str, temperature: float = 0.2, timeout: int = 2) -> str:
    """
    Invoke Gemini REST API synchronously with strict timeout and error handling.
    Returns response text or empty string on failure.
    """
    api_key = get_gemini_api_key()
    if not api_key:
        print("[Gemini Service] Warning: GEMINI_API_KEY is not set. Using local semantic fallback.")
        return ""

    url = API_ENDPOINT
    payload = {
        "contents": [
            {
                "parts": [{"text": prompt}]
            }
        ],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": 800
        }
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            res_body = response.read().decode("utf-8")
            res_json = json.loads(res_body)
            candidates = res_json.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    return parts[0].get("text", "").strip()
            return ""
    except Exception as e:
        print(f"[Gemini Service Error] {type(e).__name__}: request failed")
        return ""


# ==============================================================================
# PART B: SMART SEARCH UNDERSTANDING
# ==============================================================================

def parse_search_query_with_ai(query: str) -> dict:
    """
    Transform student's natural language query into structured database search intent.
    Example: 'I need a cycle for 2 months under ₹1000 near campus'
    Returns: {
        'category': 'Cycles',
        'transaction_type': 'Rent',
        'max_price': 1000.0,
        'keywords': ['cycle', 'campus'],
        'understanding_summary': 'Looking to rent a bicycle with a budget up to ₹1,000.'
    }
    """
    query_clean = query.strip()
    if not query_clean:
        return {
            "category": None,
            "transaction_type": None,
            "max_price": None,
            "keywords": [],
            "understanding_summary": "Please enter what student gear you are looking for."
        }

    # Prompt Gemini for strict JSON
    prompt = f"""You are the search engine parser for Rent2Swap, a campus peer marketplace.
A student typed this search query:
"{query_clean}"

Extract their intent as a raw JSON object (no markdown, no backticks, only valid JSON) with these exact keys:
{{
  "category": (one of: {MARKETPLACE_CATEGORIES} or null if not specified),
  "transaction_type": ("Rent", "Sell", "Exchange", or null if any),
  "max_price": (number or null),
  "keywords": (array of 1-4 relevant search words),
  "understanding_summary": (one friendly concise sentence summarizing what the student wants)
}}
"""
    ai_raw = call_gemini(prompt, temperature=0.1)

    parsed = None
    if ai_raw:
        # Extract JSON from potential code block
        match = re.search(r'\{.*\}', ai_raw, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                parsed = None

    if parsed and isinstance(parsed, dict) and "understanding_summary" in parsed:
        # Validate category
        if parsed.get("category") not in MARKETPLACE_CATEGORIES:
            parsed["category"] = _fallback_detect_category(query_clean)
        return parsed

    # Local Intelligent Fallback
    return _local_query_parser_fallback(query_clean)


def _local_query_parser_fallback(query: str) -> dict:
    """Rule-based semantic parser fallback when Gemini is offline or unavailable."""
    q_lower = query.lower()

    # Detect category
    category = _fallback_detect_category(query)

    # Detect transaction type
    trans_type = None
    if any(k in q_lower for k in ["rent", "borrow", "rental", "lease"]):
        trans_type = "Rent"
    elif any(k in q_lower for k in ["swap", "exchange", "trade"]):
        trans_type = "Exchange"
    elif any(k in q_lower for k in ["buy", "purchase", "sell", "second hand"]):
        trans_type = "Sell"

    # Detect budget
    max_price = None
    price_match = re.search(r'(?:under|below|budget|within|upto|up to|max|₹|rs\.?)\s*(\d{2,6})', q_lower)
    if price_match:
        try:
            max_price = float(price_match.group(1))
        except ValueError:
            max_price = None

    # Keywords
    tokens = [w for w in re.findall(r'\b[a-zA-Z0-9]{3,}\b', q_lower) 
              if w not in ["the", "for", "and", "under", "need", "want", "near", "campus", "with", "this"]]

    summary_parts = []
    if trans_type:
        summary_parts.append(f"{trans_type}ing")
    else:
        summary_parts.append("Searching for")
    
    if category:
        summary_parts.append(category)
    elif tokens:
        summary_parts.append(tokens[0].title())
    else:
        summary_parts.append("campus gear")

    if max_price:
        summary_parts.append(f"with budget up to ₹{int(max_price):,}")

    return {
        "category": category,
        "transaction_type": trans_type,
        "max_price": max_price,
        "keywords": tokens[:4],
        "understanding_summary": f"Looking for {' '.join(summary_parts)}."
    }


def _fallback_detect_category(text: str) -> str:
    """Smart keyword mapping to official categories."""
    t = text.lower()
    mapping = {
        "Cycles": ["cycle", "bicycle", "bike", "mtb", "firefox", "gear cycle"],
        "Cameras": ["camera", "dslr", "canon", "sony alpha", "nikon", "lens", "tripod"],
        "Laptops": ["laptop", "macbook", "thinkpad", "dell", "hp", "gaming laptop"],
        "Books": ["book", "textbook", "notes", "novel", "engineering book", "gate", "cat"],
        "Headphones": ["headphone", "earphone", "airpods", "earbuds", "bose", "sony"],
        "Gaming Devices": ["ps5", "ps4", "playstation", "xbox", "nintendo", "console"],
        "Musical Instruments": ["guitar", "keyboard", "casio", "ukulele", "flute", "drums"],
        "Mobile Phones": ["phone", "iphone", "android", "smartphone", "oneplus", "samsung"],
        "Tablets": ["tablet", "ipad", "stylus", "tab"],
        "Tools": ["drafter", "calculator", "scientific calculator", "soldering", "tool"],
        "Furniture": ["chair", "desk", "table", "mattress", "study lamp"]
    }
    for cat, kws in mapping.items():
        if any(k in t for k in kws):
            return cat
    return None


def generate_match_reason(query_summary: str, item: dict) -> str:
    """Generate a crisp, convincing match explanation badge for a listing."""
    title = item.get("title", "Item")
    category = item.get("category", "")
    t_type = item.get("transaction_type", "")
    price = item.get("price", 0)
    rent = item.get("rent_amount", 0)

    if t_type == "Rent" and rent:
        return f"Verified campus {category.lower()} available for ₹{int(rent):,}/{item.get('rent_period', 'Day')}"
    elif t_type == "Exchange":
        pref = item.get("exchange_preference")
        return f"Open to swap trade: {pref}" if pref else "Available for student swap exchange"
    elif t_type == "Sell" and price:
        return f"Affordable student deal at ₹{int(price):,}"
    return f"Direct match in {category} on campus"


# ==============================================================================
# PART C: SMART SWAP MATCHING
# ==============================================================================

def find_smart_swap_matches(user_items: list, all_exchange_items: list) -> list:
    """
    Cross-matches student swap items using preferences and category compatibility.
    Produces high-confidence barter matches with explainable scores (70% - 98%).
    """
    matches = []

    for my_item in user_items:
        my_title = my_item.get("title", "").lower()
        my_cat = my_item.get("category", "").lower()
        my_pref = my_item.get("exchange_preference", "").lower()

        for other_item in all_exchange_items:
            # Don't match user's own items
            if other_item.get("user_id") == my_item.get("user_id"):
                continue

            other_title = other_item.get("title", "").lower()
            other_cat = other_item.get("category", "").lower()
            other_pref = other_item.get("exchange_preference", "").lower()

            score = 60
            reasons = []

            # 1. Direct reciprocal swap match
            # What I want matches what they have AND what they want matches what I have
            reciprocal_a = any(w in other_title or w in other_cat for w in _extract_meaningful_words(my_pref)) if my_pref else False
            reciprocal_b = any(w in my_title or w in my_cat for w in _extract_meaningful_words(other_pref)) if other_pref else False

            if reciprocal_a and reciprocal_b:
                score = 96
                reasons.append("Perfect two-way barter match: your items directly satisfy each other's swap wishlist!")
            elif reciprocal_a:
                score = 88
                reasons.append(f"They have the '{other_item.get('title')}' that matches your swap preference '{my_item.get('exchange_preference')}'")
            elif reciprocal_b:
                score = 84
                reasons.append(f"Your '{my_item.get('title')}' fulfills what this student is searching for!")
            elif my_cat == other_cat:
                score = 75
                reasons.append(f"Same academic category ({my_item.get('category')}) suitable for peer upgrade or exchange")
            else:
                score = 68
                reasons.append("Compatible campus valuation and student exchange interest")

            matches.append({
                "my_item": my_item,
                "other_item": other_item,
                "score": score,
                "reason": " ".join(reasons)
            })

    # Sort by match score descending
    matches.sort(key=lambda m: m["score"], reverse=True)
    return matches[:12]


def find_campus_wide_swap_pairs(all_exchange_items: list) -> list:
    """Find trending campus-wide swap pairings for students to browse."""
    pairs = []
    seen = set()

    for i in range(len(all_exchange_items)):
        item_a = all_exchange_items[i]
        pref_a = (item_a.get("exchange_preference") or "").lower()

        for j in range(i + 1, len(all_exchange_items)):
            item_b = all_exchange_items[j]
            if item_a.get("user_id") == item_b.get("user_id"):
                continue

            pair_id = tuple(sorted([item_a["item_id"], item_b["item_id"]]))
            if pair_id in seen:
                continue

            pref_b = (item_b.get("exchange_preference") or "").lower()
            title_a = item_a.get("title", "").lower()
            title_b = item_b.get("title", "").lower()

            words_a = _extract_meaningful_words(pref_a)
            words_b = _extract_meaningful_words(pref_b)

            match_a = any(w in title_b or w in item_b.get("category", "").lower() for w in words_a) if words_a else False
            match_b = any(w in title_a or w in item_a.get("category", "").lower() for w in words_b) if words_b else False

            score = 70
            if match_a and match_b:
                score = 95
                reason = "Reciprocal Wishlist Match: Both students are seeking each other's equipment."
            elif match_a or match_b:
                score = 86
                reason = "Wishlist Match: One student's swap preference directly matches the other item."
            elif item_a.get("category") == item_b.get("category"):
                score = 78
                reason = f"Category Parallel ({item_a.get('category')}): Ideal for semester course change."
            else:
                score = 72
                reason = "Active Campus Barter: High peer rating and verified student listing."

            seen.add(pair_id)
            pairs.append({
                "item_a": item_a,
                "item_b": item_b,
                "score": score,
                "reason": reason
            })

    pairs.sort(key=lambda p: p["score"], reverse=True)
    return pairs[:12]


def _extract_meaningful_words(text: str) -> list:
    """Helper to extract searchable terms from exchange preferences."""
    if not text:
        return []
    words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())
    stop_words = {"any", "good", "condition", "with", "and", "for", "the", "like", "preferably"}
    return [w for w in words if w not in stop_words]
