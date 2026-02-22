"""
Products repository: reads products from Supabase.

Expects a 'products' table with columns matching products.csv
"""
import pandas as pd
from typing import Optional, List, Dict, Any
from ..supabase_client import select, is_enabled

# Column mapping: Supabase snake_case -> Code expectations for normalized CSV
# Maps from Supabase column names to what the code expects after CSV loads
COLUMN_MAPPING = {
    "product_display_name": "ProductDisplayName",
    "sub_category": "subcategory",
    "review_count": "review_count",  # Keep as is - code expects review_count
    "rating": "ratings",             # Supabase uses singular; code expects plural
    "master_category": "category",   # Supabase uses master_category; CSV uses category
    # Note: 'category' and other fields exist as-is in new CSV schema, no mapping needed
}


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename Supabase columns to match CSV column names."""
    rename_map = {k: v for k, v in COLUMN_MAPPING.items() if k in df.columns}
    if rename_map:
        df = df.rename(columns=rename_map)
    return df


def get_all_products() -> Optional[pd.DataFrame]:
    """
    Get all products from Supabase as a DataFrame.
    Paginates with limit/offset to fetch beyond the default 1000-row PostgREST cap.
    """
    if not is_enabled():
        return None

    try:
        all_rows = []
        page_size = 1000
        offset = 0
        while True:
            rows = select("products", params=f"limit={page_size}&offset={offset}")
            if not rows:
                break
            all_rows.extend(rows)
            if len(rows) < page_size:
                break  # last page
            offset += page_size

        if not all_rows:
            print("[products_repo] No products found in Supabase")
            return None

        df = pd.DataFrame(all_rows)
        df = _normalize_columns(df)
        print(f"[products_repo] Loaded {len(df)} products from Supabase")
        return df
    except Exception as e:
        print(f"[products_repo] Error loading products: {e}")
        return None


def get_product_by_sku(sku: str) -> Optional[Dict[str, Any]]:
    """Get a single product by SKU."""
    if not is_enabled():
        return None
    
    try:
        rows = select("products", params=f"sku=eq.{sku}")
        if not rows:
            return None
        # Normalize keys in the dict
        row = rows[0]
        for old_key, new_key in COLUMN_MAPPING.items():
            if old_key in row:
                row[new_key] = row.pop(old_key)
        return row
    except Exception as e:
        print(f"[products_repo] Error fetching SKU={sku}: {e}")
        return None


def get_products_by_category(category: str) -> Optional[List[Dict[str, Any]]]:
    """Get products filtered by category."""
    if not is_enabled():
        return None
    
    try:
        rows = select("products", params=f"category=eq.{category}")
        if not rows:
            return None
        # Normalize keys in each dict
        for row in rows:
            for old_key, new_key in COLUMN_MAPPING.items():
                if old_key in row:
                    row[new_key] = row.pop(old_key)
        return rows
    except Exception as e:
        print(f"[products_repo] Error fetching category={category}: {e}")
        return None
