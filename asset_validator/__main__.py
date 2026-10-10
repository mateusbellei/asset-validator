import argparse
import json
import sys

from .formats import FormatError
from .pipeline import load_config, run, validate_directory


def main():
    parser = argparse.ArgumentParser(description='Audit assets and static dependencies; build reviewed, ID-preserving merges')
    subs = parser.add_subparsers(dest='command', required=True)
    for command in ('audit', 'build'):
        sub = subs.add_parser(command)
        sub.add_argument('--config', required=True)
        sub.add_argument('--output', required=True, help='New directory, separate from originals')
        if command == 'build':
            sub.add_argument('--plan', help='Reviewed report.json; required for replacement conflicts')
    sub = subs.add_parser('validate')
    sub.add_argument('directory')
    sub.add_argument('--inputs', action='store_true', help='Also verify original input hashes from a v2 build manifest')
    sub = subs.add_parser('check-inputs')
    sub.add_argument('--config', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'validate':
            result = validate_directory(args.directory, args.inputs)
        elif args.command == 'check-inputs':
            from contextlib import ExitStack
            from .pipeline import analyze, sha256
            with ExitStack() as stack:
                report = analyze(load_config(args.config), stack)[0]
                for entry in report['inputs'].values():
                    if sha256(entry['path']) != entry['sha256']:
                        raise FormatError('Input changed during validation')
                result = {key: report[key] for key in ('input_validation', 'summary', 'errors', 'warnings')}
        else:
            result = run(load_config(args.config), args.output, args.command == 'build', getattr(args, 'plan', None))
            result = {key: result[key] for key in ('summary', 'errors', 'warnings', 'validation') if key in result}
        failed = bool(result.get('errors'))
        for key in ('errors', 'warnings'):
            if key in result:
                result[key + '_count'] = len(result[key])
                result[key] = result[key][:20]
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if failed else 0
    except (ValueError, OSError, KeyError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
