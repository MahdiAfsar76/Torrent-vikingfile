"""
SeedUp - Smart Torrent Management Tool
VikingFile uploader module.

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

Uses the public VikingFile API (https://vikingfile.com/api) to upload files.
Large files are uploaded using the multi-part upload flow
(get-upload-url -> PUT each part -> complete-upload), which is more
resilient over long-running Colab sessions than a single request.

No authentication is required beyond an (optional) account "user hash",
which associates uploads with a VikingFile account so they show up in
that account's file list instead of being anonymous/untraceable uploads.
"""

import os
import time
from typing import Dict, List, Optional

import requests
from tqdm import tqdm

from config import get_logger, VIKINGFILE_API_BASE, VIKINGFILE_USER_HASH, MAX_RETRIES, RETRY_DELAY, MAX_RETRY_DELAY

logger = get_logger(__name__)
logger.setLevel(__import__("logging").WARNING)


def _request_with_retry(method: str, url: str, **kwargs) -> requests.Response:
    """Perform an HTTP request with basic exponential-backoff retries."""
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.request(method, url, timeout=kwargs.pop("timeout", 60), **kwargs)
            resp.raise_for_status()
            return resp
        except Exception as e:
            last_exc = e
            if attempt < MAX_RETRIES:
                delay = min(RETRY_DELAY * (2 ** (attempt - 1)), MAX_RETRY_DELAY)
                logger.warning(f"Request failed ({e}); retrying in {delay}s (attempt {attempt}/{MAX_RETRIES})")
                time.sleep(delay)
    raise RuntimeError(f"Request to {url} failed after {MAX_RETRIES} attempts: {last_exc}")


def get_upload_url(size: int) -> dict:
    """Request an upload session for a file of the given size (in bytes)."""
    resp = _request_with_retry("POST", f"{VIKINGFILE_API_BASE}/get-upload-url", data={"size": size})
    return resp.json()


def get_upload_server() -> str:
    """Get the legacy single-request upload server URL."""
    resp = _request_with_retry("GET", f"{VIKINGFILE_API_BASE}/get-server")
    return resp.json()["server"]


def complete_upload(key: str, upload_id: str, parts: List[dict], name: str,
                     user: str = "", path: Optional[str] = None) -> dict:
    """Finalize a multi-part upload."""
    data = {"key": key, "uploadId": upload_id, "name": name, "user": user or ""}
    if path:
        data["path"] = path
    for i, part in enumerate(parts):
        data[f"parts[{i}][PartNumber]"] = part["PartNumber"]
        data[f"parts[{i}][ETag]"] = part["ETag"]
    resp = _request_with_retry("POST", f"{VIKINGFILE_API_BASE}/complete-upload", data=data)
    return resp.json()


def list_files(user: str, page: int = 1, path: Optional[str] = None) -> dict:
    """List files already uploaded under an account (and optional path)."""
    data = {"user": user, "page": page}
    if path:
        data["path"] = path
    resp = _request_with_retry("POST", f"{VIKINGFILE_API_BASE}/list-files", data=data)
    return resp.json()


class VikingFileUploader:
    """Uploader that mirrors local files/folders to VikingFile with progress bars
    and duplicate detection, similar in spirit to a cloud-drive sync tool."""

    def __init__(self, user: Optional[str] = VIKINGFILE_USER_HASH, skip_existing: bool = True):
        """
        Args:
            user: VikingFile account user hash. Use None/"" for anonymous uploads
                  (anonymous uploads cannot be listed/deduped or managed later).
            skip_existing: If True, skip files that already exist at the same
                           remote path/name for this account.
        """
        self.user = user or ""
        self.skip_existing = skip_existing
        self._remote_file_cache: Dict[str, Optional[dict]] = {}

    # ---- duplicate detection -------------------------------------------------

    def _remote_files_in_path(self, remote_path: Optional[str]) -> List[dict]:
        if not self.user:
            return []  # anonymous uploads can't be listed
        cache_key = remote_path or ""
        if cache_key in self._remote_file_cache:
            return self._remote_file_cache[cache_key]  # type: ignore

        all_files: List[dict] = []
        page = 1
        while True:
            try:
                result = list_files(self.user, page=page, path=remote_path)
            except Exception as e:
                logger.warning(f"Could not list existing files: {e}")
                break
            all_files.extend(result.get("files", []))
            if page >= result.get("maxPages", 1):
                break
            page += 1

        self._remote_file_cache[cache_key] = all_files
        return all_files

    def file_exists(self, file_name: str, remote_path: Optional[str]) -> Optional[dict]:
        """Check whether a file with this name already exists at remote_path."""
        for f in self._remote_files_in_path(remote_path):
            if f.get("name") == file_name:
                return f
        return None

    # ---- uploading ------------------------------------------------------------

    def upload_file(self, local_path: str, remote_path: Optional[str] = None,
                     _progress_bar=None, _uploaded_size=None) -> Optional[dict]:
        """
        Upload a single file to VikingFile.

        Args:
            local_path: Path to the local file.
            remote_path: Destination folder path on VikingFile, e.g. "SeedUp/Movies".
                         None uploads to the account root.

        Returns:
            Dict with 'name', 'size', 'hash', 'url' on success, None on failure.
        """
        file_name = os.path.basename(local_path)
        file_size = os.path.getsize(local_path)

        if self.skip_existing:
            existing = self.file_exists(file_name, remote_path)
            if existing:
                if _progress_bar is not None and _uploaded_size is not None:
                    _uploaded_size[0] += file_size
                    _progress_bar.update(file_size)
                logger.info(f"Skipping (already exists): {file_name}")
                return {"name": file_name, "size": file_size, "skipped": True,
                         "hash": existing.get("hash"), "url": f"https://vikingfile.com/f/{existing.get('hash')}"}

        try:
            session = get_upload_url(file_size)
            key = session["key"]
            upload_id = session["uploadId"]
            part_size = session["partSize"]
            urls = session["urls"]

            parts = []
            with open(local_path, "rb") as f:
                for part_number, url in enumerate(urls, start=1):
                    chunk = f.read(part_size)
                    if not chunk:
                        break
                    resp = _request_with_retry("PUT", url, data=chunk, timeout=600)
                    etag = resp.headers.get("ETag", "").strip('"')
                    parts.append({"PartNumber": part_number, "ETag": etag})

                    if _progress_bar is not None and _uploaded_size is not None:
                        _uploaded_size[0] += len(chunk)
                        _progress_bar.update(len(chunk))

            result = complete_upload(key, upload_id, parts, file_name, self.user, remote_path)

            # Invalidate the listing cache for this path since it just changed.
            self._remote_file_cache.pop(remote_path or "", None)
            return result

        except Exception as e:
            logger.error(f"Failed to upload '{file_name}': {e}")
            return None

    # ---- helpers for folder uploads --------------------------------------------

    def count_items(self, local_path: str) -> Dict[str, int]:
        """Count total files and total size under a path (file or folder)."""
        if os.path.isfile(local_path):
            return {"files": 1, "total_size": os.path.getsize(local_path)}

        files = 0
        total_size = 0
        for root, _dirs, filenames in os.walk(local_path):
            for filename in filenames:
                try:
                    files += 1
                    total_size += os.path.getsize(os.path.join(root, filename))
                except OSError:
                    pass
        return {"files": files, "total_size": total_size}

    def upload_path(self, local_path: str, remote_base_path: Optional[str] = None) -> Dict[str, list]:
        """
        Upload a file or recursively upload a folder to VikingFile, preserving
        the folder structure as a remote path string (e.g. "SeedUp/MovieName").

        Returns:
            Dict with 'success', 'failed', 'skipped' lists (of local paths) and
            'links' (list of {'local': ..., 'url': ...} for uploaded files).
        """
        results = {"success": [], "failed": [], "skipped": [], "links": []}

        if not os.path.exists(local_path):
            results["failed"].append(local_path)
            return results

        stats = self.count_items(local_path)
        size_mb = stats["total_size"] / (1024 * 1024)
        print(f"📤 Uploading {stats['files']} file(s) ({size_mb:.1f} MB) to VikingFile")

        progress_bar = tqdm(
            total=stats["total_size"],
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            desc="Upload",
            bar_format="{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{rate_fmt}] {postfix}",
            leave=True,
            ncols=100,
        )
        uploaded_size = [0]

        if os.path.isfile(local_path):
            self._upload_single(local_path, remote_base_path, results, progress_bar, uploaded_size)
        else:
            root_name = remote_base_path or os.path.basename(local_path.rstrip(os.sep))
            for dirpath, _dirnames, filenames in os.walk(local_path):
                rel_dir = os.path.relpath(dirpath, local_path)
                if rel_dir == ".":
                    remote_dir = root_name
                else:
                    remote_dir = "/".join([root_name] + rel_dir.split(os.sep))
                for filename in filenames:
                    file_path = os.path.join(dirpath, filename)
                    self._upload_single(file_path, remote_dir, results, progress_bar, uploaded_size)

        progress_bar.close()
        print()
        return results

    def _upload_single(self, local_path, remote_path, results, progress_bar, uploaded_size):
        outcome = self.upload_file(local_path, remote_path,
                                    _progress_bar=progress_bar, _uploaded_size=uploaded_size)
        if outcome is None:
            results["failed"].append(local_path)
        elif outcome.get("skipped"):
            results["skipped"].append(local_path)
            results["links"].append({"local": local_path, "url": outcome.get("url")})
        else:
            results["success"].append(local_path)
            results["links"].append({"local": local_path, "url": outcome.get("url")})

    def print_summary(self, results: Dict[str, list]):
        """Print a clean upload summary with links to each uploaded file."""
        print("\n" + "=" * 60)
        print("🎉 UPLOAD COMPLETE")
        print("=" * 60)
        print(f"✅ {len(results['success'])} file(s) uploaded successfully")

        if results.get("skipped"):
            print(f"⏭️  {len(results['skipped'])} file(s) skipped (already exist)")

        if results["failed"]:
            print(f"❌ {len(results['failed'])} file(s) failed")

        if results.get("links"):
            print("\n📁 Links:")
            for entry in results["links"]:
                name = os.path.basename(entry["local"])
                print(f"   {name} -> {entry['url']}")

        print("=" * 60)


def upload_to_vikingfile(local_path: str, remote_path: Optional[str] = None, **kwargs) -> Dict[str, list]:
    """
    Upload a file or folder to VikingFile.

    Args:
        local_path: Path to file or folder to upload.
        remote_path: Destination folder path on VikingFile (optional). Defaults
                     to a folder named after the uploaded file/folder.
        **kwargs: Additional options:
            - user (str): VikingFile account user hash (defaults to configured hash).
            - skip_existing (bool): Skip files that already exist (default: True).

    Returns:
        Dictionary with 'success', 'failed', 'skipped', and 'links' lists.
    """
    user = kwargs.get("user", VIKINGFILE_USER_HASH)
    skip_existing = kwargs.get("skip_existing", True)

    uploader = VikingFileUploader(user=user, skip_existing=skip_existing)
    results = uploader.upload_path(local_path, remote_path)
    uploader.print_summary(results)
    return results
                         
