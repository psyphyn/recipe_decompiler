"""Scrape recipe websites for reference recipes with exact measurements.

Uses a multi-strategy approach:
1. Direct HTTP scraping of recipe sites (fast, works for sites with JSON-LD)
2. DuckDuckGo search as fallback
3. Relevance filtering to discard unrelated recipes
"""

import json
import re
from urllib.parse import unquote

import httpx

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

RECIPE_SITES = [
    "allrecipes.com",
    "simplyrecipes.com",
    "seriouseats.com",
    "food.com",
    "bonappetit.com",
    "epicurious.com",
    "foodnetwork.com",
    "tasty.co",
]


def _is_recipe_type(t) -> bool:
    if isinstance(t, str):
        return t == "Recipe"
    if isinstance(t, list):
        return "Recipe" in t
    return False


def extract_jsonld_recipe(html: str) -> dict | None:
    """Extract structured recipe data from JSON-LD in HTML."""
    pattern = r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>'
    matches = re.findall(pattern, html, re.DOTALL)

    for match in matches:
        try:
            data = json.loads(match)
            if isinstance(data, dict) and "@graph" in data:
                data = data["@graph"]
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and _is_recipe_type(item.get("@type")):
                        return item
            elif isinstance(data, dict) and _is_recipe_type(data.get("@type")):
                return data
        except json.JSONDecodeError:
            continue
    return None


def is_relevant_recipe(recipe_json: dict, keywords: list[str]) -> bool:
    """Check if a scraped recipe is actually relevant to what we're looking for.

    Uses word-boundary matching to avoid false positives like
    "lemon" matching "lemongrass".
    """
    name = (recipe_json.get("name") or "").lower()
    ingredients_text = " ".join(recipe_json.get("recipeIngredient", [])).lower()
    all_text = f"{name} {ingredients_text}"

    def word_match(keyword: str, text: str) -> bool:
        return bool(re.search(r'\b' + re.escape(keyword.lower()) + r'\b', text))

    # Check name matches (strong signal)
    name_matches = sum(1 for kw in keywords if word_match(kw, name))
    # Check full text matches
    all_matches = sum(1 for kw in keywords if word_match(kw, all_text))

    # Need at least 2 name keyword matches, or 3+ total matches
    return name_matches >= 2 or all_matches >= 3


def format_recipe_data(recipe_json: dict, source_url: str = "") -> str:
    """Format JSON-LD recipe data into readable text."""
    parts = []
    name = recipe_json.get("name", "Unknown Recipe")
    parts.append(f"**{name}**")
    if source_url:
        parts.append(f"Source: {source_url}")

    if recipe_json.get("recipeYield"):
        yield_val = recipe_json["recipeYield"]
        if isinstance(yield_val, list):
            yield_val = yield_val[0]
        parts.append(f"Yield: {yield_val}")

    ingredients = recipe_json.get("recipeIngredient", [])
    if ingredients:
        parts.append("Ingredients:")
        for ing in ingredients:
            parts.append(f"  - {ing}")

    instructions = recipe_json.get("recipeInstructions", [])
    if instructions:
        parts.append("Instructions:")
        for i, step in enumerate(instructions, 1):
            if isinstance(step, dict):
                text = step.get("text", "")
            else:
                text = str(step)
            if text:
                parts.append(f"  {i}. {text}")

    nutrition = recipe_json.get("nutrition", {})
    if nutrition:
        parts.append("Nutrition:")
        for key, val in nutrition.items():
            if key != "@type" and val:
                parts.append(f"  {key}: {val}")

    return "\n".join(parts)


def scrape_recipe_url(url: str, relevance_keywords: list[str] | None = None) -> str | None:
    """Scrape a single recipe URL for structured recipe data.

    If relevance_keywords is provided, only return the recipe if it matches.
    """
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as http:
            resp = http.get(url, headers=HEADERS)
            if resp.status_code != 200:
                return None
            recipe = extract_jsonld_recipe(resp.text)
            if not recipe:
                return None
            if relevance_keywords and not is_relevant_recipe(recipe, relevance_keywords):
                return None
            return format_recipe_data(recipe, source_url=url)
    except Exception:
        pass
    return None


def search_duckduckgo_recipes(query: str, num_results: int = 5) -> list[str]:
    """Search DuckDuckGo for recipe URLs."""
    sites = " OR ".join(f"site:{s}" for s in RECIPE_SITES[:4])
    search_query = f"{query} recipe ({sites})"

    try:
        with httpx.Client(timeout=15, follow_redirects=True) as http:
            resp = http.get(
                "https://html.duckduckgo.com/html/",
                params={"q": search_query},
                headers=HEADERS,
            )
            urls = re.findall(
                r'href="(https?://[^"]+(?:' + "|".join(RECIPE_SITES) + r')[^"]*)"',
                resp.text,
            )
            uddg_urls = re.findall(r'uddg=(https?%3A%2F%2F[^&"]+)', resp.text)
            for encoded in uddg_urls:
                decoded = unquote(encoded)
                if any(site in decoded for site in RECIPE_SITES):
                    urls.append(decoded)

            seen = set()
            unique = []
            for u in urls:
                clean = u.split("&")[0].split("#")[0]
                if clean not in seen and any(site in clean for site in RECIPE_SITES):
                    seen.add(clean)
                    unique.append(clean)
            return unique[:num_results]
    except Exception:
        return []


def search_recipe_sites(query: str) -> list[str]:
    """Search recipe sites directly via their search/browse pages."""
    urls = []
    search_term = query.replace(" ", "+")

    # AllRecipes
    try:
        with httpx.Client(timeout=10, follow_redirects=True) as http:
            resp = http.get(
                f"https://www.allrecipes.com/search?q={search_term}",
                headers=HEADERS,
            )
            found = re.findall(
                r'href="(https://www\.allrecipes\.com/recipe/\d+/[^"]+)"',
                resp.text,
            )
            urls.extend(found[:5])
    except Exception:
        pass

    # Food.com
    try:
        with httpx.Client(timeout=10, follow_redirects=True) as http:
            resp = http.get(
                f"https://www.food.com/search/{search_term}",
                headers=HEADERS,
            )
            found = re.findall(
                r'href="(https://www\.food\.com/recipe/[^"]+)"',
                resp.text,
            )
            urls.extend(found[:3])
    except Exception:
        pass

    return urls


def _build_keywords(product_name: str, ingredients: list[str]) -> list[str]:
    """Build relevance keywords from product name and ingredients."""
    keywords = []
    # Extract meaningful words from product name
    for word in product_name.lower().split():
        word = word.strip("(),.")
        if len(word) > 2 and word not in ("the", "and", "with", "from", "homemade", "organic", "scratch"):
            keywords.append(word)
    # Add key ingredient words
    for ing in ingredients[:5]:
        for word in ing.lower().split():
            word = word.strip("(),.")
            if len(word) > 3 and word not in ("organic", "with"):
                keywords.append(word)
    return list(dict.fromkeys(keywords))  # dedupe preserving order


def find_reference_recipes(product_name: str, ingredients: list[str]) -> str:
    """Find reference recipes online that match the product.

    Searches multiple recipe sites, scrapes JSON-LD structured data,
    and filters for relevance before returning results.
    """
    keywords = _build_keywords(product_name, ingredients)

    # Build search queries — shorter queries work better on recipe sites
    name_words = [w for w in product_name.lower().split() if w not in ("homemade", "organic", "from", "scratch", "the")]
    queries = []
    # Full name
    queries.append(product_name)
    # Shorter variants (recipe sites choke on long queries)
    if len(name_words) > 2:
        queries.append(" ".join(name_words[:2]))
    # Key ingredient combo
    key_ingredients = [i.strip() for i in ingredients[:3] if 3 < len(i.strip()) < 30]
    if key_ingredients:
        queries.append(" ".join(key_ingredients[:2]))

    all_recipes = []
    seen_urls = set()

    for query in queries:
        # Get candidate URLs from multiple sources
        urls = search_recipe_sites(query)
        if len(urls) < 3:
            urls.extend(search_duckduckgo_recipes(query))

        for url in urls:
            if url in seen_urls:
                continue
            seen_urls.add(url)

            # Scrape with relevance check — irrelevant recipes are discarded
            recipe_text = scrape_recipe_url(url, relevance_keywords=keywords)
            if recipe_text:
                all_recipes.append(recipe_text)

            if len(all_recipes) >= 3:
                break
        if len(all_recipes) >= 3:
            break

    if not all_recipes:
        return "No relevant recipes found via direct scraping. Relying on AI web search."

    return "\n\n---\n\n".join(all_recipes)
