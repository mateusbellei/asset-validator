"""Conservative static discovery. Inspected Lua/XML/C++ is never executed.

Computed references remain visible and block builds until explicitly resolved.
"""
import ast
import hashlib
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from .formats import FormatError


def scan_files(paths):
    files = set()
    for value in paths:
        path = Path(value)
        if not path.exists():
            raise ValueError(f'Scan path does not exist: {path}')
        candidates = path.rglob('*') if path.is_dir() else [path]
        for file in candidates:
            if file.is_file() and file.suffix.lower() in ('.lua', '.xml', '.h', '.hpp', '.cpp'):
                files.add(file.resolve())
    return sorted(files)


def without_comments(text, lua=False, keep_strings=False):
    # Preserve line numbers and strings; erase only comments.
    pattern = (r'--\[(=*)\[.*?\]\1\]|--[^\n]*' if lua else r'/\*.*?\*/|//[^\n]*')
    pattern = r'"(?:\\.|[^"\\])*"|\x27(?:\\.|[^\x27\\])*\x27|' + pattern
    return re.sub(pattern, lambda m: m.group() if keep_strings and m.group()[0] in ('"', "'") else re.sub(r'[^\n]', ' ', m.group()), text, flags=re.S)


def integer(expression, symbols):
    expression = expression.strip().rstrip(',;').strip()
    if len(expression) > 256:
        return None
    try:
        tree = ast.parse(expression, mode='eval')
    except (SyntaxError, ValueError, RecursionError):
        return None
    def visit(node, depth=0):
        if depth > 16:
            raise ValueError()
        if isinstance(node, ast.Constant) and type(node.value) is int:
            result = node.value
        elif isinstance(node, ast.Name) and node.id in symbols:
            result = symbols[node.id]
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            result = visit(node.operand, depth + 1) * (-1 if isinstance(node.op, ast.USub) else 1)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.BitOr, ast.LShift)):
            left, right = visit(node.left, depth + 1), visit(node.right, depth + 1)
            if isinstance(node.op, ast.Add): result = left + right
            elif isinstance(node.op, ast.Sub): result = left - right
            elif isinstance(node.op, ast.BitOr): result = left | right
            elif 0 <= right <= 16: result = left << right
            else: raise ValueError()
        else:
            raise ValueError()
        if abs(result) > 0xffffffff:
            raise ValueError()
        return result
    try:
        return visit(tree.body)
    except (ValueError, TypeError, RecursionError):
        return None


def discover(files, options):
    contents, aliases = {}, {'effects': {}, 'missiles': {}}
    declarations = {}
    for file in files:
        text = file.read_bytes().decode('latin-1') if file.suffix.lower() == '.xml' else file.read_text(encoding=options.get('encoding', 'utf-8-sig'))
        contents[file] = text if file.suffix.lower() == '.xml' else without_comments(text, file.suffix.lower() == '.lua')
        if file.suffix.lower() in ('.h', '.hpp', '.cpp'):
            literal_tables = without_comments(text, keep_strings=True)
            for match in re.finditer(r'\{\s*"([a-zA-Z][a-zA-Z0-9_]*)"\s*,\s*(CONST_(ME|ANI)_[A-Z0-9_]+)\s*\}', literal_tables):
                category = 'effects' if match[3] == 'ME' else 'missiles'
                aliases[category].setdefault(match[1], set()).add(match[2])
        if file.suffix.lower() != '.xml':
            for match in re.finditer(r'\b([A-Z][A-Z0-9_]*)\s*=\s*([^,;\n}]+)', contents[file]):
                declarations.setdefault(match[1], set()).add(match[2].strip())
    explicit = options.get('constants', {})
    symbols = dict(explicit)
    ambiguous = set()
    for _ in range(len(declarations) + 2):
        updated = dict(explicit)
        for name, expressions in declarations.items():
            if name in explicit:
                continue
            values = {integer(e, symbols) for e in expressions}
            if None in values:
                continue
            if len(values) > 1:
                ambiguous.add(name)
            elif name not in ambiguous:
                updated[name] = values.pop()
        if updated == symbols:
            break
        symbols = updated
    references = []
    overrides = options.get('resolutions', {})
    def add(file, line, category, expression, server_id=False):
        expression = expression.strip()
        identity = f'{file.as_posix()}:{line}:{category}:{server_id}:{expression}'
        ref_id = hashlib.sha256(identity.encode()).hexdigest()[:20]
        value = integer(expression, symbols)
        if value is None and expression in aliases.get(category, {}):
            values = {integer(name, symbols) for name in aliases[category][expression]}
            if len(values) == 1 and None not in values:
                value = values.pop()
        ids = overrides.get(ref_id, [value] if value is not None else None)
        references.append(dict(reference_id=ref_id, file=str(file), line=line,
                               category=category, expression=expression, server_id=server_id,
                               ids=ids, status='resolved' if ids is not None else 'unresolved',
                               explicit_resolution=ref_id in overrides))
    for file, text in contents.items():
        if file.suffix.lower() == '.xml':
            if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
                raise FormatError(f'XML declarations/entities are unsupported: {file}')
            try:
                root = ET.fromstring(file.read_bytes())
            except ET.ParseError as error:
                raise FormatError(f'Invalid XML {file}: {error}') from error
            for element in root.iter():
                attrs = {k.lower(): v for k, v in element.attrib.items()}
                for key in ('looktype', 'looktypeex', 'lookitem'):
                    if key in attrs:
                        add(file, 0, 'outfits' if key == 'looktype' else 'items', attrs[key], key != 'looktype')
                if element.tag.lower() in ('look', 'mount') and 'type' in attrs:
                    add(file, 0, 'outfits', attrs['type'])
                if element.tag.lower() == 'item':
                    if 'id' in attrs:
                        add(file, 0, 'items', attrs['id'], True)
                    elif 'fromid' in attrs and 'toid' in attrs:
                        start, end = integer(attrs['fromid'], symbols), integer(attrs['toid'], symbols)
                        if start is not None and end is not None and 0 <= end - start <= 65535:
                            for value in range(start, end + 1): add(file, 0, 'items', str(value), True)
                        else: add(file, 0, 'items', attrs['fromid'] + '..' + attrs['toid'], True)
                if element.tag.lower() == 'attribute':
                    key, value = attrs.get('key', '').lower(), attrs.get('value')
                    if value is not None and key in ('decayto', 'rotateto', 'transformequipto', 'transformdeequipto', 'destroyto'):
                        add(file, 0, 'items', value, True)
                    elif value is not None and key in ('areaeffect', 'effect', 'shooteffect', 'distanceeffect'):
                        add(file, 0, 'missiles' if key in ('shooteffect', 'distanceeffect') else 'effects', value)
        else:
            patterns = [
                ('outfits', False, r'\blookType\s*=\s*([^,}\n;]+)'),
                ('items', True, r'\blookTypeEx\s*=\s*([^,}\n;]+)'),
                ('effects', False, r'\bsendMagicEffect\s*\(\s*([^,)\n]+)'),
                ('effects', False, r'\b(?:effect|magicEffect)\s*=\s*([^,}\n;]+)'),
                ('missiles', False, r'\b(?:shootEffect|distanceEffect)\s*=\s*([^,}\n;]+)'),
                ('missiles', False, r'\bsendDistanceEffect\s*\([^,\n]+,\s*([^,)\n]+)'),
                ('effects', False, r'\bsetParameter\s*\(\s*COMBAT_PARAM_EFFECT\s*,\s*([^,)\n]+)'),
                ('missiles', False, r'\bsetParameter\s*\(\s*COMBAT_PARAM_DISTANCEEFFECT\s*,\s*([^,)\n]+)'),
                ('items', True, r'\b(?:createItem|CreateItem|addItem|transform|doCreateItem)\s*\(\s*([^,)\n]+)'),
                ('effects', False, r'\baddMagicEffect\s*\([^,\n]+,\s*([^,)\n]+)'),
                ('missiles', False, r'\baddDistanceEffect\s*\([^,\n]+,[^,\n]+,\s*([^,)\n]+)'),
                ('items', True, r'\bdoPlayerAddItem\s*\([^,\n]+,\s*([^,)\n]+)'),
                ('effects', False, r'\bdoSendMagicEffect\s*\([^,\n]+,\s*([^,)\n]+)'),
                ('missiles', False, r'\bdoSendDistanceShoot\s*\([^,\n]+,[^,\n]+,\s*([^,)\n]+)'),
            ]
            for category, server_id, pattern in patterns:
                for match in re.finditer(pattern, text):
                    line_start = text.rfind('\n', 0, match.start()) + 1
                    if re.search(r'\bfunction\b', text[line_start:match.start()]):
                        continue
                    add(file, text.count('\n', 0, match.start()) + 1, category, match[1], server_id)
    stale = set(overrides) - {r['reference_id'] for r in references}
    if stale:
        raise ValueError(f'Unused/stale dependency resolutions: {sorted(stale)}')
    return references, dict(symbols=symbols, ambiguous_constants=sorted(ambiguous), files=len(files),
                            named_visuals={category: sorted(values) for category, values in aliases.items()})
