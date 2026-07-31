import os
import re
import requests
import threading
from flask import Flask, jsonify
from playwright.sync_api import sync_playwright

app = Flask(__name__)

WEB_APP_URL = "https://script.google.com/macros/s/AKfycbXQ6GwU4o9l4xsBykQn8GsV8RtFYGHfOvGihBSvfMV8W9KR8K7a2ntYbOY5o7ZMuzhB8A/exec"

def parse_distance(card_text):
    match = re.search(r'([\d\.]+)\s*km\s*away', card_text, re.IGNORECASE)
    return float(match.group(1)) if match else 999.0

def fetch_and_update():
    try:
        res = requests.get(WEB_APP_URL, timeout=10)
        data = res.json()
    except Exception as e:
        data = {}

    pickup_date = data.get('pickupDate') if isinstance(data, dict) and data.get('pickupDate') else '12-Sep-2026'
    pickup_time = data.get('pickupTime') if isinstance(data, dict) and data.get('pickupTime') else '08:00'
    drop_date = data.get('dropDate') if isinstance(data, dict) and data.get('dropDate') else '13-Sep-2026'
    drop_time = data.get('dropTime') if isinstance(data, dict) and data.get('dropTime') else '08:00'
    car_name = (data.get('carName') if isinstance(data, dict) and data.get('carName') else 'Punch').strip()

    zoom_url = f"https://www.zoomcar.com/in/mumbai/search?pickup_date={pickup_date}&pickup_time={pickup_time}&drop_date={drop_date}&drop_time={drop_time}"

    selected_price = 0

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
            )
            context = browser.new_context()
            page = context.new_page()
            page.goto(zoom_url, timeout=60000)
            page.wait_for_timeout(5000)

            # Get all car cards
            cards = page.query_selector_all("div[class*='component-car-item'], div[class*='car-card']")
            
            nearest_card = None
            min_dist = 999.0

            for card in cards:
                text = card.inner_text()
                if car_name.lower() in text.lower():
                    dist = parse_distance(text)
                    if dist < min_dist:
                        min_dist = dist
                        nearest_card = card

            if nearest_card:
                # Click on the nearest car card to go to details page
                nearest_card.click()
                page.wait_for_timeout(4000)

                # Look for "Unlimited Kms Included" price element in details page
                page_text = page.content()
                
                # Check for "Unlimited Kms Included" price block
                unlimited_match = re.search(r'Unlimited\s*Kms\s*Included.*?₹\s*([\d,]+)', page_text, re.IGNORECASE | re.DOTALL)
                
                if unlimited_match:
                    selected_price = int(unlimited_match.group(1).replace(',', ''))
                else:
                    # Fallback parsing on details page text
                    prices = re.findall(r'₹\s*([\d,]+)', page.inner_text())
                    valid_prices = [int(p.replace(',', '')) for p in prices if int(p.replace(',', '')) > 1000]
                    selected_price = max(valid_prices) if valid_prices else 0

            browser.close()
    except Exception as e:
        print(f"Scraper error: {e}")

    # Update Google Sheet
    post_data = {
        "zoomcar_rate": selected_price,
        "revv_rate": "Not Found",
        "max_rate": selected_price
    }
    try:
        requests.post(WEB_APP_URL, json=post_data, timeout=10)
    except Exception as e:
        print(f"Error posting back: {e}")

@app.route('/trigger-check', methods=['GET', 'POST'])
def trigger():
    threading.Thread(target=fetch_and_update).start()
    return jsonify({"status": "started", "message": "Scraper triggered successfully"})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
