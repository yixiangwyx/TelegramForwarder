import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from PIL import Image
except ImportError:  # pragma: no cover - runtime dependency guard
    Image = None

logger = logging.getLogger(__name__)

SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


@dataclass
class ImageCropSettings:
    enabled: bool = False
    top_px: Optional[int] = None
    bottom_px: Optional[int] = None
    left_px: Optional[int] = None
    right_px: Optional[int] = None
    top_ratio: Optional[float] = None
    bottom_ratio: Optional[float] = None
    left_ratio: Optional[float] = None
    right_ratio: Optional[float] = None
    top_px_if_qr: Optional[int] = None
    top_ratio_if_qr: Optional[float] = None
    auto_bottom_qr: bool = False
    qr_search_top_ratio: float = 0.55
    qr_top_padding_px: int = 8
    qr_bright_threshold: int = 200
    qr_dark_threshold: int = 90
    qr_min_side_ratio: float = 0.08
    qr_max_side_ratio: float = 0.28
    qr_min_transition_density: float = 0.10
    preserve_square: bool = False
    jpeg_quality: int = 95


def image_cropping_enabled() -> bool:
    return os.getenv("ENABLE_IMAGE_CROP", "true").strip().lower() == "true"


def load_crop_settings_from_env() -> ImageCropSettings:
    return ImageCropSettings(
        enabled=image_cropping_enabled(),
        top_px=_read_int_env("IMAGE_CROP_TOP_PX", zero_as_none=True),
        bottom_px=_read_int_env("IMAGE_CROP_BOTTOM_PX", zero_as_none=True),
        left_px=_read_int_env("IMAGE_CROP_LEFT_PX", zero_as_none=True),
        right_px=_read_int_env("IMAGE_CROP_RIGHT_PX", zero_as_none=True),
        top_ratio=_read_float_env("IMAGE_CROP_TOP_RATIO"),
        bottom_ratio=_read_float_env("IMAGE_CROP_BOTTOM_RATIO"),
        left_ratio=_read_float_env("IMAGE_CROP_LEFT_RATIO"),
        right_ratio=_read_float_env("IMAGE_CROP_RIGHT_RATIO"),
        top_px_if_qr=_read_int_env("IMAGE_CROP_TOP_PX_IF_QR", zero_as_none=True),
        top_ratio_if_qr=_read_float_env("IMAGE_CROP_TOP_RATIO_IF_QR", 0.1475),
        auto_bottom_qr=os.getenv("IMAGE_CROP_AUTO_BOTTOM_QR", "true").strip().lower() == "true",
        qr_search_top_ratio=_read_float_env("IMAGE_CROP_QR_SEARCH_TOP_RATIO", 0.55) or 0.55,
        qr_top_padding_px=_read_int_env("IMAGE_CROP_QR_TOP_PADDING_PX", 8) or 8,
        qr_bright_threshold=_read_int_env("IMAGE_CROP_QR_BRIGHT_THRESHOLD", 200) or 200,
        qr_dark_threshold=_read_int_env("IMAGE_CROP_QR_DARK_THRESHOLD", 90) or 90,
        qr_min_side_ratio=_read_float_env("IMAGE_CROP_QR_MIN_SIDE_RATIO", 0.08) or 0.08,
        qr_max_side_ratio=_read_float_env("IMAGE_CROP_QR_MAX_SIDE_RATIO", 0.28) or 0.28,
        qr_min_transition_density=_read_float_env("IMAGE_CROP_QR_MIN_TRANSITION_DENSITY", 0.10) or 0.10,
        preserve_square=os.getenv("IMAGE_CROP_PRESERVE_SQUARE", "false").strip().lower() == "true",
        jpeg_quality=_read_int_env("IMAGE_CROP_JPEG_QUALITY", 95) or 95,
    )


def crop_image_file(file_path: str, settings: Optional[ImageCropSettings] = None) -> Optional[dict]:
    if Image is None:
        logger.warning("Pillow 未安装，跳过图片裁剪")
        return None

    path = Path(file_path)
    if path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
        logger.info(f"文件不是支持的图片格式，跳过裁剪: {file_path}")
        return None

    settings = settings or load_crop_settings_from_env()
    if not settings.enabled:
        return None

    with Image.open(path) as image:
        image.load()
        original_width, original_height = image.size

        left = _resolve_crop_amount(settings.left_px, settings.left_ratio, original_width)
        right = _resolve_crop_amount(settings.right_px, settings.right_ratio, original_width)
        top = _resolve_crop_amount(settings.top_px, settings.top_ratio, original_height)
        bottom = _resolve_crop_amount(settings.bottom_px, settings.bottom_ratio, original_height)
        qr_detection = _detect_bottom_qr_crop(image, settings)
        if qr_detection:
            top = max(top, _resolve_crop_amount(settings.top_px_if_qr, settings.top_ratio_if_qr, original_height))
            bottom = max(bottom, qr_detection["bottom_crop_px"])

        crop_left = min(max(left, 0), max(original_width - 1, 0))
        crop_top = min(max(top, 0), max(original_height - 1, 0))
        crop_right = max(crop_left + 1, original_width - max(right, 0))
        crop_bottom = max(crop_top + 1, original_height - max(bottom, 0))

        if (
            crop_left == 0
            and crop_top == 0
            and crop_right == original_width
            and crop_bottom == original_height
        ):
            logger.info(f"图片未命中任何裁剪参数，保持原样: {file_path}")
            return {
                "path": str(path),
                "changed": False,
                "original_size": (original_width, original_height),
                "cropped_size": (original_width, original_height),
                "qr_detection": qr_detection,
            }

        cropped = image.crop((crop_left, crop_top, crop_right, crop_bottom))
        if settings.preserve_square:
            cropped = _pad_to_square(cropped, _pick_background_color(image))

        temp_output = path.with_name(f"{path.stem}.cropping{path.suffix}")
        save_kwargs = {}
        output_format = image.format or _detect_output_format(path.suffix)

        if output_format == "JPEG":
            if cropped.mode not in ("RGB", "L"):
                cropped = cropped.convert("RGB")
            save_kwargs["quality"] = settings.jpeg_quality
            save_kwargs["optimize"] = True

        cropped.save(temp_output, format=output_format, **save_kwargs)
        os.replace(temp_output, path)

        result = {
            "path": str(path),
            "changed": True,
            "original_size": (original_width, original_height),
            "cropped_size": cropped.size,
            "qr_detection": qr_detection,
        }
        logger.info(
            "图片裁剪完成: %s -> %s",
            result["original_size"],
            result["cropped_size"],
        )
        return result


def copy_and_crop_image(
    source_path: str,
    output_path: str,
    settings: ImageCropSettings,
) -> dict:
    if Image is None:
        raise RuntimeError("Pillow 未安装，无法执行图片裁剪测试")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with open(source_path, "rb") as source_file, open(output, "wb") as target_file:
        target_file.write(source_file.read())

    result = crop_image_file(str(output), settings=settings)
    if result is None:
        raise RuntimeError("裁剪未执行，请检查裁剪参数或图片格式")
    return result


def _resolve_crop_amount(px_value: Optional[int], ratio_value: Optional[float], total: int) -> int:
    if px_value is not None:
        return px_value
    if ratio_value is not None:
        return int(round(total * ratio_value))
    return 0


def _read_int_env(
    name: str,
    default: Optional[int] = None,
    zero_as_none: bool = False,
) -> Optional[int]:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
        if zero_as_none and parsed == 0:
            return default
        return parsed
    except ValueError:
        logger.warning(f"环境变量 {name} 不是有效整数: {value}")
        return default


def _read_float_env(name: str, default: Optional[float] = None) -> Optional[float]:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        logger.warning(f"环境变量 {name} 不是有效小数: {value}")
        return default


def _detect_bottom_qr_crop(image, settings: ImageCropSettings):
    if not settings.auto_bottom_qr:
        return None

    grayscale = image.convert("L")
    width, height = grayscale.size
    search_top = min(max(int(height * settings.qr_search_top_ratio), 0), max(height - 1, 0))
    search_height = height - search_top
    if search_height <= 0:
        return None

    pixels = grayscale.load()
    visited = bytearray(width * search_height)
    min_side = max(12, int(min(width, height) * settings.qr_min_side_ratio))
    max_side = max(min_side, int(min(width, height) * settings.qr_max_side_ratio))
    best_candidate = None

    for y in range(search_height):
        for x in range(width):
            index = y * width + x
            if visited[index]:
                continue
            visited[index] = 1
            if pixels[x, y + search_top] < settings.qr_bright_threshold:
                continue

            component = _collect_bright_component(
                pixels,
                width,
                search_height,
                search_top,
                x,
                y,
                visited,
                settings.qr_bright_threshold,
            )
            if not component:
                continue

            x0, y0, x1, y1, area = component
            box_width = x1 - x0 + 1
            box_height = y1 - y0 + 1
            side_ratio = box_width / box_height if box_height else 0
            if box_width < min_side or box_height < min_side:
                continue
            if box_width > max_side or box_height > max_side:
                continue
            if not 0.75 <= side_ratio <= 1.35:
                continue
            if area < box_width * box_height * 0.18:
                continue

            candidate = _score_qr_candidate(
                pixels,
                x0,
                y0 + search_top,
                x1,
                y1 + search_top,
                settings,
            )
            if not candidate:
                continue

            if best_candidate is None or candidate["score"] > best_candidate["score"]:
                best_candidate = candidate

    if not best_candidate:
        logger.info("未检测到底部二维码区域")
        return None

    crop_y = max(0, best_candidate["top"] - settings.qr_top_padding_px)
    bottom_crop_px = height - crop_y
    logger.info(
        "检测到底部二维码候选区域: bbox=%s, transition_density=%.3f, bottom_crop_px=%s",
        best_candidate["bbox"],
        best_candidate["transition_density"],
        bottom_crop_px,
    )
    return {
        "bbox": best_candidate["bbox"],
        "bottom_crop_px": bottom_crop_px,
        "transition_density": best_candidate["transition_density"],
        "dark_ratio": best_candidate["dark_ratio"],
    }


def _collect_bright_component(
    pixels,
    width: int,
    search_height: int,
    search_top: int,
    start_x: int,
    start_y: int,
    visited,
    bright_threshold: int,
):
    queue = [(start_x, start_y)]
    x0 = x1 = start_x
    y0 = y1 = start_y
    area = 0

    while queue:
        x, y = queue.pop()
        if pixels[x, y + search_top] < bright_threshold:
            continue
        area += 1
        if x < x0:
            x0 = x
        if x > x1:
            x1 = x
        if y < y0:
            y0 = y
        if y > y1:
            y1 = y

        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if nx < 0 or ny < 0 or nx >= width or ny >= search_height:
                continue
            index = ny * width + nx
            if visited[index]:
                continue
            visited[index] = 1
            if pixels[nx, ny + search_top] >= bright_threshold:
                queue.append((nx, ny))

    return x0, y0, x1, y1, area


def _score_qr_candidate(pixels, x0: int, y0: int, x1: int, y1: int, settings: ImageCropSettings):
    box_width = x1 - x0 + 1
    box_height = y1 - y0 + 1
    step_x = max(1, box_width // 48)
    step_y = max(1, box_height // 48)
    dark_count = 0
    light_count = 0
    samples = 0

    for y in range(y0, y1 + 1, step_y):
        for x in range(x0, x1 + 1, step_x):
            samples += 1
            value = pixels[x, y]
            if value <= settings.qr_dark_threshold:
                dark_count += 1
            if value >= settings.qr_bright_threshold:
                light_count += 1

    if samples == 0:
        return None

    dark_ratio = dark_count / samples
    light_ratio = light_count / samples
    if dark_ratio < 0.10 or dark_ratio > 0.70:
        return None
    if light_ratio < 0.10:
        return None

    transition_density = _calculate_transition_density(
        pixels,
        x0,
        y0,
        x1,
        y1,
        settings.qr_dark_threshold,
    )
    if transition_density < settings.qr_min_transition_density:
        return None

    score = transition_density + min(dark_ratio, light_ratio) * 0.35
    return {
        "bbox": (x0, y0, x1, y1),
        "top": y0,
        "score": score,
        "transition_density": transition_density,
        "dark_ratio": dark_ratio,
    }


def _calculate_transition_density(pixels, x0: int, y0: int, x1: int, y1: int, dark_threshold: int) -> float:
    row_step = max(1, (y1 - y0 + 1) // 18)
    col_step = max(1, (x1 - x0 + 1) // 18)
    transitions = 0
    opportunities = 0

    for y in range(y0, y1 + 1, row_step):
        prev_dark = pixels[x0, y] <= dark_threshold
        for x in range(x0 + col_step, x1 + 1, col_step):
            current_dark = pixels[x, y] <= dark_threshold
            if current_dark != prev_dark:
                transitions += 1
            opportunities += 1
            prev_dark = current_dark

    for x in range(x0, x1 + 1, col_step):
        prev_dark = pixels[x, y0] <= dark_threshold
        for y in range(y0 + row_step, y1 + 1, row_step):
            current_dark = pixels[x, y] <= dark_threshold
            if current_dark != prev_dark:
                transitions += 1
            opportunities += 1
            prev_dark = current_dark

    if opportunities == 0:
        return 0.0
    return transitions / opportunities


def _pad_to_square(image, background_color):
    width, height = image.size
    if width == height:
        return image

    side = max(width, height)
    padded = Image.new(image.mode, (side, side), background_color)
    offset_x = (side - width) // 2
    offset_y = (side - height) // 2
    padded.paste(image, (offset_x, offset_y))
    return padded


def _pick_background_color(image):
    try:
        return image.getpixel((0, 0))
    except Exception:
        return (0, 0, 0)


def _detect_output_format(suffix: str) -> str:
    if suffix.lower() in {".jpg", ".jpeg"}:
        return "JPEG"
    if suffix.lower() == ".png":
        return "PNG"
    if suffix.lower() == ".webp":
        return "WEBP"
    if suffix.lower() == ".bmp":
        return "BMP"
    return "PNG"
