import argparse
from contextlib import closing
import json
import sys

from .formats import Dat, FormatError, Spr, check_profile
from .pipeline import load_config, run, verify


def main():
    parser = argparse.ArgumentParser(description='Audit and migrate extended RGBA 10.98 assets preserving map IDs')
    subs = parser.add_subparsers(dest='command', required=True)
    for command in ('audit', 'build'):
        sub = subs.add_parser(command)
        sub.add_argument('--config', required=True)
        sub.add_argument('--output', required=True, help='New directory, separate from originals')
    sub = subs.add_parser('validate')
    sub.add_argument('directory')
    args = parser.parse_args()
    try:
        if args.command == 'validate':
            from pathlib import Path
            directory = Path(args.directory)
            check_profile(directory)
            with closing(Spr(directory / 'Tibia.spr')) as spr:
                result = verify(Dat(directory / 'Tibia.dat'), spr)
        else:
            result = run(load_config(args.config), args.output, args.command == 'build')
            result = {key: result[key] for key in ('summary', 'errors', 'validation') if key in result}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get('errors') else 0
    except (ValueError, OSError, KeyError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
