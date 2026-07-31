import os
import json
import requests
import threading
from datetime import datetime
from flask import Flask, jsonify
from playwright.sync_api import sync_playwright

app = Flask(__name__)

WEB_APP_URL = "https://script.google.com/macros/s/AKfycbx9lWNCjUPqCpQwStvioWbfmqb6os25E8iRlXKFfWSrJXJyQGwXaWmQ9UREjUFgKN8D/exec"

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
        raise e

def parse_exact_zoomcar_json(json_data, target_car):
    """
    Strictly parses Zoomcar JSON response using verified DevTools schema:
    - car_info.name -> Vehicle Name
    - car_info.location_data.distance -> Distance
    - amount.trip_fare_after_discounts -> Exact Rental Price
    """
    extracted_vehicles = []
    
    # Helper recursive walker to find objects with 'car_info' and 'amount'
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

            # 1. Extract Car Name (car_info.name)
            car_name = str(car_info.get("name", "")).strip().lower()
            if not car_name:
                continue

            # Check if this object matches requested target car (Nexon or Punch)
            if target_car not in car_name:
                continue

            # 2. Extract Distance (car_info.location_data.distance)
            location_data = car_info.get("location_data", {})
            distance = float(location_data.get("distance", 999.0))

            # 3. Extract Rental Price (amount.trip_fare_after_discounts)
            trip_fare = amount.get("trip_fare_after_discounts")
            
            # Fallback check if trip_fare is directly inside amount or integer-formatted
            if trip_fare is not None:
                rental_price = int(float(trip_fare))
                
                if rental_price > 0:
                    extracted_vehicles.append({
                        "model": car_info.get("name", target_car.title()),
                        "distance": distance,
                        "price": rental_price
                    })
        except Exception as err:
            print(f"[ERROR] Error parsing JSON object node: {err}")

    return extracted_vehicles

def run_scraper_attempt(pickup_date, pickup_time, drop_date, drop_time, target_car):
    """
    Executes Playwright session, intercepts API responses, and parses strictly
    by verified DevTools field schema.
    """
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
        except Exception as e:
            print(f"[ERROR] Response handler exception on {response.url}: {e}")

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

            # Event-based wait for network activity to settle
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception as e:
                print(f"[WARN] Network idle wait timed out: {e}")

            context.close()
            browser.close()

    except Exception as e:
        print(f"[ERROR] Playwright session exception: {e}")
        if context:
            try: context.close()
            except: pass
        if browser:
            try: browser.close()
            except: pass
        raise e

    return intercepted_vehicles

def fetch_and_update():
    """
    Main orchestrator: Reads Apps Script request, fetches prices via exact JSON schema,
    picks nearest car, and posts price back to Google Sheet.
    """
    print("\n=================== [NEW SCRAPE JOB - DEVTOOLS SCHEMA] ===================")
    try:
        res = requests.get(WEB_APP_URL, timeout=10)
        data = res.json()
        print(f"[INFO] Parameters from Google Sheet: {data}")
    except Exception as e:
        print(f"[ERROR] Failed to fetch parameters from Google Sheet API: {e}")
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
    except Exception as err1:
        print(f"[ERROR] Attempt 1 failed: {err1}")

    # Retry Once if empty
    if not found_vehicles:
        print("[WARN] Retrying... Attempt 2...")
        try:
            found_vehicles = run_scraper_attempt(pickup_date, pickup_time, drop_date, drop_time, target_car)
        except Exception as err2:
            print(f"[ERROR] Attempt 2 failed: {err2}")

    print(f"[INFO] Total '{target_car.upper()}' cars matched: {len(found_vehicles)}")

    selected_price = 0
    if found_vehicles:
        # Sort by minimum distance
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

    # Post exact price back to Google Sheet
    post_data = {
        "zoomcar_rate": selected_price,
        "revv_rate": "Not Found",
        "max_rate": selected_price
    }
    try:
        response = requests.post(WEB_APP_URL, json=post_data, timeout=10)
        print(f"[SUCCESS] Updated Google Sheet via Webhook. Status code: {response.status_code}")
    except Exception as post_err:
        print(f"[ERROR] Failed to post result back to Google Sheet: {post_err}")

@app.route('/trigger-check', methods=['GET', 'POST'])
def trigger():
    threading.Thread(target=fetch_and_update).start()
    return jsonify({"status": "started", "message": "Exact Schema Scraper triggered successfully"})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
