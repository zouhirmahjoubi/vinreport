from flask import Flask, request, jsonify
import requests
import re
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from email.mime.base import MIMEBase
from email import encoders
from fpdf import FPDF
from fpdf.enums import XPos, YPos

app = Flask(__name__)

# --- TOKENS EXTRACTED SECURELY FROM DOKPLOY ENV ---
GOODCAR_API_KEY = os.environ.get("GOODCAR_API_KEY")
ETSY_OAUTH_TOKEN = os.environ.get("ETSY_OAUTH_TOKEN")
ETSY_API_KEY = os.environ.get("ETSY_API_KEY")
SMTP_EMAIL = os.environ.get("SMTP_EMAIL")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
SMTP_HOST = os.environ.get("SMTP_HOST", "mail.spacemail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USE_SSL = os.environ.get("SMTP_USE_SSL", "true").lower() in ("true", "1", "yes")

def generate_pdf_report(target_vin, specs):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_margins(15, 15, 15)
    
    # Logo or Brand name
    logo_path = os.path.join(os.path.dirname(__file__), 'logo.png')
    has_logo = os.path.exists(logo_path)
    
    if has_logo:
        try:
            pdf.image(logo_path, x=15, y=15, h=12)
            pdf.ln(15)
        except Exception:
            # Fallback text if logo fails to render
            pdf.set_font("Helvetica", "B", 20)
            pdf.set_text_color(13, 44, 84) # Dark Blue
            pdf.cell(14, 10, "VIN")
            pdf.set_text_color(216, 30, 30) # Red
            pdf.cell(30, 10, "report", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(5)
    else:
        pdf.set_font("Helvetica", "B", 20)
        pdf.set_text_color(13, 44, 84) # Dark Blue
        pdf.cell(14, 10, "VIN")
        pdf.set_text_color(216, 30, 30) # Red
        pdf.cell(30, 10, "report", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(5)

    # Powered by GoodCar
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(122, 139, 154)
    pdf.cell(0, 5, "POWERED BY GOODCAR", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="R")
    
    # Divider line
    pdf.set_draw_color(240, 243, 246)
    pdf.set_line_width(0.5)
    pdf.line(15, pdf.get_y(), 195, pdf.get_y())
    pdf.ln(8)
    
    # Hero box
    pdf.set_fill_color(13, 44, 84) # #0d2c54
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 12, "  Vehicle History Report", new_x=XPos.LMARGIN, new_y=YPos.NEXT, fill=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(176, 196, 222)
    pdf.cell(0, 8, f"  VIN: {target_vin}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, fill=True)
    pdf.ln(10)
    
    # Specs Section
    pdf.set_text_color(13, 44, 84)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 10, "SPECIFICATIONS", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    
    pdf.set_draw_color(13, 44, 84)
    pdf.set_line_width(1)
    pdf.line(15, pdf.get_y(), 50, pdf.get_y())
    pdf.ln(5)
    
    def draw_row(label, value):
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(90, 110, 133)
        pdf.cell(60, 10, f" {label}", border="B")
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(44, 62, 80)
        pdf.cell(0, 10, f" {value}", border="B", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    draw_row("Year", str(specs.get('year', 'N/A')))
    draw_row("Make", str(specs.get('make', 'N/A')))
    draw_row("Model", str(specs.get('model', 'N/A')))
    draw_row("Engine Type", str(specs.get('engine_type', 'N/A')))
    pdf.ln(25)
    
    # Disclaimer
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(85, 85, 85)
    pdf.cell(0, 5, "NMVTIS Disclaimer", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(127, 140, 141)
    disclaimer_text = (
        "The National Motor Vehicle Title Information System (NMVTIS) is an electronic system that contains "
        "information on certain automobiles titled in the United States. NMVTIS is intended to serve as a reliable "
        "source of title and brand history, but it does not contain detailed information regarding a vehicle's "
        "repair history."
    )
    pdf.multi_cell(0, 4, disclaimer_text)
    
    return pdf.output()


def send_vin_report(target_vin, customer_email, specs):
    # Determine if logo.png exists for branding
    logo_path = os.path.join(os.path.dirname(__file__), 'logo.png')
    has_logo = os.path.exists(logo_path)
    
    if has_logo:
        # Use inline Content-ID reference for the logo
        logo_html = '<img src="cid:logo" alt="VINreport Logo" style="height: 60px; max-width: 250px; object-fit: contain;">'
    else:
        # Styled text logo fallback
        logo_html = '<h1 style="margin: 0; font-family: Arial, sans-serif; font-size: 28px; color: #0d2c54;">VIN<span style="color: #d81e1e;">report</span></h1>'

    # HTML Generation Layout (Meets GoodCar branding mandates and visual standards)
    html_report = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{
                font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
                background-color: #f4f6f9;
                margin: 0;
                padding: 0;
                color: #333333;
            }}
            .container {{
                max-width: 600px;
                margin: 20px auto;
                background-color: #ffffff;
                border-radius: 8px;
                overflow: hidden;
                box-shadow: 0 4px 10px rgba(0,0,0,0.05);
                border: 1px solid #e1e8ed;
            }}
            .header {{
                padding: 30px;
                border-bottom: 1px solid #f0f3f6;
                display: flex;
                align-items: center;
                justify-content: space-between;
            }}
            .badge-container {{
                text-align: right;
            }}
            .badge {{
                font-size: 11px;
                color: #7a8b9a;
                text-transform: uppercase;
                letter-spacing: 1px;
                margin: 0;
                font-weight: bold;
            }}
            .content {{
                padding: 30px;
            }}
            .hero {{
                background: linear-gradient(135deg, #0d2c54 0%, #1b497e 100%);
                color: #ffffff;
                padding: 30px;
                border-radius: 6px;
                margin-bottom: 30px;
                text-align: center;
            }}
            .hero h2 {{
                margin: 0 0 10px 0;
                font-size: 24px;
                font-weight: 600;
            }}
            .hero p {{
                margin: 0;
                font-size: 14px;
                color: #b0c4de;
                letter-spacing: 0.5px;
            }}
            .section-title {{
                font-size: 16px;
                font-weight: bold;
                color: #0d2c54;
                margin-top: 0;
                margin-bottom: 15px;
                text-transform: uppercase;
                letter-spacing: 0.5px;
                border-bottom: 2px solid #f0f3f6;
                padding-bottom: 8px;
            }}
            .specs-table {{
                width: 100%;
                border-collapse: collapse;
                margin-bottom: 30px;
            }}
            .specs-table td {{
                padding: 12px 10px;
                border-bottom: 1px solid #f0f3f6;
                font-size: 14px;
            }}
            .specs-label {{
                font-weight: 600;
                color: #5a6e85;
                width: 35%;
            }}
            .specs-value {{
                color: #2c3e50;
            }}
            .footer {{
                background-color: #f8fafc;
                padding: 20px 30px;
                border-top: 1px solid #f0f3f6;
                font-size: 11px;
                color: #7f8c8d;
                line-height: 1.6;
            }}
            .disclaimer-title {{
                font-weight: bold;
                margin-bottom: 5px;
                color: #555;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div style="display: inline-block; vertical-align: middle;">
                    {logo_html}
                </div>
                <div class="badge-container" style="display: inline-block; vertical-align: middle; float: right; margin-top: 15px;">
                    <p class="badge">Powered by GoodCar</p>
                </div>
            </div>
            <div class="content">
                <div class="hero">
                    <h2>Vehicle History Report</h2>
                    <p>VIN: <strong style="color: #ffffff;">{target_vin}</strong></p>
                </div>
                
                <h3 class="section-title">Specifications</h3>
                <table class="specs-table">
                    <tr>
                        <td class="specs-label">Year</td>
                        <td class="specs-value">{specs.get('year', 'N/A')}</td>
                    </tr>
                    <tr>
                        <td class="specs-label">Make</td>
                        <td class="specs-value">{specs.get('make', 'N/A')}</td>
                    </tr>
                    <tr>
                        <td class="specs-label">Model</td>
                        <td class="specs-value">{specs.get('model', 'N/A')}</td>
                    </tr>
                    <tr>
                        <td class="specs-label">Engine Type</td>
                        <td class="specs-value">{specs.get('engine_type', 'N/A')}</td>
                    </tr>
                </table>
            </div>
            <div class="footer">
                <div class="disclaimer-title">NMVTIS Disclaimer</div>
                <p style="margin: 0;">The National Motor Vehicle Title Information System (NMVTIS) is an electronic system that contains information on certain automobiles titled in the United States. NMVTIS is intended to serve as a reliable source of title and brand history, but it does not contain detailed information regarding a vehicle's repair history.</p>
            </div>
        </div>
    </body>
    </html>
    """

    # Deliver Email (Construct related multipart for inline image support)
    msg = MIMEMultipart('related')
    msg['From'] = SMTP_EMAIL
    msg['To'] = customer_email
    msg['Subject'] = f"Your VINreport for VIN: {target_vin}"

    # Create the HTML alternative part
    msg_alternative = MIMEMultipart('alternative')
    msg.attach(msg_alternative)
    msg_alternative.attach(MIMEText(html_report, 'html'))

    # If logo.png exists, attach it as inline content
    if has_logo:
        try:
            with open(logo_path, 'rb') as f:
                msg_image = MIMEImage(f.read())
            msg_image.add_header('Content-ID', '<logo>')
            msg_image.add_header('Content-Disposition', 'inline', filename='logo.png')
            msg.attach(msg_image)
        except Exception as img_err:
            print(f"Failed to attach inline logo: {img_err}")

    # Generate PDF report and attach it
    try:
        pdf_bytes = generate_pdf_report(target_vin, specs)
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(bytes(pdf_bytes))
        encoders.encode_base64(part)
        part.add_header(
            'Content-Disposition',
            f'attachment; filename="VINreport_{target_vin}.pdf"',
        )
        msg.attach(part)
    except Exception as pdf_err:
        print(f"Failed to generate or attach PDF: {pdf_err}")

    if SMTP_USE_SSL:
        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
    else:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.starttls()
    server.login(SMTP_EMAIL, SMTP_PASSWORD)
    server.sendmail(SMTP_EMAIL, customer_email, msg.as_string())
    server.quit()

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
    try:
        receipt_response = requests.get(resource_url, headers=etsy_headers)
        receipt_response.raise_for_status()
        receipt_details = receipt_response.json()
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to retrieve receipt from Etsy: {str(e)}"}), 400
    
    # Extract personalization
    personalization_text = ""
    for transaction in receipt_details.get("transactions", []):
        for prop in transaction.get("property_values", []):
            if prop.get("property_name") == "Personalization":
                val = prop.get("values", [""])[0]
                if val:
                    personalization_text = val
                    break
        if personalization_text:
            break

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
    
    try:
        car_response = requests.post(goodcar_url, headers=goodcar_headers, data=goodcar_payload)
        car_response.raise_for_status()
        car_data = car_response.json()
        specs = car_data.get("specifications", {})
    except Exception as e:
        return jsonify({"status": "failed", "error": f"GoodCar API call failed: {str(e)}"}), 500

    try:
        send_vin_report(target_vin, customer_email, specs)
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "failed", "error": f"Email sending failed: {str(e)}"}), 500

@app.route('/test-report', methods=['GET', 'POST'])
def test_report():
    if request.method == 'POST':
        data = request.json or {}
        target_vin = data.get('vin')
        customer_email = data.get('email')
    else:
        target_vin = request.args.get('vin')
        customer_email = request.args.get('email')

    if not target_vin or not customer_email:
        return jsonify({"status": "error", "message": "Missing 'vin' or 'email' parameters"}), 400

    # GoodCar API Call
    goodcar_url = 'https://goodcar.com/business/api/vin-report-comprehensive'
    goodcar_headers = {'Authorization': f'Bearer {GOODCAR_API_KEY}'}
    goodcar_payload = {'vin': target_vin}

    try:
        car_response = requests.post(goodcar_url, headers=goodcar_headers, data=goodcar_payload)
        car_response.raise_for_status()
        car_data = car_response.json()
        return jsonify({"status": "success", "raw_car_data": car_data}), 200
    except Exception as e:
        return jsonify({"status": "failed", "error": f"GoodCar API call failed: {str(e)}"}), 500

if __name__ == '__main__':
    # Fallback default port for local testing
    app.run(host='0.0.0.0', port=5000)
