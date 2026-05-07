import re
import os
import logging

import requests

logger = logging.getLogger(__name__)

OCR_API_KEY = os.getenv('OCR_API_KEY', 'helloworld')  # 'helloworld' is the free demo key


def extract_amount_from_image(image_bytes: bytes) -> float | None:
    """Send image to OCR.space and try to extract the total amount."""
    try:
        response = requests.post(
            'https://api.ocr.space/parse/image',
            files={'filename': ('receipt.jpg', image_bytes, 'image/jpeg')},
            data={
                'apikey': OCR_API_KEY,
                'language': 'eng',
                'isOverlayRequired': False,
                'detectOrientation': True,
                'scale': True,
                'OCREngine': 2,
            },
            timeout=30
        )
        result = response.json()

        if result.get('IsErroredOnProcessing'):
            logger.error(f"OCR error: {result.get('ErrorMessage')}")
            return None

        parsed = result.get('ParsedResults')
        if not parsed:
            return None

        text = parsed[0].get('ParsedText', '')
        logger.info(f"OCR raw text:\n{text}")
        return _parse_total(text)

    except Exception as e:
        logger.error(f"OCR request failed: {e}")
        return None


def _parse_total(text: str) -> float | None:
    """
    Parse the total amount from OCR text.
    Strategy:
      1. Look for a line containing a TOTAL keyword and extract the amount.
      2. Fallback: return the largest dollar amount found on the page.
    """
    lines = text.upper().split('\n')
    amount_re = re.compile(r'\$?\s*(\d{1,6}[.,]\d{2})')

    total_keywords = [
        'TOTAL', 'GRAND TOTAL', 'AMOUNT DUE', 'BALANCE DUE',
        'SUBTOTAL', 'ИТОГО', 'СУММА', 'К ОПЛАТЕ'
    ]

    # First pass — lines that contain a total keyword (scan from bottom)
    for line in reversed(lines):
        for kw in total_keywords:
            if kw in line:
                matches = amount_re.findall(line)
                if matches:
                    try:
                        return float(matches[-1].replace(',', '.'))
                    except ValueError:
                        pass

    # Second pass — largest amount on the page
    all_amounts = []
    for line in lines:
        for match in amount_re.findall(line):
            try:
                all_amounts.append(float(match.replace(',', '.')))
            except ValueError:
                pass

    return max(all_amounts) if all_amounts else None
