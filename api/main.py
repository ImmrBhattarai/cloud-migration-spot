from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

from common.config import LOCAL_INPUT_DIR, STORAGE_BACKEND
from common.storage import create_job_from_bytes, get_job, download_result_to_tempfile

app = FastAPI(title="Cloud Migration Demo API")

# Static files and templates
# check_dir=False prevents crashes if the directory is present but empty (or even missing).
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent / "static"), check_dir=False),
    name="static",
)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# ---------- API endpoints ----------

@app.post("/jobs")
async def create_job(file: UploadFile = File(...)):
    content = await file.read()

    from os import getenv
    gcs_bucket = getenv("GCS_BUCKET", None)

    job = create_job_from_bytes(file.filename, content, gcs_bucket=gcs_bucket)
    return {"job_id": job.id, "status": job.status}

@app.get("/jobs/{job_id}")
def read_job(job_id: str):
    from os import getenv
    gcs_bucket = getenv("GCS_BUCKET", None)
    job = get_job(job_id, gcs_bucket=gcs_bucket) if STORAGE_BACKEND == "gcp" else get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

@app.get("/jobs/{job_id}/result")
def get_result(job_id: str):
    from os import getenv
    gcs_bucket = getenv("GCS_BUCKET", None)
    job = get_job(job_id, gcs_bucket=gcs_bucket) if STORAGE_BACKEND == "gcp" else get_job(job_id)
    if not job or not job.result_path:
        raise HTTPException(status_code=404, detail="Result not available")
    path = download_result_to_tempfile(job, gcs_bucket=gcs_bucket) if STORAGE_BACKEND == "gcp" else Path(job.result_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Result file missing")
    return FileResponse(path)

# ---------- Web UI endpoints ----------
@app.get("/", response_class=HTMLResponse)
def home(request: Request, job_id: str | None = None):
    from os import getenv
    gcs_bucket = getenv("GCS_BUCKET", None)
    job = None
    if job_id:
        job = get_job(job_id, gcs_bucket=gcs_bucket) if STORAGE_BACKEND == "gcp" else get_job(job_id)
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "job": job, "job_id": job_id},
    )

@app.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "job": None, "job_id": None},
    )

@app.post("/upload", response_class=HTMLResponse)
async def upload_image(request: Request, file: UploadFile = File(...)):
    if not file:
        raise HTTPException(status_code=400, detail="File required")

    content = await file.read()

    from os import getenv
    gcs_bucket = getenv("GCS_BUCKET", None)

    job = create_job_from_bytes(file.filename, content, gcs_bucket=gcs_bucket)
    return RedirectResponse(url=f"/?job_id={job.id}", status_code=303)
