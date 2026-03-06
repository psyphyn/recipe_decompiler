"""BrowserAct integration for scraping nutrition data and recipe references."""

import os
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

BROWSERACT_API_URL = "https://api.browseract.com/v2"
BROWSERACT_API_KEY = os.getenv("BROWSERACT_API_KEY", "")


def _headers() -> dict:
    return {"Authorization": f"Bearer {BROWSERACT_API_KEY}"}


def run_task(workflow_id: str, input_parameters: dict | None = None) -> dict:
    """Run a BrowserAct workflow and wait for completion."""
    with httpx.Client(timeout=120) as http:
        # Start the task
        payload = {"workflow_id": workflow_id}
        if input_parameters:
            payload["input_parameters"] = input_parameters

        resp = http.post(
            f"{BROWSERACT_API_URL}/workflow/run-task",
            headers=_headers(),
            json=payload,
        )
        resp.raise_for_status()
        task = resp.json()
        task_id = task["id"]

        # Poll for completion
        for _ in range(60):
            status_resp = http.get(
                f"{BROWSERACT_API_URL}/workflow/get-task",
                headers=_headers(),
                params={"taskId": task_id},
            )
            status_resp.raise_for_status()
            task_data = status_resp.json()

            if task_data.get("status") in ("finished", "failed", "canceled"):
                return task_data

            time.sleep(3)

        return {"status": "timeout", "id": task_id}


def run_template(template_id: str, input_parameters: dict | None = None) -> dict:
    """Run a BrowserAct official template and wait for completion."""
    with httpx.Client(timeout=120) as http:
        payload = {"workflow_template_id": template_id}
        if input_parameters:
            payload["input_parameters"] = input_parameters

        resp = http.post(
            f"{BROWSERACT_API_URL}/workflow/run-task-by-template",
            headers=_headers(),
            json=payload,
        )
        resp.raise_for_status()
        task = resp.json()
        task_id = task["id"]

        for _ in range(60):
            status_resp = http.get(
                f"{BROWSERACT_API_URL}/workflow/get-task",
                headers=_headers(),
                params={"taskId": task_id},
            )
            status_resp.raise_for_status()
            task_data = status_resp.json()

            if task_data.get("status") in ("finished", "failed", "canceled"):
                return task_data

            time.sleep(3)

        return {"status": "timeout", "id": task_id}


def list_templates(query: str = "") -> list[dict]:
    """List available BrowserAct official workflow templates."""
    with httpx.Client(timeout=30) as http:
        params = {}
        if query:
            params["query"] = query
        resp = http.get(
            f"{BROWSERACT_API_URL}/workflow/list-official-workflow-templates",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


def list_workflows() -> list[dict]:
    """List user's custom workflows."""
    with httpx.Client(timeout=30) as http:
        resp = http.get(
            f"{BROWSERACT_API_URL}/workflow/list-workflows",
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()


def scrape_usda_nutrition(ingredient: str) -> dict:
    """Scrape USDA FoodData Central for an ingredient's nutrition profile.

    Uses BrowserAct to search and extract nutrition data from the USDA database.
    Falls back gracefully if BrowserAct is unavailable.
    """
    if not BROWSERACT_API_KEY:
        return {"error": "BROWSERACT_API_KEY not set", "ingredient": ingredient}

    try:
        # Use a generic web scraping approach via BrowserAct
        # This assumes a workflow is set up, or we use the API to navigate
        result = run_template(
            template_id="web-scraper",  # Will need to find actual template ID
            input_parameters={
                "url": f"https://fdc.nal.usda.gov/search?query={ingredient}",
                "extract": "nutrition facts table, serving size, calories, macronutrients",
            },
        )
        return {"ingredient": ingredient, "data": result}
    except Exception as e:
        return {"ingredient": ingredient, "error": str(e)}
