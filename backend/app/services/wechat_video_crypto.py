"""WeChat Channels encrypted MP4 helper."""
from __future__ import annotations

import struct


_MASK64 = 0xFFFFFFFFFFFFFFFF
_GOLDEN = 0x9E3779B97F4A7C13
_DEFAULT_ENCRYPTED_BYTES = 131072


def decrypt_wechat_channels_video(data: bytes, decode_key: int | str, *, enc_len: int = _DEFAULT_ENCRYPTED_BYTES) -> bytes:
    """Decrypt WeChat Channels MP4 bytes encrypted with the per-video decode key."""
    key = int(decode_key)
    output = bytearray(data)
    limit = min(max(0, int(enc_len)), len(output))
    rng = _Isaac64(key)
    for offset in range(0, limit, 8):
        key_bytes = struct.pack(">Q", rng.next())
        for index in range(min(8, limit - offset)):
            output[offset + index] ^= key_bytes[index]
    return bytes(output)


def _mix64(a: int, b: int, c: int, d: int, e: int, f: int, g: int, h: int) -> tuple[int, ...]:
    a = (a - e) & _MASK64
    f ^= (h >> 9) & _MASK64
    h = (h + a) & _MASK64
    b = (b - f) & _MASK64
    g ^= (a << 9) & _MASK64
    a = (a + b) & _MASK64
    c = (c - g) & _MASK64
    h ^= (b >> 23) & _MASK64
    b = (b + c) & _MASK64
    d = (d - h) & _MASK64
    a ^= (c << 15) & _MASK64
    c = (c + d) & _MASK64
    e = (e - a) & _MASK64
    b ^= (d >> 14) & _MASK64
    d = (d + e) & _MASK64
    f = (f - b) & _MASK64
    c ^= (e << 20) & _MASK64
    e = (e + f) & _MASK64
    g = (g - c) & _MASK64
    d ^= (f >> 17) & _MASK64
    f = (f + g) & _MASK64
    h = (h - d) & _MASK64
    e ^= (g << 14) & _MASK64
    g = (g + h) & _MASK64
    return a, b, c, d, e, f, g, h


class _Isaac64:
    def __init__(self, key: int) -> None:
        self.seed = [0] * 256
        self.mm = [0] * 256
        self.aa = 0
        self.bb = 0
        self.cc = 0
        self.count = 255
        self.seed[0] = key
        self._init()

    def _init(self) -> None:
        a = b = c = d = e = f = g = h = _GOLDEN
        for _ in range(4):
            a, b, c, d, e, f, g, h = _mix64(a, b, c, d, e, f, g, h)
        for offset in range(0, 256, 8):
            a = (a + self.seed[offset]) & _MASK64
            b = (b + self.seed[offset + 1]) & _MASK64
            c = (c + self.seed[offset + 2]) & _MASK64
            d = (d + self.seed[offset + 3]) & _MASK64
            e = (e + self.seed[offset + 4]) & _MASK64
            f = (f + self.seed[offset + 5]) & _MASK64
            g = (g + self.seed[offset + 6]) & _MASK64
            h = (h + self.seed[offset + 7]) & _MASK64
            a, b, c, d, e, f, g, h = _mix64(a, b, c, d, e, f, g, h)
            self.mm[offset : offset + 8] = [a, b, c, d, e, f, g, h]
        for offset in range(0, 256, 8):
            a = (a + self.mm[offset]) & _MASK64
            b = (b + self.mm[offset + 1]) & _MASK64
            c = (c + self.mm[offset + 2]) & _MASK64
            d = (d + self.mm[offset + 3]) & _MASK64
            e = (e + self.mm[offset + 4]) & _MASK64
            f = (f + self.mm[offset + 5]) & _MASK64
            g = (g + self.mm[offset + 6]) & _MASK64
            h = (h + self.mm[offset + 7]) & _MASK64
            a, b, c, d, e, f, g, h = _mix64(a, b, c, d, e, f, g, h)
            self.mm[offset : offset + 8] = [a, b, c, d, e, f, g, h]
        self._generate()

    def _generate(self) -> None:
        self.cc = (self.cc + 1) & _MASK64
        self.bb = (self.bb + self.cc) & _MASK64
        for index in range(256):
            x = self.mm[index]
            if index % 4 == 0:
                self.aa = ((self.aa ^ ((self.aa << 21) & _MASK64)) ^ _MASK64) & _MASK64
            elif index % 4 == 1:
                self.aa = (self.aa ^ (self.aa >> 5)) & _MASK64
            elif index % 4 == 2:
                self.aa = (self.aa ^ (self.aa << 12)) & _MASK64
            else:
                self.aa = (self.aa ^ (self.aa >> 33)) & _MASK64
            self.aa = (self.mm[(index + 128) % 256] + self.aa) & _MASK64
            y = (self.mm[(x >> 3) % 256] + self.aa + self.bb) & _MASK64
            self.mm[index] = y
            self.bb = (self.mm[(y >> 11) % 256] + x) & _MASK64
            self.seed[index] = self.bb

    def next(self) -> int:
        value = self.seed[self.count]
        if self.count == 0:
            self._generate()
            self.count = 255
        else:
            self.count -= 1
        return value
