import csv
import html
import json
from pathlib import Path


def save_report(directory, report):
    directory = Path(directory)
    (directory / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (directory / 'reserved-ids.json').write_text(json.dumps(report['selected'], indent=2), encoding='utf-8')
    for filename, values in (('items.csv', report['items']), ('assets.csv', report['assets']), ('dependencies.csv', report['dependencies'])):
        fields = list(dict.fromkeys(k for value in values for k in value)) or ['status']
        with (directory / filename).open('w', newline='', encoding='utf-8-sig') as file:
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            writer.writerows(values)
    def table(values, columns):
        return '<table><thead><tr>' + ''.join('<th>' + html.escape(c) + '</th>' for c in columns) + '</tr></thead><tbody>' + ''.join(
            '<tr data-status="' + html.escape(str(row.get('status', ''))) + '">' + ''.join('<td>' + html.escape(str(row.get(c, ''))) + '</td>' for c in columns) + '</tr>' for row in values) + '</tbody></table>'
    sections = [('Map items', report['items'], ('server_id', 'client_id', 'occurrences', 'status', 'sprite_slots')),
                ('Selected assets', report['assets'], ('category', 'client_id', 'status', 'sprite_slots')),
                ('Script and XML dependencies', report['dependencies'], ('reference_id', 'file', 'line', 'category', 'expression', 'ids', 'status')),
                ('OTB definition conflicts', report['otb_conflicts'], ('server_id', 'client_id', 'source_sha256', 'target_sha256'))]
    body = ''.join('<h2>' + name + '</h2>' + table(values, columns) for name, values, columns in sections)
    summary = html.escape(json.dumps(report['summary'], ensure_ascii=False, indent=2))
    messages = html.escape('\n'.join(report['errors'] + report['warnings']) or 'No missing dependencies.')
    limits = ''.join('<li>' + html.escape(s) + '</li>' for s in report['limitations'])
    (directory / 'report.html').write_text('''<!doctype html><html lang="en"><meta charset="utf-8">
<title>Asset Validator</title><style>body{font:16px system-ui;background:#101827;color:#e8eef8;margin:32px}h1{color:#75ded2}input,select{padding:10px;margin:8px;background:#202e45;color:white}table{border-collapse:collapse;width:100%;overflow-wrap:anywhere}td,th{padding:8px;text-align:left;border-bottom:1px solid #31405a}thead{background:#202e45}tr[data-status=replace],tr[data-status=unresolved]{color:#ffc36f}pre{white-space:pre-wrap}</style>
<h1>Asset and dependency audit</h1><p>Replacement conflicts require a reviewed plan. Unresolved references block builds. Map IDs remain unchanged.</p><pre>''' + summary + '</pre><pre>' + messages + '''</pre>
<label>Search <input id="q" type="search"></label><label>Status <select id="status"><option value="">All</option><option>used</option><option>replace</option><option>identical</option><option>new</option><option>resolved</option><option>unresolved</option></select></label>''' + body + '<h2>Scope and limits</h2><ul>' + limits + '''</ul>
<script>const q=document.getElementById('q'),s=document.getElementById('status');function filter(){for(const row of document.querySelectorAll('tbody tr'))row.hidden=!(row.textContent.toLowerCase().includes(q.value.toLowerCase())&&(!s.value||row.dataset.status===s.value))}q.oninput=filter;s.onchange=filter;</script></html>''', encoding='utf-8')
