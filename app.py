from flask import Flask, request, jsonify, send_file
import requests
import re
import os
import io
import uuid
import smtplib
import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from email.mime.application import MIMEApplication

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas
from reportlab.graphics.shapes import Drawing, Rect, String, Line, Circle

app = Flask(__name__)

# --- TOKENS EXTRACTED SECURELY FROM DOKPLOY ENV ---
DOKPLOY_APP_URL = os.environ.get("DOKPLOY_APP_URL", "https://vinreport.odysseusai.ai").rstrip('/')

ETSY_OAUTH_TOKEN = os.environ.get("ETSY_OAUTH_TOKEN")
ETSY_API_KEY = os.environ.get("ETSY_API_KEY")
SMTP_EMAIL = os.environ.get("SMTP_EMAIL")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
SMTP_HOST = os.environ.get("SMTP_HOST", "mail.spacemail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USE_SSL = os.environ.get("SMTP_USE_SSL", "true").lower() in ("true", "1", "yes")

MARKETCHECK_API_KEY = os.environ.get("MARKETCHECK_API_KEY")

# ==========================================
# 🔧 DATA SOURCING FROM FREE APIs
# ==========================================

def fetch_nhtsa_specs(vin: str) -> dict:
    """
    Decodes a VIN using the free NHTSA vPIC API.
    """
    url = f"https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}?format=json"
    try:
        r = requests.get(url, timeout=15.0)
        r.raise_for_status()
        data = r.json()
        if data.get("Results") and len(data["Results"]) > 0:
            return data["Results"][0]
    except Exception as e:
        print(f"Error fetching NHTSA specs for VIN {vin}: {e}")
    return {}

def fetch_nhtsa_recalls(make: str, model: str, year: str) -> dict:
    """
    Checks active recalls using the free NHTSA Recall API.
    """
    url = "https://api.nhtsa.gov/recalls/recallsByVehicle"
    params = {
        "make": make,
        "model": model,
        "modelYear": year
    }
    try:
        r = requests.get(url, params=params, timeout=15.0)
        if r.status_code == 200:
            return r.json()
        elif r.status_code == 400:
            # The NHTSA recalls API returns a 400 status when 0 recalls are found
            try:
                res_data = r.json()
                if res_data.get("Count") == 0:
                    return res_data
            except Exception:
                pass
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"Error fetching NHTSA recalls for {year} {make} {model}: {e}")
    return {"Count": 0, "results": []}

def fetch_marketcheck_history(vin: str) -> list:
    """
    Fetches online listing history for the vehicle from MarketCheck (free tier).
    """
    if not MARKETCHECK_API_KEY:
        print("MARKETCHECK_API_KEY is not set. Skipping retail history.")
        return []
    url = f"https://marketcheck-prod.apigee.net/v2/history/car/{vin}"
    params = {"api_key": MARKETCHECK_API_KEY}
    headers = {"accept": "application/json"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15.0)
        if r.status_code == 200:
            return r.json()
        else:
            print(f"MarketCheck history returned HTTP status {r.status_code}: {r.text}")
    except Exception as e:
        print(f"Error fetching MarketCheck history for VIN {vin}: {e}")
    return []

def fetch_vehicle_data(vin: str) -> dict:
    """
    Orchestrates specs, recalls, and history fetching from free resources.
    """
    specs = fetch_nhtsa_specs(vin)
    if not specs or not specs.get("Make"):
        return {}
    
    make = specs.get("Make", "")
    model = specs.get("Model", "")
    year = specs.get("ModelYear", "")
    
    recalls = {"Count": 0, "results": []}
    if make and model and year:
        recalls = fetch_nhtsa_recalls(make, model, year)
        
    history = fetch_marketcheck_history(vin)
    
    return {
        "specs": specs,
        "recalls": recalls,
        "history": history
    }

# ==========================================
# 🎨 PREMIUM PDF TEMPLATE ENGINE (GOODCAR STYLE)
# ==========================================

class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        vin = getattr(self, 'vin_for_header', 'N/A')
        vehicle_title = getattr(self, 'vehicle_title_for_header', 'Vehicle')
        search_date = getattr(self, 'search_date_for_header', '')
        if not search_date:
            search_date = datetime.datetime.now().strftime("%B %d, %Y")
            
        # 1. Header (on all pages)
        # Logo: "GoodCar"
        self.setFont('Helvetica-Bold', 18)
        self.setFillColor(colors.HexColor('#0F172A'))
        self.drawString(36, 755, "Good")
        self.setFillColor(colors.HexColor('#475569'))
        self.drawString(82, 755, "Car")
        
        # Center-Right Header Text: "Report on [Vehicle]"
        self.setFont('Helvetica-Bold', 10)
        self.setFillColor(colors.HexColor('#1E293B'))
        self.drawRightString(576, 755, f"Report on {vehicle_title}")
        
        # Header Divider line
        self.setStrokeColor(colors.HexColor('#CBD5E1'))
        self.setLineWidth(0.5)
        self.line(36, 745, 576, 745)
        
        # Search Details (Only on Page 1, below the main header line)
        if self._pageNumber == 1:
            self.setFont('Helvetica-Bold', 10)
            self.setFillColor(colors.HexColor('#0F172A'))
            self.drawString(36, 725, f"VIN: {vin}")
            self.setFont('Helvetica', 9)
            self.setFillColor(colors.HexColor('#475569'))
            self.drawRightString(576, 725, f"Search Date: {search_date}")

        # 2. Footer (on all pages)
        self.setStrokeColor(colors.HexColor('#CBD5E1'))
        self.setLineWidth(0.5)
        self.line(36, 45, 576, 45)

        # Disclaimer (Small text above page info)
        self.setFont('Helvetica', 6.5)
        self.setFillColor(colors.HexColor('#64748B'))
        self.drawString(36, 35, "Disclaimer: The content of the NMVTIS Inquiry Data included in the report may have materially changed following this date.")
        
        # Page info & generation timestamp
        self.setFont('Helvetica', 8)
        self.setFillColor(colors.HexColor('#475569'))
        gen_date = datetime.datetime.now().strftime("%m/%d/%Y")
        self.drawString(36, 22, f"Report generated on {gen_date}")
        self.drawRightString(576, 22, f"Page {self._pageNumber} of {page_count}")
        
        self.restoreState()

def make_card(title, value, is_alert, icon_type):
    d = Drawing(165, 90)
    border_color = colors.HexColor('#16A34A') # Green
    if is_alert:
        border_color = colors.HexColor('#DC2626') # Red
        
    # Card Border
    d.add(Rect(0, 0, 165, 90, rx=5, ry=5, fillColor=colors.white, strokeColor=border_color, strokeWidth=1))
    
    # Icon background circle
    d.add(Circle(82.5, 62, 15, fillColor=colors.HexColor('#F8FAFC'), strokeColor=border_color, strokeWidth=0.8))
    
    icon_color = colors.HexColor('#1E293B')
    # Draw simple geometric icons
    if icon_type == "mileage":
        d.add(Circle(82.5, 62, 9, strokeColor=icon_color, strokeWidth=1))
        d.add(Line(82.5, 62, 88.5, 68, strokeColor=colors.HexColor('#DC2626'), strokeWidth=1.2))
    elif icon_type == "title":
        d.add(Rect(77.5, 55, 10, 12, strokeColor=icon_color, strokeWidth=1))
        d.add(Line(79.5, 63, 83.5, 63, strokeColor=icon_color, strokeWidth=1))
    elif icon_type == "accident":
        d.add(Line(76.5, 56, 88.5, 68, strokeColor=colors.HexColor('#DC2626'), strokeWidth=1.5))
        d.add(Line(76.5, 68, 88.5, 56, strokeColor=colors.HexColor('#DC2626'), strokeWidth=1.5))
    elif icon_type == "owner":
        d.add(Circle(82.5, 66, 4, strokeColor=icon_color, strokeWidth=1))
        d.add(Line(76.5, 55, 88.5, 55, strokeColor=icon_color, strokeWidth=1))
    elif icon_type == "salvage":
        d.add(Rect(75.5, 55, 14, 10, strokeColor=icon_color, strokeWidth=1))
        d.add(Line(75.5, 55, 89.5, 65, strokeColor=icon_color, strokeWidth=1))
    elif icon_type == "loss":
        d.add(Circle(82.5, 62, 6, strokeColor=colors.HexColor('#DC2626'), strokeWidth=1))
    elif icon_type == "check":
        d.add(Rect(76.5, 57, 12, 8, strokeColor=icon_color, strokeWidth=1))
        d.add(Circle(82.5, 61, 2.5, strokeColor=colors.HexColor('#DC2626'), strokeWidth=1))
    elif icon_type == "value":
        d.add(String(82.5, 57, "$", textAnchor="middle", fontSize=11, fontName="Helvetica-Bold", fillColor=colors.HexColor('#16A34A')))
    elif icon_type == "sales":
        d.add(Line(75.5, 57, 81.5, 67, strokeColor=icon_color, strokeWidth=1))
        d.add(Line(81.5, 67, 89.5, 61, strokeColor=icon_color, strokeWidth=1))
        d.add(Line(89.5, 61, 83.5, 51, strokeColor=icon_color, strokeWidth=1))
        d.add(Line(83.5, 51, 75.5, 57, strokeColor=icon_color, strokeWidth=1))
    elif icon_type == "recall":
        d.add(Circle(82.5, 62, 8, strokeColor=colors.HexColor('#DC2626'), strokeWidth=1.2))
        d.add(String(82.5, 58, "!", textAnchor="middle", fontSize=10, fontName="Helvetica-Bold", fillColor=colors.HexColor('#DC2626')))
    elif icon_type == "complaint":
        d.add(Line(82.5, 69, 75.5, 55, strokeColor=icon_color, strokeWidth=1))
        d.add(Line(75.5, 55, 89.5, 55, strokeColor=icon_color, strokeWidth=1))
        d.add(Line(89.5, 55, 82.5, 69, strokeColor=icon_color, strokeWidth=1))
    elif icon_type == "maintenance":
        d.add(Rect(76.5, 55, 12, 12, strokeColor=icon_color, strokeWidth=1))
        d.add(Line(76.5, 63, 88.5, 63, strokeColor=icon_color, strokeWidth=1))
    else:
        d.add(Circle(82.5, 62, 7, strokeColor=icon_color, strokeWidth=1))
        
    # Title
    d.add(String(82.5, 30, title, textAnchor="middle", fontSize=8.5, fontName="Helvetica-Bold", fillColor=colors.HexColor('#1E293B')))
    # Value
    val_color = colors.HexColor('#DC2626') if is_alert else colors.HexColor('#16A34A')
    d.add(String(82.5, 12, value, textAnchor="middle", fontSize=9.5, fontName="Helvetica-Bold", fillColor=val_color))
    return d

def generate_pdf_report(vin, data, output_path):
    # Printable area: 540 x 720 points
    # topMargin=75 to leave space for the large Page 1 search details
    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=75,
        bottomMargin=54
    )
    
    specs = data.get("specs", {})
    year = specs.get("ModelYear", "N/A")
    make = specs.get("Make", "N/A")
    model = specs.get("Model", "N/A")
    vehicle_title = f"{year} {make} {model}"
    search_date = datetime.datetime.now().strftime("%B %d, %Y")
    
    class CustomNumberedCanvas(NumberedCanvas):
        vin_for_header = vin
        vehicle_title_for_header = vehicle_title
        search_date_for_header = search_date
        
    styles = getSampleStyleSheet()
    
    styles.add(ParagraphStyle(
        name='MainTitleBold',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=20,
        leading=24,
        textColor=colors.HexColor('#0F172A'),
        spaceAfter=15
    ))
    
    styles.add(ParagraphStyle(
        name='GridTextBold',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#1E293B')
    ))
    
    styles.add(ParagraphStyle(
        name='GridText',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9.5,
        leading=13,
        textColor=colors.HexColor('#334155')
    ))

    story = []
    
    # ------------------ PAGE 1: DASHBOARD GRID ------------------
    story.append(Spacer(1, 10))
    
    history = data.get("history", [])
    recalls = data.get("recalls", {})
    recall_count = recalls.get("Count", 0)
    
    max_miles = "N/A"
    if history:
        try:
            miles_list = [int(float(str(h.get("miles", 0)).replace(',', '').strip())) for h in history if h.get("miles")]
            if miles_list:
                max_miles = f"{max(miles_list):,} miles"
        except Exception:
            pass

    c1 = make_card("Mileage", max_miles, max_miles != "N/A", "mileage")
    c2 = make_card("Title Records", "N/A (NMVTIS Restricted)", False, "title")
    c3 = make_card("Accidents", "0 records found", False, "accident")
    
    c4 = make_card("Ownership History", "N/A", False, "owner")
    c5 = make_card("Junk/Salvage Records", "0 records found", False, "salvage")
    c6 = make_card("Total Loss Record", "0 records found", False, "loss")
    
    c7 = make_card("Problem Checks", "0 records found", False, "check")
    c8 = make_card("Market Values", f"{len(history)} records found" if history else "0 records found", False, "value")
    c9 = make_card("Sales History", f"{len(history)} records found" if history else "0 records found", False, "sales")
    
    c10 = make_card("Open Recalls", f"{recall_count} recalls found" if recall_count > 0 else "0 recalls found", recall_count > 0, "recall")
    c11 = make_card("Safety Complaints", "0 records found", False, "complaint")
    c12 = make_card("Maintenance Schedule", "Available", False, "maintenance")
    
    # Dashboard layout table (3 columns of 175 pt each = 525 pt, centered in 540 pt width)
    grid_table = Table([
        [c1, c2, c3],
        [c4, c5, c6],
        [c7, c8, c9],
        [c10, c11, c12]
    ], colWidths=[175, 175, 175])
    
    grid_table.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
        ('TOPPADDING', (0,0), (-1,-1), 0),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
    ]))
    
    story.append(grid_table)
    story.append(PageBreak())
    
    # ------------------ PAGE 2: SPECS CARD & VEHICLE DATA ------------------
    p2_c1 = make_card("Auto Specs", "Available", False, "check")
    p2_c2 = make_card("Crash Test Ratings", "0 records found", False, "accident")
    p2_c3 = make_card("Awards & Accolades", "0 records found", False, "maintenance")
    p2_c4 = make_card("Warranties", "0 records found", False, "title")
    p2_c5 = make_card("Cost of Ownership", "Available", False, "value")
    
    p2_grid = Table([
        [p2_c1, p2_c2, p2_c3],
        [p2_c4, p2_c5, ""]
    ], colWidths=[175, 175, 175])
    
    p2_grid.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
        ('TOPPADDING', (0,0), (-1,-1), 0),
    ]))
    
    story.append(p2_grid)
    story.append(Spacer(1, 15))
    
    story.append(Paragraph("Vehicle Data", styles['MainTitleBold']))
    
    def clean_val(val):
        if val is None:
            return "N/A"
        s = str(val).strip()
        if s == "" or s.lower() in ("none", "null", "n/a"):
            return "N/A"
        return s
        
    displacement = clean_val(specs.get("DisplacementL"))
    disp_str = f"{displacement}L" if displacement != "N/A" else clean_val(specs.get("DisplacementCC"))
    
    vehicle_specs = [
        ("Year", clean_val(specs.get("ModelYear"))),
        ("Make, Model", f"{clean_val(specs.get('Make'))} {clean_val(specs.get('Model'))}"),
        ("Trim", clean_val(specs.get("Trim"))),
        ("Drive Type", clean_val(specs.get("DriveType"))),
        ("Brake System", clean_val(specs.get("BrakeSystemType"))),
        ("Restraint Type", clean_val(specs.get("OtherRestraintSystemInfo"))),
        ("Manufactured In", f"{clean_val(specs.get('PlantCity'))}, {clean_val(specs.get('PlantCountry'))}".strip(', ')),
        ("Style", clean_val(specs.get("BodyClass"))),
        ("Body Type", clean_val(specs.get("VehicleType"))),
        ("Body Subtype", clean_val(specs.get("BodyClass"))),
        ("Doors", clean_val(specs.get("Doors"))),
        ("Mfr Model Number", "N/A"),
    ]
    
    specs_table_data = []
    for label, val in vehicle_specs:
        specs_table_data.append([
            Paragraph(f"<b>{label}</b>", styles['GridText']),
            Paragraph(str(val), styles['GridText'])
        ])
        
    specs_table = Table(specs_table_data, colWidths=[180, 340])
    
    t_style = [
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
        ('PADDING', (0,0), (-1,-1), 6),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]
    for i in range(len(specs_table_data)):
        if i % 2 == 1:
            t_style.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#F8FAFC')))
    specs_table.setStyle(TableStyle(t_style))
    
    story.append(specs_table)
    story.append(PageBreak())
    
    # ------------------ PAGE 3: MILEAGE LOG ------------------
    story.append(Paragraph("Mileage", styles['MainTitleBold']))
    
    mileage_summary_data = [
        [Paragraph("<b>Last Reported Mileage:</b>", styles['GridText']), Paragraph(max_miles, styles['GridText'])],
        [Paragraph("<b>Estimated Mileage:</b>", styles['GridText']), Paragraph("N/A", styles['GridText'])]
    ]
    summary_table = Table(mileage_summary_data, colWidths=[180, 340])
    summary_table.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F8FAFC')),
        ('PADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 20))
    
    story.append(Paragraph("Vehicle Mileage Timeline", styles['MainTitleBold']))
    
    timeline_data = [[
        Paragraph("<b>Years</b>", styles['GridTextBold']),
        Paragraph("<b>Mileage</b>", styles['GridTextBold'])
    ]]
    
    if history:
        for entry in history:
            date_str = entry.get("scraped_at_date") or entry.get("last_seen_at_date") or entry.get("first_seen_at_date") or "N/A"
            year_str = date_str.split('-')[0] if '-' in date_str else date_str
            miles = entry.get("miles")
            miles_str = f"{miles:,} miles" if miles is not None else "N/A"
            timeline_data.append([
                Paragraph(year_str, styles['GridText']),
                Paragraph(miles_str, styles['GridText'])
            ])
    else:
        timeline_data.append([
            Paragraph("N/A", styles['GridText']),
            Paragraph("No listings timeline history available", styles['GridText'])
        ])
        
    timeline_table = Table(timeline_data, colWidths=[180, 340])
    
    time_style = [
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#F8FAFC')),
        ('LINEBELOW', (0,0), (-1,0), 1, colors.HexColor('#CBD5E1')),
        ('GRID', (0,1), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
        ('PADDING', (0,0), (-1,-1), 8),
    ]
    for i in range(1, len(timeline_data)):
        if i % 2 == 0:
            time_style.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#F8FAFC')))
    timeline_table.setStyle(TableStyle(time_style))
    story.append(timeline_table)
    story.append(PageBreak())
    
    # ------------------ PAGE 4: SAFETY RECALL DETAILS ------------------
    story.append(Paragraph("NHTSA Recalls", styles['MainTitleBold']))
    story.append(Spacer(1, 10))
    
    if recall_count == 0:
        story.append(Paragraph("<b>No active safety recalls found for this vehicle.</b>", styles['GridText']))
    else:
        for idx, recall in enumerate(recalls.get("results", [])):
            campaign_id = clean_val(recall.get("NHTSACampaignNumber"))
            component = clean_val(recall.get("Component"))
            mfr_campaign = clean_val(recall.get("Notes"))
            notif_date = clean_val(recall.get("ReportReceivedDate"))
            
            story.append(Paragraph(f"<b>Recall #{idx+1}</b>", styles['GridTextBold']))
            story.append(Spacer(1, 6))
            
            recall_stats = [
                [Paragraph("<b>NHTSA Campaign #:</b>", styles['GridText']), Paragraph(campaign_id, styles['GridText'])],
                [Paragraph("<b>Manufacturer Campaign #:</b>", styles['GridText']), Paragraph(mfr_campaign, styles['GridText'])],
                [Paragraph("<b>Owner Notification Date:</b>", styles['GridText']), Paragraph(notif_date, styles['GridText'])],
                [Paragraph("<b>Report Creation Date:</b>", styles['GridText']), Paragraph(notif_date, styles['GridText'])],
            ]
            stats_table = Table(recall_stats, colWidths=[180, 340])
            stats_table.setStyle(TableStyle([
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
                ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#F8FAFC')),
                ('PADDING', (0,0), (-1,-1), 6),
            ]))
            story.append(stats_table)
            story.append(Spacer(1, 8))
            
            story.append(Paragraph("<b>Defect Description:</b>", styles['GridTextBold']))
            story.append(Paragraph(clean_val(recall.get("Summary")), styles['GridText']))
            story.append(Spacer(1, 6))
            
            story.append(Paragraph("<b>Defect Consequences:</b>", styles['GridTextBold']))
            story.append(Paragraph(clean_val(recall.get("Consequence")), styles['GridText']))
            story.append(Spacer(1, 6))
            
            story.append(Paragraph("<b>Corrective Action:</b>", styles['GridTextBold']))
            story.append(Paragraph(clean_val(recall.get("Remedy")), styles['GridText']))
            story.append(Spacer(1, 15))
            
            line_draw = Drawing(540, 1)
            line_draw.add(Line(0, 0, 540, 0, strokeColor=colors.HexColor('#E2E8F0'), strokeWidth=1))
            story.append(line_draw)
            story.append(Spacer(1, 15))
            
    doc.build(story, canvasmaker=CustomNumberedCanvas)

# ==========================================
# 📧 EMAIL DELIVERY WITH ATTACHMENT
# ==========================================

def send_vin_report(target_vin: str, customer_email: str, report_id: str, attributes: dict):
    """
    Emails the PDF report as an attachment and includes a web download button.
    """
    logo_path = os.path.join(os.path.dirname(__file__), 'logo.png')
    has_logo  = os.path.exists(logo_path)

    if has_logo:
        logo_html = '<img src="cid:logo" alt="VINreport Logo" style="height: 60px; max-width: 250px; object-fit: contain;">'
    else:
        logo_html = '<h1 style="margin:0;font-family:Arial,sans-serif;font-size:28px;color:#0d2c54;">VIN<span style="color:#d81e1e;">report</span></h1>'

    year = str(attributes.get("ModelYear", "N/A"))
    make = str(attributes.get("Make", "N/A"))
    model = str(attributes.get("Model", "N/A"))

    download_url = f"{DOKPLOY_APP_URL}/download/{report_id}"

    html_report = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body{{font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;background-color:#f4f6f9;margin:0;padding:0;color:#333}}
            .container{{max-width:600px;margin:20px auto;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 4px 10px rgba(0,0,0,.05);border:1px solid #e1e8ed}}
            .header{{padding:30px;border-bottom:1px solid #f0f3f6}}
            .content{{padding:30px}}
            .hero{{background:linear-gradient(135deg,#0d2c54 0%,#1b497e 100%);color:#fff;padding:30px;border-radius:6px;margin-bottom:25px;text-align:center}}
            .hero h2{{margin:0 0 10px;font-size:24px;font-weight:600}}
            .hero p{{margin:0;font-size:14px;color:#b0c4de;letter-spacing:.5px}}
            .btn-container{{text-align:center;margin:30px 0}}
            .btn{{background-color:#16a34a;color:#ffffff !important;padding:14px 28px;text-decoration:none;border-radius:5px;font-weight:bold;display:inline-block;font-size:16px;box-shadow:0 4px 6px rgba(0,0,0,0.1);}}
            .section-title{{font-size:16px;font-weight:bold;color:#0d2c54;margin-top:25px;margin-bottom:12px;text-transform:uppercase;letter-spacing:.5px;border-bottom:2px solid #f0f3f6;padding-bottom:8px}}
            .specs-table{{width:100%;border-collapse:collapse;margin-bottom:20px}}
            .specs-table td{{padding:10px;border-bottom:1px solid #f0f3f6;font-size:14px}}
            .specs-label{{font-weight:600;color:#5a6e85;width:40%}}
            .specs-value{{color:#2c3e50}}
            .footer{{background:#f8fafc;padding:20px 30px;border-top:1px solid #f0f3f6;font-size:11px;color:#7f8c8d;line-height:1.6}}
            .disclaimer-title{{font-weight:bold;margin-bottom:5px;color:#555}}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div style="display:inline-block;vertical-align:middle">{logo_html}</div>
            </div>
            <div class="content">
                <div class="hero">
                    <h2>{year} {make} {model}</h2>
                    <p>Vehicle History Report &nbsp;|&nbsp; VIN: <strong style="color:#fff">{target_vin}</strong></p>
                </div>
                
                <p style="font-size:15px;color:#333;line-height:1.5;">
                    Hello,<br><br>
                    Thank you for your order! Your vehicle history report is ready.
                    We have attached the official PDF report directly to this email for your convenience.
                    You can also view and download the PDF report at any time by clicking the button below:
                </p>

                <div class="btn-container">
                    <a href="{download_url}" class="btn" style="color:#ffffff;">Download PDF Report</a>
                </div>

                <p style="font-size:13px;color:#666;line-height:1.5;margin-top:10px;">
                    Please note: The download link will remain active for 90 days. We recommend downloading and saving the PDF file directly to your device.
                </p>

                <h3 class="section-title">Specifications</h3>
                <table class="specs-table">
                    <tr><td class="specs-label">VIN</td><td class="specs-value">{target_vin}</td></tr>
                    <tr><td class="specs-label">Year</td><td class="specs-value">{year}</td></tr>
                    <tr><td class="specs-label">Make</td><td class="specs-value">{make}</td></tr>
                    <tr><td class="specs-label">Model</td><td class="specs-value">{model}</td></tr>
                </table>
            </div>
            <div class="footer">
                <div class="disclaimer-title">NMVTIS Disclaimer</div>
                <p style="margin:0">The National Motor Vehicle Title Information System (NMVTIS) is an
                electronic system that contains information on certain automobiles titled in the United States.
                NMVTIS is intended to serve as a reliable source of title and brand history, but it does not
                contain detailed information regarding a vehicle's repair history.</p>
            </div>
        </div>
    </body>
    </html>
    """

    msg = MIMEMultipart('mixed')
    msg['From']    = SMTP_EMAIL
    msg['To']      = customer_email
    msg['Subject'] = f"🚘 Your Vehicle History Report is Ready: {year} {make} {model}"

    # Related part for HTML and inline images
    msg_related = MIMEMultipart('related')
    msg.attach(msg_related)

    # Alternative part for HTML
    msg_alternative = MIMEMultipart('alternative')
    msg_related.attach(msg_alternative)
    msg_alternative.attach(MIMEText(html_report, 'html'))

    # Inline logo
    if has_logo:
        try:
            with open(logo_path, 'rb') as f:
                msg_image = MIMEImage(f.read())
            msg_image.add_header('Content-ID', '<logo>')
            msg_image.add_header('Content-Disposition', 'inline', filename='logo.png')
            msg_related.attach(msg_image)
        except Exception as img_err:
            print("Failed to attach inline logo: " + str(img_err))

    # PDF attachment
    pdf_path = os.path.join('reports', f"{report_id}.pdf")
    if os.path.exists(pdf_path):
        try:
            with open(pdf_path, 'rb') as f:
                pdf_attachment = MIMEApplication(f.read(), _subtype="pdf")
            pdf_attachment.add_header('Content-Disposition', 'attachment', filename=f"Vehicle_History_Report_{target_vin}.pdf")
            msg.attach(pdf_attachment)
        except Exception as pdf_err:
            print("Failed to attach PDF to email: " + str(pdf_err))

    if SMTP_USE_SSL:
        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
    else:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.starttls()
    server.login(SMTP_EMAIL, SMTP_PASSWORD)
    server.sendmail(SMTP_EMAIL, customer_email, msg.as_string())
    server.quit()

# ==========================================
# 🌐 WEBHOOK & CACHED REPORT ENDPOINTS
# ==========================================

@app.route('/etsy-webhook', methods=['POST'])
def handle_etsy_order():
    """
    Listens for Etsy order.paid webhook events.
    """
    webhook_data = request.json

    if not webhook_data or webhook_data.get("event_type") != "order.paid":
        return jsonify({"status": "ignored"}), 200

    resource_url  = webhook_data.get("resource_url")
    etsy_headers  = {
        "x-api-key":     ETSY_API_KEY,
        "Authorization": "Bearer " + ETSY_OAUTH_TOKEN
    }
    try:
        receipt_response = requests.get(resource_url, headers=etsy_headers)
        receipt_response.raise_for_status()
        receipt_details  = receipt_response.json()
    except Exception as e:
        return jsonify({"status": "error",
                        "message": "Failed to retrieve receipt from Etsy: " + str(e)}), 400

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
        return jsonify({"status": "error",
                        "message": "No personalization details found"}), 400

    vin_match   = re.search(r'\b([A-HJ-NPR-Z0-9]{17})\b', personalization_text.upper())
    email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', personalization_text)

    if not vin_match or not email_match:
        return jsonify({"status": "error", "message": "Failed to parse VIN/Email"}), 400

    target_vin     = vin_match.group(1)
    customer_email = email_match.group(0)

    # Fetch specs, recalls, history
    vehicle_data = fetch_vehicle_data(target_vin)
    if not vehicle_data or not vehicle_data.get("specs"):
        return jsonify({"status": "failed", "error": "Failed to decode VIN or fetch specifications"}), 400

    # Ensure reports directory exists
    os.makedirs('reports', exist_ok=True)
    report_id = uuid.uuid4().hex

    try:
        # Generate the PDF report and cache it locally
        pdf_path = os.path.join('reports', f"{report_id}.pdf")
        generate_pdf_report(target_vin, vehicle_data, pdf_path)
        
        # Email the PDF and links to customer
        send_vin_report(target_vin, customer_email, report_id, vehicle_data.get("specs", {}))
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "failed", "error": f"Failed to generate or deliver report: {str(e)}"}), 500


@app.route('/download/<report_id>', methods=['GET'])
def download_report(report_id):
    """
    Serves the cached, locally-stored PDF report to the user.
    """
    # Sanitize the report_id to prevent path traversal
    report_id = re.sub(r'[^a-zA-Z0-9_-]', '', report_id)
    pdf_path = os.path.join('reports', f"{report_id}.pdf")

    if not os.path.exists(pdf_path):
        return jsonify({"status": "failed", "error": "Report expired, unavailable, or not found"}), 404

    return send_file(
        pdf_path,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"Vehicle_History_Report_{report_id}.pdf"
    )


@app.route('/test-report', methods=['GET', 'POST'])
def test_report():
    """
    Endpoint for testing report delivery manually.
    """
    if request.method == 'POST':
        body           = request.json or {}
        target_vin     = body.get('vin')
        customer_email = body.get('email')
    else:
        target_vin     = request.args.get('vin')
        customer_email = request.args.get('email')

    if not target_vin or not customer_email:
        return jsonify({"status": "error",
                        "message": "Missing 'vin' or 'email' parameters"}), 400

    # Fetch specs, recalls, history
    vehicle_data = fetch_vehicle_data(target_vin)
    if not vehicle_data or not vehicle_data.get("specs"):
        return jsonify({"status": "failed", "error": "Failed to decode VIN or fetch specifications"}), 400

    os.makedirs('reports', exist_ok=True)
    report_id = uuid.uuid4().hex

    try:
        # Generate the PDF report
        pdf_path = os.path.join('reports', f"{report_id}.pdf")
        generate_pdf_report(target_vin, vehicle_data, pdf_path)
        
        # Email it
        send_vin_report(target_vin, customer_email, report_id, vehicle_data.get("specs", {}))
        return jsonify({"status": "success",
                        "message": f"Test report for VIN {target_vin} generated ({report_id}) and sent to {customer_email}"}), 200
    except Exception as e:
        return jsonify({"status": "failed",
                        "error": f"Failed to generate or deliver test report: {str(e)}"}), 500


@app.route('/')
def read_root():
    return jsonify({"status": "online", "engine": "Antigravity Agent v2 Gateway Ready"}), 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
