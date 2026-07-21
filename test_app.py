import unittest
from unittest.mock import patch, MagicMock, ANY
import os
import json

# Set dummy environment variables before importing app
os.environ["GOODCAR_API_KEY"] = "dummy_goodcar_key"
os.environ["ETSY_OAUTH_TOKEN"] = "dummy_etsy_oauth"
os.environ["ETSY_API_KEY"] = "dummy_etsy_key"
os.environ["SMTP_EMAIL"] = "test@example.com"
os.environ["SMTP_PASSWORD"] = "dummy_password"
os.environ["SMTP_HOST"] = "smtp.example.com"
os.environ["SMTP_PORT"] = "587"
os.environ["SMTP_USE_SSL"] = "false"

from app import app, generate_pdf_report, send_vin_report

class TestVinReportApp(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True

    def test_generate_pdf_report_no_logo(self):
        # Ensure logo.png does not exist for this test
        logo_path = os.path.join(os.path.dirname(__file__), 'logo.png')
        logo_existed = os.path.exists(logo_path)
        if logo_existed:
            os.rename(logo_path, logo_path + '.tmp')

        try:
            specs = {
                'year': 2020,
                'make': 'Toyota',
                'model': 'Camry',
                'engine_type': 'V6'
            }
            pdf_bytes = generate_pdf_report("1HGCR2F81HA000000", specs)
            self.assertIsInstance(pdf_bytes, (bytes, bytearray))
            self.assertTrue(len(pdf_bytes) > 0)
            self.assertIn(b"/Count 3", pdf_bytes)
        finally:
            if logo_existed:
                os.rename(logo_path + '.tmp', logo_path)

    def test_generate_pdf_report_vinchk(self):
        specs = {
            'year': 2020,
            'make': 'Toyota',
            'model': 'Camry',
            'engine_type': 'V6'
        }
        pdf_bytes = generate_pdf_report("1HGCR2F81HA000000", specs, template="vinchk")
        self.assertIsInstance(pdf_bytes, (bytes, bytearray))
        self.assertTrue(len(pdf_bytes) > 0)
        self.assertIn(b"/Count 1", pdf_bytes)

    def test_generate_pdf_report_vinreport(self):
        specs = {
            'year': 2020,
            'make': 'Toyota',
            'model': 'Camry',
            'engine_type': 'V6'
        }
        pdf_bytes = generate_pdf_report("1HGCR2F81HA000000", specs, template="vinreport")
        self.assertIsInstance(pdf_bytes, (bytes, bytearray))
        self.assertTrue(len(pdf_bytes) > 0)
        self.assertIn(b"/Count 3", pdf_bytes)

    def test_generate_pdf_report_silverado_carfax(self):
        specs = {
            'year': 2007,
            'make': 'Chevrolet',
            'model': 'Silverado 1500',
            'engine_type': 'V8'
        }
        pdf_bytes = generate_pdf_report("2GCEC19J471591320", specs, template="vinreport")
        self.assertIsInstance(pdf_bytes, (bytes, bytearray))
        self.assertTrue(len(pdf_bytes) > 0)
        self.assertIn(b"/Count 7", pdf_bytes)

    @patch('smtplib.SMTP')
    def test_send_vin_report_no_logo(self, mock_smtp):
        # Mock SMTP server
        mock_server = MagicMock()
        mock_smtp.return_value = mock_server

        # Ensure logo.png does not exist for this test
        logo_path = os.path.join(os.path.dirname(__file__), 'logo.png')
        logo_existed = os.path.exists(logo_path)
        if logo_existed:
            os.rename(logo_path, logo_path + '.tmp')

        try:
            specs = {
                'year': 2020,
                'make': 'Toyota',
                'model': 'Camry',
                'engine_type': 'V6'
            }
            send_vin_report("1HGCR2F81HA000000", "customer@example.com", "test_report_id", specs)
            
            # Verify SMTP was called
            mock_smtp.assert_called_once_with("smtp.example.com", 587)
            mock_server.starttls.assert_called_once()
            mock_server.login.assert_called_once_with("test@example.com", "dummy_password")
            mock_server.sendmail.assert_called_once()
            mock_server.quit.assert_called_once()
        finally:
            if logo_existed:
                os.rename(logo_path + '.tmp', logo_path)

    @patch('requests.get')
    @patch('requests.post')
    @patch('app.send_vin_report')
    def test_handle_etsy_webhook_success(self, mock_send_email, mock_post, mock_get):
        # Mock Etsy receipt response
        mock_receipt_response = MagicMock()
        mock_receipt_response.json.return_value = {
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
        mock_get.return_value = mock_receipt_response

        # Mock GoodCar API response
        mock_car_response = MagicMock()
        mock_car_response.json.return_value = {
            "content": {
                "main": {
                    "vehicleDataRaw": {
                        "make": "Toyota",
                        "model": "Camry",
                        "year": "2020"
                    }
                },
                "section_specs": {
                    "engine": {
                        "Brand Name": "V6"
                    }
                }
            }
        }
        mock_post.return_value = mock_car_response

        # Call Etsy Webhook endpoint
        webhook_payload = {
            "event_type": "order.paid",
            "resource_url": "https://api.etsy.com/v3/application/receipts/12345"
        }
        response = self.app.post('/etsy-webhook', 
                                 data=json.dumps(webhook_payload),
                                 content_type='application/json')
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "success"})

        # Verify Etsy API was called with correct headers
        mock_get.assert_called_once_with(
            "https://api.etsy.com/v3/application/receipts/12345",
            headers={
                "x-api-key": "dummy_etsy_key",
                "Authorization": "Bearer dummy_etsy_oauth"
            }
        )

        # Verify GoodCar API was called
        mock_post.assert_called_once_with(
            'https://goodcar.com/business/api/vin-report-comprehensive',
            headers={'Authorization': 'Bearer dummy_goodcar_key'},
            data={'vin': '1HGCR2F81HA000000'}
        )

        # Verify Email was sent
        mock_send_email.assert_called_once_with(
            '1HGCR2F81HA000000',
            'customer@example.com',
            ANY,
            ANY,
            template=ANY
        )

    def test_handle_etsy_webhook_ignored_event(self):
        # Call Etsy Webhook with ignored event type
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
        # Mock Etsy receipt response with no personalization
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
        # Mock Etsy receipt response with invalid VIN and email format
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

    @patch('requests.post')
    @patch('app.send_vin_report')
    def test_test_report_endpoint(self, mock_send_email, mock_post):
        # Mock GoodCar API response
        mock_car_response = MagicMock()
        mock_car_response.json.return_value = {
            "content": {
                "main": {
                    "vehicleDataRaw": {
                        "make": "Honda",
                        "model": "Accord",
                        "year": "2018"
                    }
                },
                "section_specs": {
                    "engine": {
                        "Brand Name": "I4"
                    }
                }
            }
        }
        mock_post.return_value = mock_car_response

        response = self.app.get('/test-report?vin=1HGCR2F81HA000000&email=test_customer@example.com')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json.get("status"), "success")

        # Verify GoodCar API was called
        mock_post.assert_called_once_with(
            'https://goodcar.com/business/api/vin-report-comprehensive',
            headers={'Authorization': 'Bearer dummy_goodcar_key'},
            data={'vin': '1HGCR2F81HA000000'}
        )

        # Verify email function was called
        mock_send_email.assert_called_once_with(
            '1HGCR2F81HA000000',
            'test_customer@example.com',
            ANY,
            ANY,
            template=ANY
        )

    @patch('requests.post')
    def test_owner_by_vin_endpoint(self, mock_post):
        mock_owner_response = MagicMock()
        mock_owner_response.status_code = 200
        mock_owner_response.json.return_value = {
            "status": 200,
            "content": [
                {
                    "first_name": "MATTHEW",
                    "last_name": "GONZALEZ",
                    "full_address": "877 TAYLOR AVE APT 3E",
                    "city": "BRONX",
                    "state": "NY",
                    "zip": "10473",
                    "mileage_code": "Y",
                    "email": "MATTHEWJOSEPHGONZALEZ62@GMAIL.COM",
                    "phone": 9179827451
                }
            ],
            "remainingBalance": 18.66
        }
        mock_post.return_value = mock_owner_response

        response = self.app.get('/owner-by-vin?vin=2GCEC19J471591320')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json.get("status"), 200)
        self.assertEqual(len(response.json.get("content", [])), 1)
        self.assertEqual(response.json["content"][0]["first_name"], "MATTHEW")

        mock_post.assert_called_once_with(
            'https://goodcar.com/business/api/vin-to-owners',
            headers={'Authorization': 'Bearer dummy_goodcar_key'},
            data={'vin': '2GCEC19J471591320'}
        )

if __name__ == '__main__':
    unittest.main()
