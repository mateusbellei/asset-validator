from contextlib import closing, ExitStack
import json
from pathlib import Path
import struct
import tempfile
import unittest

from asset_validator.formats import Dat, EMPTY_ITEM, FormatError, Spr, read_map, read_otb, tree_nodes
from asset_validator.pipeline import fingerprint, load_config, run, verify


def u16(value):
    return struct.pack('<H', value)


def u32(value):
    return struct.pack('<I', value)


def node(kind, props=b'', children=()):
    escaped = bytearray()
    for byte in bytes([kind]) + props:
        if byte >= 253:
            escaped.append(253)
        escaped.append(byte)
    return b'\xfe' + escaped + b''.join(children) + b'\xff'


def item(sprite, attrs=b''):
    return attrs + b'\xff' + b'\x01' * 7 + u32(sprite)


def animated_outfit(sprite):
    dimensions = b'\x01' * 6 + b'\x02'
    animation = b'\x00' + u32(0) + b'\x00' + u32(100) + u32(200) + u32(200) + u32(300)
    return b'\xff\x02' + b'\x00' + dimensions + animation + u32(sprite) * 2 + b'\x01' + dimensions + animation + u32(sprite) * 2


def make_base(directory, items, outfits=(), effects=(), missiles=(), colors=((255, 0, 253, 254),)):
    directory.mkdir()
    counts = (len(items) + 99, len(outfits), len(effects), len(missiles))
    (directory / 'Tibia.dat').write_bytes(struct.pack('<I4H', 0x12345678, *counts) + b''.join(items + list(outfits) + list(effects) + list(missiles)))
    blobs = []
    for color in colors:
        payload = u16(0) + u16(1) + bytes(color)
        blobs.append(b'\xff\x00\xff' + u16(len(payload)) + payload)
    offset = 8 + len(blobs) * 4
    addresses = []
    for blob in blobs:
        addresses.append(u32(offset))
        offset += len(blob)
    (directory / 'Tibia.spr').write_bytes(u32(0x87654321) + u32(len(blobs)) + b''.join(addresses) + b''.join(blobs))
    (directory / 'Tibia.otfi').write_text('''DatSpr
  extended: true
  transparency: true
  frame-durations: true
  frame-groups: true
  metadata-file: Tibia.dat
  sprites-file: Tibia.spr
  sprite-size: 32
  sprite-data-size: 4096
''', encoding='utf-8')


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source, self.target = self.root / 'source', self.root / 'target'
        make_base(self.source, [item(1, b'\x00' + u16(253)), item(2), item(1)],
                  outfits=[animated_outfit(2)], effects=[item(2)], missiles=[item(1)],
                  colors=((255, 0, 253, 254), (0, 255, 0, 128)))
        make_base(self.target, [item(1, b'\x0c')], outfits=[animated_outfit(1)],
                  colors=((0, 0, 255, 255),))
        attrs = lambda sid, cid: u32(0) + b'\x10\x02\x00' + u16(sid) + b'\x11\x02\x00' + u16(cid)
        self.otb = self.root / 'items.otb'
        self.otb.write_bytes(b'\0' * 4 + node(0, u32(0), [node(1, attrs(253, 100)), node(1, attrs(254, 102))]))
        self.map = self.root / 'map.otbm'
        tile = node(5, b'\x00\x00\x03' + u32(253) + b'\x09' + u16(253),
                    [node(6, u16(254), [node(6, u16(253))])])
        house = node(14, b'\x01\x01' + u32(1) + b'\x09' + u16(254))
        header = u32(2) + u16(6000) * 2 + u32(3) + u32(57)
        self.map.write_bytes(b'OTBM' + node(0, header, [node(2, b'', [node(4, u16(0) * 2 + b'\x07', [tile, house])])]))
        self.config_path = self.root / 'config.json'
        self.config_path.write_text(json.dumps(dict(map='map.otbm', otb='items.otb', source='source', target='target')), encoding='utf-8')
        self.config = load_config(self.config_path)

    def test_all_map_items_including_escaped_house_and_nested_items(self):
        counts, info = read_map(self.map)
        self.assertEqual(dict(counts), {253: 2, 254: 2})
        self.assertEqual(info['tiles'], 2)
        self.assertEqual(read_otb(self.otb)[0], {253: 100, 254: 102})

    def test_merge_preserves_client_ids_metadata_target_outfits_and_originals(self):
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        report = run(self.config, self.root / 'result', build=True)
        self.assertEqual(report['selected']['items'], [100, 102])
        self.assertTrue(report['validation']['all_record_fingerprints_match'])
        result = Dat(self.root / 'result/Tibia.dat')
        self.assertEqual(result.counts, [102, 1, 0, 0])
        self.assertEqual(result.records['items'][101].raw, EMPTY_ITEM)
        self.assertEqual((self.root / 'result/items.otb').read_bytes(), self.otb.read_bytes())
        with ExitStack() as stack:
            out = Spr(self.root / 'result/Tibia.spr'); stack.callback(out.close)
            src = Spr(self.source / 'Tibia.spr'); stack.callback(src.close)
            dst = Spr(self.target / 'Tibia.spr'); stack.callback(dst.close)
            for cid in (100, 102):
                self.assertEqual(fingerprint(result.records['items'][cid], out), fingerprint(Dat(self.source / 'Tibia.dat').records['items'][cid], src))
            self.assertEqual(fingerprint(result.records['outfits'][1], out), fingerprint(Dat(self.target / 'Tibia.dat').records['outfits'][1], dst))
            self.assertEqual(out.count, 2)  # source IDs 100 and 102 share one sprite
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_map_only_keeps_holes_and_imports_explicit_animated_outfits(self):
        self.config['mode'] = 'map-only'
        self.config['extra'] = {'outfits': [1], 'effects': [1], 'missiles': [1]}
        report = run(self.config, self.root / 'clean', build=True)
        result = Dat(self.root / 'clean/Tibia.dat')
        self.assertEqual(result.counts, [102, 1, 1, 1])
        self.assertEqual(report['validation']['sprites'], 2)
        self.assertEqual(len(result.records['outfits'][1].offsets), 4)

    def test_missing_otb_dependency_reports_and_blocks_build(self):
        self.otb.write_bytes(b'\0' * 4 + node(0, u32(0), [node(1, u32(0) + b'\x10\x02\x00' + u16(253) + b'\x11\x02\x00' + u16(100))]))
        report = run(self.config, self.root / 'audit')
        self.assertTrue(report['errors'])
        with self.assertRaises(FormatError):
            run(self.config, self.root / 'failed', build=True)
        self.assertFalse((self.root / 'failed').exists())
        self.assertFalse(list(self.root.glob('.asset-validator-*')))

    def test_deprecated_otb_item_cannot_be_migrated(self):
        self.otb.write_bytes(b'\0' * 4 + node(0, u32(0), [node(14, u32(0) + b'\x10\x02\x00' + u16(253))]))
        self.assertEqual(read_otb(self.otb)[0][253], 0)
        with self.assertRaises(FormatError):
            run(self.config, self.root / 'failed', build=True)

    def test_unsupported_profile_and_unknown_tile_attribute_are_rejected(self):
        profile = self.source / 'Tibia.otfi'
        profile.write_text(profile.read_text().replace('transparency: true', 'transparency: false'))
        with self.assertRaises(FormatError):
            run(self.config, self.root / 'failed')
        header = u32(2) + u16(10) * 2 + u32(3) + u32(57)
        self.map.write_bytes(b'OTBM' + node(0, header, [node(2, b'', [node(4, b'', [node(5, b'\x00\x00\x7f')])])]))
        with self.assertRaisesRegex(FormatError, 'tile attribute'):
            read_map(self.map)

    def test_truncated_tree_and_dat_and_invalid_sprite_are_rejected(self):
        self.map.write_bytes(self.map.read_bytes()[:-1])
        with self.assertRaises(FormatError):
            read_map(self.map)
        dat = self.source / 'Tibia.dat'
        dat.write_bytes(dat.read_bytes()[:-1])
        with self.assertRaises(FormatError):
            Dat(dat)
        with closing(Spr(self.source / 'Tibia.spr')) as spr:
            with self.assertRaises(FormatError):
                spr.blob(999)

    def test_overwrite_and_output_inside_original_base_are_rejected(self):
        with self.assertRaises(ValueError):
            run(self.config, self.target)
        with self.assertRaises(ValueError):
            run(self.config, self.source / 'generated')

    def test_duplicate_otb_ids_rejected(self):
        props = u32(0) + b'\x10\x02\x00' + u16(253) + b'\x11\x02\x00' + u16(100)
        self.otb.write_bytes(b'\0' * 4 + node(0, u32(0), [node(1, props), node(1, props)]))
        with self.assertRaisesRegex(FormatError, 'Duplicate OTB server'):
            read_otb(self.otb)

    def test_corrupt_rgba_runs_and_offsets_rejected(self):
        spr = self.source / 'Tibia.spr'
        data = bytearray(spr.read_bytes())
        first = struct.unpack_from('<I', data, 8)[0]
        struct.pack_into('<H', data, first + 5, 1025)
        spr.write_bytes(data)
        with closing(Spr(spr)) as reader:
            with self.assertRaisesRegex(FormatError, 'RGBA'):
                reader.blob(1)
        struct.pack_into('<I', data, 8, 1)
        spr.write_bytes(data)
        with closing(Spr(spr)) as reader:
            with self.assertRaisesRegex(FormatError, 'offset'):
                reader.blob(1)


if __name__ == '__main__':
    unittest.main()
