# Hinglish Exam-Prep YouTube Shorts Pipeline

Zero-cost, fully automated pipeline producing 10-15 educational Shorts per day in natural Hinglish, with dynamic, learning-oriented visuals generated per topic.

**Script generation: OpenRouter primary, Gemini fallback.**
**TTS: self-hosted Chatterbox on GitHub Actions (no external server).**
**Everything runs on GitHub Actions free tier. No card required.**

## Create a New Repo — Step-by-Step

### Prerequisites

- GitHub account
- OpenRouter account (free): https://openrouter.ai
- Google AI Studio account (free): https://aistudio.google.com
- Pexels account (free): https://www.pexels.com/api/
- Google Cloud project with YouTube Data API v3 enabled

### Step 1: Get your OpenRouter API key

1. Go to https
```

**Option B — via the GitHub website:**

1. Go to https://github.com/new
2. **Repository name:** `youtube-automation`
3. Set visibility to **Public** (critical — Actions minutes are unlimited on public repos)
4. Do NOT check any initialization boxes
5. Click **Create repository**
6. In your terminal:

```
cd /path/where/you/want/the/project
git clone https://github.com/YOUR_USERNAME/youtube-automation.git
cd youtube-automation
```

### Step 3: Add all the project files

Copy every file from this project into the cloned repo folder. The structure should look like this:

```
youtube-automation/
├── .github/workflows/
│   ├── build-tts.yml
│   └── generate.yml
├── assets/music/.gitkeep
├── pipeline/
│   ├── __init__.py
│   ├── config.py
│   ├── script_gen.py
│   ├── tts.py
│   ├── visuals.py
│   ├── gemini_images.py
│   ├── manim_renderer.py
│   ├── render.py
│   ├── quota.py
│   ├── upload.py
│   └── generate.py
├── scripts/
│   ├── setup_oauth.py
│   └── encode_creds.py
├── tts-server/
│   ├── Dockerfile
│   ├── server.py
│   ├── requirements.txt
│   ├── docker-compose.yml
│   └── references/
│       ├── .gitkeep
│       └── README.md
├── .env.example
├── .gitignore
├── content_plan.json
├── requirements.txt
└── README.md
```

Then push:

```
git add .
git commit -m "Initial commit"
git push -u origin main
```

### Step 4: Build the Chatterbox TTS Docker image (one time)

1. Go to your repo → **Actions** tab
2. If prompted, click **"I understand my workflows, go ahead and enable them"**
3. In the left sidebar, click **Build TTS Docker Image**
4. Click **Run workflow** → leave branch as `main` → **Run workflow**
5. Wait 20-40 minutes. When it succeeds, the image is live at
`ghcr.io/YOUR_USERNAME/youtube-automation/chatterbox-tts:latest`

### Step 5: Add GitHub Secrets

Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.

| Name ↕▾ | Value ↕▾ |
|---|---|
| −`OPENROUTER_API_KEY` | `sk-or-v1-...` |
| −`GEMINI_API_KEY` | AIza... |
| −`PEXELS_API_KEY` | from Pexels |
| −`TTS_SERVER_TOKEN` | `openssl rand -hex 32` |
| −`YT_CREDS_1` | from the OAuth script (Step 7) |
⚙

### Step 6: Add GitHub Variables

Same page → switch to **Variables** tab → **New repository variable**.

| Name ↕▾ | Value ↕▾ |
|---|---|
| −`MANIM_ENABLED` | `1` |
| −`NICHES_ENABLED` | `upsc_polity,ssc_math,general_science` |
| −`LANGUAGES_ENABLED` | `hinglish,hi,en` |
| −`GEMINI_IMAGE_MODEL` | `gemini-3.1-flash-image` |
⚙

### Step 7: Generate YouTube OAuth credentials

```
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/setup_oauth.py /path/to/client_secrets.json
```

Sign in with the YouTube channel. Copy the base64 string into `YT_CREDS_1` in GitHub Secrets.

### Step 8: Dry run

Actions → **Generate YouTube Short** → Run workflow → check **dry_run** → Run. Download the artifact and inspect it.

### Step 9: Go live

Run again with **dry_run unchecked**. The cron (`17 * * * *`) takes over.

## How Script Generation Works

OpenRouter models tried in order:

1. `deepseek/deepseek-chat-v3.1:free` — best JSON adherence
2. `meta-llama/llama-3.3-70b-instruct:free`
3. `google/gemini-2.0-flash-exp:free`

Override via GitHub Variable `OPENROUTER_MODELS` (comma-separated). Gemini 2.5 Flash is used **only** if all OpenRouter models fail.

## Cost Breakdown

| Component ↕▾ | Cost ↕▾ |
|---|---|
| −GitHub Actions (public repo) | ₹0 — unlimited |
| −GitHub Container Registry | ₹0 — unlimited |
| −OpenRouter free models | ₹0 (50 req/day) or one-time $10 for 1,000 req/day |
| −Chatterbox TTS (MIT) | ₹0 |
| −Manim (MIT) | ₹0 |
| −Gemini image generation | ₹0 (verify free tier) |
| −Pexels API | ₹0 |
| −YouTube Data API | ₹0 |
⚙

**Total: ₹0/month + optional one-time $10 OpenRouter credit.**

## File Map

```
pipeline/
├── config.py           # env loading
├── script_gen.py       # OpenRouter primary, Gemini fallback
├── tts.py              # Chatterbox client + Edge-TTS fallback
├── visuals.py          # scene router
├── gemini_images.py    # Gemini image client
├── manim_renderer.py   # Manim wrapper
├── render.py           # FFmpeg assembly
├── quota.py            # YouTube quota tracking
├── upload.py           # YouTube upload
└── generate.py         # orchestrator

tts
```

## License

MIT

