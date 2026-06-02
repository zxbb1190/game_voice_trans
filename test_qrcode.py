"""
Generate a QR code PNG for manual scan testing.
"""

from pathlib import Path

import qrcode
from qrcode.constants import ERROR_CORRECT_M


def main():
    url = "http://199.199.199.77:8765/mobile"
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=12, border=5)
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    output = Path("mobile_qr_test.png")
    image.save(output)
    print(f"ok {output.resolve()}")


if __name__ == "__main__":
    main()
