"""Strict readers for extended 10.98 DAT/SPR and escaped OTB/OTBM trees.

Records retain their original bytes; only sprite references are rewritten.
"""
from dataclasses import dataclass
from pathlib import Path
import math
import mmap
import re
import struct


class FormatError(ValueError):
    pass


class Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def take(self, size):
        if size < 0 or self.pos + size > len(self.data):
            raise FormatError(f"Truncated data at byte {self.pos}, need {size} bytes")
        result = self.data[self.pos:self.pos + size]
        self.pos += size
        return result

    def number(self, fmt):
        return struct.unpack('<' + fmt, self.take(struct.calcsize('<' + fmt)))[0]

    def u8(self):
        return self.number('B')

    def u16(self):
        return self.number('H')

    def u32(self):
        return self.number('I')

    def end(self):
        if self.pos != len(self.data):
            raise FormatError(f"Unexpected trailing data at byte {self.pos}")


def tree_nodes(path):
    """Yield (type, depth, properties), including containers, without retaining trees."""
    with Path(path).open('rb') as file:
        with mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as data:
            if data[:4] not in (b'OTBM', b'\0' * 4) or data[4:5] != b'\xfe':
                raise FormatError(f"Invalid OTB/OTBM header: {path}")
            stack, pos, skip, roots = [], 4, -1, 0
            for match in re.finditer(b'[\xfd-\xff]', data):
                index = match.start()
                if index < 4 or index == skip:
                    continue
                if stack:
                    if stack[-1][1] is None and index > pos:
                        raise FormatError('Properties after children are unsupported')
                    if stack[-1][1] is not None:
                        stack[-1][1].extend(data[pos:index])
                elif index != pos:
                    raise FormatError('Data outside root node')
                control = data[index]
                if control == 253:
                    if not stack or stack[-1][1] is None or index + 1 >= len(data):
                        raise FormatError('Invalid node escape')
                    stack[-1][1].append(data[index + 1])
                    skip, pos = index + 1, index + 2
                    continue
                if control == 254:
                    if stack and stack[-1][1] is not None:
                        props = bytes(stack[-1][1])
                        if not props:
                            raise FormatError('Empty node')
                        yield props[0], len(stack) - 1, props[1:]
                        stack[-1][1] = None
                    if not stack:
                        roots += 1
                        if roots != 1:
                            raise FormatError('Multiple root nodes')
                    if len(stack) >= 128:
                        raise FormatError('Excessive node nesting')
                    stack.append([index, bytearray()])
                else:
                    if not stack:
                        raise FormatError('Unexpected node end')
                    if stack[-1][1] is not None:
                        props = bytes(stack[-1][1])
                        if not props:
                            raise FormatError('Empty node')
                        yield props[0], len(stack) - 1, props[1:]
                    stack.pop()
                pos = index + 1
            if stack or roots != 1 or pos != len(data):
                raise FormatError('Truncated tree or trailing bytes')


def read_otb(path, allow_empty=False):
    mappings, flags, version = {}, {}, None
    for kind, depth, props in tree_nodes(path):
        r = Reader(props)
        value = r.u32()
        attributes = {}
        while r.pos < len(props):
            key, length = r.u8(), r.u16()
            if key in attributes:
                raise FormatError(f'Duplicate OTB attribute {key}')
            attributes[key] = r.take(length)
        if depth == 0:
            if kind != 0:
                raise FormatError('Invalid OTB root')
            if 1 in attributes:
                if len(attributes[1]) < 12:
                    raise FormatError('Invalid OTB version')
                version = list(struct.unpack('<III', attributes[1][:12]))
            continue
        if depth != 1:
            raise FormatError('Unexpected nested OTB item')
        if kind == 14 and 17 not in attributes:
            attributes[17] = b'\0\0'  # deprecated item; never a renderable dependency
        if any(len(attributes.get(key, b'')) != 2 for key in (16, 17)):
            raise FormatError('OTB item missing server/client ID')
        sid = struct.unpack('<H', attributes[16])[0]
        cid = struct.unpack('<H', attributes[17])[0]
        if sid in mappings:
            raise FormatError(f'Duplicate OTB server ID {sid}')
        mappings[sid], flags[sid] = cid, value
    if not mappings and not allow_empty:
        raise FormatError('Empty OTB')
    return mappings, flags, version


def read_map(path, item_flags=None):
    from collections import Counter
    counts, tiles, root, map_data = Counter(), 0, None, False
    parents = {}
    for kind, depth, props in tree_nodes(path):
        parents[depth] = kind
        r = Reader(props)
        if depth == 0:
            if kind not in (0, 1) or len(props) != 16:
                raise FormatError('Unsupported map root')
            version = r.u32()
            if version not in (0, 1, 2, 3):
                raise FormatError(f'Unsupported OTBM version {version}')
            root = dict(version=version, width=r.u16(), height=r.u16(),
                        otb_major=r.u32(), otb_minor=r.u32())
        elif kind == 2 and depth == 1:
            map_data = True
        elif kind in (5, 14):
            if parents.get(depth - 1) != 4:
                raise FormatError('Tile outside tile area')
            tiles += 1
            r.take(6 if kind == 14 else 2)
            while r.pos < len(props):
                attr = r.u8()
                if attr == 3:
                    r.take(4)
                elif attr == 9:
                    sid = r.u16()
                    counts[sid] += 1
                    if root['version'] == 0:
                        if item_flags is None or sid not in item_flags:
                            raise FormatError(f'OTBM v0 needs OTB flags for compact item {sid}')
                        if item_flags[sid] & (1 << 7):
                            r.u8()  # legacy compact stack count
                else:
                    raise FormatError(f'Unsupported tile attribute {attr}')
        elif kind == 6:
            if parents.get(depth - 1) not in (5, 14, 6):
                raise FormatError('Item outside tile/container')
            counts[r.u16()] += 1
        elif kind in (7, 8, 3):
            raise FormatError(f'Unsupported map item/tile encoding {kind}')
    if root is None or not map_data or not tiles or not counts:
        raise FormatError('Map has no readable tiles/items')
    return counts, dict(root, tiles=tiles, occurrences=sum(counts.values()))


CATEGORIES = ('items', 'outfits', 'effects', 'missiles')


@dataclass(frozen=True)
class Profile:
    version: int = 1098
    extended: bool = True
    transparency: bool = True
    durations: bool = True
    groups: bool = True
    size: int = 32

    @property
    def index_bytes(self):
        return 4 if self.extended else 2

    @property
    def color_bytes(self):
        return 4 if self.transparency else 3

    @property
    def modern_flags(self):
        return self.version >= 1010


@dataclass
class Record:
    raw: bytes
    offsets: tuple
    index_bytes: int = 4

    def sprites(self):
        return tuple(struct.unpack_from('<I' if self.index_bytes == 4 else '<H', self.raw, p)[0] for p in self.offsets)

    def rewrite(self, mapping):
        raw = bytearray(self.raw)
        for offset, sid in zip(self.offsets, self.sprites()):
            struct.pack_into('<I' if self.index_bytes == 4 else '<H', raw, offset, mapping[sid])
        return bytes(raw)

    def metadata(self):
        raw = bytearray(self.raw)
        for offset in self.offsets:
            raw[offset:offset + self.index_bytes] = b'\0' * self.index_bytes
        return bytes(raw)


class Dat:
    def __init__(self, path, profile=None):
        self.profile = profile or Profile()
        self.path = Path(path)
        r = Reader(self.path.read_bytes())
        self.signature = r.u32()
        self.counts = [r.u16() for _ in CATEGORIES]
        if self.counts[0] < 99:
            raise FormatError('Invalid DAT item count')
        self.records = {}
        for category, count in zip(CATEGORIES, self.counts):
            records = self.records[category] = {}
            for cid in range(100 if category == 'items' else 1, count + 1):
                try:
                    start, offsets = r.pos, []
                    attributes = set()
                    while True:
                        attr = r.u8()
                        if attr == 255:
                            break
                        if attr in attributes:
                            raise FormatError(f'Duplicate DAT attribute {attr}')
                        attributes.add(attr)
                        if not self.profile.modern_flags and attr >= 16:
                            attr += 1
                        if attr in (0, 8, 9, 26, 29, 30, 33, 35):
                            r.take(2)
                        elif attr in (22, 25):
                            r.take(4)
                        elif attr == 34:
                            r.take(6)
                            r.take(r.u16())
                            r.take(4)
                        elif attr not in range(39) and not (self.profile.modern_flags and attr == 254):
                            raise FormatError(f'Unknown DAT attribute {attr}')
                    has_groups = self.profile.groups and category == 'outfits'
                    groups = r.u8() if has_groups else 1
                    if groups not in (1, 2):
                        raise FormatError(f'Invalid frame group count {groups}')
                    for _ in range(groups):
                        if has_groups:
                            if r.u8() not in (0, 1):
                                raise FormatError('Invalid frame group type')
                        width, height = r.u8(), r.u8()
                        if width > 1 or height > 1:
                            r.u8()
                        dimensions = [width, height] + [r.u8() for _ in range(5)]
                        total = math.prod(dimensions)
                        if not total or total > 1048576:
                            raise FormatError(f'Invalid dimensions {dimensions}')
                        phases = dimensions[-1]
                        if phases > 1 and self.profile.durations:
                            mode = r.u8()
                            loops = r.number('i')
                            first = r.number('b')
                            if mode not in (0, 1) or loops < -1 or first < -1 or first >= phases:
                                raise FormatError('Invalid animation parameters')
                            for _ in range(phases):
                                minimum, maximum = r.u32(), r.u32()
                                if minimum > maximum:
                                    raise FormatError('Invalid animation duration range')
                        index_bytes = self.profile.index_bytes
                        offsets.extend(range(r.pos - start, r.pos - start + total * index_bytes, index_bytes))
                        r.take(total * index_bytes)
                    records[cid] = Record(r.data[start:r.pos], tuple(offsets), index_bytes)
                except FormatError as error:
                    raise FormatError(f'{self.path}: {category} ID {cid}: {error}') from error
        r.end()

    def write(self, path, records):
        counts = [max(records[c], default=99 if c == 'items' else 0) for c in CATEGORIES]
        with Path(path).open('wb') as file:
            file.write(struct.pack('<I4H', self.signature, *counts))
            for category, count in zip(CATEGORIES, counts):
                for cid in range(100 if category == 'items' else 1, count + 1):
                    file.write(records[category][cid])


EMPTY_ITEM = b'\xff' + b'\x01' * 7 + b'\0' * 4


class Spr:
    def __init__(self, path, profile=None):
        self.profile = profile or Profile()
        self.path = Path(path)
        self.file = self.path.open('rb')
        try:
            self.data = mmap.mmap(self.file.fileno(), 0, access=mmap.ACCESS_READ)
            r = Reader(self.data)
            self.signature = r.u32()
            self.count = r.u32() if self.profile.extended else r.u16()
            self.header_size = r.pos
            if self.count > (len(self.data) - self.header_size) // 4:
                raise FormatError('Truncated SPR address table')
            self.validated = set()
        except Exception:
            self.close()
            raise

    def close(self):
        if hasattr(self, 'data'):
            self.data.close()
        self.file.close()

    def validate_all(self):
        ranges = set()
        for sid in range(1, self.count + 1):
            blob = self.blob(sid)
            if blob:
                offset = struct.unpack_from('<I', self.data, self.header_size + (sid - 1) * 4)[0]
                ranges.add((offset, offset + len(blob)))
        previous_end = 0
        for start, end in sorted(ranges):
            if start < previous_end:
                raise FormatError('Overlapping SPR blocks')
            previous_end = end

    def blob(self, sid):
        if sid == 0:
            return b''
        if sid < 0 or sid > self.count:
            raise FormatError(f'{self.path}: sprite {sid} outside 1..{self.count}')
        offset = struct.unpack_from('<I', self.data, self.header_size + (sid - 1) * 4)[0]
        if offset == 0:
            return b''
        if offset < self.header_size + self.count * 4 or offset + 5 > len(self.data):
            raise FormatError(f'Invalid SPR offset for {sid}')
        size = struct.unpack_from('<H', self.data, offset + 3)[0]
        if offset + 5 + size > len(self.data):
            raise FormatError(f'Truncated SPR sprite {sid}')
        blob = self.data[offset:offset + 5 + size]
        if sid not in self.validated:
            r, pixels = Reader(blob[5:]), 0
            while r.pos < len(r.data):
                transparent, colored = r.u16(), r.u16()
                pixels += transparent + colored
                if pixels > self.profile.size ** 2 or transparent + colored == 0:
                    raise FormatError(f'Invalid RGBA/RGB sprite run for {sid}')
                r.take(colored * self.profile.color_bytes)
            self.validated.add(sid)
        return blob


def read_profile(directory):
    path = Path(directory) / 'Tibia.otfi'
    if not path.exists():
        raise FormatError(f'Missing {path}; explicit asset profile required')
    values = {}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if ':' in line:
            key, value = line.strip().split(':', 1)
            if key in values:
                raise FormatError(f'Duplicate OTFI key {key}')
            values[key] = value.strip()
    def boolean(key):
        if values.get(key) not in ('true', 'false'):
            raise FormatError(f'OTFI {key} must be explicitly true/false')
        return values[key] == 'true'
    version = int(values.get('client-version', '1098'))
    size = int(values.get('sprite-size', '32'))
    if not (860 <= version <= 986 or 1010 <= version <= 1098) or size not in (32, 64, 128):
        raise FormatError(f'Unsupported client version/sprite size in {path}')
    profile = Profile(version, boolean('extended'), boolean('transparency'),
                      boolean('frame-durations'), boolean('frame-groups'), size)
    if profile.version < 1010 and (profile.durations or profile.groups):
        raise FormatError('Legacy DAT cannot declare enhanced animations/frame groups')
    if values.get('metadata-file') != 'Tibia.dat' or values.get('sprites-file') != 'Tibia.spr':
        raise FormatError('Only explicit Tibia.dat/Tibia.spr filenames are supported')
    if int(values.get('sprite-data-size', '0')) != size * size * 4:
        raise FormatError('sprite-data-size must describe the decoded RGBA buffer')
    return profile


def check_profile(directory):
    read_profile(directory)
    path = Path(directory) / 'Tibia.otfi'
    return path


def empty_record(category, profile):
    raw = b'\xff'
    if category == 'outfits' and profile.groups:
        raw += b'\x01\x00'
    raw += b'\x01' * 7 + b'\0' * profile.index_bytes
    return Record(raw, (len(raw) - profile.index_bytes,), profile.index_bytes)
