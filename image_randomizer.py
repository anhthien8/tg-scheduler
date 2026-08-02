"""
Image Randomization Utility
Slightly modifies images before each send to change their hash/fingerprint,
preventing Telegram from detecting mass-sent identical media.
"""
import io
import os
import random
import logging
import tempfile

logger = logging.getLogger("tg-scheduler.image_randomizer")


def randomize_image(image_path: str) -> str:
    """
    Takes an image file path, applies subtle random modifications to change
    the file hash, and returns the path to the modified temp file.
    
    Modifications (invisible to human eye):
    - Random JPEG quality (78-95)
    - Tiny random resize (±1-3px)
    - Optional subtle pixel noise
    - Random EXIF-like metadata strip
    
    Returns: path to a temp file (caller should clean up or let OS handle it)
    """
    try:
        from PIL import Image
    except ImportError:
        logger.warning("[ImageRandomizer] Pillow not installed, using fallback byte-swap method")
        return _fallback_randomize(image_path)
    
    try:
        img = Image.open(image_path)
        
        # Convert to RGB if needed (handles RGBA, P, etc.)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        
        w, h = img.size
        
        # 1. Tiny random resize (±1-3px) — imperceptible
        delta_w = random.randint(-3, 3)
        delta_h = random.randint(-3, 3)
        new_w = max(w + delta_w, w - 5)  # Ensure reasonable size
        new_h = max(h + delta_h, h - 5)
        if (new_w, new_h) != (w, h):
            img = img.resize((new_w, new_h), Image.LANCZOS)
        
        # 2. Add subtle pixel noise to a few random pixels
        try:
            import numpy as np
            pixels = np.array(img)
            # Change 5-15 random pixels by ±1-2 value (invisible)
            num_noise = random.randint(5, 15)
            for _ in range(num_noise):
                rx = random.randint(0, pixels.shape[0] - 1)
                ry = random.randint(0, pixels.shape[1] - 1)
                for c in range(min(pixels.shape[2], 3)):
                    delta = random.choice([-2, -1, 1, 2])
                    pixels[rx, ry, c] = max(0, min(255, int(pixels[rx, ry, c]) + delta))
            img = Image.fromarray(pixels)
        except ImportError:
            pass  # numpy not available, skip noise step
        
        # 3. Save with random JPEG quality
        quality = random.randint(78, 95)
        
        # Determine output format
        ext = os.path.splitext(image_path)[1].lower()
        if ext in (".png",):
            fmt = "PNG"
            suffix = ".png"
        else:
            fmt = "JPEG"
            suffix = ".jpg"
        
        # Write to temp file
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="tg_rand_")
        if fmt == "JPEG":
            img.save(tmp, format=fmt, quality=quality, optimize=True)
        else:
            img.save(tmp, format=fmt, optimize=True)
        tmp.close()
        
        orig_size = os.path.getsize(image_path)
        new_size = os.path.getsize(tmp.name)
        logger.debug(
            f"[ImageRandomizer] {os.path.basename(image_path)}: "
            f"{orig_size}B -> {new_size}B (q={quality}, Δw={delta_w}, Δh={delta_h})"
        )
        return tmp.name
        
    except Exception as e:
        logger.warning(f"[ImageRandomizer] PIL method failed: {e}, using fallback")
        return _fallback_randomize(image_path)


def _fallback_randomize(image_path: str) -> str:
    """
    Fallback: append random bytes to JPEG EXIF/comment section.
    Changes file hash without modifying visible image.
    """
    try:
        with open(image_path, "rb") as f:
            data = bytearray(f.read())
        
        # For JPEG: insert a random COM (comment) marker before the last bytes
        # COM marker = FF FE + 2-byte length + data
        if data[:2] == b'\xff\xd8':  # JPEG
            comment = bytes([random.randint(0, 255) for _ in range(random.randint(8, 32))])
            com_marker = b'\xff\xfe' + len(comment + b'\x00\x00').to_bytes(2, 'big') + comment
            # Insert after SOI marker (first 2 bytes)
            data = data[:2] + com_marker + data[2:]
        else:
            # For other formats, just append random bytes (many formats ignore trailing data)
            data += bytes([random.randint(0, 255) for _ in range(random.randint(4, 16))])
        
        ext = os.path.splitext(image_path)[1] or ".jpg"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext, prefix="tg_rand_")
        tmp.write(data)
        tmp.close()
        
        logger.debug(f"[ImageRandomizer] Fallback: {os.path.basename(image_path)} -> {tmp.name}")
        return tmp.name
        
    except Exception as e:
        logger.error(f"[ImageRandomizer] Fallback failed: {e}")
        return image_path  # Return original as last resort


def cleanup_temp_image(temp_path: str, original_path: str):
    """Remove temp file if it differs from the original."""
    try:
        if temp_path and temp_path != original_path and os.path.exists(temp_path):
            os.unlink(temp_path)
    except Exception:
        pass
