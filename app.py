import os
import requests
from flask import Flask, jsonify
from playwright.sync_api import sync_playwright

app = Flask(__name__)

WEB_APP_URL = "https://script.google.com/macros/s/AKfycbxQ6GwU4o9l4xsBykQn8GsV8RtFYGHfOvGihBSvfMV8W9KR8K7a2ntYbOY5o7ZMuzhB8A/exec"

def fetch_and_update():
    # 1. Fetch query from Google Sheet
    res = requests.get(WEB_APP_URL)
    data = res.json()
    
    pickup_date = data.get('pickupDate')
    pickup_time = data.get('pickupTime')
    drop_date = data.get('dropDate')
    drop_time = data.get('dropTime')
    car_name = data.get('carName', 'Punch')

    # Zoomcar URL construction
    zoom_url = f"https://www.zoomcar.com/in/mumbai/search?pickup_date={pickup_date}&pickup_time={pickup_time}&drop_date={drop_date}&drop_time={drop_time}"

    selected_price = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(zoom_url, timeout=60000)
        page.wait_for_timeout(5000)
        
        cards = page.query_selector_all(".car-item")
        for card in cards:
            text = card.inner_text()
            if car_name.lower() in text.lower():
                pass
        browser.close()

    # 2. Update Google Sheet
    post_data = {
        "zoomcar_rate": selected_price,
        "revv_rate": "Not Found",
        "max_rate": selected_price
    }
    requests.post(WEB_APP_URL, json=post_data)
    return True

@app.route('/trigger-check', methods=['GET', 'POST'])
def trigger():
    try:
        fetch_and_update()
        return jsonify({"status": "success", "message": "Rates fetched and updated successfully!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)