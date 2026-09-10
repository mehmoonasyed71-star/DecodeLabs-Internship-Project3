"""
=====================================================================================
 PROJECT 3 : MULTIMODAL IMAGE GENERATION STUDIO   (DecodeLabs - Generative AI Batch 2026)
=====================================================================================
Goal (from project brief):
    Build a visual application that translates natural language text descriptions
    into high-quality digital artwork using a text-to-image API.

Engine used: Pollinations.ai text-to-image API (https://pollinations.ai)
    - Completely FREE, no API key, no billing account required.
    - Chosen as a drop-in replacement after Gemini/Imagen locked all image-generation
      models behind a mandatory billing account on the free tier (Sept 2026).

Requirements implemented (mapped straight from the PDF):
    1. Prompt Payload Formulation   -> aspect-ratio -> exact resolution map, style presets
    2. Network API Gateway          -> split connect/read timeout via requests
    3. Security & Moderation Gates  -> pre & post generation errors caught gracefully
    4. Transport Protocol           -> memory-safe CHUNKED binary streaming to disk (64KB chunks)
    5. Integrity Verification       -> forced Image.open().load() pixel-level decode
    6. Resilience                   -> exponential backoff + jitter, retry only on
                                       transient errors (network/5xx/429), fail-fast on
                                       hard client errors
    7. Clean UI                     -> Streamlit gallery with per-image download buttons

--------------------------------------------------------------------------------------
HOW TO RUN
--------------------------------------------------------------------------------------
1) pip install -r requirements.txt
2) streamlit run app.py
3) Type a prompt, pick aspect ratio / style / count, click "Generate" -- that's it,
   no API key needed.
--------------------------------------------------------------------------------------
"""

import io
import os
import time
import random
import uuid
from datetime import datetime
from urllib.parse import quote

import requests
import streamlit as st
from PIL import Image, UnidentifiedImageError


# =====================================================================================
# 1. PROMPT PAYLOAD FORMULATION  -> aspect ratio to exact pixel resolution map
# =====================================================================================
ASPECT_RATIO_MAP = {
    "1:1  (Square - avatars, product grids)":  (1024, 1024),
    "16:9 (Landscape - web banners, presentations)": (1344, 768),
    "9:16 (Vertical - mobile reels, wallpapers)": (768, 1344),
    "4:3  (Classic photo)": (1184, 864),
    "3:4  (Portrait photo)": (864, 1184),
}

STYLE_PRESETS = {
    "None (use prompt as-is)": "",
    "Cyberpunk": "cyberpunk style, neon lighting, futuristic, high contrast",
    "Minimalism": "minimalist style, clean lines, flat colors, lots of negative space",
    "Photorealistic": "photorealistic, ultra-detailed, professional photography, 8k",
    "Watercolor": "watercolor painting style, soft brush strokes, pastel palette",
    "3D Render": "3D render, octane render, studio lighting, high detail",
}

MODEL_OPTIONS = ["flux", "turbo", "flux-realism", "flux-anime", "flux-3d"]

BASE_URL = "https://image.pollinations.ai/prompt"
OUTPUT_DIR = "generated_assets"
os.makedirs(OUTPUT_DIR, exist_ok=True)


# =====================================================================================
# 2. NETWORK API GATEWAY  -> split connect/read timeout policy
# =====================================================================================
CONNECT_TIMEOUT = 5.0   # seconds -- fail fast if the server is unreachable
READ_TIMEOUT = 90.0     # seconds -- generation can be slow, give it room


def build_request_url(prompt: str, width: int, height: int, seed: int, model: str) -> str:
    encoded_prompt = quote(prompt)
    return (
        f"{BASE_URL}/{encoded_prompt}"
        f"?width={width}&height={height}&seed={seed}&model={model}"
        f"&nologo=true&private=true"
    )


# =====================================================================================
# 3 & 6. SECURITY GATES + RESILIENCE  -> retry wrapper with backoff + jitter
# =====================================================================================
def is_transient_error(exc: Exception, status_code: int | None) -> bool:
    """Decide whether an error is worth retrying (network / server-side / rate-limit)."""
    if isinstance(exc, (requests.exceptions.ConnectTimeout,
                         requests.exceptions.ReadTimeout,
                         requests.exceptions.ConnectionError)):
        return True
    if status_code is not None and (status_code == 429 or status_code >= 500):
        return True
    return False


def is_hard_client_error(status_code: int | None) -> bool:
    """4xx errors other than 429 mean a bad request -- retrying won't help."""
    return status_code is not None and 400 <= status_code < 500 and status_code != 429


def fetch_image_with_retry(prompt, width, height, model, max_retries=4, base_delay=2.0):
    """
    Handles GATE 1/2 style failures + Process phase + retries in one place:
      - fail-fast on hard client errors (bad params, content policy rejection)
      - exponential backoff + jitter on transient network/server/rate-limit errors
    Returns (response_or_None, error_message_or_None, seed_used)
    """
    seed = random.randint(0, 2_000_000_000)
    url = build_request_url(prompt, width, height, seed, model)
    last_exception = None
    last_status = None

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(
                url,
                stream=True,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )
            last_status = response.status_code

            if response.status_code == 200:
                return response, None, seed

            if is_hard_client_error(response.status_code):
                return None, (
                    f"❌ Request rejected (HTTP {response.status_code}). "
                    "Prompt policy-violating ho sakta hai ya parameters ghalat hain. "
                    "Prompt rephrase karke dobara try karein."
                ), seed

            if not is_transient_error(None, response.status_code):
                return None, f"❌ Unexpected error (HTTP {response.status_code}).", seed

        except Exception as exc:  # noqa: BLE001 -- single entry gate for network faults
            last_exception = exc
            if not is_transient_error(exc, None):
                return None, f"❌ Request Error: {exc}", seed

        if attempt == max_retries:
            break

        # Exponential backoff + jitter so we don't hammer a struggling server
        delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1)
        st.toast(f"Temporary error. Retry {attempt}/{max_retries} in {delay:.1f}s...", icon="⏳")
        time.sleep(delay)

    return None, (
        f"❌ Network/server error after {max_retries} attempts "
        f"(last status: {last_status}, last exception: {last_exception})."
    ), seed


# =====================================================================================
# 4. TRANSPORT PROTOCOL  -> memory-safe chunked binary write
# =====================================================================================
def save_stream_safely(response: requests.Response, filepath: str, chunk_size: int = 65536):
    """
    Never loads the whole payload into memory in one go -- pipes the network
    response through iter_content() and writes sequentially in fixed-size
    chunks straight to disk.
    """
    with open(filepath, "wb") as f:
        for chunk in response.iter_content(chunk_size=chunk_size):
            if chunk:
                f.write(chunk)


# =====================================================================================
# 5. INTEGRITY VERIFICATION  -> forced pixel-level decode (catches truncated streams)
# =====================================================================================
def verify_image_integrity(filepath: str) -> bool:
    """
    A file can look fine from its headers but still be a silently truncated
    disaster if the connection dropped mid-transfer. Image.open() only reads
    headers -- .load() forces Pillow to decode every pixel, which is the only
    reliable way to catch a corrupted/truncated binary.
    """
    try:
        img = Image.open(filepath)
        img.load()
        return True
    except (UnidentifiedImageError, OSError):
        return False


# =====================================================================================
# STREAMLIT UI
# =====================================================================================
st.set_page_config(page_title="Multimodal Image Generation Studio", page_icon="🎨", layout="wide")

if "history" not in st.session_state:
    st.session_state.history = []

st.title("🎨 Multimodal Image Generation Studio")
st.caption("Project 3 · DecodeLabs Generative AI Batch 2026 · Powered by Pollinations.ai (free, no API key)")

# ---------------- Sidebar : configuration / parameter payload ----------------
with st.sidebar:
    st.header("⚙️ Configuration")
    st.info("Yeh engine bilkul free hai — koi API key ya billing card nahi chahiye.")

    aspect_label = st.selectbox("Aspect Ratio", list(ASPECT_RATIO_MAP.keys()))
    width, height = ASPECT_RATIO_MAP[aspect_label]
    st.caption(f"Target resolution: **{width} x {height}** px")

    num_images = st.slider("Number of images to generate", min_value=1, max_value=4, value=1)

    style_label = st.selectbox("Style Preset", list(STYLE_PRESETS.keys()))

    model_choice = st.selectbox("Model", MODEL_OPTIONS, index=0)

    st.divider()
    st.caption(
        "Architecture: split timeout · backoff+jitter retries · fail-fast on hard "
        "errors · chunked memory-safe streaming · forced pixel-level integrity check."
    )

# ---------------- Main area : prompt + generate ----------------
prompt_text = st.text_area(
    "Describe the image you want to create",
    placeholder="e.g. A futuristic city skyline at sunset, flying cars, tall glass towers",
    height=100,
)

generate_clicked = st.button("🚀 Generate", type="primary", use_container_width=False)

if generate_clicked:
    if not prompt_text.strip():
        st.error("⚠️ Pehle koi prompt likhein.")
    else:
        style_suffix = STYLE_PRESETS[style_label]
        final_prompt = f"{prompt_text.strip()}, {style_suffix}" if style_suffix else prompt_text.strip()

        results = []
        with st.spinner(f"Generating {num_images} image(s) via Pollinations.ai..."):
            for i in range(num_images):
                response, error_message, seed = fetch_image_with_retry(
                    prompt=final_prompt, width=width, height=height, model=model_choice
                )
                results.append((response, error_message, seed))

        cols = st.columns(min(num_images, 4))
        success_count = 0

        for idx, (response, error_message, seed) in enumerate(results):
            if error_message:
                st.error(f"Image {idx+1}: {error_message}")
                continue

            # ---- Transport: memory-safe chunked write ----
            filename = f"{uuid.uuid4().hex}.png"
            filepath = os.path.join(OUTPUT_DIR, filename)
            save_stream_safely(response, filepath)

            # ---- Integrity: forced pixel-level decode ----
            if not verify_image_integrity(filepath):
                st.error(f"❌ Image {idx+1} corrupted / truncated during transfer. Skipped.")
                os.remove(filepath)
                continue

            success_count += 1
            with cols[idx % len(cols)]:
                st.image(filepath, use_column_width=True)
                with open(filepath, "rb") as f:
                    st.download_button(
                        label="⬇️ Download",
                        data=f.read(),
                        file_name=filename,
                        mime="image/png",
                        key=f"dl_{filename}",
                    )

            st.session_state.history.insert(0, {
                "path": filepath,
                "prompt": final_prompt,
                "resolution": f"{width}x{height}",
                "seed": seed,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })

        if success_count:
            st.success(f"✅ {success_count} image(s) generated successfully!")

# ---------------- History gallery ----------------
if st.session_state.history:
    st.divider()
    st.subheader("🖼️ Generation History (this session)")
    hist_cols = st.columns(4)
    for i, item in enumerate(st.session_state.history):
        with hist_cols[i % 4]:
            if os.path.exists(item["path"]):
                st.image(item["path"], use_column_width=True)
                st.caption(f"{item['resolution']} · seed {item['seed']} · {item['timestamp']}")
