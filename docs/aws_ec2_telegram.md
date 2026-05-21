# Deploying the Telegram Backend on AWS EC2

This project uses Telegram long polling by default. That is the simplest EC2
shape: the instance makes outbound HTTPS requests to Telegram, so you do not
need a domain name, TLS certificate, or inbound web server port.

## 1. Create the Telegram Bot

1. In Telegram, open a chat with `@BotFather`.
2. Send `/newbot`.
3. Choose a display name, then a username ending in `bot`.
4. Copy the token. Treat it like a password.
5. Optional: send `/setdescription` and `/setabouttext` in BotFather.

The backend also sets these runtime commands when it starts:

```text
start - Show bot overview
whoami - Show your Telegram user ID
mappings - List saved tier mappings
publications - List and remove web publications
use - Use a saved mapping for the pending file
cancel - Cancel the pending file
help - Show usage help
```

## 2. Launch EC2

Use an Ubuntu 24.04 LTS instance unless you prefer Amazon Linux. A small
instance is fine for ordinary EAF files; PDF rendering is the heaviest part.

Security group:

- Inbound: SSH port `22` from your IP address only.
- Outbound: leave HTTPS open. Long polling needs outbound access to Telegram
  and GitHub.
- No inbound `80` or `443` is needed for long polling.

Connect from your machine:

```bash
chmod 400 elan-pretty-ec2.pem
ssh -i elan-pretty-ec2.pem ubuntu@EC2_PUBLIC_IP
```

## 3. Give EC2 GitHub Push Access

On the EC2 instance:

```bash
ssh-keygen -t ed25519 -C "ec2-elan-pretty"
cat ~/.ssh/id_ed25519.pub
```

In GitHub:

1. Open `mkurg/elan-pretty`.
2. Go to Settings -> Deploy keys -> Add deploy key.
3. Paste the public key.
4. Enable write access.

Back on EC2, test it:

```bash
ssh -T git@github.com
git clone git@github.com:mkurg/elan-pretty.git
cd elan-pretty
```

Make sure GitHub Pages is configured for branch `main`, folder `/root`.

## 4. Install Runtime Dependencies

```bash
sudo apt update
sudo apt install -y \
  git \
  python3.12 \
  python3.12-venv \
  python3-pip \
  libcairo2 \
  libpango-1.0-0 \
  libpangocairo-1.0-0 \
  libgdk-pixbuf2.0-0 \
  libffi-dev \
  shared-mime-info
python3.12 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e '.[bot,pdf]'
```

Those native libraries are required by WeasyPrint. If the bot reports
`cannot load library 'libpango-1.0-0'`, install the packages above and restart
the service.

For the best-looking PDFs, use Chromium. It prints the same modern HTML/CSS
that the browser page uses:

```bash
sudo apt install -y chromium-browser
```

Then set `ELAN_PRETTY_PDF_BACKEND=chromium` below. If Chromium is not
available on your image, `ELAN_PRETTY_PDF_BACKEND=auto` will try Chromium first
and fall back to WeasyPrint.

## 5. Configure the Bot

Create an environment file:

```bash
sudo nano /etc/elan-pretty-bot.env
```

Paste:

```bash
TELEGRAM_BOT_TOKEN=123456:replace-with-your-token
ELAN_PRETTY_REPO=/home/ubuntu/elan-pretty
ELAN_PRETTY_WORK_DIR=/home/ubuntu/elan-pretty/data/bot
ELAN_PRETTY_MAPPING_DIR=/home/ubuntu/elan-pretty/mappings
ELAN_PRETTY_PAGES_DIR=/home/ubuntu/elan-pretty/published
ELAN_PRETTY_AUTO_GIT_PUSH=true
ELAN_PRETTY_PDF_BACKEND=chromium
```

For a private bot, first start without `TELEGRAM_ALLOWED_USER_IDS`, send
`/whoami` to the bot, then add:

```bash
TELEGRAM_ALLOWED_USER_IDS=123456789
```

Restart the service after changing the env file.

## 6. Run Under systemd

Create the service:

```bash
sudo nano /etc/systemd/system/elan-pretty-bot.service
```

Paste:

```ini
[Unit]
Description=ELAN Pretty Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/elan-pretty
EnvironmentFile=/etc/elan-pretty-bot.env
ExecStart=/home/ubuntu/elan-pretty/.venv/bin/python -m elan_pretty.bot.telegram_bot
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now elan-pretty-bot
sudo systemctl status elan-pretty-bot
```

Watch logs:

```bash
journalctl -u elan-pretty-bot -f
```

## 7. Use the Bot

1. Send an `.eaf` file to the bot.
2. The bot suggests a tier mapping.
3. Tap `Render now`, `Save mapping and render`, `Edit mapping`, or
   `Saved mappings`.
4. If you need a fine correction, you can still type:

```text
words=wd@A morphemes=mb@A gloss=ge@A translation=ft@A
```

When rendering succeeds, the bot sends:

- a public GitHub Pages HTML link,
- a PDF file,
- any parser/normalizer warnings.

For files with parallel speaker tiers such as `tx@A` and `tx@B`, the bot
suggests both tiers for the same role and the rendered page color-codes each
speaker.

Use `/publications` to list items currently on the public page. Tap
`Remove: ...`, then confirm. The bot deletes that publication folder, rebuilds
`published/index.html`, and pushes the change when `ELAN_PRETTY_AUTO_GIT_PUSH`
is true.

Saved mappings are YAML files in `mappings/`. The bot scores them against new
uploads before falling back to heuristic tier detection.

## Updating the App While the Service Is Running

You can pull code while the bot is still running. The running process keeps the
old Python code until you restart the service.

```bash
ssh -i elan-pretty-ec2.pem ubuntu@EC2_PUBLIC_IP
cd /home/ubuntu/elan-pretty
git status --short
git pull --ff-only
source .venv/bin/activate
pip install -e '.[bot,pdf]'
sudo systemctl restart elan-pretty-bot
sudo systemctl status elan-pretty-bot --no-pager
journalctl -u elan-pretty-bot -f
```

If `git status --short` shows local generated output and
`ELAN_PRETTY_AUTO_GIT_PUSH=false`, commit or remove those local changes before
pulling. If auto-push is enabled, the bot should normally leave the EC2 checkout
clean after each publication.

## Operational Notes

- GitHub Pages is public. Do not use this deployment for sensitive fieldwork
  files unless you move outputs to a private destination.
- Long polling is good for one bot on one instance. Later, if you put this
  behind a domain and HTTPS, the Telegram adapter can be moved to webhooks.
- If `ELAN_PRETTY_AUTO_GIT_PUSH=false`, the bot still renders locally but the
  public link will not update until you commit and push from EC2.
- Mapping learning is explicit. The bot remembers mappings only when you tap
  `Save mapping and render`.
