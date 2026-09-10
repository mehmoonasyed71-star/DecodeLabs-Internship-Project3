# 🎨 Multimodal Image Generation Studio

**Project 3 — DecodeLabs Generative AI Training (Batch 2026)**

A Streamlit web app that turns natural language prompts into digital art using a free text-to-image API — engineered with production-grade resilience, not just a bare API call.

---

## ✨ Features

- **Text-to-image generation** — describe an image in plain English, get back digital art.
- **Aspect ratio control** — 1:1, 16:9, 9:16, 4:3, 3:4, each mapped to an exact pixel resolution.
- **Style presets** — Cyberpunk, Minimalism, Photorealistic, Watercolor, 3D Render (or write your own style into the prompt).
- **Multiple images per request** — generate 1 to 4 variations at once.
- **No API key, no billing account** — powered by [Pollinations.ai](https://pollinations.ai), completely free.
- **Production-style resilience engineering**:
  - Split connect/read timeouts (fails fast on dead connections, stays patient during real generation)
  - Exponential backoff + jitter on retries (no hammering a struggling server)
  - Fail-fast on hard client errors, retry-only on transient network/server/rate-limit errors
  - Memory-safe **chunked binary streaming** to disk (never loads a full image into RAM at once)
  - Forced **pixel-level integrity verification** (`Image.load()`) to catch silently truncated downloads
- **Clean gallery UI** — generated images, download buttons, and session history.

---

## 📦 Requirements

- Python 3.9+
- Dependencies listed in `requirements.txt`:
  ```
  streamlit==1.38.0
  requests>=2.31.0
  Pillow==10.4.0
  ```

---

## 🚀 Setup & Run

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Run the app:**
   ```bash
   streamlit run app.py
   ```

3. Your browser will open automatically at `http://localhost:8501`. If not, open that link manually.

4. Type a prompt, pick an aspect ratio / style / number of images, and click **Generate** — no API key needed.

---

## 🖼️ Example Prompt

```
A serene mountain lake at sunrise, misty forest surrounding it, golden light
reflecting on calm water, snow-capped peaks in the background, ultra detailed,
cinematic
```

---

## 🏗️ Architecture Overview

| Stage | What it does |
|---|---|
| **1. Prompt Payload Formulation** | Maps chosen aspect ratio to an exact pixel resolution and appends the selected style preset to the prompt |
| **2. Network API Gateway** | Sends the request with a split timeout: short connect window, longer read window |
| **3. Security & Resilience Gate** | Distinguishes hard client errors (fail fast) from transient errors (retry with exponential backoff + jitter) |
| **4. Transport Protocol** | Streams the binary image response in fixed-size chunks straight to disk — never buffers the whole file in memory |
| **5. Integrity Verification** | Forces Pillow to fully decode every pixel (`Image.open().load()`) to catch corrupted/truncated files before showing them |
| **6. UI Layer** | Streamlit gallery with download buttons and a session-based generation history |

---

## ⚠️ Notes

- Engine: [Pollinations.ai](https://pollinations.ai) text-to-image API (models: `flux`, `turbo`, `flux-realism`, `flux-anime`, `flux-3d`).
- No signup, API key, or billing required — ideal for coursework, prototypes, and portfolios.
- Generated images are saved locally in the `generated_assets/` folder.

---

