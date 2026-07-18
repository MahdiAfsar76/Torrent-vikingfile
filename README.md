# SeedUp - Smart Torrent Management V1 (VikingFile Edition)

## Overview

SeedUp is a Python based tool that combines torrent downloading with [VikingFile](https://vikingfile.com) uploading capabilities. It's designed to work both as a standalone application and in Google Colab environments, making it perfect for managing downloads without using local resources.

Perfect for users who need to download large files through torrents and automatically store them on VikingFile, especially useful in resource-constrained environments like Google Colab's free tier.

<br>

## Features

- **High-speed torrent downloading** using libtorrent
- **Selective file downloads** - pick specific files out of a multi-file torrent instead of grabbing everything
- **Concurrent multi-torrent downloads** - download several torrents at once in one shared session, with combined progress tracking
- **Automatic VikingFile upload** — no account login flow required, just a user hash
- **Resume capability** for interrupted downloads
- **Resilient multi-part uploads** for large files, with real-time progress bars
- **Smart duplicate detection** - skip files that already exist remotely
- **Flexible input support** - both magnet links and .torrent files
- **Google Colab optimized** with automatic environment detection
- **Organized uploads** with configurable destination folder paths
- **Command-line interface** for advanced users

<br>

## Installation

### For Google Colab (Recommended)

Open `SeedUp.ipynb` in Google Colab and follow the step-by-step guide.

### For Local Installation

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Install libtorrent:**
   ```bash
   # Ubuntu/Debian
   sudo apt-get install python3-libtorrent

   # macOS
   brew install libtorrent-rasterbar

   # Or via pip (may require compilation)
   pip install libtorrent
   ```

<br>

## Configuration

### VikingFile Account

SeedUp associates uploads with a VikingFile account using a **user hash** rather than a login/OAuth flow. Set your hash once in `config.py`:

```python
VIKINGFILE_USER_HASH = "your_user_hash_here"
```

You can find your user hash on your VikingFile account page. Leave it blank, or pass `--anonymous` on the CLI, to upload without associating files with any account (anonymous uploads can't be listed or de-duplicated later).

<br>

## Quick Start

### Command Line Usage

#### List the files inside a torrent (no data downloaded):
```bash
python main.py files -t "magnet:?xt=urn:btih:..."
```
Prints each file's index, size, and path — use the indices with `--select-files` below.

#### Download torrent only:
```bash
python main.py download -t "magnet:?xt=urn:btih:..."
python main.py download -t movie.torrent
```

#### Download only specific files from a multi-file torrent:
```bash
python main.py download -t "magnet:?xt=urn:btih:..." --select-files 0,2,5

# Ranges are supported too
python main.py download -t "magnet:?xt=urn:btih:..." --select-files 0-3,7
```
Deselected files are never requested from peers at all — this saves disk space and download time for torrents where you only want some of the content (e.g. one file out of a season pack).

#### Download and upload to VikingFile:
```bash
# Uploads to a folder named after the torrent
python main.py download -t "magnet:?xt=urn:btih:..." --upload

# Upload to a specific destination folder path
python main.py download -t "magnet:?xt=urn:btih:..." --upload -p "SeedUp/Movies"
```

#### Upload existing files to VikingFile:
```bash
python main.py upload -p /path/to/folder

# Upload to a specific destination folder path
python main.py upload -p /path/to/folder -r "SeedUp/Movies"

# Upload anonymously
python main.py upload -p /path/to/folder --anonymous
```

#### Download multiple torrents at once (single shared session, combined progress):
```bash
python main.py download-multi -t "magnet:...one" -t "magnet:...two" --upload

# With per-torrent file selection (lines up with -t order; "" = download that one in full)
python main.py download-multi -t "magnet:...one" -t "magnet:...two" -s "0,2" -s ""

# Shared upload destination base — each torrent uploads to "<path>/<torrent name>"
python main.py download-multi -t "magnet:...one" -t "magnet:...two" --upload -p "SeedUp/Batch"
```
All torrents in the batch share one libtorrent session (same shared disk/bandwidth Colab gives you either way), with a combined progress display and one shared resume session — if interrupted, re-running the same command picks up wherever each torrent left off.

#### Check download status:
```bash
python main.py status
```

#### Clear session:
```bash
python main.py clear
```

### Advanced Options

- `--no-resume`: Start fresh download (ignore previous session)
- `--no-skip`: Force re-upload even if a same-named file exists remotely
- `-d PATH`: Custom download destination
- `-p PATH` / `-r PATH`: VikingFile destination folder path (optional)
- `--anonymous`: Upload without associating files with your account

<br>

## Project Structure

```
SeedUp/
├── main.py                 # Main CLI entry point
├── torrent_downloader.py    # Torrent downloading logic
├── vikingfile_uploader.py   # VikingFile upload functionality
├── config.py                # Configuration and constants
├── requirements.txt         # Python dependencies
├── SeedUp.ipynb             # Google Colab notebook
├── LICENSE                  # Apache License 2.0
└── README.md                # This file
```

<br>

## How Uploads Work

Files are uploaded using [VikingFile's public API](https://vikingfile.com/api):

1. A multi-part upload session is requested for the file's size.
2. The file is streamed to VikingFile in chunks (parts), which is more resilient than a single request for large torrent downloads.
3. The upload is finalized, returning a shareable `https://vikingfile.com/f/<hash>` link for the file.
4. If a user hash is configured, VikingFile's file listing is used to skip files that were already uploaded to the same destination path.

Uploaded file links are printed to the console at the end of each upload.

<br>

## Important Considerations

### Legal Compliance
- **Only download content you have legal rights to access**
- **Respect copyright and intellectual property laws**
- **Be aware of your local regulations regarding torrents**
- **This tool is for legitimate use cases only**

### Google Colab Limitations
- **Runtime Limit**: ~12 hours maximum session
- **Disk Space**: Limited to ~100GB temporary storage
- **Session Management**: May disconnect if idle

<br>

## License

This project is licensed under the **Apache License 2.0** - see the [LICENSE](LICENSE) file for details.

<br>

## Acknowledgments

- **[libtorrent](https://github.com/arvidn/libtorrent)** team for the excellent torrent library
- **[VikingFile](https://vikingfile.com)** for the file hosting platform and public API
- **Python community** for amazing ecosystem
