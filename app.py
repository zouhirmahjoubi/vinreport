from flask import Flask, request, jsonify
import requests
import re
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)

# --- TOKENS EXTRACTED SECURELY FROM DOKPLOY ENV ---
GOODCAR_API_KEY = os.environ.get("GOODCAR_API_KEY")
ETSY_OAUTH_TOKEN = os.environ.get("ETSY_OAUTH_TOKEN")
ETSY_API_KEY = os.environ.get("ETSY_API_KEY")
SMTP_EMAIL = os.environ.get("SMTP_EMAIL")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")

@app.route('/etsy-webhook', methods=['POST'])
def handle_etsy_order():
    webhook_data = request.json
    
    if not webhook_data or webhook_data.get("event_type") != "order.paid":
        return jsonify({"status": "ignored"}), 200

    resource_url = webhook_data.get("resource_url")
    
    # Query Etsy for the actual receipt data
    etsy_headers = {
        "x-api-key": ETSY_API_KEY,
        "Authorization": f"Bearer {ETSY_OAUTH_TOKEN}"
    }
    receipt_response = requests.get(resource_url, headers=etsy_headers)
    receipt_details = receipt_response.json()
    
    # Extract personalization
    personalization_text = ""
    for transaction in receipt_details.get("transactions", []):
        for prop in transaction.get("property_values", []):
            if prop.get("property_name") == "Personalization":
                personalization_text = prop.get("values", [""])[0]

    if not personalization_text:
        return jsonify({"status": "error", "message": "No personalization details found"}), 400

    # Match VIN & Email
    vin_match = re.search(r'\b([A-HJ-NPR-Z0-9]{17})\b', personalization_text.upper())
    email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', personalization_text)
    
    if not vin_match or not email_match:
        return jsonify({"status": "error", "message": "Failed to parse"}), 400
        
    target_vin = vin_match.group(1)
    customer_email = email_match.group(0)

    # GoodCar API Call
    goodcar_url = 'https://goodcar.com/business/api/vin-report-comprehensive'
    goodcar_headers = {'Authorization': f'Bearer {GOODCAR_API_KEY}'}
    goodcar_payload = {'vin': target_vin}
    
    car_response = requests.post(goodcar_url, headers=goodcar_headers, data=goodcar_payload)
    car_data = car_response.json()
    specs = car_data.get("specifications", {})

    # HTML Generation Layout (Meets GoodCar branding mandates)[cite: 1]
    html_report = f"""
    <html>
    <body style="font-family: Arial; padding: 20px;">
        <div style="display: flex; justify-content: space-between;">
            <h1>VINreport</h1>
            <p>Powered by GoodCar</p> <!-- MANDATORY BADGE -->
        </div>
        <hr>
        <h3>Vehicle: {specs.get('year')} {specs.get('make')} {specs.get('model')}</h3>
        <p>Engine: {specs.get('engine_type')}</p>
        <hr>
        <p style="font-size: 8px; color: gray;">NMVTIS Disclaimer: [Insert Text]</p> <!-- MANDATORY FOOTER -->
    </body>
    </html>
    """

    # Deliver Email
    msg = MIMEMultipart()
    msg['From'] = SMTP_EMAIL
    msg['To'] = customer_email
    msg['Subject'] = f"Your VINreport for VIN: {target_vin}"
    msg.attach(MIMEText(html_report, 'html'))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.sendmail(SMTP_EMAIL, customer_email, msg.as_string())
        server.quit()
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "failed", "error": str(e)}), 500

if __name__ == '__main__':
    # Fallback default port for local testing
    app.run(host='0.0.0.0', port=5000)
