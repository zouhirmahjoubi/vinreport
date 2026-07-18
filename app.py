from flask import Flask, request, jsonify, send_file
import requests
import re
import os
import io
import uuid
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from email.mime.application import MIMEApplication

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas
from reportlab.graphics.shapes import Drawing, Rect, String, Line

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
# 🎨 PREMIUM PDF TEMPLATE ENGINE (REPORTLAB)
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
        
        # Header on pages > 1
        if self._pageNumber > 1:
            self.setFont('Helvetica', 8)
            self.setFillColor(colors.HexColor('#475569'))
            self.drawString(36, 755, "VEHICLE SPECIFICATION & HISTORY REPORT")
            self.drawRightString(576, 755, f"VIN: {vin}")
            self.setStrokeColor(colors.HexColor('#E2E8F0'))
            self.setLineWidth(0.5)
            self.line(36, 747, 576, 747)

        # Footer on all pages
        self.setStrokeColor(colors.HexColor('#E2E8F0'))
        self.setLineWidth(0.5)
        self.line(36, 45, 576, 45)

        self.setFont('Helvetica', 8)
        self.setFillColor(colors.HexColor('#475569'))
        self.drawString(36, 32, "Sourced from public databases (NHTSA, MarketCheck). Verify details locally.")
        self.drawRightString(576, 32, f"Page {self._pageNumber} of {page_count}")
        self.restoreState()

def generate_pdf_report(vin, data, output_path):
    # Setup document: margins are 36pt (0.5 in). Printable area: 540 x 720 points
    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=54,
        bottomMargin=54
    )
    
    class CustomNumberedCanvas(NumberedCanvas):
        vin_for_header = vin
        
    styles = getSampleStyleSheet()
    
    # Custom styles
    primary_color = colors.HexColor('#0B1B3D') # Deep Navy
    secondary_color = colors.HexColor('#475569') # Slate Gray
    accent_gray = colors.HexColor('#E2E8F0') # Light Slate
    bg_light = colors.HexColor('#F8FAFC') # Off-White
    
    styles.add(ParagraphStyle(
        name='MainTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=24,
        leading=28,
        textColor=primary_color,
        spaceAfter=6
    ))
    
    styles.add(ParagraphStyle(
        name='SubTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=12,
        leading=16,
        textColor=secondary_color,
        spaceAfter=15
    ))

    styles.add(ParagraphStyle(
        name='SectionHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=18,
        textColor=colors.white,
        spaceAfter=0
    ))
    
    styles.add(ParagraphStyle(
        name='TableHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=12,
        textColor=colors.white
    ))
    
    styles.add(ParagraphStyle(
        name='TableCellBold',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#1E293B')
    ))
    
    styles.add(ParagraphStyle(
        name='TableCell',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#334155')
    ))
    
    styles.add(ParagraphStyle(
        name='PassedBanner',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=16,
        leading=20,
        textColor=colors.HexColor('#15803D')
    ))
    
    styles.add(ParagraphStyle(
        name='PassedText',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#166534')
    ))
    
    styles.add(ParagraphStyle(
        name='AlertBanner',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=16,
        leading=20,
        textColor=colors.HexColor('#B91C1C')
    ))
    
    styles.add(ParagraphStyle(
        name='AlertText',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#991B1B')
    ))

    story = []
    
    def build_header_band(title_text):
        p = Paragraph(title_text, styles['SectionHeader'])
        t = Table([[p]], colWidths=[540])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), primary_color),
            ('TOPPADDING', (0,0), (-1,-1), 8),
            ('BOTTOMPADDING', (0,0), (-1,-1), 8),
            ('LEFTPADDING', (0,0), (-1,-1), 12),
            ('RIGHTPADDING', (0,0), (-1,-1), 12),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ]))
        return t

    # ------------------ PAGE 1: VEHICLE PASSPORT ------------------
    story.append(build_header_band("VEHICLE PASSPORT"))
    story.append(Spacer(1, 15))
    
    specs = data.get("specs", {})
    
    def clean_val(val):
        if val is None:
            return "N/A"
        s = str(val).strip()
        if s == "" or s.lower() in ("none", "null", "n/a"):
            return "N/A"
        return s

    year = clean_val(specs.get("ModelYear"))
    make = clean_val(specs.get("Make"))
    model = clean_val(specs.get("Model"))
    trim = specs.get("Trim")
    trim_clean = clean_val(trim)
    full_name = f"{year} {make} {model}".upper()
    sub_text = f"TRIM: {trim_clean.upper()}" if trim_clean != "N/A" else "SPECIFICATION CERTIFICATE"
    
    story.append(Paragraph(full_name, styles['MainTitle']))
    story.append(Paragraph(sub_text, styles['SubTitle']))
    
    vin_p = Paragraph(f"<b>VIN:</b> {vin}", styles['TableCellBold'])
    vin_table = Table([[vin_p]], colWidths=[250])
    vin_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), bg_light),
        ('GRID', (0,0), (-1,-1), 1, accent_gray),
        ('PADDING', (0,0), (-1,-1), 6),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ]))
    story.append(vin_table)
    story.append(Spacer(1, 15))
    
    # Modern tech graphic placeholder
    d = Drawing(540, 100)
    d.add(Rect(0, 0, 540, 100, fillColor=bg_light, strokeColor=accent_gray, strokeWidth=1))
    d.add(Line(10, 10, 50, 10, strokeColor=primary_color, strokeWidth=2))
    d.add(Line(10, 10, 10, 30, strokeColor=primary_color, strokeWidth=2))
    d.add(Line(530, 90, 490, 90, strokeColor=primary_color, strokeWidth=2))
    d.add(Line(530, 90, 530, 70, strokeColor=primary_color, strokeWidth=2))
    for x in range(100, 450, 40):
        d.add(Line(x, 15, x, 85, strokeColor=colors.HexColor('#F1F5F9'), strokeWidth=1))
    for y in range(20, 90, 20):
        d.add(Line(100, y, 440, y, strokeColor=colors.HexColor('#F1F5F9'), strokeWidth=1))
    d.add(String(270, 55, "VEHICLE SPECIFICATION & HISTORY ARCHIVE", textAnchor="middle", fontSize=11, fontName="Helvetica-Bold", fillColor=primary_color))
    d.add(String(270, 35, "OFFICIAL NHTSA & MARKET DATA PRE-RECORDED", textAnchor="middle", fontSize=8, fontName="Helvetica", fillColor=secondary_color))
    story.append(d)
    story.append(Spacer(1, 20))
    
    disp_val = clean_val(specs.get("DisplacementL"))
    if disp_val == "N/A":
        disp_val = clean_val(specs.get("DisplacementCC"))
        if disp_val != "N/A":
            disp_val = f"{disp_val} cc"
    else:
        disp_val = f"{disp_val}L"
        
    cyl_val = clean_val(specs.get("EngineCylinders"))
    if cyl_val != "N/A":
        config = clean_val(specs.get("EngineConfiguration"))
        if config == "In-Line":
            cyl_val = f"I-{cyl_val}"
        elif config == "V-Engine" or "V" in config or config.startswith("V"):
            cyl_val = f"V-{cyl_val}"
        else:
            cyl_val = f"{cyl_val}-Cylinder"
            
    hp_val = clean_val(specs.get("EngineHP"))
    if hp_val != "N/A":
        hp_val = f"{hp_val} HP"
        
    engine_parts = [p for p in [disp_val, cyl_val, hp_val] if p != "N/A"]
    engine_desc = " / ".join(engine_parts) if engine_parts else "N/A"
    
    city = clean_val(specs.get("PlantCity"))
    state = clean_val(specs.get("PlantState"))
    country = clean_val(specs.get("PlantCountry"))
    origin_parts = [p for p in [city, state, country] if p != "N/A"]
    origin = ", ".join(origin_parts) if origin_parts else "N/A"
    
    passport_specs = [
        ("Body Class", clean_val(specs.get("BodyClass"))),
        ("Engine", engine_desc),
        ("Fuel Type", clean_val(specs.get("FuelTypePrimary"))),
        ("Drivetrain", clean_val(specs.get("DriveType"))),
        ("Transmission Style", clean_val(specs.get("TransmissionStyle"))),
        ("Origin / Assembly Plant", origin),
    ]
    
    passport_table_data = []
    for label, val in passport_specs:
        p_label = Paragraph(f"<b>{label}</b>", styles['TableCellBold'])
        p_val = Paragraph(str(val), styles['TableCell'])
        passport_table_data.append([p_label, p_val])
        
    passport_table = Table(passport_table_data, colWidths=[180, 360])
    passport_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), bg_light),
        ('GRID', (0,0), (-1,-1), 0.5, accent_gray),
        ('PADDING', (0,0), (-1,-1), 10),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(passport_table)
    story.append(PageBreak())
    
    # ------------------ PAGE 2: EQUIPMENT & OPTIONS ------------------
    story.append(build_header_band("EQUIPMENT & OPTIONS"))
    story.append(Spacer(1, 15))
    
    mech_specs = [
        ("Engine Configuration", clean_val(specs.get("EngineConfiguration"))),
        ("Cylinders", clean_val(specs.get("EngineCylinders"))),
        ("Displacement (L)", clean_val(specs.get("DisplacementL"))),
        ("Horsepower", clean_val(specs.get("EngineHP"))),
        ("Valve Train Design", clean_val(specs.get("ValveTrainDesign"))),
        ("Transmission Style", clean_val(specs.get("TransmissionStyle"))),
        ("Wheel Size Front (in)", clean_val(specs.get("WheelSizeFront"))),
        ("Wheel Size Rear (in)", clean_val(specs.get("WheelSizeRear"))),
        ("Steering Location", clean_val(specs.get("SteeringLocation"))),
    ]
    
    mech_table_data = [[Paragraph("<b>Mechanical Specification</b>", styles['TableCellBold']), ""]]
    for label, val in mech_specs:
        p_label = Paragraph(label, styles['TableCell'])
        p_val = Paragraph(str(val), styles['TableCellBold'])
        mech_table_data.append([p_label, p_val])
        
    mech_table = Table(mech_table_data, colWidths=[140, 110])
    mech_table.setStyle(TableStyle([
        ('SPAN', (0,0), (1,0)),
        ('BACKGROUND', (0,0), (-1,0), bg_light),
        ('LINEBELOW', (0,0), (-1,0), 1, primary_color),
        ('GRID', (0,1), (-1,-1), 0.5, accent_gray),
        ('PADDING', (0,0), (-1,-1), 6),
    ]))
    
    safety_specs = [
        ("Antilock Brakes (ABS)", clean_val(specs.get("ABS"))),
        ("Stability Control (ESC)", clean_val(specs.get("ESC"))),
        ("Traction Control", clean_val(specs.get("TractionControl"))),
        ("Tire Pressure (TPMS)", clean_val(specs.get("TPMS"))),
        ("Front Airbags", clean_val(specs.get("AirBagLocFront"))),
        ("Side Airbags", clean_val(specs.get("AirBagLocSide"))),
        ("Curtain Airbags", clean_val(specs.get("AirBagLocCurtain"))),
        ("Forward Collision Warning", clean_val(specs.get("ForwardCollisionWarning"))),
        ("Lane Departure Warning", clean_val(specs.get("LaneDepartureWarning"))),
        ("Blind Spot Monitor", clean_val(specs.get("BlindSpotMon"))),
    ]
    
    safety_table_data = [[Paragraph("<b>Safety & Driver Assist</b>", styles['TableCellBold']), ""]]
    for label, val in safety_specs:
        p_label = Paragraph(label, styles['TableCell'])
        p_val = Paragraph(str(val), styles['TableCellBold'])
        safety_table_data.append([p_label, p_val])
        
    safety_table = Table(safety_table_data, colWidths=[150, 110])
    safety_table.setStyle(TableStyle([
        ('SPAN', (0,0), (1,0)),
        ('BACKGROUND', (0,0), (-1,0), bg_light),
        ('LINEBELOW', (0,0), (-1,0), 1, primary_color),
        ('GRID', (0,1), (-1,-1), 0.5, accent_gray),
        ('PADDING', (0,0), (-1,-1), 6),
    ]))
    
    parent_table = Table([[mech_table, "", safety_table]], colWidths=[250, 40, 250])
    parent_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('PADDING', (0,0), (-1,-1), 0),
    ]))
    
    story.append(parent_table)
    story.append(PageBreak())
    
    # ------------------ PAGE 3: MARKET & MILEAGE LOG ------------------
    story.append(build_header_band("MARKET & MILEAGE LOG"))
    story.append(Spacer(1, 15))
    
    desc_p = Paragraph(
        "This section details the online retail dealer listing history for this vehicle VIN. It captures historical pricing, recorded odometer milestones, and dealer listing locations over time.",
        styles['TableCell']
    )
    story.append(desc_p)
    story.append(Spacer(1, 15))
    
    history_list = data.get("history", [])
    if history_list:
        # Cap historical logs to 15 records to preserve template format on exactly 1 page
        history_list = history_list[:15]
        
        history_table_data = [[
            Paragraph("<b>Date Spoken</b>", styles['TableHeader']),
            Paragraph("<b>Mileage</b>", styles['TableHeader']),
            Paragraph("<b>Asking Price</b>", styles['TableHeader']),
            Paragraph("<b>Dealer / Location</b>", styles['TableHeader'])
        ]]
        
        for entry in history_list:
            date_str = entry.get("scraped_at_date") or entry.get("last_seen_at_date") or entry.get("first_seen_at_date") or "N/A"
            miles = entry.get("miles")
            try:
                miles_val = int(float(str(miles).replace(',', '').strip()))
                miles_str = f"{miles_val:,} mi"
            except Exception:
                miles_str = f"{miles} mi" if miles else "N/A"
                
            price = entry.get("price")
            try:
                price_val = int(float(str(price).replace('$', '').replace(',', '').strip()))
                price_str = f"${price_val:,}"
            except Exception:
                price_str = f"${price}" if price else "N/A"
            
            dealer = entry.get("dealer", {})
            dealer_name = dealer.get("name") or "Dealer"
            dealer_city = dealer.get("city") or ""
            dealer_state = dealer.get("state") or ""
            loc_str = f"{dealer_name} ({dealer_city}, {dealer_state})" if dealer_city else dealer_name
            
            row_style = styles['TableCell']
            history_table_data.append([
                Paragraph(date_str, row_style),
                Paragraph(miles_str, row_style),
                Paragraph(price_str, row_style),
                Paragraph(loc_str, row_style)
            ])
            
        history_table = Table(history_table_data, colWidths=[80, 80, 80, 300])
        
        t_style = [
            ('BACKGROUND', (0,0), (-1,0), primary_color),
            ('GRID', (0,0), (-1,-1), 0.5, accent_gray),
            ('PADDING', (0,0), (-1,-1), 8),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ]
        for i in range(1, len(history_table_data)):
            if i % 2 == 0:
                t_style.append(('BACKGROUND', (0, i), (-1, i), bg_light))
        history_table.setStyle(TableStyle(t_style))
        story.append(history_table)
    else:
        card_content = [
            [Paragraph("<b>NO HISTORICAL DEALER LISTINGS FOUND</b>", styles['TableCellBold'])],
            [Paragraph("A search of dealer retail databases indicates that no listing records have been logged for this VIN. This is common and typical for vehicles that have been owned by a single private owner for their entire lifespan, or sold exclusively through private party transactions where retail inventory tracking systems do not scrape listings.", styles['TableCell'])]
        ]
        card_table = Table(card_content, colWidths=[520])
        card_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), bg_light),
            ('BOX', (0,0), (-1,-1), 1, secondary_color),
            ('PADDING', (0,0), (-1,-1), 12),
            ('BOTTOMPADDING', (0,0), (-1,0), 6),
        ]))
        story.append(card_table)
        
    story.append(PageBreak())
    
    # ------------------ PAGE 4: SAFETY & RECALL CHECK ------------------
    story.append(build_header_band("SAFETY & RECALL CHECK"))
    story.append(Spacer(1, 15))
    
    recalls = data.get("recalls", {})
    count = recalls.get("Count", 0)
    results = recalls.get("results", [])
    
    if count == 0 or not results:
        banner_content = [
            [Paragraph("✔ PASSED - NO OPEN RECALLS DETECTED", styles['PassedBanner'])],
            [Paragraph("The National Highway Traffic Safety Administration (NHTSA) database indicates that there are currently no active, unremedied safety recalls open for this vehicle make, model, and year. Safety recalls are critical repairs mandated by the federal government and must be repaired by authorized dealerships at zero cost to the owner.", styles['PassedText'])]
        ]
        banner_table = Table(banner_content, colWidths=[520])
        banner_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#DCFCE7')),
            ('BOX', (0,0), (-1,-1), 1.5, colors.HexColor('#16A34A')),
            ('PADDING', (0,0), (-1,-1), 15),
            ('BOTTOMPADDING', (0,0), (-1,0), 6),
        ]))
        story.append(banner_table)
    else:
        banner_content = [
            [Paragraph("⚠ WARNING - ACTIVE SAFETY RECALLS FOUND", styles['AlertBanner'])],
            [Paragraph(f"The NHTSA database has identified {count} open safety recall(s) associated with this vehicle make, model, and year. Active recalls represent critical, unresolved manufacturer defects that compromise passenger safety. Owners should immediately contact their local authorized dealer to schedule a free remedy repair.", styles['AlertText'])]
        ]
        banner_table = Table(banner_content, colWidths=[520])
        banner_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#FEE2E2')),
            ('BOX', (0,0), (-1,-1), 1.5, colors.HexColor('#DC2626')),
            ('PADDING', (0,0), (-1,-1), 15),
            ('BOTTOMPADDING', (0,0), (-1,0), 6),
        ]))
        story.append(Spacer(1, 15))
        story.append(banner_table)
        story.append(Spacer(1, 15))
        
        # Display recalls list (max 3 for spacing)
        displayed_recalls = results[:3]
        for recall in displayed_recalls:
            campaign_id = recall.get("NHTSACampaignNumber", "N/A")
            component = recall.get("Component", "N/A")
            reported_date = recall.get("ReportReceivedDate", "N/A")
            summary = recall.get("Summary", "No summary provided.")
            consequence = recall.get("Consequence", "No consequence provided.")
            remedy = recall.get("Remedy", "No remedy specified.")
            
            recall_data = [
                (Paragraph("<b>NHTSA Campaign:</b>", styles['TableCellBold']), Paragraph(campaign_id, styles['TableCell'])),
                (Paragraph("<b>Component:</b>", styles['TableCellBold']), Paragraph(component, styles['TableCell'])),
                (Paragraph("<b>Report Date:</b>", styles['TableCellBold']), Paragraph(reported_date, styles['TableCell'])),
                (Paragraph("<b>Defect Summary:</b>", styles['TableCellBold']), Paragraph(summary, styles['TableCell'])),
                (Paragraph("<b>Safety Risk:</b>", styles['TableCellBold']), Paragraph(consequence, styles['TableCell'])),
                (Paragraph("<b>Remedy Action:</b>", styles['TableCellBold']), Paragraph(remedy, styles['TableCell'])),
            ]
            
            recall_table = Table(recall_data, colWidths=[120, 380])
            recall_table.setStyle(TableStyle([
                ('GRID', (0,0), (-1,-1), 0.5, accent_gray),
                ('PADDING', (0,0), (-1,-1), 6),
                ('BACKGROUND', (0,0), (0,-1), bg_light),
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ]))
            story.append(KeepTogether([
                recall_table,
                Spacer(1, 10)
            ]))
            
        if len(results) > 3:
            more_p = Paragraph(
                f"<i>Note: There are {len(results) - 3} additional recalls. Showing the 3 most recent records. Visit NHTSA.gov for the full list.</i>",
                styles['TableCell']
            )
            story.append(more_p)
            
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
