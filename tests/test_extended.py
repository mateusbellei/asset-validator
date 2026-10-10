from contextlib import ExitStack
import json
from pathlib import Path
import struct
import unittest

import test_pipeline as fixture
from test_pipeline import item, node, u16, u32
from asset_validator.dependencies import discover, integer, scan_files
from asset_validator.formats import Dat, FormatError, Spr, read_otb, read_profile
from asset_validator.pipeline import load_config, run, validate_directory, sha256


class ExtendedTests(unittest.TestCase):
    def setUp(self):
        fixture.PipelineTests.setUp(self)

    def source_only(self):
        self.config.pop('target')
        self.config['mode'] = 'map-only'

    def script(self, name, text):
        path = self.root / name
        path.write_text(text, encoding='utf-8')
        self.config['scan']['paths'].append(str(path))
        return path

    def test_source_only_audit_and_build_have_no_target_dependency(self):
        self.source_only()
        report = run(self.config, self.root / 'audit')
        self.assertIsNone(report['target_counts'])
        self.assertEqual(report['summary']['conflicts'], 0)
        run(self.config, self.root / 'clean', build=True)
        self.assertTrue(validate_directory(self.root / 'clean', inputs=True)['manifest_verified'])

    def test_default_merge_blocks_conflicts_and_reviewed_plan_allows_them(self):
        with self.assertRaisesRegex(FormatError, 'reviewed'):
            run(self.config, self.root / 'blocked', build=True)
        self.assertFalse((self.root / 'blocked').exists())
        self.config['conflicts'] = 'replace-reviewed'
        run(self.config, self.root / 'audit')
        run(self.config, self.root / 'merged', build=True, plan=self.root / 'audit/report.json')
        self.assertTrue(validate_directory(self.root / 'merged', inputs=True)['inputs_verified'])

    def test_stale_plan_rejects_changed_valid_input(self):
        self.config['conflicts'] = 'replace-reviewed'
        run(self.config, self.root / 'audit')
        spr = self.target / 'Tibia.spr'
        data = bytearray(spr.read_bytes()); data[-1] = 128; spr.write_bytes(data)
        with self.assertRaisesRegex(FormatError, 'Stale'):
            run(self.config, self.root / 'merged', build=True, plan=self.root / 'audit/report.json')

    def test_lua_xml_and_cpp_resolve_items_outfits_effects_and_missiles(self):
        self.source_only()
        self.script('constants.h', 'enum { CONST_ME_TEST = 1, CONST_ANI_TEST = 1 };')
        self.script('spell.lua', '''-- Game.createItem(999)
local text = "lookType = 999"
Game.createItem(253)
position:sendMagicEffect(CONST_ME_TEST)
position:sendDistanceEffect(target, CONST_ANI_TEST)
combat:setParameter(COMBAT_PARAM_EFFECT, CONST_ME_TEST)
local outfit = {lookType = 1}
''')
        self.script('monster.xml', '<monster><look type="1"/><item id="254"><attribute key="decayTo" value="253"/></item></monster>')
        report = run(self.config, self.root / 'audit')
        self.assertFalse(report['errors'])
        self.assertEqual(report['selected'], dict(items=[100, 102], outfits=[1], effects=[1], missiles=[1]))
        run(self.config, self.root / 'base', build=True)
        self.assertTrue(validate_directory(self.root / 'base', inputs=True)['inputs_verified'])

    def test_computed_references_block_until_resolved_explicitly(self):
        self.source_only()
        self.script('dynamic.lua', 'Game.createItem(itemId + 1)')
        report = run(self.config, self.root / 'audit')
        self.assertEqual(report['summary']['unresolved'], 1)
        with self.assertRaisesRegex(FormatError, 'Unresolved'):
            run(self.config, self.root / 'blocked', build=True)
        ref = report['dependencies'][0]['reference_id']
        self.config['scan']['resolutions'][ref] = [253, 254]
        run(self.config, self.root / 'resolved', build=True)
        self.assertTrue(validate_directory(self.root / 'resolved')['manifest_verified'])

    def test_constants_ambiguity_propagates_to_aliases(self):
        file = self.script('constants.lua', 'CONST_A = 1\nCONST_A = 2\nCONST_ALIAS = CONST_A\np:sendMagicEffect(CONST_ALIAS)')
        refs, info = discover([file], {})
        self.assertIsNone(refs[0]['ids'])
        self.assertIn('CONST_A', info['ambiguous_constants'])
        refs, _ = discover([file], {'constants': {'CONST_A': 1}})
        self.assertEqual(refs[0]['ids'], [1])

    def test_named_xml_visuals_are_resolved_from_cpp_tables_by_category(self):
        self.source_only()
        self.script('names.cpp', 'CONST_ME_TEST = 1; CONST_ANI_TEST = 1;\nNames effects = {{"fire", CONST_ME_TEST}};\nNames shots = {{"fire", CONST_ANI_TEST}};')
        self.script('spell.xml', '<spell><attribute key="areaEffect" value="fire"/><attribute key="shootEffect" value="fire"/></spell>')
        report = run(self.config, self.root / 'audit')
        self.assertFalse(report['errors'])
        self.assertEqual(report['selected']['effects'], [1])
        self.assertEqual(report['selected']['missiles'], [1])

    def test_generated_output_cannot_silently_downgrade_when_manifest_removed(self):
        self.source_only()
        run(self.config, self.root / 'base', build=True)
        (self.root / 'base/report.json').unlink()
        with self.assertRaisesRegex(FormatError, 'manifest'):
            validate_directory(self.root / 'base')

    def test_unused_corrupt_sprite_is_detected_in_input(self):
        path = self.source / 'Tibia.spr'
        spr = Spr(path)
        try:
            blobs = [spr.blob(1), spr.blob(2)]
        finally:
            spr.close()
        bad = bytearray(blobs[0]); struct.pack_into('<H', bad, 5, 1025)
        blobs.append(bytes(bad))
        offset, addresses = 20, []
        for blob in blobs:
            addresses.append(u32(offset)); offset += len(blob)
        path.write_bytes(u32(456) + u32(3) + b''.join(addresses) + b''.join(blobs))
        with self.assertRaisesRegex(FormatError, 'sprite run'):
            run(self.config, self.root / 'audit')

    def test_128_pixel_profile_and_u32_sprites_round_trip(self):
        self.source_only()
        file = self.source / 'Tibia.otfi'
        file.write_text(file.read_text().replace('sprite-size: 32', 'sprite-size: 128').replace('sprite-data-size: 4096', 'sprite-data-size: 65536'))
        report = run(self.config, self.root / 'large', build=True)
        self.assertEqual(report['profiles']['source']['size'], 128)
        self.assertTrue(validate_directory(self.root / 'large', inputs=True)['manifest_verified'])

    def test_safe_static_expression_evaluator_never_executes_code(self):
        self.assertEqual(integer('1 + (CONST_X << 2)', {'CONST_X': 2}), 9)
        self.assertIsNone(integer('__import__("os").system("bad")', {}))
        self.assertIsNone(integer('1 << 999999999', {}))
        self.assertIsNone(integer('random(10)', {}))

    def test_zero_effect_is_not_a_missing_asset(self):
        self.source_only()
        self.script('none.lua', 'p:sendMagicEffect(0)\nlocal look = {lookType = 0}')
        report = run(self.config, self.root / 'audit')
        self.assertFalse(report['errors'])
        self.assertEqual(report['selected']['effects'], [])

    def test_output_and_input_tampering_are_detected_independently(self):
        self.source_only()
        run(self.config, self.root / 'base', build=True)
        original = self.source / 'Tibia.spr'
        original.write_bytes(original.read_bytes() + b'\0')
        self.assertTrue(validate_directory(self.root / 'base')['manifest_verified'])
        with self.assertRaisesRegex(FormatError, 'Input integrity'):
            validate_directory(self.root / 'base', inputs=True)
        file = self.root / 'base/sprite-mapping.json'
        file.write_text('{}')
        with self.assertRaisesRegex(FormatError, 'Output integrity'):
            validate_directory(self.root / 'base')

    def test_record_fingerprints_detect_corruption_even_after_output_hash_updated(self):
        self.source_only()
        run(self.config, self.root / 'base', build=True)
        path = self.root / 'base/Tibia.dat'
        data = bytearray(path.read_bytes()); data[13] = 1; path.write_bytes(data)
        report_path = self.root / 'base/report.json'
        report = json.loads(report_path.read_text())
        report['outputs']['Tibia.dat']['sha256'] = sha256(path)
        report_path.write_text(json.dumps(report))
        with self.assertRaisesRegex(FormatError, 'Round-trip'):
            validate_directory(self.root / 'base')

    def test_multiple_maps_count_all_occurrences(self):
        self.config['maps'] = [self.config.pop('map'), str(self.map)]
        report = run(self.config, self.root / 'audit')
        self.assertEqual(len(report['maps']), 2)
        self.assertEqual(report['items'][0]['occurrences'], 4)

    def test_legacy_otbm_zero_uses_otb_flags_to_skip_stack_counts(self):
        props = u32(128) + b'\x10\x02\x00' + u16(253) + b'\x11\x02\x00' + u16(100)
        self.otb.write_bytes(b'\0' * 4 + node(0, u32(0), [node(1, props)]))
        header = u32(0) + u16(10) * 2 + u32(3) + u32(57)
        tile = node(5, b'\x00\x00\x09' + u16(253) + b'\xfe\x03' + u32(0))
        self.map.write_bytes(b'OTBM' + node(0, header, [node(2, b'', [node(4, u16(0) * 2 + b'\x07', [tile])])]))
        self.source_only()
        report = run(self.config, self.root / 'audit')
        self.assertEqual(report['items'][0]['occurrences'], 1)
        self.assertFalse(report['errors'])

    def test_target_otb_preserves_extra_mappings_and_reports_metadata_conflicts(self):
        props = lambda sid, cid, flags=0: u32(flags) + b'\x10\x02\x00' + u16(sid) + b'\x11\x02\x00' + u16(cid)
        target = self.root / 'target.otb'
        target.write_bytes(b'\0' * 4 + node(0, u32(0), [node(1, props(253, 100, 1)), node(1, props(777, 100))]))
        self.config['target_otb'] = str(target)
        self.config['conflicts'] = 'replace-reviewed'
        report = run(self.config, self.root / 'audit')
        self.assertEqual(len(report['otb_conflicts']), 1)
        run(self.config, self.root / 'merged', build=True, plan=self.root / 'audit/report.json')
        self.assertEqual(read_otb(self.root / 'merged/items.otb')[0], {253: 100, 254: 102, 777: 100})
        validate_directory(self.root / 'merged', inputs=True)

    def test_target_otb_mapping_conflict_cannot_be_approved(self):
        target = self.root / 'target.otb'
        props = u32(0) + b'\x10\x02\x00' + u16(253) + b'\x11\x02\x00' + u16(101)
        target.write_bytes(b'\0' * 4 + node(0, u32(0), [node(1, props)]))
        self.config['target_otb'] = str(target)
        report = run(self.config, self.root / 'audit')
        self.assertTrue(any('OTB collision' in e for e in report['errors']))
        with self.assertRaises(FormatError):
            run(self.config, self.root / 'merged', build=True)

    def test_assets_only_audit_does_not_require_map_or_target(self):
        self.source_only(); self.config.pop('map')
        self.config['extra'] = {'effects': [1]}
        report = run(self.config, self.root / 'audit')
        self.assertFalse(report['items'])
        run(self.config, self.root / 'base', build=True)
        validate_directory(self.root / 'base', inputs=True)

    def test_rgb_u16_legacy_and_64_pixel_profiles_round_trip(self):
        self.source_only()
        self.config.pop('map'); self.config['extra'] = {'items': [100]}
        profile_file = self.source / 'Tibia.otfi'
        profile_file.write_text('''DatSpr
  client-version: 986
  extended: false
  transparency: false
  frame-durations: false
  frame-groups: false
  metadata-file: Tibia.dat
  sprites-file: Tibia.spr
  sprite-size: 64
  sprite-data-size: 16384
''')
        # Legacy wire flag 21 = light; two animation phases, no enhanced durations.
        raw = b'\x15' + u16(7) + u16(215) + b'\xff' + b'\x01' * 6 + b'\x02' + u16(1) * 2
        (self.source / 'Tibia.dat').write_bytes(struct.pack('<I4H', 123, 100, 0, 0, 0) + raw)
        payload = u16(0) + u16(1) + bytes([255, 0, 253])
        blob = b'\xff\x00\xff' + u16(len(payload)) + payload
        (self.source / 'Tibia.spr').write_bytes(u32(456) + u16(1) + u32(10) + blob)
        props = u32(0) + b'\x10\x02\x00' + u16(253) + b'\x11\x02\x00' + u16(100)
        self.otb.write_bytes(b'\0' * 4 + node(0, u32(0), [node(1, props)]))
        report = run(self.config, self.root / 'base', build=True)
        self.assertEqual(report['validation']['references'], 2)
        validate_directory(self.root / 'base', inputs=True)
        profile = read_profile(self.root / 'base')
        self.assertFalse(profile.extended)
        self.assertEqual(Dat(self.root / 'base/Tibia.dat', profile).records['items'][100].sprites(), (1, 1))

    def test_mismatched_profiles_are_rejected_before_merge(self):
        file = self.target / 'Tibia.otfi'
        file.write_text(file.read_text().replace('transparency: true', 'transparency: false'))
        with self.assertRaisesRegex(FormatError, 'profiles differ'):
            run(self.config, self.root / 'audit')

    def test_xml_entities_and_stale_resolutions_are_rejected(self):
        file = self.script('bad.xml', '<!DOCTYPE m [<!ENTITY x "bad">]><m/>')
        with self.assertRaises(FormatError):
            discover([file], {})
        file.write_text('<m/>')
        with self.assertRaisesRegex(ValueError, 'stale'):
            discover([file], {'resolutions': {'not-a-reference': [1]}})


if __name__ == '__main__':
    unittest.main()
