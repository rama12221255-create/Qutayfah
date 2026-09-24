# 🚕 Yalla Go Telegram Bot

Telegram ride-hailing bot prepared for **GitHub + Render**.

## Features

- Customer registration
- Driver registration
- Driver online/offline mode
- Pickup GPS
- Destination GPS
- Route distance calculation using OSRM
- Automatic fare calculation
- Ride creation
- Driver notifications
- Driver acceptance
- Customer notifications
- Driver location updates
- Ride status workflow
- Ride cancellation
- Admin statistics
- SQLite database
- Render persistent disk support

## 1. Create Telegram Bot

Open **@BotFather** in Telegram.

Create a bot and copy the token.

Never put the real token inside GitHub source code.

## 2. Upload to GitHub

Create a repository, for example:

`yalla-go-telegram-bot`

Upload all files from this repository.

Recommended structure:

```text
yalla-go-telegram-bot/
├── bot.py
├── requirements.txt
├── Dockerfile
├── render.yaml
├── .env.example
├── .gitignore
└── README.md
```

## 3. Deploy on Render

On Render:

1. New
2. Blueprint
3. Select your GitHub repository
4. Render reads `render.yaml`
5. Create the service

The service is a **Background Worker**, which is appropriate for Telegram long polling.

## 4. Environment variables

Set:

```text
BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
ADMIN_ID=YOUR_TELEGRAM_USER_ID
BASE_FARE=2.0
PRICE_PER_KM=0.80
MIN_FARE=3.0
```

`DB_PATH` is already configured by `render.yaml`:

```text
/data/yalla_go.db
```

Render persistent disk keeps the SQLite database across deployments/restarts.

## 5. Get your Telegram ID

Send `/start` to the bot.

For production, use a Telegram ID utility/bot you trust, or temporarily log `update.effective_user.id`.

Then put the numeric ID into `ADMIN_ID`.

## Pricing

Current formula:

```text
fare = max(MIN_FARE, BASE_FARE + distance_km * PRICE_PER_KM)
```

Example:

```text
BASE_FARE = 2
PRICE_PER_KM = 0.80
MIN_FARE = 3
```

For 10 km:

```text
2 + 10 × 0.80 = 10
```

## Important

This version uses Telegram polling, so Render should run it as a **Background Worker**, not a normal web service.

The current driver tracking works by receiving location messages from the driver. Continuous background GPS tracking like Uber/Careem requires a driver app or Telegram Mini App/WebApp connected to a backend.

## Production upgrades

For a commercial Yalla Go service, add:

- PostgreSQL
- Redis
- WebSocket/live tracking
- Driver proximity matching
- ETA
- Mapbox/Google Maps
- Phone verification
- Driver documents
- Vehicle information
- Ratings
- Payments
- Receipts
- Admin web dashboard
- Customer Mini App
- Driver Mini App
- HTTPS webhook architecture
- Rate limiting and stronger authorization
