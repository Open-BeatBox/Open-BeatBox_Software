#!/usr/bin/env python3
import os
import sys
import argparse
import shutil
from pathlib import Path

# Get the absolute path to the script directory and project root
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

# Default list of relative file paths to upload
DEFAULT_PATHS = [
    'libs/can_manager.py',
    'libs/beatbox_module.py',
    'libs/serial_manager.py',
    'libs/bb_pin_map.py',
]

"""Additional module files to upload"""
MODULE_SPECIFIC_PATHS = [
    'modules/feeder.py',
    'modules/nosepoke.py',
    'modules/timing_handshake_can.py',
    'modules/main_module.py',
]

def find_circuitpy_devices():
    user = os.environ.get('USER')
    if not user:
        print('Could not determine $USER environment variable.')
        sys.exit(1)
    media_path = Path(f'/media/{user}')
    if not media_path.exists():
        print(f'Media path {media_path} does not exist.')
        sys.exit(1)
    devices = sorted([d for d in media_path.iterdir() if d.is_dir() and d.name.startswith('CIRCUITPY')])
    return devices

def upload_files(device_path, file_paths):
    lib_dir = device_path / 'lib'
    lib_dir.mkdir(parents=True, exist_ok=True)
    for rel_path in file_paths:
        src = (PROJECT_ROOT / rel_path).resolve()
        if not src.exists():
            print(f'Source file {src} does not exist. Skipping.')
            continue
        dest = lib_dir / src.name
        shutil.copy2(src, dest)
        print(f'Uploaded {src} to {dest}')

def main():
    parser = argparse.ArgumentParser(description='Upload files to CIRCUITPY device.')
    parser.add_argument('paths', nargs='*', help='Relative file paths to upload (optional, always uploads default list)')
    parser.add_argument('-n', '--number', type=int, default=0, help='Index of CIRCUITPY device (default: 0)')
    args = parser.parse_args()

    # Combine default paths and provided paths, deduplicate while preserving order
    seen = set()
    file_paths = []
    for path in DEFAULT_PATHS + args.paths:
        if path not in seen:
            seen.add(path)
            file_paths.append(path)

    if not file_paths:
        print('No files to upload. Exiting.')
        sys.exit(1)

    devices = find_circuitpy_devices()
    if not devices:
        print('No CIRCUITPY devices found.')
        sys.exit(1)
    if args.number >= len(devices):
        print(f'Requested device index {args.number} but only {len(devices)} device(s) found.')
        sys.exit(1)
    device = devices[args.number]
    print(f'Uploading to device: {device}')
    upload_files(device, file_paths)

if __name__ == '__main__':
    main()
