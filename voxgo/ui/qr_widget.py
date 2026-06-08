"""
Small QR code generator and widget for the mobile page URL.

The encoder supports byte-mode QR codes up to version 10 with error
correction level L, which is enough for local mobile URLs.
"""

from dataclasses import dataclass
from typing import List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt5.QtWidgets import QWidget


_GALOIS_EXP = [0] * 512
_GALOIS_LOG = [0] * 256


def _init_galois():
    value = 1
    for i in range(255):
        _GALOIS_EXP[i] = value
        _GALOIS_LOG[value] = i
        value <<= 1
        if value & 0x100:
            value ^= 0x11D
    for i in range(255, 512):
        _GALOIS_EXP[i] = _GALOIS_EXP[i - 255]


_init_galois()


def _gf_mul(x: int, y: int) -> int:
    if x == 0 or y == 0:
        return 0
    return _GALOIS_EXP[_GALOIS_LOG[x] + _GALOIS_LOG[y]]


def _rs_generator_poly(degree: int) -> List[int]:
    result = [1]
    for i in range(degree):
        result = _poly_mul(result, [1, _GALOIS_EXP[i]])
    return result


def _poly_mul(left: List[int], right: List[int]) -> List[int]:
    result = [0] * (len(left) + len(right) - 1)
    for i, x in enumerate(left):
        for j, y in enumerate(right):
            result[i + j] ^= _gf_mul(x, y)
    return result


def _rs_remainder(data: List[int], degree: int) -> List[int]:
    generator = _rs_generator_poly(degree)
    result = data + [0] * degree
    for i, value in enumerate(data):
        if value == 0:
            continue
        factor = result[i]
        for j, coef in enumerate(generator):
            result[i + j] ^= _gf_mul(coef, factor)
    return result[-degree:]


_QR_L_CAPACITY = {
    1: (19, 7),
    2: (34, 10),
    3: (55, 15),
    4: (80, 20),
    5: (108, 26),
    6: (136, 36),
    7: (156, 40),
    8: (194, 48),
    9: (232, 60),
    10: (274, 72),
}


@dataclass
class QrMatrix:
    modules: List[List[bool]]
    size: int


class QrCodeGenerator:
    """Generate simple byte-mode QR matrices for short URLs."""

    @classmethod
    def encode(cls, text: str) -> QrMatrix:
        data = text.encode("utf-8")
        version = cls._choose_version(len(data))
        data_codewords, ecc_codewords = _QR_L_CAPACITY[version]
        size = version * 4 + 17
        modules: List[List[Optional[bool]]] = [[None] * size for _ in range(size)]
        reserved = [[False] * size for _ in range(size)]

        cls._draw_function_patterns(modules, reserved, version)
        bits = cls._make_data_bits(data, data_codewords)
        codewords = cls._bits_to_codewords(bits)
        codewords.extend(_rs_remainder(codewords, ecc_codewords))
        cls._draw_codewords(modules, reserved, codewords)
        mask = cls._choose_mask(modules, reserved)
        cls._apply_mask(modules, reserved, mask)
        cls._draw_format_bits(modules, reserved, mask)

        return QrMatrix(
            modules=[[bool(cell) for cell in row] for row in modules],
            size=size,
        )

    @staticmethod
    def _choose_version(data_len: int) -> int:
        for version, (data_codewords, _) in _QR_L_CAPACITY.items():
            if data_len + 2 <= data_codewords:
                return version
        raise ValueError("QR data is too long")

    @classmethod
    def _make_data_bits(cls, data: bytes, data_codewords: int) -> List[int]:
        bits: List[int] = []
        cls._append_bits(bits, 0b0100, 4)
        cls._append_bits(bits, len(data), 8)
        for byte in data:
            cls._append_bits(bits, byte, 8)

        capacity = data_codewords * 8
        cls._append_bits(bits, 0, min(4, capacity - len(bits)))
        while len(bits) % 8:
            bits.append(0)

        pad = 0xEC
        while len(bits) < capacity:
            cls._append_bits(bits, pad, 8)
            pad ^= 0xEC ^ 0x11
        return bits

    @staticmethod
    def _append_bits(bits: List[int], value: int, count: int):
        for i in range(count - 1, -1, -1):
            bits.append((value >> i) & 1)

    @staticmethod
    def _bits_to_codewords(bits: List[int]) -> List[int]:
        return [
            sum(bits[i + j] << (7 - j) for j in range(8))
            for i in range(0, len(bits), 8)
        ]

    @classmethod
    def _draw_function_patterns(cls, modules, reserved, version: int):
        size = len(modules)
        cls._draw_finder(modules, reserved, 0, 0)
        cls._draw_finder(modules, reserved, size - 7, 0)
        cls._draw_finder(modules, reserved, 0, size - 7)

        for i in range(8, size - 8):
            value = i % 2 == 0
            cls._set_function(modules, reserved, i, 6, value)
            cls._set_function(modules, reserved, 6, i, value)

        cls._set_function(modules, reserved, 8, size - 8, True)

        if version >= 2:
            positions = cls._alignment_positions(version)
            for y in positions:
                for x in positions:
                    if (x <= 8 and y <= 8) or (x >= size - 9 and y <= 8) or (x <= 8 and y >= size - 9):
                        continue
                    cls._draw_alignment(modules, reserved, x - 2, y - 2)

        for i in range(9):
            cls._reserve(modules, reserved, 8, i)
            cls._reserve(modules, reserved, i, 8)
        for i in range(size - 8, size):
            cls._reserve(modules, reserved, 8, i)
            cls._reserve(modules, reserved, i, 8)

    @staticmethod
    def _alignment_positions(version: int) -> List[int]:
        if version == 1:
            return []
        size = version * 4 + 17
        count = version // 7 + 2
        if count == 2:
            return [6, size - 7]
        step = ((size - 13) + count - 2) // (count - 1)
        step += step & 1
        return [6] + [size - 7 - step * i for i in range(count - 2, -1, -1)]

    @staticmethod
    def _set_function(modules, reserved, x: int, y: int, value: bool):
        modules[y][x] = value
        reserved[y][x] = True

    @staticmethod
    def _reserve(modules, reserved, x: int, y: int):
        if 0 <= x < len(modules) and 0 <= y < len(modules):
            if modules[y][x] is None:
                modules[y][x] = False
            reserved[y][x] = True

    @classmethod
    def _draw_finder(cls, modules, reserved, x: int, y: int):
        for dy in range(-1, 8):
            for dx in range(-1, 8):
                xx, yy = x + dx, y + dy
                if not (0 <= xx < len(modules) and 0 <= yy < len(modules)):
                    continue
                value = (
                    0 <= dx <= 6
                    and 0 <= dy <= 6
                    and (dx in (0, 6) or dy in (0, 6) or (2 <= dx <= 4 and 2 <= dy <= 4))
                )
                cls._set_function(modules, reserved, xx, yy, value)

    @classmethod
    def _draw_alignment(cls, modules, reserved, x: int, y: int):
        for dy in range(5):
            for dx in range(5):
                value = dx in (0, 4) or dy in (0, 4) or (dx == 2 and dy == 2)
                cls._set_function(modules, reserved, x + dx, y + dy, value)

    @staticmethod
    def _draw_codewords(modules, reserved, codewords: List[int]):
        bits = []
        for codeword in codewords:
            bits.extend((codeword >> i) & 1 for i in range(7, -1, -1))

        size = len(modules)
        bit_index = 0
        direction = -1
        x = size - 1
        y = size - 1
        while x > 0:
            if x == 6:
                x -= 1
            for _ in range(size):
                for dx in (0, 1):
                    xx = x - dx
                    if not reserved[y][xx]:
                        modules[y][xx] = bit_index < len(bits) and bits[bit_index] == 1
                        bit_index += 1
                y += direction
                if y < 0 or y >= size:
                    y -= direction
                    direction = -direction
                    break
            x -= 2

    @staticmethod
    def _mask_bit(mask: int, x: int, y: int) -> bool:
        patterns = [
            (x + y) % 2 == 0,
            y % 2 == 0,
            x % 3 == 0,
            (x + y) % 3 == 0,
            ((y // 2) + (x // 3)) % 2 == 0,
            (x * y) % 2 + (x * y) % 3 == 0,
            ((x * y) % 2 + (x * y) % 3) % 2 == 0,
            ((x + y) % 2 + (x * y) % 3) % 2 == 0,
        ]
        return patterns[mask]

    @classmethod
    def _choose_mask(cls, modules, reserved) -> int:
        best_mask = 0
        best_score = None
        for mask in range(8):
            trial = [[False if cell is None else bool(cell) for cell in row] for row in modules]
            cls._apply_mask(trial, reserved, mask)
            score = cls._penalty_score(trial)
            if best_score is None or score < best_score:
                best_mask = mask
                best_score = score
        return best_mask

    @classmethod
    def _apply_mask(cls, modules, reserved, mask: int):
        size = len(modules)
        for y in range(size):
            for x in range(size):
                if not reserved[y][x] and cls._mask_bit(mask, x, y):
                    modules[y][x] = not modules[y][x]

    @staticmethod
    def _penalty_score(modules) -> int:
        size = len(modules)
        score = 0
        for y in range(size):
            run_color = modules[y][0]
            run_len = 1
            for x in range(1, size):
                if modules[y][x] == run_color:
                    run_len += 1
                else:
                    if run_len >= 5:
                        score += run_len - 2
                    run_color = modules[y][x]
                    run_len = 1
            if run_len >= 5:
                score += run_len - 2

        for x in range(size):
            run_color = modules[0][x]
            run_len = 1
            for y in range(1, size):
                if modules[y][x] == run_color:
                    run_len += 1
                else:
                    if run_len >= 5:
                        score += run_len - 2
                    run_color = modules[y][x]
                    run_len = 1
            if run_len >= 5:
                score += run_len - 2
        return score

    @classmethod
    def _draw_format_bits(cls, modules, reserved, mask: int):
        size = len(modules)
        data = mask
        bits = data << 10
        generator = 0x537
        for i in range(14, 9, -1):
            if (bits >> i) & 1:
                bits ^= generator << (i - 10)
        format_bits = ((data << 10) | bits) ^ 0x5412

        positions1 = [
            (8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5),
            (8, 7), (8, 8), (7, 8), (5, 8), (4, 8), (3, 8),
            (2, 8), (1, 8), (0, 8),
        ]
        positions2 = [
            (size - 1, 8), (size - 2, 8), (size - 3, 8), (size - 4, 8),
            (size - 5, 8), (size - 6, 8), (size - 7, 8), (8, size - 8),
            (8, size - 7), (8, size - 6), (8, size - 5), (8, size - 4),
            (8, size - 3), (8, size - 2), (8, size - 1),
        ]
        for i in range(15):
            bit = ((format_bits >> i) & 1) == 1
            x, y = positions1[i]
            cls._set_function(modules, reserved, x, y, bit)
            x, y = positions2[i]
            cls._set_function(modules, reserved, x, y, bit)


class QrCodeWidget(QWidget):
    """Paint a generated QR matrix."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._matrix: Optional[QrMatrix] = None
        self._pixmap: Optional[QPixmap] = None
        self._text = ""
        self.setFixedSize(236, 236)
        if text:
            self.set_text(text)

    def set_text(self, text: str):
        self._text = text
        self.setToolTip(text)
        self._pixmap = self._make_library_pixmap(text)
        try:
            self._matrix = QrCodeGenerator.encode(text)
        except Exception:
            self._matrix = None
        self.update()

    def _make_library_pixmap(self, text: str) -> Optional[QPixmap]:
        try:
            import qrcode
            from qrcode.constants import ERROR_CORRECT_M

            qr = qrcode.QRCode(
                version=None,
                error_correction=ERROR_CORRECT_M,
                box_size=10,
                border=5,
            )
            qr.add_data(text)
            qr.make(fit=True)
            image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
            image = image.resize((228, 228))
            data = image.tobytes("raw", "RGB")
            qimage = QImage(data, image.width, image.height, QImage.Format_RGB888)
            return QPixmap.fromImage(qimage.copy())
        except Exception:
            return None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillRect(self.rect(), QColor("#FFFFFF"))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#111111"))

        if self._pixmap:
            x = (self.width() - self._pixmap.width()) // 2
            y = (self.height() - self._pixmap.height()) // 2
            painter.drawPixmap(x, y, self._pixmap)
            return

        if not self._matrix:
            painter.setPen(QColor("#111111"))
            painter.drawText(self.rect(), Qt.AlignCenter | Qt.TextWordWrap, self._text)
            return

        quiet = 4
        count = self._matrix.size + quiet * 2
        module = min(self.width(), self.height()) // count
        origin_x = (self.width() - module * count) // 2 + quiet * module
        origin_y = (self.height() - module * count) // 2 + quiet * module
        for y, row in enumerate(self._matrix.modules):
            for x, dark in enumerate(row):
                if dark:
                    painter.drawRect(origin_x + x * module, origin_y + y * module, module, module)
