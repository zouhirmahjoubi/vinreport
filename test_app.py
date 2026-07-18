import unittest
from unittest.mock import patch, MagicMock, ANY
import os
import json
import io
import shutil

# Set dummy environment variables before importing app
os.environ["DOKPLOY_APP_URL"] = "https://vinreport.odysseusai.ai"
os.environ["ETSY_OAUTH_TOKEN"] = "dummy_etsy_oauth"
os.environ["ETSY_API_KEY"] = "dummy_etsy_key"
os.environ["SMTP_EMAIL"] = "test@example.com"
os.environ["SMTP_PASSWORD"] = "dummy_password"
os.environ["SMTP_HOST"] = "smtp.example.com"
os.environ["SMTP_PORT"] = "587"
os.environ["SMTP_USE_SSL"] = "false"
os.environ["MARKETCHECK_API_KEY"] = "dummy_marketcheck_key"

from app import app, send_vin_report, fetch_vehicle_data, generate_pdf_report

def mock_requests_get(url, *args, **kwargs):
    response = MagicMock()
    params = kwargs.get('params', {})
    
    if "api.etsy.com" in url or "receipts" in url:
        response.status_code = 200
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "transactions": [
                {
                    "property_values": [
                        {
                            "property_name": "Personalization",
                            "values": ["VIN: 1HGCR2F81HA000000, Email: customer@example.com"]
                        }
                    ]
                }
            ]
        }
    elif "vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues" in url:
        response.status_code = 200
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "Results": [{
                "ModelYear": "2017",
                "Make": "HONDA",
                "Model": "Accord",
                "Trim": "EX-L",
                "BodyClass": "Sedan/Saloon",
                "EngineHP": "185",
                "DisplacementL": "2.4",
                "EngineConfiguration": "In-Line",
                "EngineCylinders": "4",
                "DriveType": "4x2",
                "TransmissionStyle": "Continuously Variable Transmission (CVT)",
                "FuelTypePrimary": "Gasoline",
                "PlantCity": "MARYSVILLE",
                "PlantState": "OHIO",
                "PlantCountry": "UNITED STATES (USA)"
            }]
        }
    elif "api.nhtsa.gov/recalls/recallsByVehicle" in url:
        response.status_code = 200
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "Count": 0,
            "Message": "Results returned successfully",
            "results": []
        }
    elif "marketcheck-prod.apigee.net/v2/history/car" in url:
        response.status_code = 200
        response.json.return_value = [
            {
                "scraped_at_date": "2023-05-12",
                "miles": 45200,
                "price": 18900,
                "dealer": {
                    "name": "Honda Auto Center",
                    "city": "Columbus",
                    "state": "OH"
                }
            }
        ]
    else:
        response.status_code = 404
        
    return response

class TestVinReportApp(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
        # Ensure clean reports dir for tests
        if os.path.exists('reports'):
            shutil.rmtree('reports')
        os.makedirs('reports', exist_ok=True)

    def tearDown(self):
        if os.path.exists('reports'):
            shutil.rmtree('reports')

    @patch('smtplib.SMTP')
    def test_send_vin_report_no_logo(self, mock_smtp):
        mock_server = MagicMock()
        mock_smtp.return_value = mock_server

        # Ensure logo.png does not exist for this test
        logo_path = os.path.join(os.path.dirname(__file__), 'logo.png')
        logo_existed = os.path.exists(logo_path)
        if logo_existed:
            os.rename(logo_path, logo_path + '.tmp')

        # Create a dummy pdf file on disk since send_vin_report expects it
        report_id = "test_report_id"
        pdf_path = os.path.join('reports', f"{report_id}.pdf")
        with open(pdf_path, 'wb') as f:
            f.write(b"%PDF-1.4 mock pdf content")

        try:
            attributes = {
                'ModelYear': '2017',
                'Make': 'HONDA',
                'Model': 'Accord'
            }
            send_vin_report("1HGCR2F81HA000000", "customer@example.com", report_id, attributes)
            
            # Verify SMTP calls
            mock_smtp.assert_called_once_with("smtp.example.com", 587)
            mock_server.starttls.assert_called_once()
            mock_server.login.assert_called_once_with("test@example.com", "dummy_password")
            mock_server.sendmail.assert_called_once()
            mock_server.quit.assert_called_once()
        finally:
            if logo_existed:
                os.rename(logo_path + '.tmp', logo_path)

    @patch('requests.get', side_effect=mock_requests_get)
    @patch('app.send_vin_report')
    def test_handle_etsy_webhook_success(self, mock_send_email, mock_get):
        webhook_payload = {
            "event_type": "order.paid",
            "resource_url": "https://api.etsy.com/v3/application/receipts/12345"
        }
        response = self.app.post('/etsy-webhook', 
                                 data=json.dumps(webhook_payload),
                                 content_type='application/json')
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "success"})

        # Verify Etsy API was called
        mock_get.assert_any_call(
            "https://api.etsy.com/v3/application/receipts/12345",
            headers={
                "x-api-key": "dummy_etsy_key",
                "Authorization": "Bearer dummy_etsy_oauth"
            }
        )

        # Verify Email was sent with correct attributes
        mock_send_email.assert_called_once()
        call_args = mock_send_email.call_args[0]
        self.assertEqual(call_args[0], '1HGCR2F81HA000000')
        self.assertEqual(call_args[1], 'customer@example.com')
        # Check that attributes are present
        self.assertEqual(call_args[3]['Make'], 'HONDA')
        self.assertEqual(call_args[3]['Model'], 'Accord')

    def test_handle_etsy_webhook_ignored_event(self):
        webhook_payload = {
            "event_type": "order.updated",
            "resource_url": "https://api.etsy.com/v3/application/receipts/12345"
        }
        response = self.app.post('/etsy-webhook', 
                                 data=json.dumps(webhook_payload),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "ignored"})

    @patch('requests.get')
    def test_handle_etsy_webhook_no_personalization(self, mock_get):
        mock_receipt_response = MagicMock()
        mock_receipt_response.json.return_value = {
            "transactions": [
                {
                    "property_values": [
                        {
                            "property_name": "Gift Message",
                            "values": ["Happy Birthday!"]
                        }
                    ]
                }
            ]
        }
        mock_get.return_value = mock_receipt_response

        webhook_payload = {
            "event_type": "order.paid",
            "resource_url": "https://api.etsy.com/v3/application/receipts/12345"
        }
        response = self.app.post('/etsy-webhook', 
                                 data=json.dumps(webhook_payload),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertIn("No personalization details found", response.json.get("message", ""))

    @patch('requests.get')
    def test_handle_etsy_webhook_invalid_personalization(self, mock_get):
        mock_receipt_response = MagicMock()
        mock_receipt_response.json.return_value = {
            "transactions": [
                {
                    "property_values": [
                        {
                            "property_name": "Personalization",
                            "values": ["Just a note, no VIN or email here!"]
                        }
                    ]
                }
            ]
        }
        mock_get.return_value = mock_receipt_response

        webhook_payload = {
            "event_type": "order.paid",
            "resource_url": "https://api.etsy.com/v3/application/receipts/12345"
        }
        response = self.app.post('/etsy-webhook', 
                                 data=json.dumps(webhook_payload),
                                 content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertIn("Failed to parse", response.json.get("message", ""))

    def test_download_report_success(self):
        report_id = "test_download_success_id"
        pdf_path = os.path.join('reports', f"{report_id}.pdf")
        with open(pdf_path, 'wb') as f:
            f.write(b"%PDF-1.4 mock pdf content")

        response = self.app.get(f'/download/{report_id}')
        try:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content_type, 'application/pdf')
            self.assertEqual(response.data, b"%PDF-1.4 mock pdf content")
            self.assertIn(f"attachment; filename=Vehicle_History_Report_{report_id}.pdf", response.headers.get("Content-Disposition", ""))
        finally:
            response.close()

    def test_download_report_not_found(self):
        response = self.app.get('/download/nonexistent_id')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json.get("status"), "failed")
        self.assertIn("Report expired, unavailable, or not found", response.json.get("error", ""))

    @patch('requests.get', side_effect=mock_requests_get)
    @patch('app.send_vin_report')
    def test_test_report_endpoint(self, mock_send_email, mock_get):
        response = self.app.get('/test-report?vin=1HGCR2F81HA000000&email=test_customer@example.com')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json.get("status"), "success")

        # Verify email function was called
        mock_send_email.assert_called_once()
        call_args = mock_send_email.call_args[0]
        self.assertEqual(call_args[0], '1HGCR2F81HA000000')
        self.assertEqual(call_args[1], 'test_customer@example.com')
        self.assertEqual(call_args[3]['Make'], 'HONDA')

if __name__ == '__main__':
    unittest.main()
