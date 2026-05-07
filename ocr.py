import re
import os
import logging

import requests

logger = logging.getLogger(__name__)

OCR_API_KEY = os.getenv('OCR_API_KEY', 'helloworld')

# Keywords to skip when parsing line items
SKIP_KEYWORDS = [
    'TOTAL', 'SUBTOTAL', 'TAX', 'HST', 'GST', 'PST', 'QST',
    'CASH', 'CHANGE', 'VISA', 'MASTERCARD', 'DEBIT', 'CREDIT',
    'THANK', 'SAVE', 'BALANCE', 'DUE', 'TENDER', 'APPROVED',
    'LOYALTY', 'POINTS', 'MEMBER', 'RECEIPT', 'STORE',
    'ИТОГО', 'СУММА', 'НДС', 'СДАЧА', 'НАЛИЧНЫЕ', 'К ОПЛАТЕ',
]


def extract_from_image(image_bytes: bytes) -> tuple:
    """
    Returns (ocr_text: str | None, lines: list[dict], total: float | None)
    lines = [{'name': str, 'amount': float}, ...]
    """
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
            return None, [], None

        parsed = result.get('ParsedResults')
        if not parsed:
            return None, [], None

        text = parsed[0].get('ParsedText', '').strip()
        logger.info(f"OCR text:\n{text}")

        lines = parse_receipt_lines(text)
        total = _parse_total(text)

        return text, lines, total

    except Exception as e:
        logger.error(f"OCR failed: {e}")
        return None, [], None


def parse_receipt_lines(text: str) -> list:
    """
    Parse individual line items from receipt text.
    Returns list of {'name': str, 'amount': float}
    """
    lines = text.split('\n')
    items = []

    # Price at end of line: optional $ then digits.decimals
    price_re = re.compile(r'\$?\s*(\d{1,5}[.,]\d{2})\s*$')

    for line in lines:
        line = line.strip()
        if not line or len(line) < 4:
            continue

        upper = line.upper()
        if any(kw in upper for kw in SKIP_KEYWORDS):
            continue

        match = price_re.search(line)
        if not match:
            continue

        try:
            amount = float(match.group(1).replace(',', '.'))
        except ValueError:
            continue

        if amount <= 0:
            continue

        # Name = everything before the price, cleaned up
        name = price_re.sub('', line).strip()
        name = re.sub(r'\s{2,}', ' ', name)       # collapse spaces
        name = re.sub(r'[.]{3,}', '', name).strip()  # remove dot leaders ......
        name = re.sub(r'^\d+\s*', '', name).strip()  # remove leading item numbers

        if name and len(name) >= 2:
            items.append({'name': name, 'amount': amount})

    return items


def _parse_total(text: str) -> float | None:
    lines = text.upper().split('\n')
    amount_re = re.compile(r'\$?\s*(\d{1,6}[.,]\d{2})')

    keywords = [
        'TOTAL', 'GRAND TOTAL', 'AMOUNT DUE', 'BALANCE DUE',
        'BALANCE', 'SUBTOTAL', 'ИТОГО', 'СУММА', 'К ОПЛАТЕ',
    ]

    for line in reversed(lines):
        for kw in keywords:
            if kw in line:
                matches = amount_re.findall(line)
                if matches:
                    try:
                        return float(matches[-1].replace(',', '.'))
                    except ValueError:
                        pass

    all_amounts = []
    for line in lines:
        for m in amount_re.findall(line):
            try:
                all_amounts.append(float(m.replace(',', '.')))
            except ValueError:
                pass

    return max(all_amounts) if all_amounts else None
