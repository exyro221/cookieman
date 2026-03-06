import os
import uuid
import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import numpy as np
import cv2

from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException
from fastapi.responses import JSONResponse

API_KEY = os.getenv("API_KEY", "")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "20"))

GEN_DIR = Path("/generated")
GEN_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Cookie Cutter Generator API", version="0.2.0")


def require_key(x_api_key: Optional[str]):
    if not API_KEY or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorised")


def run(cmd, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}"
        )
    return p.stdout


def write_status(job_dir: Path, status: str, error: Optional[str] = None, extra: dict = None):
    payload = {"status": status}
    if error:
        payload["error"] = error
    if extra:
        payload.update(extra)
    (job_dir / "status.json").write_text(json.dumps(payload, indent=2))


def load_status(job_dir: Path):
    p = job_dir / "status.json"
    if not p.exists():
        return {"status": "unknown"}
    return json.loads(p.read_text())


def safe_ext(filename: str) -> str:
    return Path(filename).suffix.lower()


def preprocess_image_to_bw(
    img_path: Path,
    out_bw_path: Path,
    remove_background: bool,
    invert: bool,
    threshold: float,
    despeckle: int,
    blur: int,
):
    """
    Produces a clean black/white image suitable for potrace:
    - optional foreground extraction (GrabCut)
    - grayscale
    - blur (smoothing edges)
    - threshold (user controlled)
    - despeckle (morphology open) to remove tiny blobs
    """
    img = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("Could not read image file")

    h, w = img.shape[:2]

    # Optional background removal: GrabCut with a centered rectangle.
    # This works well for “object in the middle” uploads (common for logos/outlines).
    if remove_background:
        mask = np.zeros((h, w), np.uint8)
        bgdModel = np.zeros((1, 65), np.float64)
        fgdModel = np.zeros((1, 65), np.float64)

        # rectangle inset by 5% each edge
        rect = (int(w * 0.05), int(h * 0.05), int(w * 0.90), int(h * 0.90))
        cv2.grabCut(img, mask, rect, bgdModel, fgdModel, 5, cv2.GC_INIT_WITH_RECT)

        # probable/definite foreground => 1, else 0
        fg = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype("uint8")

        # put FG on white background
        white_bg = np.full_like(img, 255)
        img = np.where(fg[:, :, None] == 255, img, white_bg)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Blur: make jagged edges smoother before threshold
    if blur > 0:
        k = blur if blur % 2 == 1 else blur + 1
        gray = cv2.GaussianBlur(gray, (k, k), 0)

    # Threshold (0..1)
    t = int(max(0.01, min(0.99, threshold)) * 255)
    _, bw = cv2.threshold(gray, t, 255, cv2.THRESH_BINARY)

    # Invert if user wants the opposite
    if invert:
        bw = 255 - bw

    # Despeckle (remove small noise)
    if despeckle > 0:
        k = despeckle if despeckle % 2 == 1 else despeckle + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kernel)

    cv2.imwrite(str(out_bw_path), bw)


def bw_to_svg_potrace(bw_path: Path, out_svg: Path):
    """
    potrace works best with PGM/PPM input, so convert BW png -> pgm then potrace.
    """
    tmp_pgm = bw_path.with_suffix(".pgm")
    run(["convert", str(bw_path), str(tmp_pgm)])
    run(["potrace", str(tmp_pgm), "-s", "-o", str(out_svg)])
    tmp_pgm.unlink(missing_ok=True)

def papooch_svg_to_stl(svg_path: Path, job_dir: Path) -> Path:
    """
    Run Papooch with the correct arguments:
    ./run.sh <input_svg> <output_dir>

    Some versions also accept a third generator arg, but this should work
    with the default stamp.scad shown in your error output.
    """
    repo = Path("/opt/cookie-cutter-generator")
    output_dir = job_dir / "papooch_out"
    output_dir.mkdir(parents=True, exist_ok=True)

    run([
        "bash",
        "-lc",
        f"cd {repo} && chmod +x ./run.sh && ./run.sh '{svg_path}' '{output_dir}'"
    ])

    stls = list(output_dir.rglob("*.stl"))
    if not stls:
        raise RuntimeError("No STL generated in Papooch output directory.")

    out_stl = job_dir / "output.stl"
    shutil.copy2(stls[0], out_stl)
    return out_stl

@app.post("/generate")
async def generate(
    file: UploadFile = File(...),
    mode: str = Form("auto"),  # auto|image|svg
    remove_background: bool = Form(True),
    invert: bool = Form(False),
    threshold: float = Form(0.55),
    despeckle: int = Form(3),   # 0=off, else kernel size-ish
    blur: int = Form(3),        # 0=off
    x_api_key: Optional[str] = Header(default=None),
):
    require_key(x_api_key)

    data = await file.read()
    size_mb = len(data) / (1024 * 1024)
    if size_mb > MAX_UPLOAD_MB:
        raise HTTPException(413, f"File too large ({size_mb:.1f}MB). Limit {MAX_UPLOAD_MB}MB.")

    job_id = uuid.uuid4().hex[:12]
    job_dir = GEN_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    write_status(job_dir, "running")

    filename = file.filename or "upload"
    in_path = job_dir / filename
    in_path.write_bytes(data)

    ext = safe_ext(filename)
    is_svg = ext == ".svg"
    is_img = ext in [".png", ".jpg", ".jpeg", ".webp"]

    if mode == "auto":
        mode = "svg" if is_svg else "image"

    svg_path = job_dir / "input.svg"

    try:
        if mode == "svg":
            if not is_svg:
                raise HTTPException(400, "SVG mode requires .svg file.")
            shutil.copy2(in_path, svg_path)
        elif mode == "image":
            if not is_img:
                raise HTTPException(400, "Image mode requires PNG/JPG/WEBP.")
            bw_path = job_dir / "bw.png"
            preprocess_image_to_bw(
                in_path,
                bw_path,
                remove_background=remove_background,
                invert=invert,
                threshold=threshold,
                despeckle=despeckle,
                blur=blur,
            )
            bw_to_svg_potrace(bw_path, svg_path)
        else:
            raise HTTPException(400, "Invalid mode. Use auto|image|svg")

        # Generate STL and keep server-side (not returned to the user)
        papooch_svg_to_stl(svg_path, job_dir)

        write_status(job_dir, "done", extra={"svg_ready": True, "stl_ready": True})
        return JSONResponse({
            "job_id": job_id,
            "status": "done",
            # Return only a preview path (still requires API key to fetch if you implement it)
            # Your Lovable backend can fetch it if you later add a protected /files endpoint.
        })
    except HTTPException:
        write_status(job_dir, "error", error="Bad request")
        raise
    except Exception as e:
        write_status(job_dir, "error", error=str(e))
        return JSONResponse({"job_id": job_id, "status": "error", "error": str(e)}, status_code=500)


@app.get("/jobs/{job_id}")
def jobs(job_id: str, x_api_key: Optional[str] = Header(default=None)):
    require_key(x_api_key)
    job_dir = GEN_DIR / job_id
    if not job_dir.exists():
        raise HTTPException(404, "Job not found")

    st = load_status(job_dir)
    # Provide minimal safe status info (no STL URLs)
    return {
        "job_id": job_id,
        "status": st.get("status", "unknown"),
        "error": st.get("error"),
        "svg_ready": bool(st.get("svg_ready")),
        "stl_ready": bool(st.get("stl_ready")),
    }
