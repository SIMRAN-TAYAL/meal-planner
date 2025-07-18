import os
import json
import io
import logging
import requests
import pandas as pd
import time
import sys
from flask import Flask, Response
from googletrans import Translator
from openpyxl.utils import get_column_letter
from dotenv import load_dotenv

# Load .env values
load_dotenv()

# Flask setup
app = Flask(__name__)
app.debug = True  

@app.route("/")
def index():
    return "Inventory Export App is running!"

translator = Translator()
logging.basicConfig(level=logging.INFO)

sys.stdout = sys.stderr

 # API config
LOGIN_URL = "http://91.138.181.46:7024/exesjson/elogin"
DATA_URL = "http://91.138.181.46:7024/exesjson/getdata"
CREDENTIALS = {
    "apicode": os.getenv("API_CODE"),
    "applicationname": os.getenv("APP_NAME"),
    "databasealias": os.getenv("DB_ALIAS"),
    "username": os.getenv("API_USERNAME"),
    "password": os.getenv("API_PASSWORD")
}
BASE_REQUEST = {
    "apicode": os.getenv("API_CODE"),
    "entitycode": "Items",
    "packagesize": 1000
}

def get_api_cookie():
    try:
        print("\n" + "="*80)
        print("DEBUG: ATTEMPTING TO GET API COOKIE")
        print("="*80)
        response = requests.post(LOGIN_URL, json=CREDENTIALS, timeout=10)
        response.raise_for_status()
        login_data = response.json()
        print("Login response received")

        if login_data['Status'] != 'OK':
            raise Exception(f"Login failed: {login_data.get('Error')}")
        
        result = json.loads(login_data['Result'])
        print("SUCCESSFULLY OBTAINED API COOKIE")
        print(f"Cookie: {result['cookie']}")
        return result['cookie']
    except Exception as e:
        logging.exception("Cookie retrieval error")
        print(f"\n!!! ERROR GETTING COOKIE: {str(e)}")
        raise

def get_inventory_data(cookie):
    all_items = []
    page = 1

    print("\n" + "="*80)
    print("DEBUG: STARTING INVENTORY DATA RETRIEVAL")
    print("="*80)
    
    while True:
        try:
            print(f"\nFetching page {page}...")
            request_data = {**BASE_REQUEST, "cookie": cookie, "packagenumber": page}
            response = requests.post(DATA_URL, json=request_data, timeout=15)
            response.raise_for_status()
            data_response = response.json()

            if data_response['Status'] != 'OK':
                raise Exception(f"Data fetch failed: {data_response.get('Error')}")
            
            result = json.loads(data_response['Result'])
            items = result['Data']['Items']
            print(f"Retrieved {len(items)} items on page {page}")

            if not items:
                print("\nNO MORE ITEMS - ENDING PAGINATION")
                break

            all_items.extend(items)
            page += 1
        except Exception as e:
            logging.exception("Data retrieval error")
            print(f"\n!!! ERROR RETRIEVING DATA: {str(e)}")
            raise

    print(f"\nTOTAL ITEMS RETRIEVED: {len(all_items)}")
    if all_items:
        print("\nSAMPLE ITEM:")
        print(json.dumps(all_items[0], indent=2))
    return all_items

def translate_column(series):
    """Translate Greek text to English with robust error handling"""
    print("\n" + "="*80)
    print("DEBUG: STARTING TRANSLATION")
    print("="*80)
    print(f"Translating column: {series.name}")
    print(f"Total rows: {len(series)}")
    print(f"\nFirst 5 values before translation:")
    print(series.head().to_string())
    
    mask = series.apply(lambda x: isinstance(x, str) and (x != "") and (not pd.isna(x)))
    to_translate = series[mask]
    
    print("\n" + "-"*80)
    print(f"Found {len(to_translate)} non-empty strings to translate")
    if len(to_translate) > 0:
        print("Sample values to translate:")
        print(to_translate.head().to_string())
    else:
        print("NO VALUES TO TRANSLATE!")
    
    if to_translate.empty:
        print("\nNo strings to translate - returning original series")
        return series


    unique_values = to_translate.unique()
    print("\n" + "-"*80)
    print(f"Found {len(unique_values)} unique values to translate")
    print("First 5 unique values:")
    for i, val in enumerate(unique_values[:5], 1):
        print(f"{i}. '{val}'")

    translation_map = {}
    print("\nStarting translations...")
    
    for i, text in enumerate(unique_values, 1):
        try:
            time.sleep(0.1)  
            print(f"\n[{i}/{len(unique_values)}] Translating: '{text}'")
            
            # Handle empty strings
            if not text.strip():
                print("Skipping empty string")
                translation_map[text] = text
                continue
                
            # Perform translation
            translated = translator.translate(text, src='el', dest='en').text
            translation_map[text] = translated
            print(f"SUCCESS! Translation: '{translated}'")
        except Exception as e:
            print(f"!!! TRANSLATION FAILED FOR '{text}'. ERROR: {str(e)}")
            translation_map[text] = text  # Keep original on failure
            logging.error(f"Translation failed for '{text}': {str(e)}")

    # Create new series with translated values
    translated_series = series.copy()
    translated_series[mask] = to_translate.map(translation_map)
    
    print("\n" + "="*80)
    print("TRANSLATION RESULTS SUMMARY")
    print("="*80)
    print("First 5 translation pairs:")
    for orig, trans in list(translation_map.items())[:5]:
        print(f"'{orig}' → '{trans}'")
    
    print("\nFirst 5 translated values in series:")
    print(translated_series.head().to_string())
    
    return translated_series

@app.route('/export-inventory', methods=['GET'])
def export_inventory():
    try:
        print("\n" + "="*80)
        print("DEBUG: STARTING EXPORT PROCESS")
        print("="*80)
        
        cookie = get_api_cookie()
        inventory_data = get_inventory_data(cookie)
        df = pd.DataFrame(inventory_data)

        print("\n" + "="*80)
        print("DEBUG: RAW DATA")
        print("="*80)
        print(f"Total items: {len(df)}")
        print("Columns:", df.columns.tolist())
        print("\nFirst 3 rows:")
        print(df.head(3).to_string())

        column_mapping = {
            'ITEMID': 'id',
            'ITEMCODE': 'ITEMCODE',
            'ITEMNAME': 'item_name-greek',
            'DETAILEDDESCR': 'DETAILEDDESCR',
            'MSNTCODE': 'MSNTCODE',
            'MSNTNAME': 'MSNTNAME',
            'ABBREVIATION': 'ABBREVIATION',
            'ABALANCE': 'quantity_in_stock'
        }
        df = df.rename(columns=column_mapping)
        
        print("\n" + "="*80)
        print("DEBUG: AFTER RENAMING COLUMNS")
        print("="*80)
        print("New columns:", df.columns.tolist())
        print("\nFirst 3 rows after rename:")
        print(df.head(3).to_string())
        
        # Translate item names
        print("\n" + "="*80)
        print("DEBUG: STARTING TRANSLATION PROCESS")
        print("="*80)
        df['item_name'] = translate_column(df['item_name-greek'])

        print("\n" + "="*80)
        print("DEBUG: AFTER TRANSLATION")
        print("="*80)
        print("Columns after translation:", df.columns.tolist())
        print("\nFirst 3 rows with translations:")
        print(df[['item_name-greek', 'item_name']].head(3).to_string())

        column_order = [
            'id', 'ITEMCODE', 'item_name', 'item_name-greek',
            'DETAILEDDESCR',
            'MSNTCODE',
            'MSNTNAME',
            'ABBREVIATION',
            'quantity_in_stock'
        ]
        df = df[column_order]

        print("\n" + "="*80)
        print("DEBUG: FINAL DATAFRAME")
        print("="*80)
        print("Columns in final export:", df.columns.tolist())
        print("\nFirst 3 rows of final data:")
        print(df.head(3).to_string())

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Inventory')
            worksheet = writer.sheets['Inventory']

            for idx, col in enumerate(df.columns):
                max_len = max(df[col].astype(str).map(len).max(), len(col)) + 2
                worksheet.column_dimensions[get_column_letter(idx + 1)].width = min(max_len, 50)

        output.seek(0)
        print("\n" + "="*80)
        print("DEBUG: EXCEL FILE GENERATED SUCCESSFULLY")
        print("="*80)
        return Response(
            output.getvalue(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment;filename=inventory_report.xlsx"}
        )

    except Exception as e:
        logging.exception("Export failed")
        print("\n" + "!"*80)
        print(f"!!! EXPORT ERROR: {str(e)}")
        print("!"*80)
        return Response(f"Internal server error: {str(e)}", status=500)

@app.route('/test-translation', methods=['GET'])
def test_translation():
    """Test endpoint for translation debugging"""
    test_phrases = [
        "Διάφορα Τρόφιμα",
        "Collect - Super Market/ Σχολεία / Εταιρείες",
        "Καφές",
        "Σνακς",
        "Νερά"
    ]
    
    results = []
    for phrase in test_phrases:
        try:
            print(f"\nTranslating test phrase: '{phrase}'")
            translation = translator.translate(phrase, src='el', dest='en').text
            results.append({
                "original": phrase,
                "translation": translation,
                "status": "success"
            })
            print(f"Success: '{phrase}' → '{translation}'")
        except Exception as e:
            results.append({
                "original": phrase,
                "error": str(e),
                "status": "failed"
            })
            print(f"Failed: '{phrase}' - {str(e)}")
    
    return Response(
        json.dumps(results, indent=2, ensure_ascii=False),
        mimetype="application/json"
    )

if __name__ == '__main__':
    print("\n" + "="*80)
    print("STARTING FLASK APPLICATION WITH DEBUG MODE")
    print("="*80)
    app.run(host='0.0.0.0', port=5005)