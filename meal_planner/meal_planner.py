from flask import Flask, request, jsonify
from pydantic import BaseModel, ValidationError
from typing import List, Dict, Optional
import pandas as pd
import re
import ast
import copy
import os
import requests
from collections import defaultdict
import logging

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------ MODELS ------------------
class Physical(BaseModel):
    activity_level: Optional[str] = "moderate"  # Default to "moderate" if not provided

class Cultural(BaseModel):
    diet_type: Optional[List[str]] = None  # Default to None if not provided

class Profile(BaseModel):
    age: int  # Only required field
    gender: Optional[str] = None
    weight: Optional[float] = None
    height: Optional[float] = None
    month: Optional[int] = 1  # Default to January if not provided
    physical: Optional[Physical] = None  # Default to None if not provided
    cultural: Optional[Cultural] = None  # Default to None if not provided

# ------------------ CONSTANTS ------------------
CATEGORY_MAP = {
    "seasonal & local fruits/vegetables": "fruit_veg",
    "milk & dairy": "dairy",
    "meat/fish/eggs/pulses": "protein",
    "grains": "cereal",
    "oil": "oil",
    "misc": "misc"
}

MEAL_REQUIREMENT = {
    "fruit_veg": 2,
    "cereal": 1,
    "dairy": 0,
    "protein": 1,
    "oil": 1,
    "misc": 2
}

MISC_PRIORITY = ["Salt/Pepper", "Sugar", "Chocolates", "Millefiori honey 500 g"]

# ------------------ HELPER FUNCTIONS ------------------
def fetch_inventory_file():
    """Download inventory file from inventory service"""
    # USE ENVIRONMENT VARIABLE FOR SERVICE URL
    base_url = os.environ.get('INVENTORY_SERVICE_URL', 'http://localhost:5005')
    url = f"{base_url}/export-inventory"
    output_path = "data/excel_file.xlsx"
    logger.info("Downloading inventory file from Flask app...")
    
    try:
        response = requests.get(url, timeout=300)  # 5 minutes
        if response.status_code == 200:
            os.makedirs("data", exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(response.content)
            logger.info(f"Downloaded and saved to {output_path}")
            return True
        else:
            logger.error(f"Failed to download inventory: {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"Error fetching inventory: {str(e)}")
        return False

def load_food_reference():
    """Load food category reference data"""
    ref_path = "/app/data/DATA SET FOOD CATEGORY.xlsx"
    if not os.path.exists(ref_path):
        raise FileNotFoundError(f"Food reference file not found: {ref_path}")
    
    df = pd.read_excel(ref_path)
    return {
        str(row["item_name"]).strip().lower(): str(row["item_category"]).strip().lower()
        for _, row in df.iterrows()
    }

def load_inventory_items(ref_map: Dict[str, str]):
    """Load main inventory items"""
    inv_path = "data/excel_file.xlsx"
    if not os.path.exists(inv_path):
        raise FileNotFoundError(f"Inventory file not found: {inv_path}")
    
    df = pd.read_excel(inv_path, sheet_name="Inventory")
    df["quantity_in_stock"] = pd.to_numeric(df["quantity_in_stock"], errors="coerce").fillna(0)
    
    inventory = []
    for _, row in df.iterrows():
        name = str(row["item_name"]).strip()
        key = name.lower()
        if key in ref_map:
            category = CATEGORY_MAP.get(ref_map[key])
            if category:
                inventory.append({
                    "item_name": name,
                    "category": category,
                    "servings_available": row["quantity_in_stock"]
                })
    return inventory

def allocate_specific_item(inventory: List[Dict], item_name: str, 
                          category: str, needed: int) -> tuple:
    """Allocate a specific item from inventory"""
    used = []
    for item in inventory:
        if needed <= 0:
            break
        if (item["category"] == category and 
            item["item_name"].strip().lower() == item_name.strip().lower() and
            item["servings_available"] > 0):
            
            take = min(item["servings_available"], needed)
            used.append({
                "item_name": item["item_name"],
                "category": category,
                "servings_used": take,
                "source": "main"
            })
            item["servings_available"] -= take
            needed -= take
    return used, needed

def allocate_category(inventory: List[Dict], category: str, needed: int) -> tuple:
    """Allocate items from a category"""
    used = []
    if category != "misc":
        for item in inventory:
            if needed <= 0:
                break
            if item["category"] == category and item["servings_available"] > 0:
                take = min(item["servings_available"], needed)
                used.append({
                    "item_name": item["item_name"],
                    "category": category,
                    "servings_used": take,
                    "source": "main"
                })
                item["servings_available"] -= take
                needed -= take
    else:
        for misc_item in MISC_PRIORITY:
            u, needed = allocate_specific_item(inventory, misc_item, category, needed)
            used.extend(u)
            if needed <= 0:
                break
    return used, needed

# ------------------ FILTER FUNCTIONS ------------------
def filter_recipes(recipes: List[Dict], physical: Optional[Physical], cultural: Optional[Cultural]) -> List[Dict]:
    """Apply cultural and physical filters to recipes"""
    filtered = []
    
    for recipe in recipes:
        # Apply cultural filters (only if cultural data exists)
        cultural_match = True
        if cultural and cultural.diet_type:
            recipe_cats = str(recipe.get("Categories", "")).lower()
            cultural_match = any(
                diet.lower() in recipe_cats 
                for diet in cultural.diet_type
            )
            if not cultural_match:
                continue
        
        # Apply physical filter (calorie-based, only if physical data exists)
        if physical and physical.activity_level:
            nutrition = recipe.get("Nutrition")
            calories = 0
            
            # Parse nutrition data
            if isinstance(nutrition, str):
                try:
                    nutrition_dict = ast.literal_eval(nutrition)
                    calories = nutrition_dict.get("calories", 0)
                except:
                    calories = 0
            elif isinstance(nutrition, dict):
                calories = nutrition.get("calories", 0)
            
            # Filter by activity level
            if physical.activity_level == "low" and calories > 350:
                continue
            elif physical.activity_level == "moderate" and not (350 < calories <= 500):
                continue
            elif physical.activity_level == "high" and calories <= 500:
                continue
        
        filtered.append(recipe)
    
    return filtered

# ------------------ RECIPE LOADERS ------------------
def get_recipe_file(age: int) -> str:
    """Determine recipe file based on age group"""
    if age >= 60:
        return "/app/data/data_set/senior.xlsx"
    elif age >= 18:
        return "/app/data/data_set/adult.xlsx"
    elif age >= 10:
        return "/app/data/data_set/teen.xlsx"
    else:
        return "/app/data/data_set/kid.xlsx"

def load_recipes(age: int) -> List[Dict]:
    """Load recipes based on age group"""
    recipe_file = get_recipe_file(age)
    if not os.path.exists(recipe_file):
        raise FileNotFoundError(f"Recipe file not found: {recipe_file}")
    
    df = pd.read_excel(recipe_file)
    return df.to_dict(orient="records")

# ------------------ SENIOR SPECIFIC FUNCTIONS ------------------
def get_senior_box_cycle(month: Optional[int] = None) -> int:
    """Determine senior box cycle based on month"""
    if month is None:
        return 1  # Default to first month cycle
    return ((month - 1) % 3) + 1  # 1 to 3

def load_senior_box_items(cycle: int, ref_map: Dict[str, str]) -> List[Dict]:
    """Load senior box items for the given cycle month"""
    box_path = "/app/data/senior_box.xlsx"
    if not os.path.exists(box_path):
        raise FileNotFoundError(f"Senior box file not found: {box_path}")
    
    sheet_map = {
        1: "Senior Box First Month",
        2: "Senior Box Second Month",
        3: "Senior Box Third Month"
    }
    sheet_name = sheet_map.get(cycle, "Senior Box First Month")
    
    df = pd.read_excel(box_path, sheet_name=sheet_name)
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0)
    
    box_items = []
    for _, row in df.iterrows():
        name = str(row["item_name"]).strip()
        key = name.lower()
        if key in ref_map:
            category = CATEGORY_MAP.get(ref_map[key])
            if category:
                box_items.append({
                    "item_name": name,
                    "category": category,
                    "servings_available": row["quantity"]
                })
    return box_items

def allocate_senior_item(box: List[Dict], main: List[Dict], 
                        item_name: str, category: str, needed: int) -> tuple:
    """Allocate item from senior box or main inventory"""
    used = []
    
    # First try senior box
    u, needed = allocate_specific_item(box, item_name, category, needed)
    used.extend(u)
    
    # Then try main inventory if still needed
    if needed > 0:
        u, needed = allocate_specific_item(main, item_name, category, needed)
        used.extend(u)
    
    return used, needed

def allocate_senior_category(box: List[Dict], main: List[Dict], 
                           category: str, needed: int) -> tuple:
    """Allocate category items for seniors"""
    used = []
    
    # First try senior box
    for item in box:
        if needed <= 0:
            break
        if item["category"] == category and item["servings_available"] > 0:
            take = min(item["servings_available"], needed)
            used.append({
                "item_name": item["item_name"],
                "category": category,
                "servings_used": take,
                "source": "box"
            })
            item["servings_available"] -= take
            needed -= take
    
    # Then try main inventory if still needed
    if needed > 0:
        u, needed = allocate_category(main, category, needed)
        for item in u:
            item["source"] = "main"
        used.extend(u)
    
    return used, needed

def allocate_senior_misc(box: List[Dict], main: List[Dict], needed: int) -> tuple:
    """Special allocation for misc category with priority"""
    used = []
    for item_name in MISC_PRIORITY:
        if needed <= 0:
            break
        u, needed = allocate_senior_item(box, main, item_name, "misc", needed)
        used.extend(u)
    return used, needed

# ------------------ MEAL PLAN GENERATORS ------------------
def generate_daily_plan(recipe: Dict, inventory: List[Dict], ref_map: Dict[str, str]) -> Dict:
    """Generate daily meal plan for non-seniors"""
    inventory_copy = copy.deepcopy(inventory)
    raw_ingredients = [i.strip() for i in str(recipe.get("Ingredients", "")).split(",") if i.strip()]
    
    used_items = []
    category_counts = defaultdict(int)
    foodbank_items = []
    user_items = []
    
    # Allocate recipe ingredients
    for ing in raw_ingredients:
        base_name = re.sub(r"\s+\d+.*$", "", ing, flags=re.IGNORECASE).strip().lower()
        category = CATEGORY_MAP.get(ref_map.get(base_name, ""))
        
        if not category:
            user_items.append(ing)
            continue
        
        allocated, remaining = allocate_specific_item(inventory_copy, ing, category, 1)
        used_items.extend(allocated)
        
        if remaining > 0:
            user_items.append(ing)
        else:
            foodbank_items.append(ing)
        
        category_counts[category] += 1 - remaining
    
    # Allocate extra items to meet meal requirements
    extra_needed = {}
    for cat, required in MEAL_REQUIREMENT.items():
        shortfall = max(0, required - category_counts.get(cat, 0))
        if shortfall > 0:
            extra, _ = allocate_category(inventory_copy, cat, shortfall)
            used_items.extend(extra)
            category_counts[cat] += sum(e["servings_used"] for e in extra)
            remaining_shortfall = required - category_counts[cat]
            if remaining_shortfall > 0:
                extra_needed[cat] = remaining_shortfall
    
    # Parse nutrition data
    nutrition = recipe.get("Nutrition")
    if isinstance(nutrition, str):
        try:
            nutrition = ast.literal_eval(nutrition)
        except:
            nutrition = {"calories": 0, "carbs_g": 0, "protein_g": 0, "sugar_g": 0}
    elif not isinstance(nutrition, dict):
        nutrition = {"calories": 0, "carbs_g": 0, "protein_g": 0, "sugar_g": 0}
    
    return {
        "title": recipe.get("Title", ""),
        "description": recipe.get("Description", ""),
        "ingredients": raw_ingredients,
        "foodbank_items": foodbank_items,
        "user_items": user_items,
        "instructions": recipe.get("Preparation Instructions", ""),
        "duration": recipe.get("Duration", ""),
        "meal_coverage": recipe.get("Meal Coverage", "Lunch"),
        "additional_recommendations": recipe.get("Additional Recommendations", ""),
        "warnings": recipe.get("Warnings", ""),
        "categories": recipe.get("Categories", ""),
        "nutrition": nutrition,
        "used_items": used_items,
        "extra_needed": extra_needed
    }

def generate_senior_daily_plan(recipe: Dict, box: List[Dict], 
                              main: List[Dict], ref_map: Dict[str, str]) -> Dict:
    """Generate daily meal plan for seniors"""
    box_copy = copy.deepcopy(box)
    main_copy = copy.deepcopy(main)
    raw_ingredients = [i.strip() for i in str(recipe.get("Ingredients", "")).split(",") if i.strip()]
    
    used_items = []
    category_counts = defaultdict(int)
    foodbank_items = []
    user_items = []
    
    # Allocate recipe ingredients
    for ing in raw_ingredients:
        base_name = re.sub(r"\s+\d+.*$", "", ing, flags=re.IGNORECASE).strip().lower()
        category = CATEGORY_MAP.get(ref_map.get(base_name, ""))
        
        if not category:
            user_items.append(ing)
            continue
        
        allocated, remaining = allocate_senior_item(box_copy, main_copy, ing, category, 1)
        used_items.extend(allocated)
        
        if remaining > 0:
            user_items.append(ing)
        else:
            foodbank_items.append(ing)
        
        category_counts[category] += 1 - remaining
    
    # Allocate extra items to meet meal requirements
    extra_needed = {}
    for cat, required in MEAL_REQUIREMENT.items():
        shortfall = max(0, required - category_counts.get(cat, 0))
        if shortfall > 0:
            if cat == "misc":
                allocated, _ = allocate_senior_misc(box_copy, main_copy, shortfall)
            else:
                allocated, _ = allocate_senior_category(box_copy, main_copy, cat, shortfall)
            used_items.extend(allocated)
            category_counts[cat] += sum(e["servings_used"] for e in allocated)
            remaining_shortfall = required - category_counts[cat]
            if remaining_shortfall > 0:
                extra_needed[cat] = remaining_shortfall
    
    # Parse nutrition data
    nutrition = recipe.get("Nutrition")
    if isinstance(nutrition, str):
        try:
            nutrition = ast.literal_eval(nutrition)
        except:
            nutrition = {"calories": 0, "carbs_g": 0, "protein_g": 0, "sugar_g": 0}
    elif not isinstance(nutrition, dict):
        nutrition = {"calories": 0, "carbs_g": 0, "protein_g": 0, "sugar_g": 0}
    
    return {
        "title": recipe.get("Title", ""),
        "description": recipe.get("Description", ""),
        "ingredients": raw_ingredients,
        "foodbank_items": foodbank_items,
        "user_items": user_items,
        "instructions": recipe.get("Preparation Instructions", ""),
        "duration": recipe.get("Duration", ""),
        "meal_coverage": recipe.get("Meal Coverage", "Lunch"),
        "additional_recommendations": recipe.get("Additional Recommendations", ""),
        "warnings": recipe.get("Warnings", ""),
        "categories": recipe.get("Categories", ""),
        "nutrition": nutrition,
        "used_items": used_items,
        "extra_needed": extra_needed
    }

# ------------------ MAIN ENDPOINT ------------------
@app.route('/generate_monthly_plan', methods=['POST'])
def generate_monthly_plan():
    try:
        # Validate request data
        data = request.get_json()
        if not data:
            return jsonify({"error": "No input data provided"}), 400
        
        try:
            profile = Profile(**data)
        except ValidationError as e:
            return jsonify({"error": str(e)}), 400

        # Fetch inventory and load reference data
        if not fetch_inventory_file():
            return jsonify({"error": "Failed to fetch inventory"}), 500
        
        ref_map = load_food_reference()
        main_inventory = load_inventory_items(ref_map)
        recipes = load_recipes(profile.age)
        
        # Apply cultural and physical filters (handle None cases)
        filtered_recipes = filter_recipes(
            recipes,
            profile.physical if profile.physical else None,
            profile.cultural if profile.cultural else None
        )
        
        if not filtered_recipes:
            return jsonify({
                "error": "No recipes match the provided filters"
            }), 404
        
        # Prepare senior-specific data if needed
        senior_box = []
        cycle_month = None
        
        if profile.age >= 60:
            cycle_month = get_senior_box_cycle(profile.month if profile.month else None)
            senior_box = load_senior_box_items(cycle_month, ref_map)
        
        # Generate 30-day plan
        daily_plans = []
        for day in range(1, 31):
            recipe = filtered_recipes[(day - 1) % len(filtered_recipes)]
            
            if profile.age >= 60:
                plan = generate_senior_daily_plan(
                    recipe, senior_box, main_inventory, ref_map
                )
            else:
                plan = generate_daily_plan(recipe, main_inventory, ref_map)
            
            daily_plans.append({
                "day": day,
                **plan
            })
        
        return jsonify({
            "month": profile.month if profile.month else 1,
            "age_group": "senior" if profile.age >= 60 else "adult" if profile.age >= 18 else "teen" if profile.age >= 10 else "kid",
            "senior_box_cycle": cycle_month if profile.age >= 60 else None,
            "daily_plans": daily_plans
        })
        
    except FileNotFoundError as e:
        logger.error(f"File not found: {str(e)}")
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        logger.error(f"Error generating meal plan: {str(e)}")
        return jsonify({"error": str(e)}), 500

# Add root endpoint to prevent 404 errors
@app.route('/', methods=['GET'])
def root():
    return jsonify({
        "message": "Meal Planner API",
        "endpoints": {
            "generate_meal_plan": "POST /generate_monthly_plan"
        }
    })

# Optional: Add favicon endpoint to prevent 404s
@app.route('/favicon.ico', methods=['GET'])
def favicon():
    return '', 204

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7778)
