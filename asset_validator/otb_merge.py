"""Preserve full OTB definitions while merging only selected source items."""
from pathlib import Path

from .formats import FormatError, Reader, tree_nodes


def definitions(path):
    root, items, mappings = None, {}, {}
    for kind, depth, props in tree_nodes(path):
        if depth == 0:
            root = (kind, props)
            continue
        r = Reader(props)
        r.u32()
        attrs = {}
        while r.pos < len(props):
            key, length = r.u8(), r.u16()
            attrs[key] = r.take(length)
        sid = Reader(attrs[16]).u16()
        cid = Reader(attrs[17]).u16() if 17 in attrs else 0
        items[sid], mappings[sid] = (kind, props), cid
    return root, items, mappings


def merge_otb(source, target, output, selected):
    root, items, mappings = definitions(source)
    items = {sid: item for sid, item in items.items() if mappings[sid] in selected}
    if target:
        _, other, other_mappings = definitions(target)
        for sid in items.keys() & other.keys():
            if mappings[sid] != other_mappings[sid]:
                raise FormatError(f'Conflicting OTB mapping for Server ID {sid}')
        items = other | items
    def encode(node):
        kind, props = node
        raw = bytearray()
        for value in bytes([kind]) + props:
            if value >= 253:
                raw.append(253)
            raw.append(value)
        return bytes(raw)
    with Path(output).open('wb') as file:
        file.write(b'\0' * 4 + b'\xfe' + encode(root))
        for sid, item in sorted(items.items()):
            file.write(b'\xfe' + encode(item) + b'\xff')
        file.write(b'\xff')
