from collections import Counter
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import shutil
import struct
import tempfile

from .formats import (CATEGORIES, Dat, EMPTY_ITEM, FormatError, Spr,
                      check_profile, read_map, read_otb)


def sha256(path):
    with Path(path).open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def fingerprint(record, spr):
    digest = hashlib.sha256(record.metadata())
    for sid in record.sprites():
        blob = spr.blob(sid)
        digest.update(struct.pack('<I', len(blob)))
        digest.update(blob)
    return digest.hexdigest()


def load_config(path):
    path = Path(path).resolve()
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    allowed = {'map', 'maps', 'source', 'target', 'target_otb', 'otb', 'extra', 'mode', 'conflicts', 'scan'}
    if not isinstance(config, dict) or set(config) - allowed:
        raise ValueError('Unknown config keys or invalid configuration')
    for key in ('source', 'otb'):
        if not isinstance(config.get(key), str):
            raise ValueError(f'Config requires a {key} path')
    for key in ('map', 'source', 'target', 'target_otb', 'otb'):
        if key in config:
            if not isinstance(config[key], str):
                raise ValueError(f'Invalid {key} path')
            config[key] = str((path.parent / config[key]).resolve())
    if 'map' in config and 'maps' in config:
        raise ValueError('Use map or maps, not both')
    if 'maps' in config:
        if not isinstance(config['maps'], list) or any(not isinstance(p, str) for p in config['maps']):
            raise ValueError('maps must be a list of paths')
        config['maps'] = [str((path.parent / p).resolve()) for p in config['maps']]
    config.setdefault('mode', 'merge' if 'target' in config else 'map-only')
    if config['mode'] not in ('merge', 'map-only'):
        raise ValueError('mode must be merge or map-only')
    config.setdefault('conflicts', 'error')
    if config['conflicts'] not in ('error', 'replace-reviewed'):
        raise ValueError('conflicts must be error or replace-reviewed')
    extra = config.setdefault('extra', {})
    if not isinstance(extra, dict) or set(extra) - set(CATEGORIES):
        raise ValueError('extra keys must be items, outfits, effects or missiles')
    for category, ids in extra.items():
        if not isinstance(ids, list) or any(type(i) is not int or i < (100 if category == 'items' else 1) or i > 65535 for i in ids):
            raise ValueError(f'Invalid extra IDs for {category}')
    scan = config.setdefault('scan', {})
    if not isinstance(scan, dict) or set(scan) - {'paths', 'constants', 'resolutions', 'encoding'}:
        raise ValueError('Invalid scan options')
    paths = scan.setdefault('paths', [])
    if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
        raise ValueError('scan.paths must be a list of paths')
    scan['paths'] = [str((path.parent / p).resolve()) for p in paths]
    if scan.get('encoding', 'utf-8-sig') not in ('utf-8', 'utf-8-sig', 'cp1252', 'latin-1'):
        raise ValueError('Unsupported scan encoding')
    constants = scan.setdefault('constants', {})
    if not isinstance(constants, dict) or any(not isinstance(k, str) or type(v) is not int for k, v in constants.items()):
        raise ValueError('scan.constants must map names to integers')
    resolutions = scan.setdefault('resolutions', {})
    if not isinstance(resolutions, dict) or any(not isinstance(k, str) or not isinstance(v, list) or any(type(i) is not int or not 0 <= i <= 65535 for i in v) for k, v in resolutions.items()):
        raise ValueError('scan.resolutions must map reference IDs to integer lists')
    return config


def analyze(config, stack):
    from dataclasses import asdict
    from .dependencies import discover, scan_files
    from .formats import read_profile
    source = Path(config['source'])
    target = Path(config['target']) if config.get('target') else None
    source_profile = read_profile(source)
    target_profile = read_profile(target) if target else None
    if target_profile and target_profile != source_profile:
        raise FormatError('Source and target profiles differ; cross-profile conversion is not supported')
    maps = config.get('maps', [config['map']] if config.get('map') else [])
    scripts = scan_files(config.get('scan', {}).get('paths', []))
    inputs = {'otb': config['otb'], 'source_dat': source / 'Tibia.dat',
              'source_spr': source / 'Tibia.spr', 'source_otfi': source / 'Tibia.otfi'}
    inputs.update({f'map_{i}': p for i, p in enumerate(maps)})
    inputs.update({f'script_{i}': p for i, p in enumerate(scripts)})
    if target:
        inputs.update(target_dat=target / 'Tibia.dat', target_spr=target / 'Tibia.spr', target_otfi=target / 'Tibia.otfi')
    if config.get('target_otb'):
        inputs['target_otb'] = config['target_otb']
    input_snapshot = {key: dict(path=str(path), sha256=sha256(path)) for key, path in inputs.items()}
    mappings, flags, otb_version = read_otb(config['otb'])
    counts, map_infos = Counter(), []
    for path in maps:
        items, info = read_map(path, flags)
        counts.update(items)
        map_infos.append(dict(path=path, **info))
    source_dat = Dat(source / 'Tibia.dat', source_profile)
    source_spr = Spr(source / 'Tibia.spr', source_profile)
    stack.callback(source_spr.close)
    target_dat = Dat(target / 'Tibia.dat', target_profile) if target else None
    target_spr = Spr(target / 'Tibia.spr', target_profile) if target else None
    if target_spr:
        stack.callback(target_spr.close)
    input_validation = {'source': verify(source_dat, source_spr, all_sprites=True)}
    if target:
        input_validation['target'] = verify(target_dat, target_spr, all_sprites=True)
    selected = {c: set(config.get('extra', {}).get(c, [])) for c in CATEGORIES}
    references, scan_info = discover(scripts, config.get('scan', {}))
    errors, warnings = [], []
    for sid, cid in mappings.items():
        if cid and cid not in source_dat.records['items']:
            errors.append(f'Source OTB Server ID {sid} references absent Client ID {cid}')
    for ref in references:
        if ref['ids'] is None:
            errors.append(f'Unresolved {ref["category"]} reference {ref["reference_id"]}: {ref["file"]}:{ref["line"]}')
            continue
        ref['client_ids'] = []
        for value in ref['ids']:
            if value == 0:
                continue  # protocol no-effect/no-outfit/decay removal
            cid = mappings.get(value) if ref['server_id'] else value
            if cid is None or cid < (100 if ref['category'] == 'items' else 1) or cid > 65535:
                errors.append(f'Invalid/missing dependency {value} in reference {ref["reference_id"]}')
            else:
                selected[ref['category']].add(cid)
                ref['client_ids'].append(cid)
    rows = []
    for sid, occurrences in sorted(counts.items()):
        cid = mappings.get(sid)
        record = source_dat.records['items'].get(cid)
        if record is None:
            errors.append(f'Server ID {sid}: missing source definition, client ID {cid}')
        else:
            selected['items'].add(cid)
        rows.append(dict(server_id=sid, client_id=cid, occurrences=occurrences,
                         status='missing-otb' if cid is None else 'missing-source-dat',
                         reserved=True, otb_flags=flags.get(sid),
                         sprite_slots=len(record.offsets) if record else 0,
                         source_sha256=None, target_sha256=None))
    assets, cache = [], {}
    for category, ids in selected.items():
        for cid in sorted(ids):
            record = source_dat.records[category].get(cid)
            if record is None:
                errors.append(f'Missing source {category} ID {cid}')
                continue
            source_hash = fingerprint(record, source_spr)
            other = target_dat.records[category].get(cid) if target_dat else None
            target_hash = fingerprint(other, target_spr) if other else None
            status = 'used' if not target else ('identical' if source_hash == target_hash else 'replace' if other else 'new')
            asset = dict(category=category, client_id=cid, status=status, source_sha256=source_hash,
                         target_sha256=target_hash, sprite_slots=len(record.offsets))
            assets.append(asset)
            cache[category, cid] = asset
    for row in rows:
        asset = cache.get(('items', row['client_id']))
        if asset:
            row.update({k: asset[k] for k in ('status', 'source_sha256', 'target_sha256')})
    target_otb = None
    otb_conflicts = []
    if config.get('target_otb'):
        target_otb, _, target_version = read_otb(config['target_otb'])
        if otb_version and target_version and otb_version[:2] != target_version[:2]:
            errors.append('Source and target OTB versions differ')
        for sid, cid in mappings.items():
            if cid in selected['items'] and sid in target_otb and target_otb[sid] != cid:
                errors.append(f'OTB collision: Server ID {sid}: source Client ID {cid}, target {target_otb[sid]}')
        if target_dat:
            for sid, cid in target_otb.items():
                if cid and cid not in target_dat.records['items']:
                    errors.append(f'Target OTB Server ID {sid} references absent Client ID {cid}')
        for sid, cid in target_otb.items():
            if config['mode'] == 'map-only' and cid and cid not in selected['items']:
                errors.append(f'map-only would remove target OTB dependency: Server ID {sid}, Client ID {cid}')
        from .otb_merge import definitions
        _, source_nodes, _ = definitions(config['otb'])
        _, target_nodes, _ = definitions(config['target_otb'])
        for sid in source_nodes.keys() & target_nodes.keys():
            if mappings[sid] in selected['items'] and mappings[sid] == target_otb[sid] and source_nodes[sid] != target_nodes[sid]:
                otb_conflicts.append(dict(server_id=sid, client_id=mappings[sid],
                                         source_sha256=hashlib.sha256(bytes([source_nodes[sid][0]]) + source_nodes[sid][1]).hexdigest(),
                                         target_sha256=hashlib.sha256(bytes([target_nodes[sid][0]]) + target_nodes[sid][1]).hexdigest()))
    elif target:
        warnings.append('No target_otb provided: server mappings outside the source OTB are not verified or merged.')
    for info in map_infos:
        if otb_version and (info['otb_major'] != otb_version[0] or info['otb_minor'] > otb_version[1]):
            errors.append(f'Map/OTB version mismatch: {info["path"]}')
    report = dict(schema_version=2, config=config, maps=map_infos,
                  map=map_infos[0] if len(map_infos) == 1 else {'count': len(map_infos)},
                  otb_version=otb_version, profiles={'source': asdict(source_profile), 'target': asdict(target_profile) if target_profile else None},
                  input_validation=input_validation,
                  source_counts=dict(zip(CATEGORIES, source_dat.counts)),
                  target_counts=dict(zip(CATEGORIES, target_dat.counts)) if target_dat else None,
                  summary=dict(server_ids=len(counts), client_ids=len(selected['items']),
                               statuses=dict(Counter(row['status'] for row in rows)),
                               selected_assets=len(assets), conflicts=sum(a['status'] == 'replace' for a in assets),
                               unresolved=sum(r['status'] == 'unresolved' for r in references),
                               unique_client_statuses=dict(Counter(a['status'] for a in assets if a['category'] == 'items')),
                               source_sprites=source_spr.count, target_sprites=target_spr.count if target_spr else None),
                  selected={c: sorted(ids) for c, ids in selected.items()}, assets=assets, otb_conflicts=otb_conflicts,
                  dependencies=references, scan=scan_info,
                  selected_source_sprites=sorted({sid for category, ids in selected.items()
                                                 for cid in ids if cid in source_dat.records[category]
                                                 for sid in source_dat.records[category][cid].sprites() if sid}),
                  inputs=input_snapshot, errors=errors, warnings=warnings, items=rows,
                  limitations=['Static Lua/XML/C++ discovery covers documented patterns, not arbitrary runtime behavior; computed references require explicit resolutions.',
                               'Unresolved references and unreviewed collisions block builds. Server semantics/items.xml are not rewritten.',
                               'map-only reserves holes with empty entries; it is not a complete gameplay base.',
                               'Binary validation does not replace opening the result in RME and the game client.'])
    return report, selected, source_dat, target_dat, source_spr, target_spr


from .reports import save_report


def verify(dat, spr, expected=None, all_sprites=False):
    records, references = 0, 0
    if all_sprites:
        spr.validate_all()
    for category in CATEGORIES:
        for cid, record in dat.records[category].items():
            for sid in record.sprites():
                spr.blob(sid)
                references += 1
            if expected is not None:
                if fingerprint(record, spr) != expected[category][cid]:
                    raise FormatError(f'Round-trip mismatch: {category} {cid}')
            records += 1
    return dict(records=records, references=references, sprites=spr.count)


def validate_plan(report, plan):
    if plan:
        approved = json.loads(Path(plan).read_text(encoding='utf-8-sig'))
        for key in ('schema_version', 'config', 'inputs', 'selected', 'assets', 'dependencies', 'otb_conflicts'):
            if approved.get(key) != report.get(key):
                raise FormatError(f'Stale/incompatible audit plan: {key} changed; audit again')
        if approved.get('errors'):
            raise FormatError('Audit plan contains unresolved errors')
    if (report['config']['mode'] == 'merge' and report['summary']['conflicts']) or report.get('otb_conflicts'):
        if report['config'].get('conflicts', 'error') != 'replace-reviewed' or not plan:
            raise FormatError('Merge conflicts require conflicts=replace-reviewed and --plan from a reviewed audit')


def validate_directory(directory, inputs=False):
    """Verify persistent output hashes, record fingerprints, mappings and optional originals."""
    from .formats import read_profile
    directory = Path(directory)
    profile = read_profile(directory)
    report_path = directory / 'report.json'
    report = json.loads(report_path.read_text(encoding='utf-8-sig')) if report_path.exists() else None
    if (directory / 'expected-records.json').exists() and (not report or report.get('schema_version') != 2 or not report.get('outputs')):
        raise FormatError('Generated base is missing its v2 output manifest')
    expected = None
    if report and report.get('schema_version') == 2 and report.get('outputs'):
        required = {'Tibia.dat', 'Tibia.spr', 'Tibia.otfi', 'items.otb', 'sprite-mapping.json', 'expected-records.json'}
        if set(report['outputs']) != required:
            raise FormatError('Invalid/incomplete output manifest')
        for name, entry in report['outputs'].items():
            path = directory / name
            if path.stat().st_size != entry['bytes'] or sha256(path) != entry['sha256']:
                raise FormatError(f'Output integrity mismatch: {name}')
        value = json.loads((directory / 'expected-records.json').read_text(encoding='utf-8'))
        if set(value) != set(CATEGORIES):
            raise FormatError('Invalid expected-records categories')
        expected = {c: {int(k): v for k, v in records.items()} for c, records in value.items()}
    with ExitStack() as stack:
        spr = Spr(directory / 'Tibia.spr', profile)
        stack.callback(spr.close)
        dat = Dat(directory / 'Tibia.dat', profile)
        if expected and any(set(expected[c]) != set(dat.records[c]) for c in CATEGORIES):
            raise FormatError('Output record set differs from manifest')
        result = verify(dat, spr, expected, all_sprites=True)
        otb = directory / 'items.otb'
        if otb.exists():
            mappings, _, _ = read_otb(otb, allow_empty=True)
            for sid, cid in mappings.items():
                if cid and cid not in dat.records['items']:
                    raise FormatError(f'Output OTB references absent Client ID {cid} (Server ID {sid})')
            if expected:
                for row in report['items']:
                    if mappings.get(row['server_id']) != row['client_id']:
                        raise FormatError(f'Output map mapping changed: Server ID {row["server_id"]}')
                for ref in report['dependencies']:
                    if ref['server_id']:
                        for sid in ref['ids']:
                            if sid and mappings.get(sid) not in ref['client_ids']:
                                raise FormatError(f'Output dependency mapping changed: {sid}')
        if inputs:
            if not expected:
                raise FormatError('--inputs requires a v2 build manifest')
            for entry in report['inputs'].values():
                if sha256(entry['path']) != entry['sha256']:
                    raise FormatError(f'Input integrity mismatch: {entry["path"]}')
        result.update(manifest_verified=expected is not None, inputs_verified=inputs)
        return result


def run(config, output, build=False, plan=None):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError(f'Output already exists: {output}; choose a new directory')
    for key in ('source', 'target'):
        if not config.get(key):
            continue
        base = Path(config[key]).resolve()
        if output == base or base in output.parents or output in base.parents:
            raise ValueError('Output must be separate from the original asset directories')
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.asset-validator-', dir=output.parent))
    try:
        with ExitStack() as stack:
            report, selected, source_dat, target_dat, source_spr, target_spr = analyze(config, stack)
            if build and config['mode'] == 'merge' and target_dat is None:
                raise ValueError('merge build requires target')
            if build and report['errors']:
                raise FormatError('Build blocked: ' + '; '.join(report['errors']))
            if build:
                validate_plan(report, plan)
                base_dat = target_dat or source_dat
                base_spr = target_spr or source_spr
                blobs, blob_ids = [], {}
                maps = {'source': {0: 0}, 'target': {0: 0}}
                records, expected = {c: {} for c in CATEGORIES}, {c: {} for c in CATEGORIES}
                for category in CATEGORIES:
                    maximum = max(selected[category], default=99 if category == 'items' else 0)
                    if config['mode'] == 'merge':
                        maximum = max(maximum, max(target_dat.records[category], default=99 if category == 'items' else 0))
                    for cid in range(100 if category == 'items' else 1, maximum + 1):
                        label = 'source' if cid in selected[category] else 'target'
                        dat, spr = (source_dat, source_spr) if label == 'source' else (base_dat, base_spr)
                        record = dat.records[category].get(cid)
                        if cid not in selected[category] and (config['mode'] == 'map-only' or record is None):
                            # Outfits need a frame-group count and group type before dimensions.
                            from .formats import empty_record
                            record = empty_record(category, base_dat.profile)
                        mapping = maps[label]
                        for sid in record.sprites():
                            if sid not in mapping:
                                blob = spr.blob(sid)
                                if not blob:
                                    mapping[sid] = 0
                                else:
                                    if blob not in blob_ids:
                                        blobs.append(blob)
                                        blob_ids[blob] = len(blobs)
                                    mapping[sid] = blob_ids[blob]
                        records[category][cid] = record.rewrite(mapping)
                        expected[category][cid] = fingerprint(record, spr)
                base_dat.write(stage / 'Tibia.dat', records)
                with (stage / 'Tibia.spr').open('wb') as file:
                    if not base_spr.profile.extended and len(blobs) > 65535:
                        raise FormatError('SPR exceeds 16-bit sprite ID capacity')
                    file.write(struct.pack('<I', base_spr.signature))
                    file.write(struct.pack('<I' if base_spr.profile.extended else '<H', len(blobs)))
                    offset = file.tell() + len(blobs) * 4
                    for blob in blobs:
                        if offset > 0xffffffff:
                            raise FormatError('SPR exceeds 32-bit offsets')
                        file.write(struct.pack('<I', offset))
                        offset += len(blob)
                    for blob in blobs:
                        file.write(blob)
                shutil.copyfile(Path(config.get('target') or config['source']) / 'Tibia.otfi', stage / 'Tibia.otfi')
                from .otb_merge import merge_otb
                merge_otb(config['otb'], config.get('target_otb'), stage / 'items.otb', selected['items'])
                output_dat = Dat(stage / 'Tibia.dat', base_dat.profile)
                output_spr = Spr(stage / 'Tibia.spr', base_dat.profile)
                try:
                    report['validation'] = verify(output_dat, output_spr, expected)
                finally:
                    output_spr.close()
                # Ensure every used Server ID still resolves to the same Client ID.
                result_otb, _, _ = read_otb(stage / 'items.otb', allow_empty=True)
                for row in report['items']:
                    if result_otb.get(row['server_id']) != row['client_id']:
                        raise FormatError('Output OTB mapping changed')
                (stage / 'sprite-mapping.json').write_text(json.dumps(maps, indent=2), encoding='utf-8')
                (stage / 'expected-records.json').write_text(json.dumps(expected, indent=2), encoding='utf-8')
                report['outputs'] = {p.name: dict(bytes=p.stat().st_size, sha256=sha256(p)) for p in stage.iterdir() if p.is_file()}
                report['validation']['map_ids_preserved'] = True
                report['validation']['all_record_fingerprints_match'] = True
            # Reject inputs edited while the potentially long build/audit was running.
            for entry in report['inputs'].values():
                if sha256(entry['path']) != entry['sha256']:
                    raise FormatError(f'Input changed during execution: {entry["path"]}')
            save_report(stage, report)
        stage.rename(output)
        return report
    except Exception:
        # Only the tool-owned temporary directory is removed, never input/output paths.
        shutil.rmtree(stage)
        raise
