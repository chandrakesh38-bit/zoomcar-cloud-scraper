import os
import re
import requests
import threading
from flask import Flask, jsonify

app = Flask(__name__)

WEB_APP_URL = "https://script.google.com/macros/s/AKfycbXQ6GwU4o9l4xsBykQn8GsV8RtFYGHfOvGihBSvfMV8W9KR8K7a2ntYbOY5o7ZMuzhB8A/exec"

def fetch_and_update():
    try:
        res = requests.get(WEB_APP_URL, timeout=10)
        data = res.json()
    except Exception as e:
        print(f"Error fetching sheet query: {e}")
        data = {}

    pickup_date = data.get('pickupDate') if isinstance(data, dict) and data.get('pickupDate') else '12-Sep-2026'
    pickup_time = data.get('pickupTime') if isinstance(data, dict) and data.get('pickupTime') else '08:00'
    drop_date = data.get('dropDate') if isinstance(data, dict) and data.get('dropDate') else '13-Sep-2026'
    drop_time = data.get('dropTime') if isinstance(data, dict) and data.get('dropTime') else '08:00'
    target_car = (data.get('carName') if isinstance(data, dict) and data.get('carName') else 'Punch').strip().lower()

    # Direct Zoomcar Search API
    zoom_api_url = f"https://www.zoomcar.com/in/mumbai/search?pickup_date={pickup_date}&pickup_time={pickup_time}&drop_date={drop_date}&drop_time={drop_time}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
    }

    selected_price = 0
    try:
        response = requests.get(zoom_api_url, headers=headers, timeout=15)
        html_content = response.text

        # Find price associated with Unlimited Kms or Total Fare
        unlimited_prices = re.findall(r'Unlimited\s*Kms.*?>\s*₹\s*([\d,]+)', html_content, re.IGNORECASE | re.DOTALL)
        
        if unlimited_prices:
            selected_price = int(unlimited_prices[0].replace(',', ''))
        else:
            # Fallback regex for total booking amounts
            all_prices = re.findall(r'₹\s*([\d,]+)', html_content)
            valid_prices = [int(p.replace(',', '')) for p in all_prices if int(p.replace(',', '')) > 1000]
            selected_price = valid_prices[0] if valid_prices else 0

    except Exception as e:
        print(f"Direct API Error: {e}")

    # Post exact fare back to Google Sheet
    post_data = {
        "zoomcar_rate": selected_price,
        "revv_rate": "Not Found",
        "max_rate": selected_price
    }
    try:
        requests.post(WEB_APP_URL, json=post_data, timeout=10)
        print(f"Successfully updated sheet with rate: {selected_price}")
    except Exception as e:
        print(f"Error posting back: {e}")

@app.route('/trigger-check', methods=['GET', 'POST'])
def trigger():
    threading.Thread(target=fetch_and_update).start()
    return jsonify({"status": "started", "message": "Scraper triggered successfully"})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
