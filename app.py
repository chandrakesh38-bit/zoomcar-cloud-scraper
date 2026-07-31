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

def date_to_epoch_ms(val1, val2):
    """
    Combines date and time regardless of order (Date Time or Time Date).
    """
    combined_str = f"{val1} {val2}".strip()
    
    formats_to_try = [
        "%d-%b-%Y %H:%M",
        "%H:%M %d-%b-%Y",
        "%d-%b-%Y %I:%M %p",
        "%I:%M %p %d-%b-%Y",
        "%Y-%m-%d %H:%M",
        "%H:%M %Y-%m-%d"
    ]
    
    for fmt in formats_to_try:
        try:
            dt_obj = datetime.strptime(combined_str, fmt)
            return int(dt_obj.timestamp() * 1000)
        except ValueError:
            continue

    print(f"[WARN] Date parse fallback for string: '{combined_str}'")
    return int(datetime.now().timestamp() * 1000)

def parse_exact_zoomcar_json(json_data, target_car):
    extracted_vehicles = []
    seen_candidates = []  # DEBUG: track every car-like object we saw, matched or not

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

            # DEBUG: log every candidate object's keys/name, even non-matches
            if len(seen_candidates) < 15:
                seen_candidates.append({
                    "name": car_name or "(no car_info.name)",
                    "item_keys": list(item.keys()),
                    "car_info_keys": list(car_info.keys()) if car_info else [],
                    "amount_keys": list(amount.keys()) if amount else []
                })

            if not car_name or target_car not in car_name:
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

    if not extracted_vehicles:
        print(f"[DEBUG] No '{target_car}' match in this response. "
              f"Candidate objects seen: {len(seen_candidates)}")
        for c in seen_candidates:
            print(f"[DEBUG]   name={c['name']!r} item_keys={c['item_keys']} "
                  f"car_info_keys={c['car_info_keys']} amount_keys={c['amount_keys']}")

    return extracted_vehicles

def run_scraper_attempt(pickup_date, pickup_time, drop_date, drop_time, target_car):
    print("========== Playwright STARTED ==========")
    
    start_ms = date_to_epoch_ms(pickup_date, pickup_time)
    end_ms = date_to_epoch_ms(drop_date, drop_time)

    zoom_url = f"https://www.zoomcar.com/in/mumbai/search?lat=19.1080103&lng=72.9232129&type=round_trip&starts={start_ms}&ends={end_ms}&product_type=NORMAL"
    print(f"[INFO] Opening Search URL: {zoom_url}")

    intercepted_vehicles = []
    
    def handle_response(response):
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
            page.route("**/*.{png,jpg,jpeg,svg,webp,gif,woff,woff2}", lambda route: route.abort())

            page.goto(zoom_url, wait_until="domcontentloaded", timeout=40000)

            # Wait for network activity to settle instead of a blind fixed sleep.
            # This avoids closing the browser while a response body is still
            # being read (which caused TargetClosedError).
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                print("[WARN] networkidle wait timed out, continuing anyway")

            # Small buffer so any in-flight response.json() calls in
            # handle_response finish before we tear down the browser.
            page.wait_for_timeout(2000)

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

    p_date = data.get('pickupDate', '12-Sep-2026')
    p_time = data.get('pickupTime', '08:00')
    d_date = data.get('dropDate', '13-Sep-2026')
    d_time = data.get('dropTime', '08:00')
    
    # Auto-detect if parameters are swapped from Apps Script
    if "-" in str(p_time) and ":" in str(p_date):
        p_date, p_time = p_time, p_date
    if "-" in str(d_time) and ":" in str(d_date):
        d_date, d_time = d_time, d_date

    raw_car = str(data.get('carName', 'Punch')).strip().lower()
    if "nexon" in raw_car:
        target_car = "nexon"
    elif "punch" in raw_car:
        target_car = "punch"
    else:
        target_car = "punch"

    found_vehicles = []
    try:
        print(f"[INFO] Attempt 1 for target model: '{target_car.upper()}'...")
        found_vehicles = run_scraper_attempt(p_date, p_time, d_date, d_time, target_car)
    except Exception:
        traceback.print_exc()

    if not found_vehicles:
        print("[WARN] Retrying... Attempt 2...")
        try:
            found_vehicles = run_scraper_attempt(p_date, p_time, d_date, d_time, target_car)
        except Exception:
            traceback.print_exc()

    selected_price = 0
    if found_vehicles:
        found_vehicles.sort(key=lambda x: x["distance"])
        best_match = found_vehicles[0]
        selected_price = best_match["price"]
        print(f"[SUCCESS] Best match fare: ₹{selected_price}")

    post_data = {
        "zoomcar_rate": selected_price,
        "revv_rate": "Not Found",
        "max_rate": selected_price
    }
    try:
        response = requests.post(WEB_APP_URL, json=post_data, timeout=10)
        print(f"[SUCCESS] Updated Google Sheet via Webhook. Status code: {response.status_code}")
    except Exception:
        traceback.print_exc()

@app.route('/', methods=['GET'])
def home():
    return jsonify({"status": "live", "message": "Zoomcar Cloud Scraper Service is Active"}), 200

@app.route('/trigger-check', methods=['GET', 'POST'])
def trigger_check():
    thread = threading.Thread(target=fetch_and_update)
    thread.start()
    return jsonify({"status": "started", "message": "Scraper triggered successfully"}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
