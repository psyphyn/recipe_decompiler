import base64
import io
import os
from typing import Optional

import anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

from recipe_scraper import find_reference_recipes

load_dotenv()

app = FastAPI(
    title="Recipe Decompiler",
    description="Reverse-engineer recipes from nutrition labels and ingredient lists using AI",
)

client = anthropic.Anthropic()

EXTRACTION_PROMPT = """Analyze this product label image and extract ALL information you can see:

1. **Product name** (if visible)
2. **Ingredients list** (in order as listed)
3. **Nutrition Facts** (complete, including serving size, servings per container, and all nutrients)
4. **Any other relevant details** (brand, origin, allergens, certifications)

Return the data as structured text. Be precise with numbers and units."""

DECOMPILE_PROMPT = """You are a expert food scientist and recipe developer. Your job is to "decompile" a commercial product back into a homemade recipe by reverse-engineering the exact quantities from the nutrition label and ingredient list.

Here is the product information:
{product_info}

Using your knowledge of food science, nutrition, and recipe development, reverse-engineer this into a precise homemade recipe. Follow these steps in your reasoning:

1. **Identify the total product weight** from servings x serving size.
2. **Analyze the ingredient order** — ingredients are listed by weight, most to least.
3. **Use nutrition facts as constraints** to calculate amounts:
   - Fat content tells you how much oil/butter/coconut oil
   - Sugar content (total vs added) helps separate naturally occurring sugars from added
   - Protein content constrains dairy/egg amounts
   - Carbohydrate balance helps verify sugar + starch quantities
4. **Cross-reference** with known nutritional profiles of each ingredient (e.g., coconut oil is ~100% fat, skim milk has ~3.4g protein per 100ml, lemon juice has ~2.5g sugar per oz).
5. **Validate** that your recipe's calculated nutrition roughly matches the label.

Return your response in this exact format:

## [Product Name] — Homemade Recipe

### Yield
[Total yield and number of servings]

### Ingredients
- [exact amount with unit] [ingredient name]
- ...

### Instructions
[Numbered step-by-step instructions]

### Nutrition Comparison
| Nutrient | Label (per serving) | Recipe Estimate (per serving) |
|----------|-------------------|-------------------------------|
| Calories | ... | ... |
| Total Fat | ... | ... |
| Carbohydrates | ... | ... |
| Sugar | ... | ... |
| Protein | ... | ... |

### Notes
[Any assumptions made, variations, or tips for getting closer to the commercial product]

---

## [Product Name] — Low-Carb / Keto Version

Now provide a **keto-friendly adaptation** of the recipe above. You MUST:

1. **Replace sugar** with an appropriate low-carb sweetener (monk fruit, allulose, erythritol, or blends). Use the correct conversion ratio — these sweeteners have different sweetness levels and bulk vs sugar.
2. **Address texture and consistency changes** — sugar isn't just sweetness, it affects freezing point, body, texture, and browning. Explain what changes to expect and how to compensate (e.g., adding more fat, using xanthan gum, adjusting liquid ratios, using MCT oil).
3. **Replace or reduce other high-carb ingredients** if present (flour, starch, honey, corn syrup, etc.)
4. **Keep the same format** as above (ingredients list with exact amounts, instructions, and a nutrition comparison showing the keto version's macros).
5. **Calculate net carbs** per serving for the keto version.

Use the keto substitution research below to inform your choices.

{keto_research}

---

## Ingredient Substitutions & Accessibility Guide

Finally, review every ingredient in the original recipe and identify any that might be **hard to find** in a typical grocery store (e.g., carob seed flour, guar gum, curcuma powder, specialty stabilizers, unusual extracts, etc.).

For each hard-to-find ingredient, provide:
1. **What it does** in the recipe (why it's there — texture, color, flavor, stability)
2. **Best substitute** — the most readily available alternative that serves the same function, with exact amount
3. **Runner-up substitute** — a second option
4. **Impact on the recipe** — what changes to expect (texture, flavor, appearance)

Format as a table:
| Original Ingredient | Function | Best Substitute | Runner-Up | Notes |
|---------------------|----------|-----------------|-----------|-------|
| ... | ... | ... | ... | ... |

Only list ingredients that are genuinely hard to find. Don't include common items like sugar, milk, lemon juice, etc."""


def encode_image(image_bytes: bytes, content_type: str) -> tuple[str, str]:
    """Encode image bytes to base64 and determine media type."""
    media_type_map = {
        "image/jpeg": "image/jpeg",
        "image/jpg": "image/jpeg",
        "image/png": "image/png",
        "image/gif": "image/gif",
        "image/webp": "image/webp",
        "image/heic": "image/jpeg",  # Will be converted
        "image/heif": "image/jpeg",  # Will be converted
    }

    ct = content_type.lower() if content_type else "image/jpeg"

    # Convert HEIC/HEIF or unknown formats to JPEG via Pillow
    if ct in ("image/heic", "image/heif") or ct not in media_type_map:
        img = Image.open(io.BytesIO(image_bytes))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=85)
        image_bytes = buf.getvalue()
        ct = "image/jpeg"

    media_type = media_type_map.get(ct, "image/jpeg")
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    return b64, media_type


def extract_from_images(image_data_list: list[tuple[str, str]]) -> str:
    """Use Claude Vision to extract nutrition/ingredient info from images."""
    content = []
    for b64, media_type in image_data_list:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64},
        })
    content.append({"type": "text", "text": EXTRACTION_PROMPT})

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": content}],
    )
    return response.content[0].text


def search_reference_recipes(product_info: str) -> str:
    """Use Claude with web search to find similar homemade recipes as reference."""
    search_prompt = (
        f"Based on this commercial product info, search the web for similar homemade recipes "
        f"that I can use as reference points. Find 2-3 recipes with exact measurements.\n\n"
        f"Product info:\n{product_info}\n\n"
        f"Return a summary of the recipes you found with their ingredient quantities."
    )

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=3000,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": search_prompt}],
    )

    # Extract all text blocks from the response (skipping tool_use/tool_result blocks)
    text_parts = []
    for block in response.content:
        if hasattr(block, "text"):
            text_parts.append(block.text)
    return "\n".join(text_parts) if text_parts else "No reference recipes found."


def research_keto_substitutions(product_info: str) -> str:
    """Use Claude with web search to research keto/low-carb substitutions."""
    search_prompt = (
        f"I'm making a keto/low-carb version of this product. Search the web for:\n"
        f"1. Best sugar substitutes for this type of recipe (monk fruit vs allulose vs erythritol) — "
        f"conversion ratios, how they affect texture/freezing/consistency\n"
        f"2. Any keto versions of similar recipes that exist online with exact measurements\n"
        f"3. Tips for maintaining texture when removing sugar (especially for frozen desserts, baked goods, etc.)\n\n"
        f"Product info:\n{product_info}\n\n"
        f"Return specific, actionable substitution advice with exact ratios and measurements."
    )

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=3000,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": search_prompt}],
    )

    text_parts = []
    for block in response.content:
        if hasattr(block, "text"):
            text_parts.append(block.text)
    return "\n".join(text_parts) if text_parts else "No keto substitution research found."


def decompile_recipe(product_info: str, reference_recipes: str = "", keto_research: str = "") -> str:
    """Use Claude to reverse-engineer a recipe from product info."""
    prompt = DECOMPILE_PROMPT.format(
        product_info=product_info,
        keto_research=keto_research or "No specific research available — use your knowledge of keto baking/cooking science.",
    )
    if reference_recipes:
        prompt += (
            f"\n\n---\n\n**Reference recipes found online** (use these as a starting point, "
            f"then adjust quantities to match the nutrition label):\n\n{reference_recipes}"
        )

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


@app.post("/decompile")
async def decompile_from_images(
    images: list[UploadFile] = File(..., description="Product label images"),
):
    """Upload one or more images of a product label to get a reverse-engineered recipe."""
    if not images:
        raise HTTPException(status_code=400, detail="At least one image is required")

    # Encode all images
    image_data_list = []
    for img in images:
        raw = await img.read()
        b64, media_type = encode_image(raw, img.content_type)
        image_data_list.append((b64, media_type))

    # Step 1: Extract product info from images
    product_info = extract_from_images(image_data_list)

    # Step 2: Scrape recipe websites for reference recipes with exact measurements
    scraped_recipes = find_reference_recipes(
        product_name=product_info.split("\n")[0][:100],
        ingredients=[line.strip() for line in product_info.split("\n") if line.strip()],
    )

    # Step 3: Also use Claude web search for additional references
    ai_references = search_reference_recipes(product_info)

    # Combine all references
    all_references = f"## Scraped Recipes (exact measurements)\n\n{scraped_recipes}\n\n## AI Web Search Results\n\n{ai_references}"

    # Step 4: Research keto/low-carb substitutions
    keto_research = research_keto_substitutions(product_info)

    # Step 5: Decompile into a recipe (original + keto variant)
    recipe = decompile_recipe(product_info, all_references, keto_research)

    return JSONResponse(content={
        "extracted_info": product_info,
        "scraped_recipes": scraped_recipes,
        "ai_references": ai_references,
        "keto_research": keto_research,
        "recipe": recipe,
    })


@app.post("/decompile/text")
async def decompile_from_text(
    ingredients: str = Form(..., description="Ingredient list from the product"),
    nutrition_facts: str = Form(..., description="Nutrition facts (copy/paste or describe)"),
    product_name: Optional[str] = Form(None, description="Product name"),
    serving_size: Optional[str] = Form(None, description="Serving size (e.g., '2/3 cup (90g)')"),
    servings_per_container: Optional[str] = Form(None, description="Number of servings"),
):
    """Submit product info as text to get a reverse-engineered recipe."""
    product_info = f"Product: {product_name or 'Unknown'}\n"
    if serving_size:
        product_info += f"Serving Size: {serving_size}\n"
    if servings_per_container:
        product_info += f"Servings per Container: {servings_per_container}\n"
    product_info += f"\nIngredients: {ingredients}\n"
    product_info += f"\nNutrition Facts:\n{nutrition_facts}"

    # Extract product name and ingredients for scraping
    ingredients_list = [i.strip() for i in ingredients.split(",")]
    scraped_recipes = find_reference_recipes(
        product_name=product_name or "unknown product",
        ingredients=ingredients_list,
    )
    ai_references = search_reference_recipes(product_info)
    all_references = f"## Scraped Recipes\n\n{scraped_recipes}\n\n## AI Web Search\n\n{ai_references}"

    keto_research = research_keto_substitutions(product_info)
    recipe = decompile_recipe(product_info, all_references, keto_research)

    return JSONResponse(content={
        "scraped_recipes": scraped_recipes,
        "ai_references": ai_references,
        "keto_research": keto_research,
        "recipe": recipe,
    })


@app.get("/health")
async def health():
    return {"status": "ok"}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
