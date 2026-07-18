#!/usr/bin/env python3
"""
SeedUp - Smart Torrent Management Tool
A Python-based tool that combines torrent downloading with VikingFile uploading capabilities.

Copyright 2025 Ishara Deshapriya

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

Main entry point for torrent downloader with VikingFile upload (Colab-optimized).
Combines torrent downloading and cloud storage capabilities.
"""

import sys
import argparse
import os
from pathlib import Path

from torrent_downloader import download_torrent, download_torrents, get_download_status, clear_session, list_torrent_files
from config import ConfigManager, TORRENT_DOWNLOAD_PATH, VIKINGFILE_USER_HASH, get_logger

logger = get_logger(__name__)


def get_uploader():
    """Import and return uploader function."""
    try:
        from vikingfile_uploader import upload_to_vikingfile
        return upload_to_vikingfile
    except ImportError as e:
        logger.error(f"Failed to import uploader: {str(e)}")
        print("\n" + "="*60)
        print("ERROR: Failed to import VikingFile uploader")
        print("="*60)
        print("Please ensure all required packages are installed:")
        print("  pip install requests tqdm")
        print("="*60)
        raise


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Download torrents and upload to VikingFile (Colab-optimized)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List the files inside a torrent (no data downloaded)
  python main.py files -t "magnet:?xt=urn:btih:..."
  
  # Download torrent only
  python main.py download -t movie.torrent
  python main.py download -t "magnet:?xt=urn:btih:..."
  
  # Download only specific files (indices from `files` command)
  python main.py download -t "magnet:?xt=urn:btih:..." --select-files 0,2,5

  # Ranges are supported too
  python main.py download -t "magnet:?xt=urn:btih:..." --select-files 0-3,7
  
  # Download and upload to VikingFile
  python main.py download -t "magnet:?xt=urn:btih:..." --upload -p "SeedUp/Movies"

  # Download multiple torrents at once, in one shared session
  python main.py download-multi -t "magnet:...one" -t "magnet:...two" --upload

  # Same, with per-torrent file selection (line up with -t order; use "" to skip selection for a torrent)
  python main.py download-multi -t "magnet:...one" -t "magnet:...two" -s "0,2" -s ""
  
  # Upload existing files to VikingFile
  python main.py upload -p /path/to/folder
  
  # Upload without skipping existing files
  python main.py upload -p /path --no-skip
  
  # Upload anonymously (ignores the configured user hash)
  python main.py upload -p /path --anonymous
  
  # Check for paused downloads
  python main.py status
  
  # Clear download session
  python main.py clear
  
Note: Uploads are associated with the VikingFile user hash configured in
config.py (VIKINGFILE_USER_HASH) unless --anonymous is passed.
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    # Download command
    download_parser = subparsers.add_parser('download', help='Download a torrent')
    download_parser.add_argument(
        '-t', '--torrent',
        type=str,
        required=True,
        help='Torrent file path or magnet link'
    )
    download_parser.add_argument(
        '-d', '--destination',
        type=str,
        default=TORRENT_DOWNLOAD_PATH,
        help=f'Download destination (default: {TORRENT_DOWNLOAD_PATH})'
    )
    download_parser.add_argument(
        '--no-resume',
        action='store_true',
        help='Start fresh download (ignore previous session)'
    )
    download_parser.add_argument(
        '--upload',
        action='store_true',
        help='Upload to VikingFile after download'
    )
    download_parser.add_argument(
        '-p', '--path',
        type=str,
        help='VikingFile destination folder path (optional, e.g. "SeedUp/Movies")'
    )
    download_parser.add_argument(
        '--no-skip',
        action='store_true',
        help='Force re-upload even if a file with the same name exists remotely'
    )
    download_parser.add_argument(
        '--anonymous',
        action='store_true',
        help='Upload anonymously instead of using the configured user hash'
    )
    download_parser.add_argument(
        '--select-files',
        type=str,
        help='File indices to download (from the `files` command), e.g. "0,2,5" '
             'or with ranges "0-3,7". Omit to download every file in the torrent.'
    )

    # Files command (preview a torrent's contents without downloading)
    files_parser = subparsers.add_parser('files', help="List a torrent's files without downloading any data")
    files_parser.add_argument(
        '-t', '--torrent',
        type=str,
        required=True,
        help='Torrent file path or magnet link'
    )

    # Download-multi command (concurrent multi-torrent download in one session)
    multi_parser = subparsers.add_parser(
        'download-multi',
        help='Download multiple torrents concurrently in a single shared session'
    )
    multi_parser.add_argument(
        '-t', '--torrent',
        action='append',
        required=True,
        help='Torrent file path or magnet link. Repeat -t once per torrent.'
    )
    multi_parser.add_argument(
        '-s', '--select-files',
        action='append',
        default=None,
        help='File indices for the -t entry at the same position (e.g. "0,2,5" or "0-3,7"). '
             'Use "" for a torrent you want downloaded in full. Optional — omit entirely to '
             'download every file in every torrent.'
    )
    multi_parser.add_argument(
        '-d', '--destination',
        type=str,
        default=TORRENT_DOWNLOAD_PATH,
        help=f'Shared download destination; each torrent gets its own subfolder here (default: {TORRENT_DOWNLOAD_PATH})'
    )
    multi_parser.add_argument(
        '--no-resume',
        action='store_true',
        help='Start fresh (ignore previous session)'
    )
    multi_parser.add_argument(
        '--upload',
        action='store_true',
        help='Upload each torrent to VikingFile as soon as the whole batch finishes downloading'
    )
    multi_parser.add_argument(
        '-p', '--path',
        type=str,
        help='Shared VikingFile destination base folder path. Each torrent uploads to '
             '"<path>/<torrent name>" so they don\'t collide. Omit to auto-name each one.'
    )
    multi_parser.add_argument(
        '--no-skip',
        action='store_true',
        help='Force re-upload even if a file with the same name exists remotely'
    )
    multi_parser.add_argument(
        '--anonymous',
        action='store_true',
        help='Upload anonymously instead of using the configured user hash'
    )

    # Upload command
    upload_parser = subparsers.add_parser('upload', help='Upload files to VikingFile')
    upload_parser.add_argument(
        '-p', '--path',
        type=str,
        required=True,
        help='Local path to file or folder to upload'
    )
    upload_parser.add_argument(
        '-r', '--remote-path',
        type=str,
        help='VikingFile destination folder path (optional, e.g. "SeedUp/Movies")'
    )
    upload_parser.add_argument(
        '--no-skip',
        action='store_true',
        help='Force re-upload even if a file with the same name exists remotely'
    )
    upload_parser.add_argument(
        '--anonymous',
        action='store_true',
        help='Upload anonymously instead of using the configured user hash'
    )
    
    # Status command
    subparsers.add_parser('status', help='Check download status')
    
    # Clear command
    subparsers.add_parser('clear', help='Clear download session')
    
    return parser.parse_args()


def handle_download(args):
    """Handle torrent download command."""
    print("="*60)
    print("TORRENT DOWNLOADER")
    print("="*60)
    
    # Parse --select-files into a list of indices, if given.
    # Accepts comma-separated indices and/or ranges, e.g. "0,2,5-7".
    file_indices = None
    if args.select_files:
        try:
            file_indices = parse_file_selection(args.select_files)
        except ValueError:
            logger.error(
                f"Invalid --select-files value: {args.select_files!r} "
                "(expected comma-separated indices and/or ranges, e.g. '0,2,5-7')"
            )
            return 1

    # Download the torrent
    logger.info(f"Starting download: {args.torrent}")
    downloaded_path = download_torrent(
        args.torrent,
        download_path=args.destination,
        auto_resume=not args.no_resume,
        file_indices=file_indices
    )
    
    if not downloaded_path:
        logger.error("Download failed or was cancelled")
        return 1
    
    logger.info(f"Download completed: {downloaded_path}")
    
    # Upload to VikingFile if requested
    if args.upload:
        print("\n" + "="*60)
        print("UPLOADING TO VIKINGFILE")
        print("="*60)
        
        try:
            upload_to_vikingfile = get_uploader()
            
            user = "" if args.anonymous else VIKINGFILE_USER_HASH
            results = upload_to_vikingfile(
                downloaded_path,
                args.path,
                user=user,
                skip_existing=not args.no_skip
            )
            
            if results['failed']:
                logger.warning(f"Some files failed to upload ({len(results['failed'])} items)")
                return 1
            
            logger.info("Upload completed successfully!")
            
        except Exception as e:
            logger.error(f"Upload failed: {str(e)}")
            return 1
    
    return 0


def handle_download_multi(args):
    """Handle concurrent multi-torrent download command."""
    print("="*60)
    print("MULTI-TORRENT DOWNLOADER")
    print("="*60)

    sources = args.torrent
    selections = args.select_files or []

    file_indices_map = {}
    for i, source in enumerate(sources):
        raw = selections[i] if i < len(selections) else ""
        if raw:
            try:
                file_indices_map[source] = parse_file_selection(raw)
            except ValueError:
                logger.error(
                    f"Invalid --select-files value for torrent #{i} ({source[:40]}...): {raw!r} "
                    "(expected comma-separated indices and/or ranges, e.g. '0,2,5-7')"
                )
                return 1

    # If --upload is set, each torrent is uploaded to VikingFile the moment
    # IT finishes, while the rest of the batch keeps downloading in the
    # background — rather than waiting for the whole batch to complete first.
    on_complete = None
    upload_state = {"any_failed": False}

    if args.upload:
        try:
            upload_to_vikingfile = get_uploader()
        except Exception as e:
            logger.error(f"Upload failed: {str(e)}")
            return 1

        user = "" if args.anonymous else VIKINGFILE_USER_HASH

        def on_complete(source, downloaded_path):
            name = os.path.basename(downloaded_path.rstrip(os.sep))
            remote_path = f"{args.path}/{name}" if args.path else None
            try:
                upload_results = upload_to_vikingfile(
                    downloaded_path,
                    remote_path,
                    user=user,
                    skip_existing=not args.no_skip
                )
                if upload_results['failed']:
                    upload_state["any_failed"] = True
                    return f"⚠️ {name}: uploaded with some failures"
                return f"📤 {name}: uploaded to VikingFile"
            except Exception as e:
                logger.error(f"Upload failed for '{downloaded_path}': {str(e)}")
                upload_state["any_failed"] = True
                return f"❌ {name}: upload error ({e})"

    logger.info(f"Starting batch download of {len(sources)} torrent(s)")
    results = download_torrents(
        sources,
        download_path=args.destination,
        auto_resume=not args.no_resume,
        file_indices_map=file_indices_map,
        on_torrent_complete=on_complete
    )

    succeeded = {s: p for s, p in results.items() if p}
    failed = [s for s, p in results.items() if not p]

    print("\n" + "="*60)
    print("BATCH DOWNLOAD SUMMARY")
    print("="*60)
    print(f"✅ {len(succeeded)} succeeded, ❌ {len(failed)} failed/incomplete")

    if failed:
        for s in failed:
            print(f"   ❌ {s[:70]}")

    if not succeeded:
        logger.error("No torrents completed successfully")
        return 1

    if args.upload:
        if upload_state["any_failed"]:
            logger.warning("One or more uploads had failures — see log above")
            return 1
        logger.info("All uploads completed successfully!")

    return 0 if not failed else 1


def parse_file_selection(selection: str):
    """
    Parse a file-selection string into a sorted list of unique indices.

    Accepts comma-separated indices and/or inclusive ranges, e.g.:
        "0,2,5"      -> [0, 2, 5]
        "0-3,7"      -> [0, 1, 2, 3, 7]
        "0-3,5-7"    -> [0, 1, 2, 3, 5, 6, 7]

    Raises ValueError on malformed input.
    """
    indices = set()
    for part in selection.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            start_str, end_str = part.split('-', 1)
            start, end = int(start_str.strip()), int(end_str.strip())
            if start > end:
                start, end = end, start
            indices.update(range(start, end + 1))
        else:
            indices.add(int(part))
    return sorted(indices)


def _format_size(num_bytes):
    """Human-readable file size."""
    size = float(num_bytes)
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def handle_files(args):
    """Handle listing a torrent's files without downloading."""
    print("="*60)
    print("TORRENT FILE LIST")
    print("="*60)

    logger.info(f"Fetching metadata: {args.torrent}")
    files = list_torrent_files(args.torrent)

    if files is None:
        logger.error("Failed to fetch torrent file list")
        return 1

    print(f"\nFound {len(files)} file(s):\n")
    for f in files:
        print(f"  [{f['index']:>3}] {_format_size(f['size']):>10}   {f['path']}")

    total_size = sum(f['size'] for f in files)
    print(f"\nTotal size (all files): {_format_size(total_size)}")
    print("\nTo download only some of these, use:")
    print(f'  python main.py download -t "{args.torrent}" --select-files <indices, e.g. 0,2,5-7>')
    return 0


def handle_upload(args):
    """Handle VikingFile upload command."""
    print("="*60)
    print("VIKINGFILE UPLOADER")
    print("="*60)
    
    # Validate path exists
    if not os.path.exists(args.path):
        logger.error(f"Path does not exist: {args.path}")
        return 1
    
    # Upload to VikingFile
    try:
        upload_to_vikingfile = get_uploader()
        
        user = "" if args.anonymous else VIKINGFILE_USER_HASH
        results = upload_to_vikingfile(
            args.path,
            args.remote_path,
            user=user,
            skip_existing=not args.no_skip
        )
        
        if results['failed']:
            logger.warning(f"Some files failed to upload ({len(results['failed'])} items)")
            return 1
        
        logger.info("Upload completed successfully!")
        return 0
    
    except Exception as e:
        logger.error(f"Upload failed: {str(e)}")
        return 1


def handle_status(args):
    """Handle status check command."""
    if get_download_status():
        print("✓ Found paused download session")
        print("  Run 'python main.py download -t <torrent>' to resume")
        return 0
    else:
        print("✗ No paused download session found")
        return 0


def handle_clear(args):
    """Handle clear session command."""
    if clear_session():
        print("✓ Download session cleared")
        return 0
    else:
        print("✗ Failed to clear session")
        return 1


def main():
    """Main entry point."""
    args = parse_arguments()
    
    # Show help if no command specified
    if not args.command:
        print("Error: No command specified\n")
        parse_arguments().print_help()
        return 1
    
    try:
        # Route to appropriate handler
        if args.command == 'download':
            return handle_download(args)
        elif args.command == 'files':
            return handle_files(args)
        elif args.command == 'download-multi':
            return handle_download_multi(args)
        elif args.command == 'upload':
            return handle_upload(args)
        elif args.command == 'status':
            return handle_status(args)
        elif args.command == 'clear':
            return handle_clear(args)
        else:
            logger.error(f"Unknown command: {args.command}")
            return 1
            
    except KeyboardInterrupt:
        print("\n\nOperation cancelled by user")
        if args.command == 'download':
            print("Download progress has been saved. Resume with the same command.")
        return 130
    except Exception as e:
        logger.error(f"Operation failed: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
            
