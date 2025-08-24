# video_fetcher.py
"""
Công cụ tìm và tải video stock theo từ khóa bằng API chính thức.
Nguồn hỗ trợ: Pexels, Pixabay.
Cách chạy ví dụ:
    python video_fetcher.py "nước giải khát" --limit 10 --out videos --min_width 720 --provider pexels pixabay

Yêu cầu:
    - Có API key của Pexels và/hoặc Pixabay (lưu trong .env hoặc biến môi trường)
    - Cài requests, python-dotenv (pip install requests python-dotenv)

Lưu ý:
    - Không hỗ trợ Pinterest (vi phạm điều khoản).
    - Phải kiểm tra giấy phép từng video trước khi sử dụng lại.
"""

import os
import sys
import json
import time
import argparse
from pathlib import Path
from typing import Dict, Iterable, List, Optional
import requests

# --- Tự động nạp file .env nếu có ---
try:
    from dotenv import load_dotenv
    load_dotenv()  # sẽ nạp file .env trong cùng thư mục dự án
except ImportError:
    pass

# ======================
# HÀM HỖ TRỢ CHUNG
# ======================

def safe_filename(name: str) -> str:
    """
    Chuẩn hóa chuỗi để làm tên file an toàn:
    - Giữ lại chữ cái, số và một số ký tự đặc biệt (-_.()[] {})
    - Các ký tự khác thay bằng '_'
    - Xóa ký tự '_' dư thừa ở đầu/cuối
    """
    keep = "-_.()[] {}"
    return ''.join(c if c.isalnum() or c in keep else '_' for c in name).strip('_')

def shorten_name(name: str, max_len: int = 50) -> str:
    """
    Rút gọn tên file nếu quá dài (Windows giới hạn ~260 ký tự đường dẫn).
    Giữ lại tối đa max_len ký tự sau khi safe_filename.
    """
    safe = safe_filename(name)
    if len(safe) > max_len:
        safe = safe[:max_len]
    return safe

def download_file(url: str, dest: Path, headers: Optional[Dict[str, str]] = None, retries: int = 3) -> None:
    """
    Tải file từ URL về đích dest (Path).
    - Có retry tối đa 3 lần nếu lỗi mạng.
    - Tải tạm vào file .part rồi đổi tên để tránh file hỏng khi tải lỗi.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)  # tạo thư mục đích nếu chưa có
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
        time.sleep(1.5 * attempt)  # chờ lâu hơn mỗi lần retry
    raise RuntimeError(f"Không tải được {url} -> HTTP {r.status_code}")

# ======================
# PEXELS
# ======================

def pexels_search(keyword: str, limit: int, min_width: int = 0) -> List[Dict]:
    """
    Tìm video trên Pexels theo từ khóa.
    - keyword: từ khóa tìm kiếm
    - limit: số video tối đa cần lấy
    - min_width: độ rộng tối thiểu của video (lọc chất lượng)
    Trả về: list dict chứa metadata video.
    """
    api_key = os.getenv("PEXELS_API_KEY")
    if not api_key:
        return []  # không có key thì bỏ qua
    url = "https://api.pexels.com/videos/search"
    headers = {"Authorization": api_key}
    out = []
    page = 1
    per_page = min(80, max(1, limit))  # giới hạn per_page của Pexels

    while len(out) < limit:
        params = {"query": keyword, "per_page": per_page, "page": page}
        r = requests.get(url, headers=headers, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        for v in data.get("videos", []):
            files = sorted(v.get("video_files", []), key=lambda x: x.get("width") or 0, reverse=True)
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

# ======================
# PIXABAY
# ======================

def pixabay_search(keyword: str, limit: int, min_width: int = 0) -> List[Dict]:
    """
    Tìm video trên Pixabay theo từ khóa.
    - keyword: từ khóa tìm kiếm
    - limit: số video tối đa cần lấy
    - min_width: độ rộng tối thiểu của video
    Trả về: list dict chứa metadata video.
    """
    api_key = os.getenv("PIXABAY_API_KEY")
    if not api_key:
        return []
    url = "https://pixabay.com/api/videos/"
    out = []
    page = 1
    per_page = min(200, max(3, limit))  # Pixabay cho phép per_page tới 200

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
            videos = h.get("videos") or {}
            # Lấy danh sách các phiên bản (SD, HD, FullHD...)
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

# ======================
# TRÌNH ĐIỀU PHỐI
# ======================

def fetch(keyword: str, limit: int, providers: Iterable[str], min_width: int) -> List[Dict]:
    """
    Gọi lần lượt các nguồn (pexels/pixabay) để tìm video.
    Ghép kết quả và loại bỏ trùng theo URL.
    """
    results: List[Dict] = []
    pv = [p.lower() for p in providers]
    if "pexels" in pv:
        results.extend(pexels_search(keyword, limit, min_width))
    if "pixabay" in pv:
        results.extend(pixabay_search(keyword, limit, min_width))

    # Loại bỏ trùng lặp URL
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

# ======================
# MAIN
# ======================

def main():
    parser = argparse.ArgumentParser(description="Tải video stock theo từ khóa.")
    parser.add_argument("keyword", type=str, help="Từ khóa tìm kiếm, ví dụ: 'nước giải khát'")
    parser.add_argument("--limit", type=int, default=10, help="Số video tối đa cần tải")
    parser.add_argument("--out", type=str, default="videos", help="Thư mục lưu video")
    parser.add_argument("--min_width", type=int, default=720, help="Độ rộng tối thiểu")
    parser.add_argument("--provider", nargs="+", default=["pexels", "pixabay"], help="Nguồn dữ liệu: pexels, pixabay")
    parser.add_argument("--dry_run", action="store_true", help="Chỉ liệt kê kết quả, không tải")
    args = parser.parse_args()

    # Kiểm tra có API key không
    if not (os.getenv("PEXELS_API_KEY") or os.getenv("PIXABAY_API_KEY")):
        print("Chưa có API key. Vui lòng thêm vào .env hoặc biến môi trường.", file=sys.stderr)
        sys.exit(1)

    # Thực hiện tìm kiếm
    results = fetch(args.keyword, args.limit, args.provider, args.min_width)

    if not results:
        print("Không có kết quả.")
        sys.exit(1)

    out_dir = Path(args.out) / safe_filename(args.keyword)
    meta_path = out_dir / "metadata.json"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Lưu metadata vào file JSON
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"Tìm thấy {len(results)} video. Metadata -> {meta_path}")

    # Nếu chỉ test thì in ra màn hình, không tải
    if args.dry_run:
        for i, v in enumerate(results, 1):
            print(f"[{i}] {v['provider']}: {v['title']} | {v['width']}x{v['height']} | {v['permalink']}")
        return

    # Tải từng video về
    for i, v in enumerate(results, 1):
        ext = ".mp4"
        filename = f"{i:03d}_{shorten_name(v['title'])}_{v['provider']}{ext}"
        dest = out_dir / filename
        print(f"Đang tải {i}/{len(results)} -> {dest.name}")
        download_file(v["url"], dest)

    print("Hoàn tất. Kiểm tra thư mục:", out_dir)

if __name__ == "__main__":
    main()
