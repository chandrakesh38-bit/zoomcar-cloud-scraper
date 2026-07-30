import os
import re
import requests
from flask import Flask, jsonify
from playwright.sync_api import sync_playwright

app = Flask(__name__)

# Standalone API Endpoint
WEB_APP_URL = "https://script.google.com/macros/s/AKfycbQ6GwU4o9l4xsBykQn8GsV8RtFYGHfOvGihBSvfMV8W9KR8K7a2ntYbOY5o7ZMuzhB8A/exec"

def parse_distance(card_text):
    match = re.search(r'([\d\.]+)\s*km', card_text, re.IGNORECASE)
    return float(match.group(1)) if match else 999.0

def parse_price(card_text):
    match = re.search(r'₹\s*([\d,]+)', card_text)
    return int(match.group(1).replace(',', '')) if match else 0

def fetch_and_update():
    # 1. Fetch query from Google Sheet
    res = requests.get(WEB_APP_URL)
    data = res.json()
    
    pickup_date = data.get('pickupDate', '29-Aug-2026')
    pickup_time = data.get('pickupTime', '08:00')
    drop_date = data.get('dropDate', '30-Aug-2026')
    drop_time = data.get('dropTime', '08:00')
    car_name = data.get('carName', 'Punch').strip()

    zoom_url = f"https://www.zoomcar.com/in/mumbai/search?pickup_date={pickup_date}&pickup_time={pickup_time}&drop_date={drop_date}&drop_time={drop_time}"

    matching_cars = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox']
        )
        page = browser.new_page()
        page.goto(zoom_url, timeout=60000)
        page.wait_for_timeout(5000)
        
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

    selected_price = 0
    if matching_cars:
        matching_cars.sort(key=lambda x: x["distance"])
        selected_price = matching_cars[0]["price"]

    # 2. Update Google Sheet
    post_data = {
        "zoomcar_rate": selected_price,
        "revv_rate": "Not Found",
        "max_rate": selected_price
    }
    requests.post(WEB_APP_URL, json=post_data)
    return selected_price

@app.route('/trigger-check', methods=['GET', 'POST'])
def trigger():
    try:
        rate = fetch_and_update()
        return jsonify({"status": "success", "rate": rate})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
