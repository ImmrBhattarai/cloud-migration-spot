from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Form
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

from common.config import LOCAL_INPUT_DIR
from .storage import create_job_for_local_file, get_job

app = FastAPI(title="Cloud Migration Demo API")

# Static files and templates
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# ---------- API endpoints ----------

@app.post("/jobs")
async def create_job(file: UploadFile = File(...)):
    dest = LOCAL_INPUT_DIR / file.filename
    content = await file.read()
    dest.write_bytes(content)

    job = create_job_for_local_file(file.filename)
    return {"job_id": job.id, "status": job.status}

@app.get("/jobs/{job_id}")
def read_job(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

@app.get("/jobs/{job_id}/result")
def get_result(job_id: str):
    job = get_job(job_id)
    if not job or not job.result_path:
        raise HTTPException(status_code=404, detail="Result not available")
    path = Path(job.result_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Result file missing")
    return FileResponse(path)

# ---------- Web UI endpoints ----------

@app.get("/", response_class=HTMLResponse)
def home(request: Request, job_id: str | None = None):
    """
    Landing page: 
    - Show upload form
    - If job_id is provided, show job status and result if available
    """
    job = get_job(job_id) if job_id else None
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "job": job,
            "job_id": job_id,
        },
    )

@app.post("/upload", response_class=HTMLResponse)
async def upload_image(request: Request, file: UploadFile = File(...)):
    if not file:
        raise HTTPException(status_code=400, detail="File required")

    dest = LOCAL_INPUT_DIR / file.filename
    content = await file.read()
    dest.write_bytes(content)

    job = create_job_for_local_file(file.filename)
    # Redirect to home with job_id so user can see status
    return RedirectResponse(url=f"/?job_id={job.id}", status_code=303)
