import os
import time
import json
import sys
import random
import subprocess
from google import genai
from google.genai import types

# ============================================================
# CONFIG
# ============================================================
API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    print("ERROR: GEMINI_API_KEY not set")
    sys.exit(1)

TOPIC = os.environ.get("VIDEO_TOPIC", "A brave little cat who saves a village from a storm")
NUM_SCENES = int(os.environ.get("NUM_SCENES", "8"))
OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "cartoon_video.mp4")
SCRIPT_MODEL = "gemini-2.5-flash"
VIDEO_MODEL = "veo-3.1-generate-preview"

# New advanced options
STYLE_MODE = os.environ.get("STYLE_MODE", "random")   # "random" | style id (e.g. "tom_jerry")
RESOLUTION = os.environ.get("RESOLUTION", "1080p")     # "720p" | "1080p" | "4k"
ASPECT_RATIO = os.environ.get("ASPECT_RATIO", "16:9")  # "16:9" | "9:16"
GENERATE_AUDIO = os.environ.get("GENERATE_AUDIO", "true").lower() == "true"
NEGATIVE_PROMPT = os.environ.get(
    "NEGATIVE_PROMPT",
    "blurry, low quality, distorted, deformed, extra limbs, "
    "text, subtitles, watermark, logo, signature, ugly, mutated, "
    "disfigured, bad anatomy, disfigured hands, extra fingers"
)
BASE_SEED = os.environ.get("SEED")  # optional, agar diya to reproducible

client = genai.Client(api_key=API_KEY)

# ============================================================
# STEP 1: Random style pick karo
# ============================================================
with open("styles.json", "r") as f:
    styles_data = json.load(f)["styles"]

if STYLE_MODE == "random":
    chosen_style = random.choice(styles_data)
    print(f"🎲 Random style pick hua: {chosen_style['name']} ({chosen_style['id']})")
else:
    chosen_style = next((s for s in styles_data if s["id"] == STYLE_MODE), styles_data[0])
    print(f"🎨 Style set: {chosen_style['name']}")

style_suffix = chosen_style["prompt_suffix"]

# Random seed — har run mein alag
if BASE_SEED:
    run_seed = int(BASE_SEED)
else:
    run_seed = random.randint(1, 2_147_483_647)

print(f"🌱 Seed: {run_seed}\n")

# ============================================================
# STEP 2: Gemini story + scene prompts likhega (style injected)
# ============================================================
print(f"🧠 Gemini script likh raha hai (style: {chosen_style['name']})...")

script_instruction = f"""
You are a professional cartoon video scriptwriter and AI video prompt engineer.

Topic: "{TOPIC}"

STRICT STYLE (must apply to every scene):
{style_suffix}

Rules:
- Break the story into EXACTLY {NUM_SCENES} scenes.
- Each scene = one 8-second Veo video clip.
- Each scene must be a HIGHLY DETAILED VISUAL PROMPT (not dialogue).
- Describe: characters, actions, setting, camera angle, lighting, colors, mood.
- Keep CHARACTER APPEARANCE 100% CONSISTENT across all scenes (same clothes, colors, features).
- Maintain story continuity — each scene flows naturally from the previous.
- End every prompt with the style description above.
- Do NOT include text, subtitles, watermarks or logos in the visuals.

Return ONLY valid JSON:
{{
  "title": "short story title",
  "scenes": ["scene 1 prompt...", "scene 2 prompt...", ...]
}}
"""

response = client.models.generate_content(
    model=SCRIPT_MODEL,
    contents=script_instruction,
    config=types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.9,
    ),
)

script = json.loads(response.text)
story_title = script.get("title", "Cartoon Story")
scenes = script["scenes"][:NUM_SCENES]

print(f"\n🎞️  Title: {story_title}")
print(f"📝 Scenes: {len(scenes)}\n")
for i, s in enumerate(scenes, 1):
    print(f"  Scene {i}: {s[:120]}...\n")

# ============================================================
# STEP 3: Veo 3.1 se har scene ka video generate karo
# ============================================================
scene_files = []

for i, scene_prompt in enumerate(scenes, 1):
    # Style enforce karo
    full_prompt = f"{scene_prompt}\n\nAnimation style: {style_suffix}"

    print(f"\n=== 🎥 Scene {i}/{len(scenes)} generating ===")
    print(f"   Style: {chosen_style['name']}")
    print(f"   Resolution: {RESOLUTION} | Aspect: {ASPECT_RATIO}")
    print(f"   Audio: {GENERATE_AUDIO}\n")

    # Veo config — advanced features
    veo_config = types.GenerateVideosConfig(
        aspect_ratio=ASPECT_RATIO,
        number_of_videos=1,
        negative_prompt=NEGATIVE_PROMPT,
        generate_audio=GENERATE_AUDIO,
    )

    # Resolution support (agar SDK support karta hai)
    try:
        veo_config.resolution = RESOLUTION
    except Exception:
        pass  # agar SDK version support nahi karta

    # Seed support
    try:
        veo_config.seed = run_seed + i  # har scene ka alag seed
    except Exception:
        pass

    operation = client.models.generate_videos(
        model=VIDEO_MODEL,
        prompt=full_prompt,
        config=veo_config,
    )

    # Poll till done
    wait_time = 10
    while not operation.done:
        time.sleep(wait_time)
        operation = client.operations.get(operation)
        print("   ...rendering...")
        wait_time = 15

    if not operation.response or not operation.response.generated_videos:
        print(f"❌ Scene {i} failed, skipping...")
        continue

    generated_video = operation.response.generated_videos[0]
    file_name = f"scene_{i:02d}.mp4"

    client.files.download(
        file=generated_video.video,
        destination=file_name,
    )

    size_mb = os.path.getsize(file_name) / 1024 / 1024
    print(f"✅ {file_name} saved ({size_mb:.2f} MB)")
    scene_files.append(file_name)

if not scene_files:
    print("❌ Koi scene generate nahi hua. Abort.")
    sys.exit(1)

# ============================================================
# STEP 4: FFmpeg se clips join karo (normalize + concat)
# ============================================================
print(f"\n🔗 FFmpeg se {len(scene_files)} clips join kar rahe hain...")

# Resolution ke hisaab se scale set karo
if ASPECT_RATIO == "9:16":
    scale_w, scale_h = 720, 1280
else:
    scale_w, scale_h = 1920, 1080 if RESOLUTION in ("1080p", "4k") else 1280, 720

normalized = []
for idx, f in enumerate(scene_files):
    norm = f"norm_{idx:02d}.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", f,
            "-vf", f"scale={scale_w}:{scale_h}:force_original_aspect_ratio=decrease,"
                   f"pad={scale_w}:{scale_h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            norm,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    normalized.append(norm)
    print(f"   normalized {f} → {norm}")

with open("concat_list.txt", "w") as fh:
    for nf in normalized:
        fh.write(f"file '{nf}'\n")

subprocess.run(
    [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", "concat_list.txt",
        "-c", "copy",
        "-movflags", "+faststart",
        OUTPUT_FILE,
    ],
    check=True,
)

final_size = os.path.getsize(OUTPUT_FILE) / 1024 / 1024
print(f"\n🎉 Final video: {OUTPUT_FILE} ({final_size:.2f} MB)")
print(f"🎨 Style used: {chosen_style['name']}")

# Duration check
probe = subprocess.run(
    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
     "-of", "default=noprint_wrappers=1:nokey=1", OUTPUT_FILE],
    capture_output=True, text=True,
)
try:
    duration = float(probe.stdout.strip())
    print(f"⏱️  Duration: {duration:.1f} seconds")
except Exception:
    pass

# Metadata file bhi save karo
with open("video_metadata.json", "w") as mf:
    json.dump({
        "title": story_title,
        "style": chosen_style["name"],
        "style_id": chosen_style["id"],
        "seed": run_seed,
        "resolution": RESOLUTION,
        "aspect_ratio": ASPECT_RATIO,
        "audio": GENERATE_AUDIO,
        "num_scenes": len(scene_files),
        "topic": TOPIC,
    }, mf, indent=2)
print("📄 video_metadata.json saved")