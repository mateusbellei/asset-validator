from collections import Counter
from contextlib import ExitStack
import csv
import hashlib
import html
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
    allowed = {'map', 'source', 'target', 'otb', 'extra', 'mode'}
    if set(config) - allowed:
        raise ValueError(f'Unknown config keys: {sorted(set(config) - allowed)}')
    for key in ('map', 'source', 'target', 'otb'):
        if key not in config or not isinstance(config[key], str):
            raise ValueError(f'Config requires a {key} path')
        config[key] = str((path.parent / config[key]).resolve())
    config.setdefault('mode', 'merge')
    if config['mode'] not in ('merge', 'map-only'):
        raise ValueError('mode must be merge or map-only')
    extra = config.setdefault('extra', {})
    if not isinstance(extra, dict) or set(extra) - set(CATEGORIES):
        raise ValueError('extra keys must be items, outfits, effects or missiles')
    for category, ids in extra.items():
        if not isinstance(ids, list) or any(type(i) is not int or i < (100 if category == 'items' else 1) or i > 65535 for i in ids):
            raise ValueError(f'Invalid extra IDs for {category}')
    return config


def analyze(config, stack):
    source, target = Path(config['source']), Path(config['target'])
    profiles = [check_profile(source), check_profile(target)]
    counts, map_info = read_map(config['map'])
    mappings, flags, otb_version = read_otb(config['otb'])
    source_dat, target_dat = Dat(source / 'Tibia.dat'), Dat(target / 'Tibia.dat')
    source_spr, target_spr = Spr(source / 'Tibia.spr'), Spr(target / 'Tibia.spr')
    stack.callback(source_spr.close)
    stack.callback(target_spr.close)
    selected = {c: set(config['extra'].get(c, [])) for c in CATEGORIES}
    rows, errors, cached = [], [], {}
    for sid, occurrences in sorted(counts.items()):
        cid = mappings.get(sid)
        status, source_hash, target_hash = 'missing-otb', None, None
        record = source_dat.records['items'].get(cid)
        if cid is not None:
            status = 'missing-source-dat' if record is None else 'new'
        if record is not None:
            selected['items'].add(cid)
            if cid not in cached:
                source_hash = fingerprint(record, source_spr)
                target_record = target_dat.records['items'].get(cid)
                target_hash = fingerprint(target_record, target_spr) if target_record else None
                status = 'identical' if source_hash == target_hash else ('replace' if target_record else 'new')
                cached[cid] = status, source_hash, target_hash
            status, source_hash, target_hash = cached[cid]
        else:
            errors.append(f'Server ID {sid}: {status}, client ID {cid}')
        rows.append(dict(server_id=sid, client_id=cid, occurrences=occurrences,
                         status=status, reserved=True, otb_flags=flags.get(sid),
                         sprite_slots=len(record.offsets) if record else 0,
                         source_sha256=source_hash, target_sha256=target_hash))
    for category, ids in selected.items():
        for cid in sorted(ids):
            record = source_dat.records[category].get(cid)
            if record is None:
                errors.append(f'Missing source {category} ID {cid}')
            else:
                for sprite in record.sprites():
                    source_spr.blob(sprite)
    # All retained target references must be valid, including untouched outfits/effects.
    if config['mode'] == 'merge':
        for category in CATEGORIES:
            for cid, record in target_dat.records[category].items():
                if cid not in selected[category]:
                    for sprite in record.sprites():
                        target_spr.blob(sprite)
    inputs = {'map': config['map'], 'otb': config['otb'],
              'source_dat': source / 'Tibia.dat', 'source_spr': source / 'Tibia.spr',
              'target_dat': target / 'Tibia.dat', 'target_spr': target / 'Tibia.spr',
              'source_otfi': profiles[0], 'target_otfi': profiles[1]}
    report = dict(schema_version=1, config=config, map=map_info, otb_version=otb_version,
                  source_counts=dict(zip(CATEGORIES, source_dat.counts)),
                  target_counts=dict(zip(CATEGORIES, target_dat.counts)),
                  summary=dict(server_ids=len(counts), client_ids=len({r['client_id'] for r in rows if r['source_sha256']}),
                               statuses=dict(Counter(row['status'] for row in rows)),
                               source_sprites=source_spr.count, target_sprites=target_spr.count),
                  selected={c: sorted(ids) for c, ids in selected.items()},
                  inputs={key: dict(path=str(path), sha256=sha256(path)) for key, path in inputs.items()},
                  errors=errors, items=rows,
                  limitations=['Only static map dependencies and explicit extra selections are audited.',
                               'The source items.otb preserves map server-to-client IDs. Server items.xml, scripts, transforms and destination-only OTB mappings require separate integration.',
                               'Merge replaces selected client IDs. Existing gameplay references to these destination IDs receive the source definition.',
                               'map-only reserves holes with empty entries; it is not a complete gameplay base.',
                               'Binary validation does not replace opening the result in RME and the game client.'])
    return report, selected, source_dat, target_dat, source_spr, target_spr


def save_report(directory, report):
    directory = Path(directory)
    (directory / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (directory / 'reserved-ids.json').write_text(json.dumps(report['selected'], indent=2), encoding='utf-8')
    with (directory / 'items.csv').open('w', newline='', encoding='utf-8-sig') as file:
        writer = csv.DictWriter(file, fieldnames=list(report['items'][0]))
        writer.writeheader()
        writer.writerows(report['items'])
    columns = ('server_id', 'client_id', 'occurrences', 'status', 'sprite_slots')
    body = ''.join('<tr data-status="' + html.escape(row['status']) + '">' +
                   ''.join('<td>' + html.escape(str(row[c])) + '</td>' for c in columns) + '</tr>' for row in report['items'])
    summary = html.escape(json.dumps(report['summary'], ensure_ascii=False, indent=2))
    limitations = ''.join('<li>' + html.escape(s) + '</li>' for s in report['limitations'])
    errors = html.escape('\n'.join(report['errors']) or 'Nenhuma dependência ausente.')
    (directory / 'report.html').write_text('''<!doctype html><html lang="pt-BR"><meta charset="utf-8">
<title>Asset Validator — IDs reservados do mapa</title>
<style>body{font:16px system-ui;background:#101827;color:#e8eef8;margin:40px;max-width:1200px}h1{color:#75ded2}input,select{padding:10px;margin:8px;background:#202e45;color:white;border:1px solid #536480}table{border-collapse:collapse;width:100%}td,th{padding:8px;text-align:left;border-bottom:1px solid #31405a}thead{position:sticky;top:0;background:#202e45}tr[data-status=replace]{color:#ffc36f}pre{white-space:pre-wrap}</style>
<h1>IDs reservados do mapa</h1><p>Todos os IDs listados são dependências do mapa. <b>replace</b> indica uma colisão: o objeto da origem substituirá o objeto do destino nesse Client ID. Os Server IDs e Client IDs permanecem iguais.</p>
<pre>''' + summary + '</pre><pre>' + errors + '''</pre>
<label>Buscar ID <input id="q" type="search"></label><label>Status <select id="status"><option value="">Todos</option><option>replace</option><option>identical</option><option>new</option><option>missing-otb</option><option>missing-source-dat</option></select></label>
<table><thead><tr><th>Server ID</th><th>Client ID</th><th>Ocorrências</th><th>Status</th><th>Referências de sprite</th></tr></thead><tbody>''' + body + '''</tbody></table><h2>Limites da auditoria</h2><ul>''' + limitations + '''</ul>
<script>const q=document.getElementById('q'),s=document.getElementById('status');function filter(){for(const row of document.querySelectorAll('tbody tr'))row.hidden=!(row.textContent.includes(q.value)&&(!s.value||row.dataset.status===s.value))}q.oninput=filter;s.onchange=filter;</script></html>''', encoding='utf-8')


def verify(dat, spr, expected=None):
    records, references = 0, 0
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


def run(config, output, build=False):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError(f'Output already exists: {output}; choose a new directory')
    for key in ('source', 'target'):
        base = Path(config[key]).resolve()
        if output == base or base in output.parents or output in base.parents:
            raise ValueError('Output must be separate from the original asset directories')
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.asset-validator-', dir=output.parent))
    try:
        with ExitStack() as stack:
            report, selected, source_dat, target_dat, source_spr, target_spr = analyze(config, stack)
            if build and report['errors']:
                raise FormatError('Build blocked: ' + '; '.join(report['errors']))
            if build:
                blobs, blob_ids = [], {}
                maps = {'source': {0: 0}, 'target': {0: 0}}
                records, expected = {c: {} for c in CATEGORIES}, {c: {} for c in CATEGORIES}
                for category in CATEGORIES:
                    maximum = max(selected[category], default=99 if category == 'items' else 0)
                    if config['mode'] == 'merge':
                        maximum = max(maximum, max(target_dat.records[category], default=99 if category == 'items' else 0))
                    for cid in range(100 if category == 'items' else 1, maximum + 1):
                        label = 'source' if cid in selected[category] else 'target'
                        dat, spr = (source_dat, source_spr) if label == 'source' else (target_dat, target_spr)
                        record = dat.records[category].get(cid)
                        if cid not in selected[category] and (config['mode'] == 'map-only' or record is None):
                            # Outfits need a frame-group count and group type before dimensions.
                            from .formats import Record
                            raw = EMPTY_ITEM if category != 'outfits' else b'\xff\x01\x00' + EMPTY_ITEM[1:]
                            record = Record(raw, (len(raw) - 4,))
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
                target_dat.write(stage / 'Tibia.dat', records)
                with (stage / 'Tibia.spr').open('wb') as file:
                    file.write(struct.pack('<II', target_spr.signature, len(blobs)))
                    offset = 8 + len(blobs) * 4
                    for blob in blobs:
                        if offset > 0xffffffff:
                            raise FormatError('SPR exceeds 32-bit offsets')
                        file.write(struct.pack('<I', offset))
                        offset += len(blob)
                    for blob in blobs:
                        file.write(blob)
                shutil.copyfile(Path(config['target']) / 'Tibia.otfi', stage / 'Tibia.otfi')
                shutil.copyfile(config['otb'], stage / 'items.otb')
                output_dat = Dat(stage / 'Tibia.dat')
                output_spr = Spr(stage / 'Tibia.spr')
                try:
                    report['validation'] = verify(output_dat, output_spr, expected)
                finally:
                    output_spr.close()
                # Ensure every used Server ID still resolves to the same Client ID.
                result_otb, _, _ = read_otb(stage / 'items.otb')
                for row in report['items']:
                    if result_otb.get(row['server_id']) != row['client_id']:
                        raise FormatError('Output OTB mapping changed')
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
