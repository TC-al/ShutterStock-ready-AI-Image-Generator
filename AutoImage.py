import os
import re
import csv
import time
import io
import json
import base64
import random
import requests
from PIL import Image
from together import Together

IMAGE_SAVE_PATH = "D:/Python/auto image uploader/AIImages"
TOGETHER_AI_API_KEY = "REPLACE WITH YOUR API KEY"
modelName = "meta-llama/Llama-3.3-70B-Instruct-Turbo"
imageModel = "black-forest-labs/FLUX.1-schnell-Free"
IMAGE_RETENTION_DAYS = 30  # Automatically delete images older than 30 days

# Target size for upscaling (at least 4MP)
UPSCALE_WIDTH = 2048
UPSCALE_HEIGHT = 2048

# Fixed category for cyberpunk city images
FIXED_CATEGORY = "Technology"

# Initialize Together AI client
client = Together(api_key=TOGETHER_AI_API_KEY)

# Ensure the image directory exists
if not os.path.exists(IMAGE_SAVE_PATH):
    os.makedirs(IMAGE_SAVE_PATH)


# ---------- STEP 1: AUTOMATIC CLEANUP ----------
def cleanup_old_images():
    for filename in os.listdir(IMAGE_SAVE_PATH):
        if filename.lower().endswith('.csv') or filename.lower().endswith('.jpg'):
            file_path = os.path.join(IMAGE_SAVE_PATH, filename)
            try:
                os.remove(file_path)
                print(f"Deleted file: {file_path}")
            except Exception as e:
                print(f"Failed to delete {file_path}: {e}")


# ---------- HELPER: SANITIZE FILENAME ----------
def sanitize_filename(s: str) -> str:
    """
    Remove or replace characters that are invalid in Windows/macOS/Linux filenames.
    """
    return re.sub(r'[\\/*?:"<>|]', '', s)


# ---------- STEP 2: AI PROMPT GENERATION ----------
def generate_ai_prompt():
    response = client.chat.completions.create(
        model=modelName,
        messages=[{
            "role": "user",
            "content": (
                "Generate a unique, high-quality 4K cyberpunk-themed art description with futuristic elements, "
                "neon lights, and cityscape details. Ensure the details are ultra-realistic and suitable for stock image submission."
            )
        }]
    )
    generated_text = response.choices[0].message.content.strip()
    print(f"Generated AI Prompt:\n{generated_text}\n")
    return generated_text


# ---------- STEP 3: IMAGE GENERATION (1024×1024) ----------
def generate_ai_image():
    prompt = generate_ai_prompt()
    if not prompt:
        print("No prompt generated. Skipping image generation.")
        return None, None

    try:
        # The API allows max 1024×1024. We'll upscale to 2048×2048 afterwards.
        response = client.images.generate(
            prompt=prompt,
            model=imageModel,
            steps=4,
            n=1
        )
    except Exception as e:
        print(f"Error generating image: {e}")
        return None, None

    if not response.data or len(response.data) == 0:
        print("No image data returned by Together AI.")
        return None, None

    b64_json = response.data[0].b64_json
    image = None

    # Attempt to get image from base64 first
    if b64_json:
        try:
            image_bytes = base64.b64decode(b64_json)
            image = Image.open(io.BytesIO(image_bytes))
        except Exception as e:
            print("Error decoding or opening image from base64:", e)
    else:
        # Fallback: try image URL
        image_url = response.data[0].url
        if image_url:
            try:
                image_data = requests.get(image_url).content
                image = Image.open(io.BytesIO(image_data))
            except Exception as e:
                print("Error downloading or opening image from URL:", e)
        else:
            print("No image data (base64 or URL) was returned by Together AI.")
            return None, None

    if image is None:
        print("Failed to obtain image from both base64 and URL.")
        return None, None

    # Convert to RGB if needed
    if image.mode != "RGB":
        image = image.convert("RGB")

    width, height = image.size
    print(f"Initial image size: {width}×{height}")
    # Upscale to 2048×2048 if smaller
    if width < UPSCALE_WIDTH or height < UPSCALE_HEIGHT:
        print(f"Upscaling image to {UPSCALE_WIDTH}×{UPSCALE_HEIGHT}...")
        image = image.resize((UPSCALE_WIDTH, UPSCALE_HEIGHT), Image.Resampling.LANCZOS)

    # Save as a JPEG
    temp_filename = f"generated_image_{int(time.time())}.jpg"
    temp_path = os.path.join(IMAGE_SAVE_PATH, temp_filename)
    image.save(temp_path, "JPEG", quality=95, optimize=True)
    print(f"AI Image Saved: {temp_path}\n")
    return temp_path, prompt


# ---------- STEP 4: METADATA GENERATION ----------
def generate_metadata(prompt):
    """
    We ask LLaMA for exactly 3 lines:
    1) A short, descriptive, professional stock photo title (max 6 words).
    2) A creative, paraphrased version of the title (acting as an alternative title), extremely short (under 50 words total).
    3) A comma-separated list of 5-10 relevant tags.
    """
    response = client.chat.completions.create(
        model=modelName,
        messages=[{
            "role": "user",
            "content": (
                f"Generate a short, descriptive, professional stock photo title (6 words max) for this AI-generated image: {prompt}. "
                "Then on a new line, generate a creative paraphrase of that title, adding some creative descriptive elements, and acting as an alternative title, but keep it around 10 words or so, while adding some randomness to the order that you introduce the items as well as the words used (you tend to use the words: Futuristic Metropolis, a lot), as well as in general, making the phrase make grammatical sense. "
                "Then on a new line, output a comma-separated list of 5-10 relevant tags. "
                "Do not include extra text, disclaimers, or headings. Output only these three lines in plain text."
            )
        }]
    )

    metadata_text = response.choices[0].message.content.strip()
    # Split lines
    metadata_parts = metadata_text.split("\n")

    # Ensure we have at least 3 lines
    while len(metadata_parts) < 3:
        metadata_parts.append("")

    title = metadata_parts[0].strip()
    # Use the creative paraphrase as the description (alternative title)
    description = metadata_parts[1].strip()
    tags = metadata_parts[2].strip()

    print("Generated Metadata:")
    print(f"Title: {title}")
    print(f"Creative Description: {description}")
    print(f"Tags: {tags}\n")
    return title, description, tags


# ---------- STEP 5: WRITE METADATA TO CSV ----------
def write_csv_metadata(
    image_filename,
    description,
    keywords,
    categories="Technology",
    editorial="no",
    mature_content="no",
    illustration="no"
):
    """
    Write a CSV file with the following columns:
    Filename,Description,Keywords,Categories,Editorial,"Mature content",illustration
    For cyberpunk city images, we fix the category as "Technology".
    """
    csv_path = os.path.join(IMAGE_SAVE_PATH, "shutterstock_metadata.csv")

    # Overwrite each time with a single row (use 'a' to append multiple images)
    file_mode = 'w'

    fieldnames = [
        "Filename",
        "Description",
        "Keywords",
        "Categories",
        "Editorial",
        "Mature content",
        "illustration"
    ]

    with open(csv_path, file_mode, newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow({
            "Filename": image_filename,
            "Description": description,
            "Keywords": keywords,
            "Categories": categories,
            "Editorial": editorial,
            "Mature content": mature_content,
            "illustration": illustration
        })

    print(f"CSV metadata written to {csv_path}\n")


# ---------- STEP 6: RENAME FILE TO REFLECT TITLE (NO TIMESTAMP) ----------
def rename_file_with_title(image_path, title):
    safe_title = sanitize_filename(title)
    safe_title = safe_title[:50] if len(safe_title) > 50 else safe_title
    base_dir = os.path.dirname(image_path)
    new_filename = f"{safe_title}.jpg"
    new_path = os.path.join(base_dir, new_filename)

    try:
        os.rename(image_path, new_path)
        print(f"Renamed file to: {new_path}\n")
        return new_path
    except Exception as e:
        print(f"Could not rename file: {e}")
        return image_path


# ---------- MAIN AUTOMATION ----------
def main():
    cleanup_old_images()

    # 1) Generate the image
    image_path, prompt = generate_ai_image()
    if not (image_path and prompt):
        print("Image or prompt generation failed.")
        return

    # 2) Generate metadata (title, creative description, tags)
    title, description, tags = generate_metadata(prompt)
    print("Final Metadata:")
    print(f"Title: {title}")
    print(f"Creative Description: {description}")
    print(f"Tags: {tags}\n")

    # 3) Rename the file to reflect the title (no timestamp)
    final_path = rename_file_with_title(image_path, title)
    final_filename = os.path.basename(final_path)

    # 4) Write CSV with fixed category "Technology"
    write_csv_metadata(
        image_filename=final_filename,
        description=description,
        keywords=tags,
        categories="Technology",
        editorial="no",
        mature_content="no",
        illustration="no"
    )


if __name__ == '__main__':
    main()