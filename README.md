# 🚀 Israel Route Risk Map

**Navigate Smart. Stay Out of Range.**

A real-time route risk analyzer for Israel, built on 2026 Pikud HaOref (IDF Home Front Command) alarm data. Plan safer driving routes by visualizing rocket/missile threat levels, confirmed hit sites, and public shelter locations.

🌐 **Live:** [alarm-risk-map.vercel.app](https://alarm-risk-map.vercel.app)

---

## Features

- **Risk-scored routing** — Color-coded routes (green → red) based on proximity to alarm zones and confirmed hit sites
- **Live alarm data** — Fetched from Pikud HaOref API, categorized by threat source (Iran/Houthi, Hezbollah, Hamas)
- **Confirmed hits layer** — 41 curated impact locations from 2024–2026 with severity ratings
- **Public shelters** — Shelter icons appear when zoomed in, plus markers along your route timeline
- **Safer route suggestions** — Automatically suggests lower-risk alternatives when available
- **Waze & Google Maps integration** — One-tap navigation handoff to your preferred app
- **Hebrew localization** — Auto-detects Israeli users by IP and switches to Hebrew RTL
- **Mobile-first PWA** — Installable as a home screen app, peek drawer UI, responsive design
- **Heatmap visualization** — Alarm frequency + hit intensity heatmap overlay

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Vanilla JS, Leaflet.js, Leaflet.heat |
| Backend | Python Flask (serverless on Vercel) |
| Routing | OSRM (Open Source Routing Machine) |
| Geocoding | Nominatim (OpenStreetMap) |
| Data | Pikud HaOref API (oref.org.il) |
| Auth | Google OAuth 2.0 (optional) |
| Hosting | Vercel (serverless Python) |
| Analytics | Vercel Analytics |

## Risk Algorithm

Route risk is a blend of two normalized scores:

- **60% Alarm score** — `Σ weight × e^(-distance / 14km)` for each alarm within 55 km, weighted by threat source (Iran 3×, Hezbollah 1.8×, Hamas 1×)
- **40% Hit score** — `Σ severity × source_weight × e^(-distance / 8km)` for confirmed hits within 40 km (tighter decay — actual impacts are more localized)

A time-of-day multiplier (0.85× night → 1.18× afternoon) adjusts the final score.

## Local Development

```bash
pip install -r requirements.txt
python app.py
# Open http://localhost:3030
```

Set environment variables for Google OAuth (optional):
```
SECRET_KEY=<random-string>
GOOGLE_CLIENT_ID=<your-client-id>
GOOGLE_CLIENT_SECRET=<your-client-secret>
```

## Deployment

The app is deployed on Vercel as a serverless Python function:

```bash
npm i -g vercel
vercel --prod
```

## Data Sources

- **Pikud HaOref** — Real-time and historical alarm data from the IDF Home Front Command
- **Confirmed hits** — Curated from public reporting (IDF statements, news sources) covering Iran/Houthi strikes (Apr & Oct 2024), Hezbollah rocket attacks (2024), and Gaza rocket impacts
- **Shelter locations** — Public shelter data for major Israeli cities

## Disclaimer

⚠️ For educational and planning purposes only. Does not replace real-time emergency instructions. Always follow official Pikud HaOref alerts and guidelines.

## License

MIT
