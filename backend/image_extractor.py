"""
backend/image_extractor.py
──────────────────────────
Extracts images from PDF files using PyMuPDF (fitz).

Handles:
  - Embedded images
  - Full-page raster scans
  - Mixed-content pages (text + images)

Filtering:
  - Images < 100×100 pixels: logos, icons, decorative elements → skipped
  - Images from encrypted/DRM PDFs: caught explicitly with a clear error
  - Low-resolution scans (< 150 DPI): upscaled 2× with LANCZOS before OCR

Usage:
    from backend.image_extractor import extract_images_from_pdf
    images = extract_images_from_pdf("/path/to/doc.pdf")
    # Returns list of dicts: {page, index, width, height, dpi, image_bytes, ext}
"""

import io
from typing import Generator

try:
    import fitz  # PyMuPDF
    _HAS_FITZ = True
except ImportError:
    _HAS_FITZ = False

try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False


# Minimum pixel dimensions for an image to be considered meaningful.
# Below this: logos, bullet points, decorative borders.
MIN_WIDTH  = 100
MIN_HEIGHT = 100

# Target DPI for OCR quality. Images below this are upscaled.
MIN_DPI_FOR_OCR = 150


def extract_images_from_pdf(
    pdf_path: str,
) -> list[dict]:
    """
    Extract all meaningful images from a PDF file.

    Returns a list of image dicts:
    {
        "page":         int,        # 0-indexed page number
        "index":        int,        # image index on that page
        "width":        int,        # image width in pixels
        "height":       int,        # image height in pixels
        "estimated_dpi": float,     # estimated DPI (may be 0 if unknown)
        "image_bytes":  bytes,      # raw image bytes (PNG)
        "ext":          str,        # "png" always (normalised)
    }

    Raises:
        ImportError:  if PyMuPDF (fitz) is not installed
        RuntimeError: if the PDF is encrypted and cannot be read
    """
    if not _HAS_FITZ:
        raise ImportError(
            "PyMuPDF is required for image extraction. "
            "Install with: pip install pymupdf"
        )

    results = []

    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        raise RuntimeError(f"Cannot open PDF: {e}") from e

    if doc.is_encrypted:
        doc.close()
        raise RuntimeError(
            f"PDF is encrypted / DRM-protected: {pdf_path}. "
            "Please provide an unlocked version for indexing."
        )

    for page_num, page in enumerate(doc):
        image_list = page.get_images(full=True)

        for img_idx, img_info in enumerate(image_list):
            xref = img_info[0]
            try:
                base_image = doc.extract_image(xref)
            except Exception as e:
                print(f"  ⚠ Failed to extract image xref={xref} page={page_num}: {e}")
                continue

            img_bytes = base_image["image"]
            width     = base_image.get("width",  0)
            height    = base_image.get("height", 0)
            colorspace = base_image.get("colorspace", 1)

            # ── Size filter ────────────────────────────────────────────────────
            if width < MIN_WIDTH or height < MIN_HEIGHT:
                continue

            # ── Convert to PNG (normalised format for downstream processors) ───
            try:
                pil_img = Image.open(io.BytesIO(img_bytes))

                # Upscale low-DPI images before OCR for better character recognition
                # (common with scanned runbooks from older printers)
                estimated_dpi = _estimate_dpi(page, width, height)
                if estimated_dpi > 0 and estimated_dpi < MIN_DPI_FOR_OCR:
                    scale = MIN_DPI_FOR_OCR / estimated_dpi
                    new_w = int(width  * scale)
                    new_h = int(height * scale)
                    if new_w > 0 and new_h > 0:
                        pil_img  = pil_img.resize((new_w, new_h), Image.LANCZOS)
                        width    = new_w
                        height   = new_h

                # Cap very large images to 2048×2048 (vision LLMs perform equally
                # well at this resolution; avoids 4+ GB memory spikes on huge scans)
                if width > 2048 or height > 2048:
                    pil_img.thumbnail((2048, 2048), Image.LANCZOS)
                    width, height = pil_img.size

                out_buf = io.BytesIO()
                pil_img.save(out_buf, format="PNG")
                png_bytes = out_buf.getvalue()

            except Exception as e:
                print(f"  ⚠ PIL image conversion failed page={page_num} idx={img_idx}: {e}")
                continue

            results.append({
                "page":          page_num,
                "index":         img_idx,
                "width":         width,
                "height":        height,
                "estimated_dpi": estimated_dpi,
                "image_bytes":   png_bytes,
                "ext":           "png",
            })

    doc.close()
    return results


def render_scanned_pages(
    pdf_path: str,
    page_numbers: list[int],
    dpi: int = 200,
) -> list[dict]:
    """
    Render specific PDF pages as raster images using PyMuPDF.

    Use this for scanned PDFs where get_images() returns nothing — the entire
    page IS the image (stored as a content stream, not an XObject).

    Args:
        pdf_path:     Path to the PDF.
        page_numbers: 0-indexed list of page numbers to render.
        dpi:          Render resolution (200 is a good balance for OCR quality vs speed).

    Returns the same dict format as extract_images_from_pdf.
    """
    if not _HAS_FITZ:
        raise ImportError("PyMuPDF is required. Install with: pip install pymupdf")

    results = []

    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        raise RuntimeError(f"Cannot open PDF: {e}") from e

    if doc.is_encrypted:
        doc.close()
        raise RuntimeError(f"PDF is encrypted: {pdf_path}")

    zoom = dpi / 72.0  # 72 pts/inch is the PDF baseline
    matrix = fitz.Matrix(zoom, zoom)

    for page_num in page_numbers:
        if page_num < 0 or page_num >= len(doc):
            continue
        page = doc[page_num]
        try:
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            png_bytes = pix.tobytes("png")

            # Optionally cap size for very large renders
            if _HAS_PIL:
                try:
                    img = Image.open(io.BytesIO(png_bytes))
                    if img.width > 2048 or img.height > 2048:
                        img.thumbnail((2048, 2048), Image.LANCZOS)
                        buf = io.BytesIO()
                        img.save(buf, format="PNG")
                        png_bytes = buf.getvalue()
                except Exception:
                    pass  # use original bytes

            results.append({
                "page":          page_num,
                "index":         0,       # only one "image" per rendered page
                "width":         pix.width,
                "height":        pix.height,
                "estimated_dpi": dpi,
                "image_bytes":   png_bytes,
                "ext":           "png",
            })
        except Exception as e:
            print(f"  ⚠ Failed to render page {page_num}: {e}")

    doc.close()
    return results


def _estimate_dpi(page: "fitz.Page", width_px: int, height_px: int) -> float:
    """
    Estimate the DPI of an image by comparing its pixel dimensions to the
    physical size of the PDF page. Approximate — uses page MediaBox.
    Returns 0.0 if cannot be estimated.
    """
    try:
        rect = page.rect
        page_w_pt = rect.width   # points (1 pt = 1/72 inch)
        page_h_pt = rect.height
        if page_w_pt <= 0 or page_h_pt <= 0:
            return 0.0
        # Convert points to inches: pts / 72 = inches
        page_w_in = page_w_pt / 72.0
        page_h_in = page_h_pt / 72.0
        # DPI = pixels / inches — use the dimension with more pixels for accuracy
        dpi_w = width_px  / page_w_in if page_w_in > 0 else 0
        dpi_h = height_px / page_h_in if page_h_in > 0 else 0
        return max(dpi_w, dpi_h)
    except Exception:
        return 0.0
