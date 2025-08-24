# video_fetcher.py
'''
Search and download stock videos by keyword via official APIs.
Providers: Pexels, Pixabay.
Usage:
    export PEXELS_API_KEY=...
    export PIXABAY_API_KEY=...
    python video_fetcher.py "nước giải khát" --limit 10 --out videos --min_width 720 --provider pexels pixabay
Notes:
  - Pinterest scraping is not included. Use their official APIs and terms.
  - Check each provider's license before redistribution.
'''
import os
import sys
import json
import time
import argparse
from pathlib import Path
from typing import Dict, Iterable, List, Optional
import requests

# --- Load .env automatically ---
try:
    from dotenv import load_dotenv
    load_dotenv()  # sẽ nạp file .env trong cùng thư mục nếu có
except ImportError:
    pass

# -------- Utilities --------

def safe_filename(name: str) -> str:
    keep = "-_.()[] {}{}".format("", "")
    return ''.join(c if c.isalnum() or c in keep else '_' for c in name).strip('_')

def download_file(url: str, dest: Path, headers: Optional[Dict[str, str]] = None, retries: int = 3) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    attempt = 0
    while attempt < retries:
        r = requests.get(url, headers=headers or {}, stream=True, timeout=60)
        if r.status_code == 200:
            tmp = dest.with_suffix(dest.suffix + ".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
            tmp.replace(dest)
            return
        attempt += 1
        time.sleep(1.5 * attempt)
    raise RuntimeError(f"Failed to download {url} -> HTTP {r.status_code}")

# -------- Pexels --------

def pexels_search(keyword: str, limit: int, min_width: int = 0) -> List[Dict]:
    api_key = os.getenv("PEXELS_API_KEY")
    if not api_key:
        return []
    url = "https://api.pexels.com/videos/search"
    headers = {"Authorization": api_key}
    out = []
    page = 1
    per_page = min(80, max(1, limit))
    while len(out) < limit:
        params = {"query": keyword, "per_page": per_page, "page": page}
        r = requests.get(url, headers=headers, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        for v in data.get("videos", []):
            files = v.get("video_files", [])
            files = sorted(files, key=lambda x: x.get("width") or 0, reverse=True)
            file = next((f for f in files if (f.get("width") or 0) >= min_width), None)
            if not file:
                continue
            out.append({
                "provider": "pexels",
                "id": v.get("id"),
                "title": (v.get("user", {}) or {}).get("name") or f"Pexels {v.get('id')}",
                "width": file.get("width"),
                "height": file.get("height"),
                "url": file.get("link"),
                "license": "Pexels License",
                "permalink": v.get("url"),
            })
            if len(out) >= limit:
                break
        if not data.get("videos") or len(out) >= limit:
            break
        page += 1
    return out

# -------- Pixabay --------

def pixabay_search(keyword: str, limit: int, min_width: int = 0) -> List[Dict]:
    api_key = os.getenv("PIXABAY_API_KEY")
    if not api_key:
        return []
    url = "https://pixabay.com/api/videos/"
    out = []
    page = 1
    per_page = min(200, max(3, limit))
    while len(out) < limit:
        params = {
            "key": api_key,
            "q": keyword,
            "per_page": per_page,
            "page": page,
            "safesearch": "true",
            "video_type": "all",
        }
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        hits = data.get("hits", [])
        for h in hits:
            # choose the best video matching min_width
            videos = h.get("videos") or {}
            candidates = []
            for variant in videos.values():
                w = variant.get("width") or 0
                candidates.append((w, variant.get("url"), variant.get("height")))
            candidates.sort(reverse=True)
            file = next(((w, u, ht) for w, u, ht in candidates if w >= min_width), None)
            if not file:
                continue
            w, u, ht = file
            out.append({
                "provider": "pixabay",
                "id": h.get("id"),
                "title": h.get("tags") or f"Pixabay {h.get('id')}",
                "width": w,
                "height": ht,
                "url": u,
                "license": "Pixabay License",
                "permalink": h.get("pageURL"),
            })
            if len(out) >= limit:
                break
        if not hits or len(out) >= limit:
            break
        page += 1
    return out

# -------- Orchestrator --------

def fetch(keyword: str, limit: int, providers: Iterable[str], min_width: int) -> List[Dict]:
    results: List[Dict] = []
    pv = [p.lower() for p in providers]
    if "pexels" in pv:
        results.extend(pexels_search(keyword, limit, min_width))
    if "pixabay" in pv:
        results.extend(pixabay_search(keyword, limit, min_width))
    # Deduplicate by url
    seen = set()
    deduped = []
    for item in results:
        if item["url"] in seen:
            continue
        seen.add(item["url"])
        deduped.append(item)
        if len(deduped) >= limit:
            break
    return deduped

def main():
    parser = argparse.ArgumentParser(description="Download stock videos by keyword using official APIs.")
    parser.add_argument("keyword", type=str, help="Search keyword, e.g., 'nước giải khát'")
    parser.add_argument("--limit", type=int, default=10, help="Max number of videos to download")
    parser.add_argument("--out", type=str, default="videos", help="Output folder")
    parser.add_argument("--min_width", type=int, default=720, help="Minimum video width")
    parser.add_argument("--provider", nargs="+", default=["pexels", "pixabay"], help="Providers to use")
    parser.add_argument("--dry_run", action="store_true", help="Only list results without downloading")
    args = parser.parse_args()

    results = fetch(args.keyword, args.limit, args.provider, args.min_width)

    if not results:
        print("No results or API keys missing.")
        sys.exit(1)

    out_dir = Path(args.out) / safe_filename(args.keyword)
    meta_path = out_dir / "metadata.json"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Save metadata
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"Found {len(results)} videos. Metadata -> {meta_path}")

    if args.dry_run:
        for i, v in enumerate(results, 1):
            print(f"[{i}] {v['provider']}: {v['title']} | {v['width']}x{v['height']} | {v['permalink']}")
        return

    # Download
    for i, v in enumerate(results, 1):
        ext = ".mp4"
        filename = f"{i:03d}_{safe_filename(v['title'])}_{v['provider']}{ext}"
        dest = out_dir / filename
        print(f"Downloading {i}/{len(results)} -> {dest.name}")
        download_file(v["url"], dest)

    print("Done. Check:", out_dir)

if __name__ == "__main__":
    main()
