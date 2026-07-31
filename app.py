import os
import re
import requests
import threading
from flask import Flask, jsonify
from playwright.sync_api import sync_playwright

app = Flask(__name__)

WEB_APP_URL = "https://script.google.com/macros/s/AKfycbXQ6GwU4o9l4xsBykQn8GsV8RtFYGHfOvGihBSvfMV8W9KR8K7a2ntYbOY5o7ZMuzhB8A/exec"

def parse_distance(card_text):
    match = re.search(r'([\d\.]+)\s*km', card_text, re.IGNORECASE)
    return float(match.group(1)) if match else 999.0

def parse_price(card_text):
    match = re.search(r'₹\s*([\d,]+)', card_text)
    return int(match.group(1).replace(',', '')) if match else 0

def fetch_and_update():
    try:
        res = requests.get(WEB_APP_URL, timeout=10)
        data = res.json()
    except Exception as e:
        data = {}

    pickup_date = data.get('pickupDate') if isinstance(data, dict) and data.get('pickupDate') else '29-Aug-2026'
    pickup_time = data.get('pickupTime') if isinstance(data, dict) and data.get('pickupTime') else '08:00'
    drop_date = data.get('dropDate') if isinstance(data, dict) and data.get('dropDate') else '30-Aug-2026'
    drop_time = data.get('dropTime') if isinstance(data, dict) and data.get('dropTime') else '08:00'
    car_name = (data.get('carName') if isinstance(data, dict) and data.get('carName') else 'Punch').strip()

    zoom_url = f"https://www.zoomcar.com/in/mumbai/search?pickup_date={pickup_date}&pickup_time={pickup_time}&drop_date={drop_date}&drop_time={drop_time}"

    matching_cars = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
            )
            page = browser.new_page()
            page.goto(zoom_url, timeout=60000)
            page.wait_for_timeout(3000)
            
            cards = page.query_selector_all("div[class*='component-car-item'], div[class*='car-card'], div[class*='item']")
            if not cards:
                cards = page.query_selector_all("div")

            for card in cards:
                text = card.inner_text()
                if car_name.lower() in text.lower() and '₹' in text:
                    dist = parse_distance(text)
                    price = parse_price(text)
                    if price > 0:
                        matching_cars.append({"distance": dist, "price": price})
            
            browser.close()
    except Exception as e:
        print(f"Scraper error: {e}")

    selected_price = 0
    if matching_cars:
        matching_cars.sort(key=lambda x: x["distance"])
        selected_price = matching_cars[0]["price"]

    # Update Google Sheet
    post_data = {
        "zoomcar_rate": selected_price,
        "revv_rate": "Not Found",
        "max_rate": selected_price
    }
    requests.post(WEB_APP_URL, json=post_data)

@app.route('/trigger-check', methods=['GET', 'POST'])
def trigger():
    threading.Thread(target=fetch_and_update).start()
    return jsonify({"status": "started", "message": "Scraper triggered in background"})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
