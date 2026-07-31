import os
import json
import requests
import threading
import traceback
from datetime import datetime
from flask import Flask, jsonify
from playwright.sync_api import sync_playwright

app = Flask(__name__)

WEB_APP_URL = "https://script.google.com/macros/s/AKfycbzgsd39NlvLO8JSOpAnh-r52vTPDJPdnSlh0_3acioLnSt15qg1Squby-rPUwPZwwu-/exec"

def date_to_epoch_ms(date_str, time_str):
    """
    Converts Google Apps Script date/time strings ('12-Sep-2026', '08:00')
    into JavaScript Epoch Milliseconds required by Zoomcar URLs.
    """
    try:
        dt_obj = datetime.strptime(f"{date_str} {time_str}", "%d-%b-%Y %H:%M")
        return int(dt_obj.timestamp() * 1000)
    except Exception as e:
        print(f"[ERROR] Failed to convert date '{date_str} {time_str}': {e}")
        traceback.print_exc()
        raise e

def parse_exact_zoomcar_json(json_data, target_car):
    """
    Strictly parses Zoomcar JSON response using verified DevTools schema:
    - car_info.name -> Vehicle Name
    - car_info.location_data.distance -> Distance
    - amount.trip_fare_after_discounts -> Exact Rental Price
    """
    extracted_vehicles = []
    
    def find_car_objects(data):
        if isinstance(data, dict):
            if "car_info" in data or "amount" in data:
                yield data
            for value in data.values():
                yield from find_car_objects(value)
        elif isinstance(data, list):
            for item in data:
                yield from find_car_objects(item)

    for item in find_car_objects(json_data):
        try:
            car_info = item.get("car_info", {})
            amount = item.get("amount", {})

            car_name = str(car_info.get("name", "")).strip().lower()
            if not car_name:
                continue

            if target_car not in car_name:
                continue

            location_data = car_info.get("location_data", {})
            distance = float(location_data.get("distance", 999.0))

            trip_fare = amount.get("trip_fare_after_discounts")
            if trip_fare is not None:
                rental_price = int(float(trip_fare))
                if rental_price > 0:
                    extracted_vehicles.append({
                        "model": car_info.get("name", target_car.title()),
                        "distance": distance,
                        "price": rental_price
                    })
        except Exception:
            traceback.print_exc()

    return extracted_vehicles

def run_scraper_attempt(pickup_date, pickup_time, drop_date, drop_time, target_car):
    """
    Executes Playwright session, intercepts API responses, and waits properly 
    for Zoomcar background responses to complete.
    """
    print("========== Playwright STARTED ==========")
    
    start_ms = date_to_epoch_ms(pickup_date, pickup_time)
    end_ms = date_to_epoch_ms(drop_date, drop_time)

    zoom_url = f"https://www.zoomcar.com/in/mumbai/search?lat=19.1080103&lng=72.9232129&type=round_trip&starts={start_ms}&ends={end_ms}&product_type=NORMAL"
    print(f"[INFO] Opening Search URL: {zoom_url}")

    intercepted_vehicles = []
    
    def handle_response(response):
        """Intercepts background JSON APIs and parses using exact schema."""
        try:
            content_type = response.headers.get("content-type", "")
            if "json" in content_type and response.status == 200:
                url = response.url.lower()
                if any(kw in url for kw in ["search", "cars", "sections", "vehicles", "details", "checkout"]):
                    print(f"[INFO] Intercepted API Endpoint: {response.url}")
                    json_data = response.json()
                    
                    found_cars = parse_exact_zoomcar_json(json_data, target_car)
                    if found_cars:
                        intercepted_vehicles.extend(found_cars)
        except Exception:
            traceback.print_exc()

    browser = None
    context = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
            )
            context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            page = context.new_page()

            page.on("response", handle_response)

            # Block images and fonts to boost performance
            page.route("**/*.{png,jpg,jpeg,svg,webp,gif,woff,woff2}", lambda route: route.abort())

            page.goto(zoom_url, wait_until="domcontentloaded", timeout=40000)

            # Explicit wait of 12 seconds to capture slow background API requests
            page.wait_for_timeout(12000)

            context.close()
            browser.close()

    except Exception:
        print("[ERROR] Playwright session exception caught:")
        traceback.print_exc()
        if context:
            try: context.close()
            except: pass
        if browser:
            try: browser.close()
            except: pass

    return intercepted_vehicles

def fetch_and_update():
    """
    Main orchestrator: Reads Apps Script request, fetches prices via exact JSON schema,
    picks nearest car, and posts price back to Google Sheet.
    """
    print("\n========== fetch_and_update STARTED ==========")
try:
        res = requests.get(WEB_APP_URL, timeout=15)
        try:
            data = res.json()
        except Exception:
            print(f"[WARN] Non-JSON response received: {res.text[:200]}")
            data = {}
        print(f"[INFO] Parameters from Google Sheet: {data}")
    except Exception as e:
        print(f"[ERROR] Failed to fetch parameters: {e}")
        data = {}

    pickup_date = data.get('pickupDate') if isinstance(data, dict) and data.get('pickupDate') else '12-Sep-2026'
    pickup_time = data.get('pickupTime') if isinstance(data, dict) and data.get('pickupTime') else '08:00'
    drop_date = data.get('dropDate') if isinstance(data, dict) and data.get('dropDate') else '13-Sep-2026'
    drop_time = data.get('dropTime') if isinstance(data, dict) and data.get('dropTime') else '08:00'
    
    raw_car_name = (data.get('carName') if isinstance(data, dict) and data.get('carName') else 'Punch').strip().lower()
    
    if "nexon" in raw_car_name:
        target_car = "nexon"
    elif "punch" in raw_car_name:
        target_car = "punch"
    else:
        print(f"[WARN] Car '{raw_car_name}' is not Nexon or Punch. Defaulting to Punch.")
        target_car = "punch"

    found_vehicles = []
    
    # Attempt 1
    try:
        print(f"[INFO] Attempt 1 for target model: '{target_car.upper()}'...")
        found_vehicles = run_scraper_attempt(pickup_date, pickup_time, drop_date, drop_time, target_car)
    except Exception:
        print("[ERROR] Attempt 1 failed:")
        traceback.print_exc()

    # Retry Once if empty
    if not found_vehicles:
        print("[WARN] Retrying... Attempt 2...")
        try:
            found_vehicles = run_scraper_attempt(pickup_date, pickup_time, drop_date, drop_time, target_car)
        except Exception:
            print("[ERROR] Attempt 2 failed:")
            traceback.print_exc()

    print(f"[INFO] Total '{target_car.upper()}' cars matched: {len(found_vehicles)}")

    selected_price = 0
    if found_vehicles:
        found_vehicles.sort(key=lambda x: x["distance"])
        best_match = found_vehicles[0]
        selected_price = best_match["price"]
        
        print("\n--- [EXACT MATCH SELECTION] ---")
        print(f"Selected Model   : {best_match['model']}")
        print(f"Selected Distance: {best_match['distance']} km")
        print(f"Trip Fare Rate   : ₹{selected_price} (From amount.trip_fare_after_discounts)")
        print("--------------------------------\n")
    else:
        print(f"[ERROR] No '{target_car.upper()}' cars found in API payloads.")

    post_data = {
        "zoomcar_rate": selected_price,
        "revv_rate": "Not Found",
        "max_rate": selected_price
    }
    try:
        response = requests.post(WEB_APP_URL, json=post_data, timeout=10)
        print(f"[SUCCESS] Updated Google Sheet via Webhook. Status code: {response.status_code}")
    except Exception:
        print("[ERROR] Failed to post result back to Google Sheet:")
        traceback.print_exc()

@app.route('/', methods=['GET'])
def health_check():
    """Health check endpoint for Render Port Scanner"""
    return jsonify({"status": "healthy", "service": "zoomcar-scraper"}), 200

@app.route('/trigger-check', methods=['GET', 'POST'])
def trigger():
    threading.Thread(target=fetch_and_update).start()
    return jsonify({"status": "started", "message": "Scraper triggered successfully"}), 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
