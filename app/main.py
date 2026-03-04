import os, uuid, shutil, subprocess
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException
from fastapi.responses import JSONResponse, FileResponse

API_KEY = os.getenv("API_KEY", "")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "20"))
GEN_DIR = Path("/generated")
GEN_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Cutter API", version="0.1")

def check_key(x_api_key: str | None):
    if not API_KEY or x_api_key != API_KEY:
        raise HTTPException(401, "Unauthorised")

def run(cmd, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{p.stderr}")
    return p.stdout

def image_to_svg(img_path: Path, out_svg: Path, threshold: float, invert: bool):
    # Simple: grayscale + threshold -> potrace
    tmp = img_path.with_suffix(".pgm")
    thr_pct = max(1, min(99, int(threshold * 100)))
    args = ["convert", str(img_path), "-alpha", "remove", "-background", "white", "-flatten", "-colorspace", "Gray"]
    if invert:
        args += ["-negate"]
    args += ["-threshold", f"{thr_pct}%", str(tmp)]
    run(args)
    run(["potrace", str(tmp), "-s", "-o", str(out_svg)])
    tmp.unlink(missing_ok=True)

def papooch_svg_to_stl(svg_path: Path, out_dir: Path):
    repo = Path("/opt/cookie-cutter-generator")
    job_name = out_dir.name

    # Papooch scripts vary; this is a generic approach:
    # - copy input svg into repo
    # - run their generator script
    # - copy first STL found out
    shutil.copy2(svg_path, repo / "source-vector-image.svg")

    # Try common script names (one will exist)
    for script in ["generate.sh", "run.sh", "run"]:
        if (repo / script).exists():
            run(["bash", "-lc", f"cd {repo} && chmod +x ./{script} && ./{script} {job_name}"], cwd=str(repo))
            break

    stls = list((repo / "generated").rglob("*.stl"))
    if not stls:
        raise RuntimeError("No STL generated. Check Papooch repo scripts/README and adjust wrapper.")
    out_stl = out_dir / "output.stl"
    shutil.copy2(stls[0], out_stl)
    return out_stl

@app.post("/generate")
async def generate(
    file: UploadFile = File(...),
    mode: str = Form("auto"),         # auto|image|svg
    invert: bool = Form(False),
    threshold: float = Form(0.55),
    x_api_key: str | None = Header(default=None),
):
    check_key(x_api_key)

    data = await file.read()
    size_mb = len(data)/(1024*1024)
    if size_mb > MAX_UPLOAD_MB:
        raise HTTPException(413, f"File too large ({size_mb:.1f}MB). Limit {MAX_UPLOAD_MB}MB.")

    job_id = uuid.uuid4().hex[:12]
    job_dir = GEN_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    in_path = job_dir / (file.filename or "upload")
    in_path.write_bytes(data)

    ext = in_path.suffix.lower()
    is_svg = ext == ".svg"
    is_img = ext in [".png", ".jpg", ".jpeg", ".webp"]

    if mode == "auto":
        mode = "svg" if is_svg else "image"

    svg_path = job_dir / "input.svg"

    try:
        if mode == "svg":
            if not is_svg:
                raise HTTPException(400, "SVG mode requires .svg")
            shutil.copy2(in_path, svg_path)
        else:
            if not is_img:
                raise HTTPException(400, "Image mode requires PNG/JPG/WEBP")
            image_to_svg(in_path, svg_path, threshold=threshold, invert=invert)

        # Generate STL (stored internally — you can remove files endpoint if you want)
        papooch_svg_to_stl(svg_path, job_dir)

        return JSONResponse({
            "job_id": job_id,
            "status": "done",
            "svg_preview_path": f"/files/{job_id}/input.svg"
        })
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse({"job_id": job_id, "status": "error", "error": str(e)}, status_code=500)

@app.get("/jobs/{job_id}")
def job(job_id: str, x_api_key: str | None = Header(default=None)):
    check_key(x_api_key)
    job_dir = GEN_DIR / job_id
    if not job_dir.exists():
        raise HTTPException(404, "Not found")
    stl = job_dir / "output.stl"
    svg = job_dir / "input.svg"
    status = "done" if stl.exists() else "running"
    return {
        "job_id": job_id,
        "status": status,
        "svg_preview_path": f"/files/{job_id}/input.svg" if svg.exists() else None
    }

@app.get("/files/{job_id}/{filename}")
def files(job_id: str, filename: str, x_api_key: str | None = Header(default=None)):
    check_key(x_api_key)
    path = GEN_DIR / job_id / filename
    if not path.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(str(path))
