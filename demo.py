#!/usr/bin/env python3
"""Demo script — decompile a recipe from product label images or text."""

import argparse

from dotenv import load_dotenv

load_dotenv()

from app import (
    decompile_recipe,
    encode_image,
    extract_from_images,
    research_keto_substitutions,
    search_reference_recipes,
)
from recipe_scraper import find_reference_recipes


def demo_from_images(image_paths: list[str]):
    print(f"Processing {len(image_paths)} image(s)...\n")

    image_data_list = []
    for path in image_paths:
        with open(path, "rb") as f:
            raw = f.read()
        content_type = "image/heic" if path.lower().endswith((".heic", ".heif")) else "image/jpeg"
        b64, media_type = encode_image(raw, content_type)
        image_data_list.append((b64, media_type))

    print("=== Step 1: Extracting product info from images ===\n")
    product_info = extract_from_images(image_data_list)
    print(product_info)

    print("\n=== Step 2: Scraping recipe websites ===\n")
    scraped = find_reference_recipes(
        product_name=product_info.split("\n")[0][:100],
        ingredients=[line.strip() for line in product_info.split("\n") if line.strip()],
    )
    print(scraped[:2000])

    print("\n=== Step 3: AI web search for more references ===\n")
    ai_refs = search_reference_recipes(product_info)
    print(ai_refs[:2000])

    print("\n=== Step 4: Researching keto/low-carb substitutions ===\n")
    keto = research_keto_substitutions(product_info)
    print(keto[:2000])

    all_refs = f"## Scraped Recipes\n\n{scraped}\n\n## AI Web Search\n\n{ai_refs}"

    print("\n" + "=" * 60)
    print("  DECOMPILED RECIPE (Original + Keto)")
    print("=" * 60 + "\n")
    recipe = decompile_recipe(product_info, all_refs, keto)
    print(recipe)


def demo_from_text():
    """Run with the Limoncello sorbet data extracted from the images."""
    product_info = """Product: Sprouts Organic Limoncello (Lemon Sorbet/Gelato)
Serving Size: 2/3 cup (90g)
Servings per Container: 6
Total Product Weight: ~540g

Ingredients: Organic skim milk, organic lemon juice, organic coconut oil, sugar, water, organic curcuma powder, stabilizers: organic guar gum, organic carob seed flour.

Nutrition Facts (per serving):
- Calories: 130
- Total Fat: 3g (Saturated Fat 2.5g, Trans Fat 0g)
- Cholesterol: 0mg
- Sodium: 25mg
- Total Carbohydrate: 26g (Dietary Fiber 0g, Total Sugars 26g, Includes 21g Added Sugars)
- Protein: Less than 1g
- Vitamin D: 0mcg
- Calcium: 23mg
- Iron: 0mg
- Potassium: 40mg

Contains: Milk, Coconut
Certified Organic by ICEA
Product of Italy"""

    print("=== Product Info ===\n")
    print(product_info)

    print("\n=== Step 1: Scraping recipe websites ===\n")
    scraped = find_reference_recipes(
        product_name="limoncello sorbet",
        ingredients=["skim milk", "lemon juice", "coconut oil", "sugar", "water"],
    )
    print(scraped[:2000])

    print("\n=== Step 2: AI web search ===\n")
    ai_refs = search_reference_recipes(product_info)
    print(ai_refs[:2000])

    print("\n=== Step 3: Researching keto/low-carb substitutions ===\n")
    keto = research_keto_substitutions(product_info)
    print(keto[:2000])

    all_refs = f"## Scraped Recipes\n\n{scraped}\n\n## AI Web Search\n\n{ai_refs}"

    print("\n" + "=" * 60)
    print("  DECOMPILED RECIPE (Original + Keto)")
    print("=" * 60 + "\n")
    recipe = decompile_recipe(product_info, all_refs, keto)
    print(recipe)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Recipe Decompiler Demo")
    parser.add_argument("images", nargs="*", help="Image file paths (HEIC, JPG, PNG)")
    parser.add_argument("--text", action="store_true", help="Run text-based demo with Limoncello data")
    args = parser.parse_args()

    if args.text or not args.images:
        demo_from_text()
    else:
        demo_from_images(args.images)
