MASK = (1 << 64) - 1
GOLDEN13 = 0x9e3779b97f4a7c13
GOLDEN15 = 0x9e3779b97f4a7c15

class ISAAC64:
    def __init__(self, seed, golden=GOLDEN13):
        self.golden = golden
        self.randrsl = [0] * 256
        self.mm = [0] * 256
        self.aa = self.bb = self.cc = 0
        self.randcnt = 0
        if seed is not None:
            self.randrsl[0] = seed & MASK
        self._randinit(seed is not None)

    def _mix(self, s):
        a, b, c, d, e, f, g, h = s
        a = (a - e) & MASK; f ^= h >> 9;  h = (h + a) & MASK
        b = (b - f) & MASK; g ^= (a << 9) & MASK; a = (a + b) & MASK
        c = (c - g) & MASK; h ^= b >> 23; b = (b + c) & MASK
        d = (d - h) & MASK; a ^= (c << 15) & MASK; c = (c + d) & MASK
        e = (e - a) & MASK; b ^= d >> 14; d = (d + e) & MASK
        f = (f - b) & MASK; c ^= (e << 20) & MASK; e = (e + f) & MASK
        g = (g - c) & MASK; d ^= f >> 17; f = (f + g) & MASK
        h = (h - d) & MASK; e ^= (g << 14) & MASK; g = (g + h) & MASK
        return [a, b, c, d, e, f, g, h]

    def _randinit(self, flag):
        s = [self.golden] * 8
        for _ in range(4):
            s = self._mix(s)
        for i in range(0, 256, 8):
            if flag:
                s = [(s[j] + self.randrsl[i + j]) & MASK for j in range(8)]
            s = self._mix(s)
            self.mm[i:i+8] = s
        if flag:
            for i in range(0, 256, 8):
                s = [(s[j] + self.mm[i + j]) & MASK for j in range(8)]
                s = self._mix(s)
                self.mm[i:i+8] = s
        self._isaac64()
        self.randcnt = 256

    def _isaac64(self):
        mm = self.mm
        rsl = self.randrsl
        a = self.aa
        cc = (self.cc + 1) & MASK
        self.cc = cc
        b = (self.bb + cc) & MASK
        m = 0
        r = 0
        m2 = 128
        while m < 128:
            x = mm[m]
            a = ((~(a ^ ((a << 21) & MASK))) & MASK) + mm[m2]
            a &= MASK
            y = (mm[(x >> 3) & 255] + a + b) & MASK
            mm[m] = y
            b = (mm[(y >> 11) & 255] + x) & MASK
            rsl[r] = b
            m += 1; m2 += 1; r += 1
            x = mm[m]
            a = ((a ^ (a >> 5)) & MASK) + mm[m2]
            a &= MASK
            y = (mm[(x >> 3) & 255] + a + b) & MASK
            mm[m] = y
            b = (mm[(y >> 11) & 255] + x) & MASK
            rsl[r] = b
            m += 1; m2 += 1; r += 1
            x = mm[m]
            a = ((a ^ ((a << 12) & MASK)) & MASK) + mm[m2]
            a &= MASK
            y = (mm[(x >> 3) & 255] + a + b) & MASK
            mm[m] = y
            b = (mm[(y >> 11) & 255] + x) & MASK
            rsl[r] = b
            m += 1; m2 += 1; r += 1
            x = mm[m]
            a = ((a ^ (a >> 33)) & MASK) + mm[m2]
            a &= MASK
            y = (mm[(x >> 3) & 255] + a + b) & MASK
            mm[m] = y
            b = (mm[(y >> 11) & 255] + x) & MASK
            rsl[r] = b
            m += 1; m2 += 1; r += 1
        m2 = 0
        while m < 256:
            x = mm[m]
            a = ((~(a ^ ((a << 21) & MASK))) & MASK) + mm[m2]
            a &= MASK
            y = (mm[(x >> 3) & 255] + a + b) & MASK
            mm[m] = y
            b = (mm[(y >> 11) & 255] + x) & MASK
            rsl[r] = b
            m += 1; m2 += 1; r += 1
            x = mm[m]
            a = ((a ^ (a >> 5)) & MASK) + mm[m2]
            a &= MASK
            y = (mm[(x >> 3) & 255] + a + b) & MASK
            mm[m] = y
            b = (mm[(y >> 11) & 255] + x) & MASK
            rsl[r] = b
            m += 1; m2 += 1; r += 1
            x = mm[m]
            a = ((a ^ ((a << 12) & MASK)) & MASK) + mm[m2]
            a &= MASK
            y = (mm[(x >> 3) & 255] + a + b) & MASK
            mm[m] = y
            b = (mm[(y >> 11) & 255] + x) & MASK
            rsl[r] = b
            m += 1; m2 += 1; r += 1
            x = mm[m]
            a = ((a ^ (a >> 33)) & MASK) + mm[m2]
            a &= MASK
            y = (mm[(x >> 3) & 255] + a + b) & MASK
            mm[m] = y
            b = (mm[(y >> 11) & 255] + x) & MASK
            rsl[r] = b
            m += 1; m2 += 1; r += 1
        self.aa = a
        self.bb = b

    def rand(self):
        if self.randcnt == 0:
            self._isaac64()
            self.randcnt = 255
        else:
            self.randcnt -= 1
        return self.randrsl[self.randcnt]

    def keystream(self, n, order="big", swap_words=False):
        out = bytearray()
        while len(out) < n:
            v = self.rand()
            if swap_words:
                # memory layout on little-endian x86 from IDA pseudo code: lo BE then hi BE
                lo = v & 0xffffffff
                hi = v >> 32
                out += lo.to_bytes(4, 'big') + hi.to_bytes(4, 'big')
            else:
                out += v.to_bytes(8, order)
        return bytes(out[:n])
