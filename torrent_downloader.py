"""
SeedUp - Smart Torrent Management Tool
Torrent downloader module using libtorrent with resume capability.

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
"""

import libtorrent as lt
import time
import os
import sys
from config import TORRENT_SESSION_FILE, TORRENT_DOWNLOAD_PATH, get_logger

logger = get_logger(__name__)

# Check if running in Google Colab
try:
    from google.colab import files
    IN_COLAB = True
except ImportError:
    IN_COLAB = False

# Note: this module runs as a subprocess (invoked via `!python main.py ...`
# from Colab), not as code inside the notebook's own kernel — so
# IPython.display.clear_output() is not usable here; it only works for code
# executing directly in the kernel. Multi-line progress redraws instead use
# raw ANSI cursor-movement codes written straight to stdout (see
# download_torrents() below), the same mechanism a real terminal uses.


def save_session(session, session_file=TORRENT_SESSION_FILE):
    """Save session state to resume later (correctly saves binary data)."""
    try:
        with open(session_file, "wb") as f:
            session_state = session.save_state()
            f.write(lt.bencode(session_state))
        logger.debug(f"Session saved to {session_file}")
    except Exception as e:
        logger.error(f"Failed to save session: {e}")


def load_session(session_file=TORRENT_SESSION_FILE):
    """Load session state if exists, otherwise return a new session."""
    if os.path.exists(session_file):
        try:
            with open(session_file, "rb") as f:
                session_data = f.read()
                if not session_data:
                    raise ValueError("Session file is empty.")
                
                session_state = lt.bdecode(session_data)
                ses = lt.session()
                ses.load_state(session_state)
                logger.info(f"Session loaded from {session_file}")
                return ses
        except (RuntimeError, ValueError) as e:
            logger.warning(f"Failed to load session ({e}). Starting fresh.")
            os.remove(session_file)
    
    return lt.session()


def list_torrent_files(source, timeout=60):
    """
    Fetch a torrent's file list (index, path, size) without downloading any
    of the actual file data. For magnet links this briefly connects to
    peers/DHT just long enough to retrieve metadata.

    :param source: .torrent file path or magnet link.
    :param timeout: Max seconds to wait for metadata (magnet links only).
    :return: List of dicts: {'index': int, 'path': str, 'size': int} or None on failure.
    """
    ses = lt.session()
    ses.apply_settings({'listen_interfaces': '0.0.0.0:6881'})

    params = lt.add_torrent_params()
    params.save_path = "."  # not used, no data is downloaded
    # Don't download any data yet, just metadata.
    params.flags |= lt.torrent_flags.upload_mode

    if source.startswith("magnet:"):
        params.url = source
    elif source.endswith(".torrent"):
        if not os.path.exists(source):
            logger.error(f"Torrent file not found: {source}")
            return None
        try:
            with open(source, "rb") as f:
                torrent_data = lt.bdecode(f.read())
                info = lt.torrent_info(torrent_data)
                params.ti = info
        except Exception as e:
            logger.error(f"Failed to read torrent file: {e}")
            return None
    else:
        logger.error("Invalid source. Provide a .torrent file or magnet link.")
        return None

    try:
        handle = ses.add_torrent(params)
    except Exception as e:
        logger.error(f"Failed to add torrent: {e}")
        return None

    waited = 0
    while not handle.status().has_metadata:
        if waited >= timeout:
            logger.error("Timed out waiting for torrent metadata.")
            ses.remove_torrent(handle)
            return None
        time.sleep(1)
        waited += 1

    info = handle.torrent_file()
    storage = info.files()
    files = []
    for i in range(storage.num_files()):
        files.append({
            "index": i,
            "path": storage.file_path(i),
            "size": storage.file_size(i),
        })

    ses.remove_torrent(handle)
    return files


def download_torrent(source, download_path=TORRENT_DOWNLOAD_PATH, 
                    session_file=TORRENT_SESSION_FILE, auto_resume=True,
                    file_indices=None):
    """
    Download a torrent file using libtorrent, with support for stopping/resuming.
    
    :param source: .torrent file path or magnet link.
    :param download_path: Directory to save the downloaded content.
    :param session_file: File to save/load session state.
    :param auto_resume: Automatically load previous session if available.
    :param file_indices: Optional list of file indices (from list_torrent_files)
                          to download. Files not in this list are skipped
                          entirely (never requested from peers). None downloads
                          every file in the torrent.
    :return: Path to downloaded content or None on failure.
    """
    if not os.path.exists(download_path):
        os.makedirs(download_path)
        logger.info(f"Created download directory: {download_path}")

    # Check if we're resuming from a previous session
    is_resuming = auto_resume and os.path.exists(session_file)

    # Load existing session or create new one
    ses = load_session(session_file) if auto_resume else lt.session()

    # Apply necessary settings
    settings = {
        'listen_interfaces': '0.0.0.0:6881',
    }
    ses.apply_settings(settings)

    # Initialize add_torrent_params
    params = lt.add_torrent_params()
    params.save_path = download_path
    params.storage_mode = lt.storage_mode_t.storage_mode_sparse

    # Handle magnet link or .torrent file
    if source.startswith("magnet:"):
        params.url = source
        logger.info(f"Adding magnet link: {source[:60]}...")
    elif source.endswith(".torrent"):
        if not os.path.exists(source):
            logger.error(f"Torrent file not found: {source}")
            return None
        
        try:
            with open(source, "rb") as f:
                torrent_data = lt.bdecode(f.read())
                info = lt.torrent_info(torrent_data)
                params.ti = info
            logger.info(f"Adding torrent file: {source}")
        except Exception as e:
            logger.error(f"Failed to read torrent file: {e}")
            return None
    else:
        logger.error("Invalid source. Provide a .torrent file or magnet link.")
        return None

    # Add the torrent to the session
    try:
        handle = ses.add_torrent(params)
        logger.info(f"Downloading to: {download_path}")
    except Exception as e:
        logger.error(f"Failed to add torrent: {e}")
        return None

    # Wait for metadata
    logger.info("Waiting for metadata...")
    while not handle.status().has_metadata:
        time.sleep(1)

    torrent_name = handle.status().name
    logger.info(f"Downloading: {torrent_name}")

    # If specific files were requested, skip everything else entirely so
    # unselected files are never fetched from peers in the first place.
    if file_indices is not None:
        info = handle.torrent_file()
        num_files = info.files().num_files()
        selected = set(file_indices)
        priorities = [4 if i in selected else 0 for i in range(num_files)]
        handle.prioritize_files(priorities)
        logger.info(f"Selective download: {len(selected)}/{num_files} file(s) selected")

    try:
        while handle.status().state not in (lt.torrent_status.finished, lt.torrent_status.seeding):
            s = handle.status()
            progress = s.progress * 100

            # Calculate ETA
            eta_str = "N/A"
            if s.download_rate > 0:
                total_size = s.total_wanted
                downloaded = s.total_done
                remaining = total_size - downloaded
                eta_seconds = remaining / s.download_rate
                
                if eta_seconds < 60:
                    eta_str = f"{int(eta_seconds)}s"
                elif eta_seconds < 3600:
                    eta_str = f"{int(eta_seconds / 60)}m {int(eta_seconds % 60)}s"
                else:
                    hours = int(eta_seconds / 3600)
                    minutes = int((eta_seconds % 3600) / 60)
                    eta_str = f"{hours}h {minutes}m"

            # Format download speed
            if s.download_rate > 1024 * 1024:  # > 1 MB/s
                speed_str = f"{s.download_rate / (1024 * 1024):.2f} MB/s"
            else:
                speed_str = f"{s.download_rate / 1024:.2f} KB/s"

            # Build complete progress bar string manually
            bar_length = 30
            filled_length = int(bar_length * progress / 100)
            bar = '█' * filled_length + '░' * (bar_length - filled_length)
            
            # Determine label based on actual state
            if is_resuming and progress < 95:
                label = "Resuming Download"
            elif s.download_rate == 0 and s.num_peers == 0:
                label = "Connecting to Peers"
            else:
                label = "Download Progress"
                is_resuming = False  # No longer resuming once we're actively downloading
            
            stats_str = f"Seeds: {s.num_seeds} | Peers: {s.num_peers - s.num_seeds} | Speed: {speed_str} | ETA: {eta_str}"
            progress_line = f"{label}: {bar} {progress:.1f}/100%    | {stats_str}"
            
            # Use simple print instead of tqdm to avoid interference
            print(f"\r{progress_line}", end="", flush=True)

            # Save session periodically (every 10 seconds)
            if int(time.time()) % 10 == 0:
                save_session(ses, session_file)
            
            time.sleep(1)

    except KeyboardInterrupt:
        print()  # New line after progress bar
        logger.warning("Download paused by user. Session saved for resume.")
        save_session(ses, session_file)
        return None
    
    print()  # New line after progress bar completion

    logger.info("Download complete!")

    # Remove placeholder files/folders for any files that were deselected,
    # so only the files you actually chose end up in the download folder.
    if file_indices is not None:
        info = handle.torrent_file()
        storage = info.files()
        selected = set(file_indices)
        for i in range(storage.num_files()):
            if i in selected:
                continue
            skipped_path = os.path.join(download_path, storage.file_path(i))
            try:
                if os.path.exists(skipped_path):
                    os.remove(skipped_path)
            except OSError as e:
                logger.debug(f"Could not remove skipped placeholder '{skipped_path}': {e}")
        # Clean up any now-empty subdirectories left behind.
        root_dir = os.path.join(download_path, torrent_name)
        for dirpath, dirnames, filenames in os.walk(root_dir, topdown=False):
            if not dirnames and not filenames and dirpath != root_dir:
                try:
                    os.rmdir(dirpath)
                except OSError:
                    pass

    # Clean up session file on successful completion
    if os.path.exists(session_file):
        try:
            os.remove(session_file)
            logger.debug("Session file removed after successful download")
        except Exception as e:
            logger.warning(f"Could not remove session file: {e}")
    
    # Return the path to downloaded content
    downloaded_path = os.path.join(download_path, torrent_name)
    return downloaded_path


def _format_speed(rate):
    """Human-readable download speed from a bytes/sec rate."""
    if rate > 1024 * 1024:
        return f"{rate / (1024 * 1024):.2f} MB/s"
    return f"{rate / 1024:.2f} KB/s"


def _format_eta(seconds):
    if seconds is None or seconds == float('inf'):
        return "N/A"
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds / 60)}m {int(seconds % 60)}s"
    hours = int(seconds / 3600)
    minutes = int((seconds % 3600) / 60)
    return f"{hours}h {minutes}m"


def _cleanup_unselected_files(handle, file_indices, result_dir, download_path):
    """Remove placeholder files/folders for files that were deselected, so
    only the chosen files end up in the download folder."""
    if file_indices is None:
        return
    info = handle.torrent_file()
    storage = info.files()
    selected = set(file_indices)
    for i in range(storage.num_files()):
        if i in selected:
            continue
        skipped_path = os.path.join(download_path, storage.file_path(i))
        try:
            if os.path.exists(skipped_path):
                os.remove(skipped_path)
        except OSError as e:
            logger.debug(f"Could not remove skipped placeholder '{skipped_path}': {e}")
    for dirpath, dirnames, filenames in os.walk(result_dir, topdown=False):
        if not dirnames and not filenames and dirpath != result_dir:
            try:
                os.rmdir(dirpath)
            except OSError:
                pass


def download_torrents(sources, download_path=TORRENT_DOWNLOAD_PATH,
                       session_file=TORRENT_SESSION_FILE, auto_resume=True,
                       file_indices_map=None, metadata_timeout=120,
                       on_torrent_complete=None):
    """
    Download multiple torrents concurrently within a single shared libtorrent
    session, with a combined progress display for all of them.

    Running torrents in one shared session (rather than one process per
    torrent) means they cooperatively share the same disk I/O and bandwidth
    accounting, which is the same underlying network/disk pool Colab gives
    you either way — this just avoids spinning up N separate Python
    processes/sessions to do it.

    :param sources: list of magnet links / .torrent file paths.
    :param download_path: Shared base directory; each torrent gets its own
                           subfolder here (named after the torrent), same as
                           a single download would.
    :param session_file: File to save/load combined session state.
    :param auto_resume: Automatically load previous session if available.
    :param file_indices_map: Optional dict {source: file_indices_list} for
                              per-torrent selective downloads. A source
                              that's absent or maps to None downloads every
                              file in that torrent.
    :param metadata_timeout: Max seconds to wait for a magnet link's
                              metadata before giving up on that torrent
                              (other torrents in the batch are unaffected).
    :param on_torrent_complete: Optional callback ``fn(source, downloaded_path)``
                         invoked the instant an individual torrent finishes —
                         while the rest of the batch may still be downloading.
                         Intended for kicking off a per-torrent upload without
                         waiting for the whole batch. May return a string,
                         which is kept as that torrent's permanent status
                         line in the combined progress display (e.g. an
                         upload result), instead of just "complete".
    :return: dict {source: downloaded_path_or_None}. None means that
             torrent failed, timed out, or was still incomplete when the
             batch was interrupted.
    """
    if not sources:
        return {}

    file_indices_map = file_indices_map or {}

    if not os.path.exists(download_path):
        os.makedirs(download_path)
        logger.info(f"Created download directory: {download_path}")

    ses = load_session(session_file) if auto_resume else lt.session()
    ses.apply_settings({'listen_interfaces': '0.0.0.0:6881'})

    # jobs: source -> dict with handle, name, file_indices, result, done, added_ok
    jobs = {}
    for source in sources:
        params = lt.add_torrent_params()
        params.save_path = download_path
        params.storage_mode = lt.storage_mode_t.storage_mode_sparse

        if source.startswith("magnet:"):
            params.url = source
        elif source.endswith(".torrent"):
            if not os.path.exists(source):
                logger.error(f"Torrent file not found, skipping: {source}")
                jobs[source] = {"handle": None, "name": source, "result": None,
                                 "done": True, "file_indices": None, "prioritized": True}
                continue
            try:
                with open(source, "rb") as f:
                    torrent_data = lt.bdecode(f.read())
                    params.ti = lt.torrent_info(torrent_data)
            except Exception as e:
                logger.error(f"Failed to read torrent file '{source}', skipping: {e}")
                jobs[source] = {"handle": None, "name": source, "result": None,
                                 "done": True, "file_indices": None, "prioritized": True}
                continue
        else:
            logger.error(f"Invalid source, skipping: {source}")
            jobs[source] = {"handle": None, "name": source, "result": None,
                             "done": True, "file_indices": None, "prioritized": True}
            continue

        try:
            handle = ses.add_torrent(params)
        except Exception as e:
            logger.error(f"Failed to add torrent '{source}', skipping: {e}")
            jobs[source] = {"handle": None, "name": source, "result": None,
                             "done": True, "file_indices": None, "prioritized": True}
            continue

        jobs[source] = {
            "handle": handle,
            "name": source[:40],
            "result": None,
            "done": False,
            "file_indices": file_indices_map.get(source),
            "prioritized": False,
        }

    active_sources = [s for s, j in jobs.items() if j["handle"] is not None]
    if not active_sources:
        logger.error("No valid torrents to download.")
        return {s: None for s in sources}

    print(f"📦 Added {len(active_sources)} torrent(s) to a shared session\n")

    start_time = time.time()
    PAD_WIDTH = 160  # wide enough that a shorter new line fully overwrites a longer old one

    def _finalize_job(source, job):
        """Clean up deselected placeholder files and fire on_torrent_complete, the
        instant this specific torrent finishes (not waiting on the others)."""
        job["result"] = os.path.join(download_path, job["name"])

        if job["file_indices"] is not None:
            handle = job["handle"]
            info = handle.torrent_file()
            storage = info.files()
            selected = set(job["file_indices"])
            for i in range(storage.num_files()):
                if i in selected:
                    continue
                skipped_path = os.path.join(download_path, storage.file_path(i))
                try:
                    if os.path.exists(skipped_path):
                        os.remove(skipped_path)
                except OSError as e:
                    logger.debug(f"Could not remove skipped placeholder '{skipped_path}': {e}")
            for dirpath, dirnames, filenames in os.walk(job["result"], topdown=False):
                if not dirnames and not filenames and dirpath != job["result"]:
                    try:
                        os.rmdir(dirpath)
                    except OSError:
                        pass

        message = None
        if on_torrent_complete is not None:
            try:
                message = on_torrent_complete(source, job["result"])
            except Exception as e:
                logger.error(f"on_torrent_complete callback failed for '{job['name']}': {e}")
        return message or f"✅ {job['name']}: complete"

    try:
        while True:
            all_done = True
            active_summaries = []
            newly_finished_messages = []

            for source in active_sources:
                job = jobs[source]
                if job["done"]:
                    continue  # already permanently printed, nothing more to do

                handle = job["handle"]
                s = handle.status()

                if not s.has_metadata:
                    all_done = False
                    if time.time() - start_time > metadata_timeout:
                        logger.warning(f"Timed out waiting for metadata: {source[:60]}")
                        job["done"] = True
                        job["result"] = None
                        newly_finished_messages.append(f"❌ {job['name']}: metadata timeout")
                        continue
                    active_summaries.append(f"{job['name'][:20]}: waiting for metadata...")
                    continue

                if job["name"] == source[:40]:
                    job["name"] = s.name  # switch to the real torrent name once known

                # Apply file-selection priorities once, right after metadata arrives.
                if not job["prioritized"]:
                    if job["file_indices"] is not None:
                        info = handle.torrent_file()
                        num_files = info.files().num_files()
                        selected = set(job["file_indices"])
                        priorities = [4 if i in selected else 0 for i in range(num_files)]
                        handle.prioritize_files(priorities)
                        logger.info(f"[{job['name']}] Selective download: {len(selected)}/{num_files} file(s) selected")
                    job["prioritized"] = True

                if s.state in (lt.torrent_status.finished, lt.torrent_status.seeding):
                    job["done"] = True
                    newly_finished_messages.append(_finalize_job(source, job))
                    continue

                all_done = False
                progress = s.progress * 100

                if s.download_rate == 0 and s.num_peers == 0:
                    label = "connecting"
                else:
                    label = _format_speed(s.download_rate)

                active_summaries.append(f"{job['name'][:20]}: {progress:5.1f}% ({label})")

            # Torrents that finished THIS cycle get a permanent line each,
            # appended normally (guaranteed to render correctly, no special
            # terminal support needed). Clear the rolling line first so a
            # stale in-progress percentage doesn't linger above them.
            if newly_finished_messages:
                print("\r" + " " * PAD_WIDTH + "\r", end="")
                for msg in newly_finished_messages:
                    print(msg)

            # The rest of the still-active torrents share ONE continuously
            # overwritten line at the bottom — this is the same \r mechanism
            # the single-torrent progress bar already relies on, just with
            # every active torrent's status packed into that one line
            # (genuine independent multi-line in-place updates aren't
            # reliable from this subprocess's captured stdout).
            if active_summaries:
                line = f"⬇️  [{len(active_summaries)} active] " + " | ".join(active_summaries)
                print("\r" + line[:PAD_WIDTH].ljust(PAD_WIDTH), end="", flush=True)

            if int(time.time()) % 10 == 0:
                save_session(ses, session_file)

            if all_done:
                break

            time.sleep(1)

    except KeyboardInterrupt:
        print()
        logger.warning("Download batch paused by user. Session saved for resume.")
        save_session(ses, session_file)
        return {s: jobs[s]["result"] if s in jobs else None for s in sources}

    print()
    logger.info("All torrents in batch finished downloading!")

    if os.path.exists(session_file):
        try:
            os.remove(session_file)
            logger.debug("Session file removed after successful batch download")
        except Exception as e:
            logger.warning(f"Could not remove session file: {e}")

    return {s: jobs[s]["result"] for s in sources}



def get_download_status(session_file=TORRENT_SESSION_FILE):
    """
    Check if there's a paused download that can be resumed.
    
    :return: True if a session file exists, False otherwise.
    """
    return os.path.exists(session_file)


def clear_session(session_file=TORRENT_SESSION_FILE):
    """
    Clear the session file to start fresh.
    
    :return: True if cleared successfully, False otherwise.
    """
    if os.path.exists(session_file):
        try:
            os.remove(session_file)
            logger.info("Session file cleared")
            return True
        except Exception as e:
            logger.error(f"Failed to clear session: {e}")
            return False
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python torrent_downloader.py <torrent_file/magnet_link>")
        sys.exit(1)

    source = sys.argv[1]
    result = download_torrent(source)
    
    if result:
        print(f"\nDownloaded to: {result}")
        sys.exit(0)
    else:
        sys.exit(1)
