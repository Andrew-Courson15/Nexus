"""Picture formats: viewable, AI-readable previews of any image a user uploads.

The browser and the local vision model read only a few formats (JPEG, PNG). For
everything else (TIFF, PSD, TGA, WebP, HEIC, SVG, PDF/Illustrator, Procreate,
Krita, OpenRaster, BMP, WMF/EMF ...) NEXUS renders a *preview*: a derived PNG or
JPEG stored under data/previews/. The original upload is never modified; the
preview is derived data and can always be regenerated from it.

Oversized JPEG/PNG files also get a smaller preview, so they can be analysed.
"""
from __future__ import annotations

import io
import logging
import uuid
import zipfile
from pathlib import Path

from app.config import get_settings

log = logging.getLogger("nexus.images")

DIRECT_MIME = {"image/jpeg", "image/png"}   # shown and analysed as-is
MAX_DIRECT_BYTES = 20 * 1024 * 1024        # bigger than this gets a preview too
PREVIEW_MAX_SIDE = 2048
PREVIEW_MAX_PIXELS = 400_000_000            # refuse absurd images (decompression bombs)

# Extension -> MIME type for picture formats browsers often send as application/octet-stream.
IMAGE_EXTENSIONS = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".jfif": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp", ".dib": "image/bmp",
    ".tif": "image/tiff", ".tiff": "image/tiff", ".tga": "image/x-tga", ".icb": "image/x-tga",
    ".psd": "image/vnd.adobe.photoshop", ".psb": "image/vnd.adobe.photoshop",
    ".heic": "image/heic", ".heif": "image/heif", ".avif": "image/avif",
    ".svg": "image/svg+xml", ".wmf": "image/wmf", ".emf": "image/emf", ".ico": "image/x-icon",
    ".jp2": "image/jp2", ".pcx": "image/x-pcx", ".ppm": "image/x-portable-pixmap", ".dds": "image/vnd-ms.dds",
    ".ai": "application/illustrator",
    ".procreate": "application/x-procreate", ".kra": "application/x-krita", ".ora": "image/openraster",
}
# Visual files that aren't image/* but are still artwork (kind "image").
ARTWORK_MIME = {"application/illustrator", "application/x-procreate", "application/x-krita"}
# Documents that are not artwork but still get a first-page preview.
DOCUMENT_PREVIEW_MIME = {"application/pdf"}
_RENDERED_BY_PYMUPDF = {"application/pdf", "application/illustrator", "application/postscript", "image/svg+xml"}
# Inside these zip-based painting files is a flattened picture of the whole canvas.
_ZIP_PREVIEWS = {
    "application/x-procreate": ("QuickLook/Thumbnail.png",),
    "application/x-krita": ("mergedimage.png", "preview.png"),
    "image/openraster": ("mergedimage.png", "Thumbnails/thumbnail.png"),
}


class PreviewError(RuntimeError):
    pass


def classify_image(mime: str, filename: str) -> str | None:
    """The image MIME type for a visual upload, or None if it isn't a picture format we know."""
    ext = Path(filename).suffix.lower()
    if ext in IMAGE_EXTENSIONS and (not mime.startswith("image/") or mime == "application/octet-stream"):
        return IMAGE_EXTENSIONS[ext]
    if mime.startswith("image/") or mime in ARTWORK_MIME or mime == "image/openraster":
        return mime
    return None


def needs_preview(mime: str, size_bytes: int) -> bool:
    if mime in DIRECT_MIME:
        return size_bytes > MAX_DIRECT_BYTES
    return mime.startswith("image/") or mime in ARTWORK_MIME or mime in DOCUMENT_PREVIEW_MIME or mime == "image/openraster"


def _open_pillow(data: bytes | Path, mime: str):
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = PREVIEW_MAX_PIXELS
    if mime in ("image/heic", "image/heif", "image/avif"):
        try:
            import pillow_heif

            pillow_heif.register_heif_opener()
        except ImportError as exc:
            raise PreviewError("HEIC support isn't installed (pip install pillow-heif)") from exc
    img = Image.open(io.BytesIO(data) if isinstance(data, bytes) else data)
    if mime in ("image/wmf", "image/emf"):
        img.load(dpi=144)  # Windows only: Pillow draws WMF/EMF with the Windows graphics system
    img.load()
    return img


def _render_pymupdf(path: Path, mime: str):
    try:
        import pymupdf
    except ImportError as exc:
        raise PreviewError("PDF/SVG support isn't installed (pip install pymupdf)") from exc
    from PIL import Image

    filetype = "svg" if mime == "image/svg+xml" else "pdf"
    with pymupdf.open(path, filetype=filetype) as doc:
        if doc.page_count == 0:
            raise PreviewError("the document has no pages")
        page = doc[0]
        longest = max(page.rect.width, page.rect.height) or 1
        zoom = min(4.0, max(0.5, PREVIEW_MAX_SIDE / longest))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=mime == "image/svg+xml")
        mode = "RGBA" if pix.alpha else "RGB"
        return Image.frombytes(mode, (pix.width, pix.height), pix.samples)


def _render_zip(path: Path, mime: str):
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            for inner in _ZIP_PREVIEWS[mime]:
                if inner in names:
                    return _open_pillow(zf.read(inner), "image/png")
    except zipfile.BadZipFile as exc:
        raise PreviewError("the file isn't a valid painting file") from exc
    raise PreviewError("the file has no flattened picture inside")


def render(path: Path, mime: str):
    """Open any supported format as a Pillow image (first page / frame / flattened canvas)."""
    if mime in _RENDERED_BY_PYMUPDF:
        return _render_pymupdf(path, mime)
    if mime in _ZIP_PREVIEWS:
        return _render_zip(path, mime)
    try:
        return _open_pillow(path, mime)
    except PreviewError:
        raise
    except Exception as exc:
        if mime in ("image/wmf", "image/emf"):
            raise PreviewError("WMF/EMF pictures can only be converted on Windows") from exc
        raise PreviewError(f"this {mime} file couldn't be read ({exc})") from exc


def make_preview(path: Path, mime: str) -> Path:
    """Render a preview for `path` into data/previews/ and return it. Raises PreviewError."""
    from PIL import Image, ImageOps

    try:
        img = render(path, mime)
    except PreviewError:
        raise
    except Exception as exc:
        raise PreviewError(f"the file couldn't be read ({exc})") from exc
    try:
        img.seek(0)  # first frame of animations / multi-page TIFFs
    except (AttributeError, EOFError):
        pass
    img = ImageOps.exif_transpose(img)
    img.thumbnail((PREVIEW_MAX_SIDE, PREVIEW_MAX_SIDE))
    has_alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
    if img.mode not in ("RGB", "RGBA", "L"):
        img = img.convert("RGBA" if has_alpha else "RGB")
    out_dir = get_settings().data_dir / "previews"
    out_dir.mkdir(parents=True, exist_ok=True)
    if has_alpha:
        dest = out_dir / f"{uuid.uuid4().hex}.png"
        img.save(dest, "PNG", optimize=True)
    else:
        dest = out_dir / f"{uuid.uuid4().hex}.jpg"
        img.convert("RGB").save(dest, "JPEG", quality=90)
    return dest


def ensure_preview(attachment) -> str | None:
    """Make sure an attachment that needs a preview has one. Returns an error message, or None.

    Sets `attachment.preview_path` / `preview_mime`; the caller commits.
    """
    if not needs_preview(attachment.mime_type, attachment.size_bytes):
        return None
    data_dir = get_settings().data_dir
    if attachment.preview_path and (data_dir / attachment.preview_path).is_file():
        return None
    try:
        dest = make_preview(data_dir / attachment.stored_path, attachment.mime_type)
    except PreviewError as exc:
        return f"{attachment.original_filename}: {exc}"
    attachment.preview_path = dest.relative_to(data_dir).as_posix()
    attachment.preview_mime = "image/png" if dest.suffix == ".png" else "image/jpeg"
    return None


def visual_file(attachment) -> tuple[Path, str] | None:
    """The JPEG/PNG to show or analyse for an attachment: its preview, or the original itself."""
    data_dir = get_settings().data_dir
    if attachment.preview_path:
        return data_dir / attachment.preview_path, attachment.preview_mime or "image/jpeg"
    if attachment.mime_type in DIRECT_MIME and attachment.size_bytes <= MAX_DIRECT_BYTES:
        return data_dir / attachment.stored_path, attachment.mime_type
    return None
