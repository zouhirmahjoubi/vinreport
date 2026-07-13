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

# ─────────────────────────────────────────────────────────────
# HTML STRIPPER / CLEANER
# ─────────────────────────────────────────────────────────────

def clean_html(text):
    if not text:
        return ""
    t = str(text).strip()
    # Replace common HTML tags with readable text/newlines
    t = t.replace("<p>", "").replace("</p>", "\n\n")
    t = t.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    # Strip any other tags
    t = re.sub(r'<[^>]+>', '', t)
    # Decode common HTML entities
    t = t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'").replace("&quot;", '"').replace("&nbsp;", " ")
    return t.strip()

# ─────────────────────────────────────────────────────────────
# DATA EXTRACTION  -  captures ALL GoodCar API sections
# ─────────────────────────────────────────────────────────────

def extract_all_data(car_data):
    """Extract all 15+ sections from the GoodCar API response."""
    content       = car_data.get("content", {})
    main_info     = content.get("main", {})
    raw_vehicle   = main_info.get("vehicleDataRaw", {})
    sec_specs     = content.get("section_specs", {})
    vds           = sec_specs.get("vehicleDataSpecs", {})
    engine_info   = sec_specs.get("engine", {})
    trans_info    = sec_specs.get("transmission", {})
    epa_mpg       = sec_specs.get("epaMpg", {})

    year  = raw_vehicle.get("year")  or vds.get("year",  {}).get("txt", "N/A")
    make  = raw_vehicle.get("make")  or "N/A"
    model = raw_vehicle.get("model") or "N/A"
    engine_type = (engine_info.get("Brand Name =>") or
                   vds.get("engine", {}).get("txt") or "N/A")

    # Build keys for accident sections (contain parentheses in key names)
    acc_key_v    = "section_accidents (dataSource=V)"
    acc_key_a    = "section_accidents (dataSource=A)"
    acc_key_null = "section_accidents (dataSource=null)"

    return {
        # Basic identifiers kept for email template compatibility
        "year":        year,
        "make":        make,
        "model":       model,
        "engine_type": engine_type,
        # Specs sub-sections
        "vehicle_data_specs": vds,
        "engine":             engine_info,
        "transmission":       trans_info,
        "epa_mpg":            epa_mpg,
        "standard_specs":     sec_specs.get("standardSpecifications", {}),
        "safety_equipment":   sec_specs.get("safetyEquipment", {}),
        # History sections
        "mileage":           content.get("section_mileage", {}),
        "title":             content.get("section_title", {}),
        "accidents_v":       content.get(acc_key_v, {}),
        "accidents_a":       content.get(acc_key_a, {}),
        "accidents_null":    content.get(acc_key_null, {}),
        "accidents":         content.get("section_accidents", {}),
        "junk":              content.get("section_junk", {}),
        "loss":              content.get("section_loss", {}),
        "title_issues":      content.get("section_title_issues", {}),
        "market_values":     content.get("section_market_values", {}),
        "sales":             content.get("section_sales", {}),
        "recalls":           content.get("section_recalls", {}),
        "safety_complaints": content.get("section_safety_complaints", {}),
        "maintenance":       content.get("section_maintenance_schedule", {}),
        "crash_test":        content.get("section_crash_test", {}),
        "awards":            content.get("section_awards", {}),
        # Newly discovered sections
        "warranties":              content.get("section_warranties", {}),
        "cost_ownership":          content.get("section_cost_ownership", {}),
        "location":                content.get("section_location", {}),
        "mfr":                     content.get("section_mfr", {}),
        "title_ownership_history": content.get("section_title_ownership_history", {}),
    }

# Backward-compatibility alias
def extract_specs(car_data):
    return extract_all_data(car_data)

def _safe(val, max_len=95):
    """Return a PDF-safe, truncated string."""
    if val is None:
        return ""
    s = str(val).strip()
    if len(s) > max_len:
        s = s[:max_len - 3] + "..."
    return s

# ─────────────────────────────────────────────────────────────
# COMPREHENSIVE PDF GENERATOR  (15+ sections)
# ─────────────────────────────────────────────────────────────

def generate_pdf_report(target_vin, data):
    # Color palette (RGB)
    C_DARK_BLUE  = (13,  44,  84)
    C_MED_BLUE   = (27,  73, 126)
    C_RED        = (216, 30,  30)
    C_LIGHT_GRAY = (240, 243, 246)
    C_MID_GRAY   = (122, 139, 154)
    C_TEXT_DARK  = (44,  62,  80)
    C_TEXT_LBL   = (90, 110, 133)
    C_GREEN      = (30, 130,  76)
    C_ORANGE     = (190,  85,  10)
    C_ALT_ROW    = (248, 250, 252)
    C_WHITE      = (255, 255, 255)
    C_STEEL      = (200, 215, 230)

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.set_margins(15, 15, 15)
    EW = 180  # effective page width

    # ── Drawing helpers ──────────────────────────────────────
    def tc(*rgb): pdf.set_text_color(*rgb)
    def fc(*rgb): pdf.set_fill_color(*rgb)
    def dc(*rgb): pdf.set_draw_color(*rgb)

    def section_header(title):
        pdf.ln(5)
        fc(*C_DARK_BLUE); tc(*C_WHITE)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(EW, 9, "  " + title.upper(),
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT, fill=True)
        pdf.ln(3)
        tc(*C_TEXT_DARK)

    def mini_header(title):
        pdf.ln(2)
        tc(*C_MED_BLUE); pdf.set_font("Helvetica", "B", 9)
        pdf.cell(EW, 6, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        dc(*C_STEEL); pdf.set_line_width(0.3)
        pdf.line(15, pdf.get_y(), 195, pdf.get_y())
        pdf.ln(2); tc(*C_TEXT_DARK)

    def kv_row(label, value, alt=False, col_w=65):
        v = _safe(value)
        if not v or v in ("N/A", "null", "None"):
            return
        fc(*C_ALT_ROW) if alt else fc(*C_WHITE)
        tc(*C_TEXT_LBL); pdf.set_font("Helvetica", "B", 8)
        pdf.cell(col_w, 6, " " + label, border="B", fill=True)
        tc(*C_TEXT_DARK); pdf.set_font("Helvetica", "", 8)
        pdf.cell(EW - col_w, 6, " " + v, border="B", fill=True,
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def draw_table_data(thead, tbody, col_widths):
        if not thead or not tbody:
            return
        # Header row
        fc(*C_DARK_BLUE); tc(*C_WHITE)
        pdf.set_font("Helvetica", "B", 8)
        for i, h in enumerate(thead):
            pdf.cell(col_widths[i], 7, f" {h}", border="B", fill=True)
        pdf.ln(7)
        # Body rows
        pdf.set_font("Helvetica", "", 7.5)
        for r_idx, row in enumerate(tbody):
            alt_row = (r_idx % 2 == 1)
            fc(*C_ALT_ROW) if alt_row else fc(*C_WHITE)
            tc(*C_TEXT_DARK)
            for i, val in enumerate(row):
                val_s = str(val).strip()
                pdf.cell(col_widths[i], 6, f" {val_s}", border="B", fill=True)
            pdf.ln(6)
        pdf.ln(3)

    def no_data(msg="No records found."):
        tc(*C_MID_GRAY); pdf.set_font("Helvetica", "I", 8)
        pdf.cell(EW, 5, "  " + msg, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(2); tc(*C_TEXT_DARK)

    def ok_row(text):
        tc(*C_GREEN); pdf.set_font("Helvetica", "B", 9)
        pdf.cell(EW, 6, "  [OK]  " + text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(1); tc(*C_TEXT_DARK)

    def warn_row(text):
        tc(*C_ORANGE); pdf.set_font("Helvetica", "B", 8.5)
        pdf.cell(EW, 6, "  [!]  " + _safe(text, 110),
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(1); tc(*C_TEXT_DARK)

    def desc_row(text):
        tc(*C_TEXT_LBL); pdf.set_font("Helvetica", "", 7.5)
        try:
            pdf.multi_cell(EW, 4, "  " + _safe(text, 500))
        except Exception:
            pass
        tc(*C_TEXT_DARK)

    # ── Cover page ───────────────────────────────────────────
    pdf.add_page()

    logo_path = os.path.join(os.path.dirname(__file__), "logo.png")
    if os.path.exists(logo_path):
        try:
            pdf.image(logo_path, x=15, y=15, h=12)
            pdf.ln(15)
        except Exception:
            tc(*C_DARK_BLUE); pdf.set_font("Helvetica", "B", 20)
            pdf.cell(14, 10, "VIN"); tc(*C_RED)
            pdf.cell(30, 10, "report", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(5)
    else:
        tc(*C_DARK_BLUE); pdf.set_font("Helvetica", "B", 20)
        pdf.cell(14, 10, "VIN"); tc(*C_RED)
        pdf.cell(30, 10, "report", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(5)

    pdf.set_font("Helvetica", "B", 8); tc(*C_MID_GRAY)
    pdf.cell(EW, 5, "POWERED BY GOODCAR",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="R")
    dc(*C_LIGHT_GRAY); pdf.set_line_width(0.5)
    pdf.line(15, pdf.get_y(), 195, pdf.get_y()); pdf.ln(6)

    year  = str(data.get("year",  "N/A"))
    make  = str(data.get("make",  "N/A"))
    model = str(data.get("model", "N/A"))
    target_vin_str = str(target_vin)

    fc(*C_DARK_BLUE); tc(*C_WHITE)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(EW, 11, f"  {year} {make} {model}",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT, fill=True)
    pdf.set_font("Helvetica", "", 9); tc(176, 196, 222)
    pdf.cell(EW, 7, f"  Vehicle History Report  |  VIN: {target_vin_str}",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT, fill=True)
    pdf.ln(6); tc(*C_TEXT_DARK)

    # ═══════════════════════════════════════════════════════
    # 1 - AUTO SPECIFICATIONS & MANUFACTURER INFO
    # ═══════════════════════════════════════════════════════
    section_header("Auto Specifications & Manufacturer")

    vds  = data.get("vehicle_data_specs", {})
    eng  = data.get("engine", {})
    trns = data.get("transmission", {})
    epa  = data.get("epa_mpg", {})
    std  = data.get("standard_specs", {})

    mini_header("General Info")
    gen_fields = [
        ("Year",            vds.get("year",            {}).get("txt") or year),
        ("Make / Model",    vds.get("make_model",      {}).get("txt") or (make + " " + model)),
        ("Trim",            vds.get("trim",            {}).get("txt")),
        ("Drive Type",      vds.get("drive_type",      {}).get("txt")),
        ("Color",           vds.get("color",           {}).get("txt")),
        ("Manufactured In", vds.get("manufactured_in", {}).get("txt")),
        ("Doors",           vds.get("doors",           {}).get("txt")),
        ("Seats",           vds.get("seats",           {}).get("txt")),
        ("Fuel Type",       vds.get("fuel_type",       {}).get("txt")),
    ]
    for i, (lbl, val) in enumerate(gen_fields):
        kv_row(lbl, val, alt=(i % 2 == 1))

    mini_header("Engine")
    eng_fields = [
        ("Engine",         vds.get("engine", {}).get("txt") or eng.get("Brand Name =>")),
        ("Engine Type",    eng.get("Engine type =>")),
        ("Engine Code",    eng.get("Engine Code =>")),
        ("Cylinders",      eng.get("Cylinders =>")),
        ("Displacement",   eng.get("Displacement =>")),
        ("Aspiration",     eng.get("Aspiration =>")),
        ("Block Type",     eng.get("Block Type =>")),
        ("Cam Type",       eng.get("Cam Type =>")),
        ("Fuel Induction", eng.get("Fuel Induction =>")),
        ("Valves",         eng.get("Valves =>")),
        ("Max Horsepower", eng.get("Max HP =>")),
        ("Max Torque",     eng.get("Max Torque =>")),
        ("Redline",        eng.get("Redline =>")),
        ("Oil Capacity",   eng.get("Oil Capacity =>")),
        ("Compression",    eng.get("Compression =>")),
        ("Bore",           eng.get("Bore =>")),
        ("Stroke",         eng.get("Stroke =>")),
    ]
    for i, (lbl, val) in enumerate(eng_fields):
        kv_row(lbl, val, alt=(i % 2 == 1))

    mini_header("Transmission")
    trn_fields = [
        ("Transmission", vds.get("transmissions", {}).get("txt") or trns.get("Brand Name =>")),
        ("Type",         trns.get("Type =>")),
        ("Detail Type",  trns.get("Detail Type =>")),
        ("Gears",        trns.get("Gears =>")),
    ]
    for i, (lbl, val) in enumerate(trn_fields):
        kv_row(lbl, val, alt=(i % 2 == 1))

    mini_header("Fuel Economy (EPA Estimates)")
    epa_fields = [
        ("City MPG",     epa.get("City =>")),
        ("Highway MPG",  epa.get("Highway =>")),
        ("Combined MPG", epa.get("Combined =>") or vds.get("mpg", {}).get("txt")),
        ("Fuel Grade",   epa.get("Fuel Grade =>")),
    ]
    for i, (lbl, val) in enumerate(epa_fields):
        kv_row(lbl, val, alt=(i % 2 == 1))

    weights = std.get("Weights and Capacities", {})
    dims    = std.get("Exterior Dimensions", {})
    if weights or dims:
        mini_header("Weights & Dimensions")
        wd_fields = [
            ("Curb Weight",          weights.get("Curb Weight")),
            ("Gross Vehicle Weight", weights.get("Gross Vehicle Weight Rating")),
            ("Fuel Tank Capacity",   weights.get("Fuel Tank Capacity")),
            ("Max Towing Capacity",  weights.get("Max Towing Capacity")),
            ("Max Payload",          weights.get("Max Payload")),
            ("Length",               dims.get("Length")),
            ("Width",                dims.get("Width")),
            ("Height",               dims.get("Height")),
            ("Wheelbase",            dims.get("Wheelbase")),
            ("Ground Clearance",     dims.get("Ground Clearance")),
        ]
        for i, (lbl, val) in enumerate(wd_fields):
            kv_row(lbl, val, alt=(i % 2 == 1))

    mfr_data = data.get("mfr", {})
    mfr_info = mfr_data.get("manufacturer", {})
    if mfr_info:
        mini_header("Manufacturer Information")
        kv_row("Brand Name", mfr_info.get("carBrand"), alt=False)
        kv_row("HQ Address", mfr_info.get("address"), alt=True)
        country = mfr_info.get("country")
        if country:
            kv_row("Country", country, alt=False)
        info_text = clean_html(mfr_info.get("info"))
        if info_text:
            pdf.ln(2)
            desc_row(info_text)

    # ═══════════════════════════════════════════════════════
    # 2 - MILEAGE HISTORY
    # ═══════════════════════════════════════════════════════
    section_header("Mileage / Odometer History")
    mileage = data.get("mileage", {})
    has_ml = False
    ml_fields = [
        ("Last Reported Mileage",     mileage.get("lastReportedMileage")),
        ("Estimated Current Mileage", mileage.get("estimatedMileage")),
    ]
    for i, (lbl, val) in enumerate(ml_fields):
        if val:
            kv_row(lbl, val, alt=(i % 2 == 1)); has_ml = True
    states = mileage.get("mileageRecordsStates", [])
    if states:
        kv_row("States with Records", ", ".join(str(s) for s in states), alt=True)
        has_ml = True
    if not has_ml:
        no_data("No mileage records available.")

    # ═══════════════════════════════════════════════════════
    # 3 - TITLE & OWNERSHIP HISTORY
    # ═══════════════════════════════════════════════════════
    section_header("Title Records & Ownership History")
    title_data  = data.get("title", {})
    ownerships  = title_data.get("ownerships", [])
    owner_count = title_data.get("itemsCount", len(ownerships))

    if ownerships:
        tc(*C_MED_BLUE); pdf.set_font("Helvetica", "B", 9)
        pdf.cell(EW, 6, "  " + str(owner_count) + " Owner(s) on Record (Registration Data)",
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(2); tc(*C_TEXT_DARK)
        for i, o in enumerate(ownerships):
            mini_header("Owner Record #" + str(i + 1))
            period = o.get("ownershipPeriod", {}) or {}
            yrs = period.get("years", 0) or 0
            mos = period.get("months", 0) or 0
            period_parts = []
            if yrs: period_parts.append(str(yrs) + " yr(s)")
            if mos: period_parts.append(str(mos) + " mo(s)")
            period_str = " ".join(period_parts)
            own_fields = [
                ("Purchase Year",    o.get("purchasedYear")),
                ("Title Issue Date", o.get("issueDateFormatted")),
                ("State",            o.get("state")),
                ("Odometer",         o.get("odometer")),
                ("Ownership Period", period_str or None),
            ]
            for j, (lbl, val) in enumerate(own_fields):
                kv_row(lbl, val, alt=(j % 2 == 1))
            pdf.ln(2)
    else:
        no_data("No registration title records available.")

    # Render section_title_ownership_history if available
    timeline_data = data.get("title_ownership_history", {})
    owners_timeline = timeline_data.get("ownershipTimeline", [])
    total_owners = timeline_data.get("totalOwnersCount")
    state_count = timeline_data.get("stateRegisteredCount")
    avg_own = timeline_data.get("averageOwnership")

    if total_owners or state_count or avg_own or owners_timeline:
        mini_header("Ownership Timeline Summary")
        if total_owners:
            kv_row("Total Owners on Record", str(total_owners), alt=False)
        if state_count:
            kv_row("States Registered In", str(state_count), alt=True)
        if avg_own:
            kv_row("Average Ownership Duration", str(avg_own), alt=False)

        if owners_timeline:
            pdf.ln(2)
            thead = ["Period", "State Registered", "Ownership Duration"]
            tbody = []
            for rec in owners_timeline:
                tbody.append([
                    rec.get("period", "N/A"),
                    rec.get("state", "N/A"),
                    rec.get("length", "N/A")
                ])
            draw_table_data(thead, tbody, [50, 60, 70])

    # ═══════════════════════════════════════════════════════
    # 4 - ACCIDENT & DAMAGE HISTORY
    # ═══════════════════════════════════════════════════════
    section_header("Accidents & Damage History")
    rows_v   = data.get("accidents_v", {}).get("rows", [])
    rows_a   = data.get("accidents_a", {}).get("rows", [])
    rows_main = data.get("accidents", {}).get("rows", [])
    acc_null = data.get("accidents_null", {})

    # Combine all accident rows
    combined_rows_a = list(rows_a) + list(rows_main)

    if not rows_v and not combined_rows_a:
        msg = acc_null.get("noHitMessage", "No accident records found.")
        ok_row("No Accidents Reported")
        if msg:
            desc_row(msg)
    else:
        if combined_rows_a:
            mini_header("Accident / Damage Records (" + str(len(combined_rows_a)) + " record(s))")
            for row in combined_rows_a:
                parts = [row.get("date", ""), row.get("state", ""), row.get("title", "")]
                hdr = "  |  ".join(p for p in parts if p)
                warn_row(hdr)
                if row.get("description"):
                    desc_row(row["description"])

        if rows_v:
            damage_cls = data.get("accidents_v", {}).get("damageClasses", "")
            mini_header("Detailed Wreck / Damage Classification (" + str(len(rows_v)) + " record(s))")
            if damage_cls:
                kv_row("Damage Classification", str(damage_cls))
            for row in rows_v:
                warn_row(f"Accident  |  {row.get('date') or ''}  |  State: {row.get('state') or ''}")
                tbl = row.get("table", {})
                alt_r = False
                for key in ["General Description", "Accident Type", "Nearest City",
                            "Specific Location", "Vehicle Damage Area 1",
                            "Vehicle Damage Level", "Initial Point of Impact",
                            "Vehicle Estimated Damage Amount", "Airbag Status",
                            "Vehicle Struck Fixed Object", "Police Agency Name"]:
                    v = tbl.get(key)
                    if v:
                        kv_row(key, v, alt=alt_r); alt_r = not alt_r
                pdf.ln(2)

    # ═══════════════════════════════════════════════════════
    # 5 - JUNK & SALVAGE RECORDS
    # ═══════════════════════════════════════════════════════
    section_header("Junk & Salvage Records")
    junk_records = data.get("junk", {}).get("junkAndSalvageRecords", [])
    if junk_records:
        for i, rec in enumerate(junk_records):
            mini_header("Record #" + str(i + 1))
            alt_r = False
            for k, v in rec.items():
                kv_row(k.replace(" =>", "").strip(), v, alt=alt_r); alt_r = not alt_r
            pdf.ln(2)
    else:
        ok_row("No Junk or Salvage Records Found")
    pdf.ln(2)

    # ═══════════════════════════════════════════════════════
    # 6 - INSURANCE LOSS RECORDS
    # ═══════════════════════════════════════════════════════
    section_header("Total Loss / Insurer Records")
    loss_records = data.get("loss", {}).get("insurersRecords", [])
    if loss_records:
        for i, rec in enumerate(loss_records):
            mini_header("Record #" + str(i + 1))
            alt_r = False
            for k, v in rec.items():
                kv_row(k.replace(" =>", "").strip(), v, alt=alt_r); alt_r = not alt_r
            pdf.ln(2)
    else:
        ok_row("No Insurance Total Loss Records Found")
    pdf.ln(2)

    # ═══════════════════════════════════════════════════════
    # 7 - TITLE PROBLEM CHECK
    # ═══════════════════════════════════════════════════════
    section_header("Problem Checks (Title Brands)")
    problem_rows = data.get("title_issues", {}).get("problemCheckRows", [])
    if problem_rows:
        for i, row in enumerate(problem_rows):
            title_t = row.get("title", "")
            desc    = row.get("description", "")
            date    = row.get("date", "")
            state   = row.get("state", "")
            hdr     = "  |  ".join(x for x in [title_t, date, state] if x) or "Title Record"
            fc(*C_ALT_ROW) if i % 2 else fc(*C_WHITE)
            tc(*C_TEXT_DARK); pdf.set_font("Helvetica", "B", 8)
            pdf.cell(EW, 5.5, "  " + _safe(hdr, 110), border="B", fill=True,
                     new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            if desc:
                desc_row(desc)
    else:
        ok_row("No Title Brand Brand Problems Found")
    pdf.ln(2)

    # ═══════════════════════════════════════════════════════
    # 8 - MARKET VALUES
    # ═══════════════════════════════════════════════════════
    section_header("Market Values")
    mv    = data.get("market_values", {})
    new_p = mv.get("newCarPrices") or mv.get("newCarPricesAlternative") or {}
    used_p = mv.get("usedCarPrices", {})

    if new_p:
        mini_header("Original New Car Prices")
        for i, (lbl, key) in enumerate([
            ("MSRP",               "msrp"),
            ("Invoice Price",      "invoice"),
            ("Equipped Retail",    "equippedRetail"),
            ("Destination Charge", "destinationCharge"),
        ]):
            kv_row(lbl, new_p.get(key), alt=(i % 2 == 1))

    if used_p:
        for hdr_txt, sub_key, labels in [
            ("Trade-In Value",  "tradeIn",
             [("Clean","clean"),("Average","average"),("Rough","rough")]),
            ("Retail Value",    "retail",
             [("Extra Clean","xclean"),("Clean","clean"),("Average","average"),("Rough","rough")]),
            ("Wholesale/Auction","wholesaleAuction",
             [("Extra Clean","xclean"),("Clean","clean"),("Average","average"),("Rough","rough")]),
        ]:
            sub = used_p.get(sub_key, {})
            if sub:
                mini_header(hdr_txt)
                for i, (lbl, key) in enumerate(labels):
                    kv_row(lbl, sub.get(key), alt=(i % 2 == 1))

    if not new_p and not used_p:
        no_data("No market value data available.")

    # ═══════════════════════════════════════════════════════
    # 9 - SALES HISTORY
    # ═══════════════════════════════════════════════════════
    section_header("Sales History")
    sales_records = data.get("sales", {}).get("salesHistoryRecords", [])
    if sales_records:
        for i, rec in enumerate(sales_records):
            mini_header("Sale #" + str(i + 1))
            add = rec.get("additionalInfo", {})
            sale_fields = [
                ("Found On",    rec.get("foundAt")),
                ("Year",        rec.get("year")),
                ("Price",       rec.get("price") or add.get("Price =>")),
                ("Mileage",     add.get("Miles =>")),
                ("Seller Type", add.get("Seller Type =>")),
                ("New / Used",  add.get("New/Used =>")),
                ("Seller Name", add.get("Seller Name =>")),
                ("Location",    add.get("City/State/Zip =>")),
                ("Data Source", add.get("Data Source =>")),
                ("First Seen",  add.get("First seen on =>")),
                ("Last Seen",   add.get("Last seen on =>")),
            ]
            for j, (lbl, val) in enumerate(sale_fields):
                kv_row(lbl, val, alt=(j % 2 == 1))
            pdf.ln(2)
    else:
        no_data("No sales history records available.")

    # ═══════════════════════════════════════════════════════
    # 10 - SAFETY RECALLS
    # ═══════════════════════════════════════════════════════
    section_header("Safety Recalls (NHTSA)")
    recalls     = data.get("recalls", {})
    recall_recs = recalls.get("recallRecords", [])
    if recalls.get("isNoHit") or not recall_recs:
        ok_row("No Open Recalls Found")
        msg = recalls.get("noHitMessage", "")
        if msg:
            desc_row(msg)
    else:
        for i, rec in enumerate(recall_recs):
            mini_header("Recall #" + str(i + 1))
            alt_r = False
            if isinstance(rec, dict):
                for k, v in rec.items():
                    kv_row(k.replace(" =>", "").strip(), str(v), alt=alt_r)
                    alt_r = not alt_r
            pdf.ln(2)

    # ═══════════════════════════════════════════════════════
    # 11 - SAFETY COMPLAINTS
    # ═══════════════════════════════════════════════════════
    section_header("Safety Complaints (NHTSA)")
    safety      = data.get("safety_complaints", {})
    safety_recs = safety.get("records", [])
    if safety.get("isNoHit") or not safety_recs:
        ok_row("No Safety Complaints Found")
        msg = safety.get("noHitMessage", "")
        if msg:
            desc_row(msg)
    else:
        for i, rec in enumerate(safety_recs):
            mini_header("Complaint #" + str(i + 1))
            alt_r = False
            if isinstance(rec, dict):
                for k, v in rec.items():
                    kv_row(k.replace(" =>", "").strip(), str(v), alt=alt_r)
                    alt_r = not alt_r
            pdf.ln(2)

    # ═══════════════════════════════════════════════════════
    # 12 - CRASH TEST RATINGS
    # ═══════════════════════════════════════════════════════
    section_header("Crash Test Ratings")
    crash_data = data.get("crash_test", {}).get("crashTest", {})
    rating_map = [
        ("Front Overall =>",          "Front Overall"),
        ("Front/Driver =>",           "Front / Driver"),
        ("Front/Passenger =>",        "Front / Passenger"),
        ("Side Overall =>",           "Side Overall"),
        ("Side Barrier Driver =>",    "Side Barrier (Driver)"),
        ("Side Barrier Passenger =>", "Side Barrier (Passenger)"),
        ("Side Pole Driver =>",       "Side Pole (Driver)"),
        ("Side Combined Front =>",    "Side Combined (Front)"),
        ("Side Combined Rear =>",     "Side Combined (Rear)"),
        ("Rollover =>",               "Rollover Rating"),
    ]
    has_crash = False
    for i, (api_k, lbl) in enumerate(rating_map):
        v = crash_data.get(api_k)
        if v:
            kv_row(lbl, v, alt=(i % 2 == 1)); has_crash = True
    if not has_crash:
        no_data("No crash test data available.")

    # ═══════════════════════════════════════════════════════
    # 13 - AWARDS & ACCOLADES
    # ═══════════════════════════════════════════════════════
    section_header("Awards & Accolades")
    awards_data = data.get("awards", {})
    awards_recs = awards_data.get("awardsAndAccoladesRecords", {})
    if awards_recs:
        for award_name, award_info in awards_recs.items():
            mini_header(award_name)
            source = award_info.get("Source", "N/A")
            website = award_info.get("Website", "N/A")
            snippet = clean_html(award_info.get("Snippet", ""))
            kv_row("Source", source, alt=False)
            if website and website != "N/A":
                kv_row("Website", clean_html(website), alt=True)
            if snippet and snippet != "N/A":
                desc_row(snippet)
            pdf.ln(1)
    else:
        no_data("No awards or accolades found for this vehicle.")

    # ═══════════════════════════════════════════════════════
    # 14 - RECOMMENDED MAINTENANCE SCHEDULE
    # ═══════════════════════════════════════════════════════
    section_header("Recommended Maintenance Schedule")
    maint_recs = data.get("maintenance", {}).get("maintenanceRecords", [])
    if maint_recs:
        cats = {}
        for rec in maint_recs:
            cat = rec.get("category", "General")
            cats.setdefault(cat, []).append(rec)
        for cat_name, items in cats.items():
            mini_header(cat_name)
            for j, item in enumerate(items):
                maint_name = item.get("maintenance", "")
                interval   = item.get("interval", "")
                notes      = item.get("notes", "")
                val_parts  = [x for x in [interval, notes] if x]
                kv_row(maint_name, " -- ".join(val_parts) if val_parts else "",
                       alt=(j % 2 == 1))
            pdf.ln(2)
    else:
        no_data("No maintenance schedule data available.")

    # ═══════════════════════════════════════════════════════
    # 15 - SAFETY EQUIPMENT
    # ═══════════════════════════════════════════════════════
    section_header("Safety Equipment")
    safety_eq = data.get("safety_equipment", {})
    if safety_eq:
        for cat_name, cat_items in safety_eq.items():
            mini_header(cat_name)
            alt_r = False
            if isinstance(cat_items, dict):
                for k, v in cat_items.items():
                    if v and str(v).strip() not in ("", "N/A", "null", "None"):
                        kv_row(k, str(v), alt=alt_r); alt_r = not alt_r
            pdf.ln(1)
    else:
        no_data("No safety equipment data available.")

    # ═══════════════════════════════════════════════════════
    # 16 - WARRANTIES
    # ═══════════════════════════════════════════════════════
    section_header("Warranties")
    warr_data = data.get("warranties", {})
    warr_table = warr_data.get("warrantiesTable", {})
    warr_thead = warr_table.get("thead", [])
    warr_tbody = warr_table.get("tbody", [])
    if warr_thead and warr_tbody:
        draw_table_data(warr_thead, warr_tbody, [60, 40, 40, 40])
    else:
        no_data("No warranties information available.")

    # ═══════════════════════════════════════════════════════
    # 17 - COST OF OWNERSHIP
    # ═══════════════════════════════════════════════════════
    section_header("Cost of Ownership")
    cost_data = data.get("cost_ownership", {})
    cost_state = cost_data.get("costState")
    cost_table = cost_data.get("costTable", {})
    cost_thead = cost_table.get("thead", [])
    cost_tbody = cost_table.get("tbody", [])
    if cost_state:
        kv_row("Cost Estimate State", cost_state)
        pdf.ln(2)
    if cost_thead and cost_tbody:
        draw_table_data(cost_thead, cost_tbody, [40, 23, 23, 23, 23, 23, 25])
    else:
        no_data("No cost of ownership information available.")

    # ═══════════════════════════════════════════════════════
    # 18 - LOCATION HISTORY
    # ═══════════════════════════════════════════════════════
    section_header("Location History")
    loc_data = data.get("location", {})
    loc_table = loc_data.get("locationHistoryTable", {})
    loc_tbody = loc_table.get("tbody", [])
    if loc_tbody:
        for idx, row in enumerate(loc_tbody):
            state = row[0] if len(row) > 0 else "N/A"
            date = row[1] if len(row) > 1 else "N/A"
            notice = clean_html(row[2]) if len(row) > 2 else ""
            warn_row(f"{state}  |  Date: {date}")
            if notice:
                desc_row(notice)
            pdf.ln(1)
    else:
        no_data("No location history available.")

    # ── NMVTIS DISCLAIMER ────────────────────────────────────
    pdf.ln(6)
    pdf.set_font("Helvetica", "B", 8); tc(*C_TEXT_LBL)
    pdf.cell(EW, 5, "NMVTIS Disclaimer", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 7); tc(*C_MID_GRAY)
    pdf.multi_cell(EW, 3.8, (
        "The National Motor Vehicle Title Information System (NMVTIS) is an electronic system that "
        "contains information on certain automobiles titled in the United States. NMVTIS is intended "
        "to serve as a reliable source of title and brand history, but it does not contain detailed "
        "information regarding a vehicle's repair history. Federal law requires insurers, junk and "
        "salvage yards, and certain other entities to provide information to NMVTIS. This report is "
        "provided for informational purposes only."
    ))

    return pdf.output()

# ─────────────────────────────────────────────────────────────
# EMAIL DELIVERY
# ─────────────────────────────────────────────────────────────

def send_vin_report(target_vin, customer_email, data):
    logo_path = os.path.join(os.path.dirname(__file__), 'logo.png')
    has_logo  = os.path.exists(logo_path)

    if has_logo:
        logo_html = '<img src="cid:logo" alt="VINreport Logo" style="height: 60px; max-width: 250px; object-fit: contain;">'
    else:
        logo_html = '<h1 style="margin:0;font-family:Arial,sans-serif;font-size:28px;color:#0d2c54;">VIN<span style="color:#d81e1e;">report</span></h1>'

    year   = str(data.get("year",        "N/A"))
    make   = str(data.get("make",        "N/A"))
    model  = str(data.get("model",       "N/A"))
    engine = str(data.get("engine_type", "N/A"))

    mileage    = data.get("mileage", {})
    last_miles = str(mileage.get("lastReportedMileage", "N/A"))
    est_miles  = str(mileage.get("estimatedMileage",    "N/A"))

    recalls    = data.get("recalls", {})
    recall_cnt = recalls.get("itemsCount", 0) or 0
    recall_badge = (
        '<span style="color:#c0392b;font-weight:bold;">' + str(recall_cnt) + ' Recall(s)</span>'
        if recall_cnt else
        '<span style="color:#27ae60;font-weight:bold;">No Open Recalls</span>'
    )

    acc_count = len(data.get("accidents_v", {}).get("rows", [])) + \
                len(data.get("accidents_a", {}).get("rows", [])) + \
                len(data.get("accidents", {}).get("rows", []))
    acc_badge = (
        '<span style="color:#c0392b;font-weight:bold;">' + str(acc_count) + ' Accident Record(s)</span>'
        if acc_count else
        '<span style="color:#27ae60;font-weight:bold;">No Accidents Reported</span>'
    )

    html_report = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body{font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;background-color:#f4f6f9;margin:0;padding:0;color:#333}
            .container{max-width:600px;margin:20px auto;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 4px 10px rgba(0,0,0,.05);border:1px solid #e1e8ed}
            .header{padding:30px;border-bottom:1px solid #f0f3f6}
            .badge{font-size:11px;color:#7a8b9a;text-transform:uppercase;letter-spacing:1px;margin:0;font-weight:bold}
            .content{padding:30px}
            .hero{background:linear-gradient(135deg,#0d2c54 0%,#1b497e 100%);color:#fff;padding:30px;border-radius:6px;margin-bottom:25px;text-align:center}
            .hero h2{margin:0 0 10px;font-size:24px;font-weight:600}
            .hero p{margin:0;font-size:14px;color:#b0c4de;letter-spacing:.5px}
            .summary-grid{display:flex;gap:12px;margin-bottom:25px}
            .summary-card{flex:1;background:#f8fafc;border:1px solid #e1e8ed;border-radius:6px;padding:15px;text-align:center}
            .summary-card .label{font-size:11px;color:#7a8b9a;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
            .summary-card .value{font-size:14px;font-weight:700;color:#0d2c54}
            .section-title{font-size:16px;font-weight:bold;color:#0d2c54;margin-top:25px;margin-bottom:12px;text-transform:uppercase;letter-spacing:.5px;border-bottom:2px solid #f0f3f6;padding-bottom:8px}
            .specs-table{width:100%;border-collapse:collapse;margin-bottom:20px}
            .specs-table td{padding:10px;border-bottom:1px solid #f0f3f6;font-size:14px}
            .specs-label{font-weight:600;color:#5a6e85;width:40%}
            .specs-value{color:#2c3e50}
            .footer{background:#f8fafc;padding:20px 30px;border-top:1px solid #f0f3f6;font-size:11px;color:#7f8c8d;line-height:1.6}
            .disclaimer-title{font-weight:bold;margin-bottom:5px;color:#555}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div style="display:inline-block;vertical-align:middle">""" + logo_html + """</div>
                <div style="display:inline-block;vertical-align:middle;float:right;margin-top:15px"><p class="badge">Powered by GoodCar</p></div>
            </div>
            <div class="content">
                <div class="hero">
                    <h2>""" + year + " " + make + " " + model + """</h2>
                    <p>Vehicle History Report &nbsp;|&nbsp; VIN: <strong style="color:#fff">""" + target_vin + """</strong></p>
                </div>
                <div class="summary-grid">
                    <div class="summary-card">
                        <div class="label">Last Mileage</div>
                        <div class="value">""" + str(last_miles) + """</div>
                    </div>
                    <div class="summary-card">
                        <div class="label">Accidents</div>
                        <div class="value">""" + acc_badge + """</div>
                    </div>
                    <div class="summary-card">
                        <div class="label">Recalls</div>
                        <div class="value">""" + recall_badge + """</div>
                    </div>
                </div>
                <h3 class="section-title">Specifications</h3>
                <table class="specs-table">
                    <tr><td class="specs-label">Year</td><td class="specs-value">""" + str(year) + """</td></tr>
                    <tr><td class="specs-label">Make</td><td class="specs-value">""" + str(make) + """</td></tr>
                    <tr><td class="specs-label">Model</td><td class="specs-value">""" + str(model) + """</td></tr>
                    <tr><td class="specs-label">Engine</td><td class="specs-value">""" + str(engine) + """</td></tr>
                    <tr><td class="specs-label">Est. Mileage</td><td class="specs-value">""" + str(est_miles) + """</td></tr>
                </table>
                <p style="font-size:13px;color:#555;margin-top:0">
                    Your full <strong>Vehicle History Report</strong> is attached as a PDF. It includes
                    detailed accident history, title &amp; ownership records, market values, safety recalls,
                    crash test ratings, maintenance schedule, and more.
                </p>
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

    msg = MIMEMultipart('related')
    msg['From']    = SMTP_EMAIL
    msg['To']      = customer_email
    msg['Subject'] = "Your VINreport for VIN: " + target_vin

    msg_alternative = MIMEMultipart('alternative')
    msg.attach(msg_alternative)
    msg_alternative.attach(MIMEText(html_report, 'html'))

    if has_logo:
        try:
            with open(logo_path, 'rb') as f:
                msg_image = MIMEImage(f.read())
            msg_image.add_header('Content-ID', '<logo>')
            msg_image.add_header('Content-Disposition', 'inline', filename='logo.png')
            msg.attach(msg_image)
        except Exception as img_err:
            print("Failed to attach inline logo: " + str(img_err))

    try:
        pdf_bytes = generate_pdf_report(target_vin, data)
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(bytes(pdf_bytes))
        encoders.encode_base64(part)
        part.add_header('Content-Disposition',
                        'attachment; filename="VINreport_' + target_vin + '.pdf"')
        msg.attach(part)
    except Exception as pdf_err:
        print("Failed to generate or attach PDF: " + str(pdf_err))

    if SMTP_USE_SSL:
        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
    else:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.starttls()
    server.login(SMTP_EMAIL, SMTP_PASSWORD)
    server.sendmail(SMTP_EMAIL, customer_email, msg.as_string())
    server.quit()

# ─────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────

@app.route('/etsy-webhook', methods=['POST'])
def handle_etsy_order():
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
        return jsonify({"status": "error", "message": "Failed to parse"}), 400

    target_vin     = vin_match.group(1)
    customer_email = email_match.group(0)

    goodcar_url     = 'https://goodcar.com/business/api/vin-report-comprehensive'
    goodcar_headers = {'Authorization': 'Bearer ' + GOODCAR_API_KEY}
    goodcar_payload = {'vin': target_vin}

    try:
        car_response = requests.post(goodcar_url, headers=goodcar_headers, data=goodcar_payload)
        car_response.raise_for_status()
        data = extract_all_data(car_response.json())
    except Exception as e:
        return jsonify({"status": "failed",
                        "error": "GoodCar API call failed: " + str(e)}), 500

    try:
        send_vin_report(target_vin, customer_email, data)
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "failed",
                        "error": "Email sending failed: " + str(e)}), 500

@app.route('/test-report', methods=['GET', 'POST'])
def test_report():
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

    goodcar_url     = 'https://goodcar.com/business/api/vin-report-comprehensive'
    goodcar_headers = {'Authorization': 'Bearer ' + GOODCAR_API_KEY}
    goodcar_payload = {'vin': target_vin}

    try:
        car_response = requests.post(goodcar_url, headers=goodcar_headers, data=goodcar_payload)
        car_response.raise_for_status()
        data = extract_all_data(car_response.json())
    except Exception as e:
        return jsonify({"status": "failed",
                        "error": "GoodCar API call failed: " + str(e)}), 500

    try:
        send_vin_report(target_vin, customer_email, data)
        return jsonify({"status": "success",
                        "message": "Test report for VIN " + target_vin + " sent to " + customer_email}), 200
    except Exception as e:
        return jsonify({"status": "failed",
                        "error": "Email sending failed: " + str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
