# Hetzner Static Site Deployment

This deployment keeps GitHub for source code only. The bot renders publications
directly into a directory served by nginx on the Hetzner server.

## Target Layout

```text
/opt/elan-pretty                 # git checkout and Python app
/var/lib/elan-pretty/bot         # uploads, pending jobs, runtime state
/var/lib/elan-pretty/mappings    # learned tier mappings
/var/www/elan-pretty/published   # public HTML/JSON/PDF output
```

The public index is served from:

```text
https://elan.example.org/published/
```

Replace `elan.example.org` with your actual domain.

## 1. Prepare DNS

Create an `A` record pointing your domain or subdomain to the Hetzner server IP.
For example:

```text
elan.example.org -> 1.2.3.4
```

## 2. Install System Packages

On Ubuntu 24.04:

```bash
sudo apt update
sudo apt install -y git python3.12-venv nginx chromium fonts-sil-charis
```

If you use WeasyPrint instead of Chromium, also install:

```bash
sudo apt install -y libcairo2 libpango-1.0-0 libpangocairo-1.0-0 \
  libgdk-pixbuf2.0-0 libffi-dev shared-mime-info
```

Chromium is usually the better PDF backend here because it matches the HTML
rendering more closely.

## 3. Create an App User and Directories

```bash
sudo useradd --system --home /opt/elan-pretty --shell /usr/sbin/nologin elanpretty
sudo mkdir -p /opt/elan-pretty /var/lib/elan-pretty/bot \
  /var/lib/elan-pretty/mappings /var/www/elan-pretty/published
sudo chown -R elanpretty:elanpretty /opt/elan-pretty /var/lib/elan-pretty \
  /var/www/elan-pretty
```

## 4. Clone and Install

```bash
sudo -u elanpretty git clone git@github.com:mkurg/elan-pretty.git /opt/elan-pretty
cd /opt/elan-pretty
sudo -u elanpretty python3.12 -m venv .venv
sudo -u elanpretty .venv/bin/pip install -U pip
sudo -u elanpretty .venv/bin/pip install -e '.[bot,pdf]'
```

If the server does not have access to the GitHub SSH key, clone with HTTPS
instead:

```bash
sudo -u elanpretty git clone https://github.com/mkurg/elan-pretty.git /opt/elan-pretty
```

## 5. Configure nginx

Create `/etc/nginx/sites-available/elan-pretty`:

```nginx
server {
    listen 80;
    server_name elan.example.org;

    root /var/www/elan-pretty;
    index index.html;

    location = / {
        return 302 /published/;
    }

    location /published/ {
        try_files $uri $uri/ =404;
    }

    client_max_body_size 100M;
}
```

Enable it:

```bash
sudo ln -s /etc/nginx/sites-available/elan-pretty /etc/nginx/sites-enabled/elan-pretty
sudo nginx -t
sudo systemctl reload nginx
```

After adding HTTPS with Certbot, set `ELAN_PRETTY_PUBLIC_BASE_URL` to the
`https://` URL.

## 6. Configure the Telegram Bot Service

Create `/etc/systemd/system/elan-pretty-bot.service`:

```ini
[Unit]
Description=ELAN Pretty Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=elanpretty
Group=elanpretty
WorkingDirectory=/opt/elan-pretty
Environment=TELEGRAM_BOT_TOKEN=123456:replace-me
Environment=ELAN_PRETTY_REPO=/opt/elan-pretty
Environment=ELAN_PRETTY_PUBLISH_MODE=local
Environment=ELAN_PRETTY_PAGES_DIR=/var/www/elan-pretty/published
Environment=ELAN_PRETTY_PUBLIC_BASE_URL=https://elan.example.org/published/
Environment=ELAN_PRETTY_WORK_DIR=/var/lib/elan-pretty/bot
Environment=ELAN_PRETTY_MAPPING_DIR=/var/lib/elan-pretty/mappings
Environment=ELAN_PRETTY_AUTO_GIT_PUSH=false
Environment=ELAN_PRETTY_PDF_BACKEND=chromium
ExecStart=/opt/elan-pretty/.venv/bin/python -m elan_pretty.bot.telegram_bot
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now elan-pretty-bot
sudo journalctl -u elan-pretty-bot -f
```

## 7. Manual Render Test

Before testing Telegram, verify that the server can write and serve static
publications:

```bash
cd /opt/elan-pretty
sudo -u elanpretty .venv/bin/python main.py input.eaf /var/www/elan-pretty/published \
  --static-site \
  --public-base-url https://elan.example.org/published/ \
  --auto-detect-tiers \
  --pdf
```

Open:

```text
https://elan.example.org/published/
```

## 8. Updating While the Service Is Running

```bash
cd /opt/elan-pretty
sudo -u elanpretty git pull --ff-only
sudo -u elanpretty .venv/bin/pip install -e '.[bot,pdf]'
sudo systemctl restart elan-pretty-bot
```

Existing rendered publications stay in `/var/www/elan-pretty/published`.

## Future Web Interface

The web interface should be a FastAPI app using the same publishing backend:

```text
upload .eaf
  -> suggest mapping
  -> confirm/edit mapping
  -> render into /var/www/elan-pretty/published
  -> return https://elan.example.org/published/<slug>/
```

nginx can serve both:

```nginx
location /published/ {
    try_files $uri $uri/ =404;
}

location /app/ {
    proxy_pass http://127.0.0.1:8000/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

The important rule is: generated user output goes to the public storage
directory, not to git.
