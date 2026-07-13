# Etsy VINreport Automation Receiver

A production-ready Flask server to automatically process Etsy order webhooks, retrieve personalization data (VIN & Email), query the GoodCar API, and deliver a comprehensive HTML vehicle report via email.

Designed to be easily deployed on **Dokploy** (using Nixpacks or Docker).

---

## Repository Structure

1. **[app.py](file:///d:/etsy%20vinreport/app.py)**: The main Flask application extracting configuration from environment variables, receiving webhooks, querying GoodCar, and sending SMTP emails.
2. **[requirements.txt](file:///d:/etsy%20vinreport/requirements.txt)**: Specifies project dependencies including Flask, requests, and gunicorn.
3. **[Procfile](file:///d:/etsy%20vinreport/Procfile)**: Tells Nixpacks how to run the web application.
4. **[.gitignore](file:///d:/etsy%20vinreport/.gitignore)**: Prevents accidental check-in of environment variables and Python cache files.

---

## Deployment Steps on Dokploy

### Step 1: Push to Git
1. Create a private repository on GitHub, GitLab, or your preferred Git provider.
2. Commit and push these files to the repository.

### Step 2: Configure the App in Dokploy
1. Open your Dokploy Dashboard.
2. Create or open a Project, then click **Add Application**.
3. Select **Git**, link your repository provider, and select the repository branch.
4. Under **Build Type**, select **Nixpacks** (Dokploy's default, which automatically detects Python using the `requirements.txt` and runs the start command defined in the `Procfile`).

### Step 3: Set Environment Variables
Navigate to your application's **Environment Variables** tab in Dokploy and add the following keys:

| Environment Variable | Description |
| :--- | :--- |
| `GOODCAR_API_KEY` | Your GoodCar API Key |
| `ETSY_OAUTH_TOKEN` | Your Etsy OAuth Access Token |
| `ETSY_API_KEY` | Your Etsy Keystring API Key |
| `SMTP_EMAIL` | Your sending email address (e.g. Gmail) |
| `SMTP_PASSWORD` | Your SMTP Email password or Gmail App Password |

### Step 4: Expose Domain & Connect Etsy Webhook
1. Navigate to the **Domains** tab inside your Dokploy application setup.
2. Click **Add Domain** and fill in your custom routing link (e.g., `api.vinreport.com`).
3. Map the target **Container Port** to `5000`.
4. Click **Save**. Dokploy will automatically configure Traefik and issue a free Let’s Encrypt HTTPS/SSL certificate.
