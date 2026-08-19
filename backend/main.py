import os, uuid, time, json, sqlite3, subprocess, threading, re
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()
BASE = Path(__file__).resolve().parent
FRONT = BASE.parent / "frontend"
WORK = BASE / "work"
OUT = BASE / "output"
WORK.mkdir(exist_ok=True); OUT.mkdir(exist_ok=True)
DB = BASE / "jobs.sqlite3"

app = FastAPI(title="ConfusedAI")


def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,status TEXT,message TEXT,error TEXT,clips TEXT,created INTEGER)")
    c.commit()
    return c

class Req(BaseModel):
    url: str


def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode:
        raise RuntimeError(p.stderr[-3500:])
    return p.stdout


def render_clips(job_id, src, source_label):
    c = db()
    probe = float(run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(src)]).strip())
    if probe <= 0: raise RuntimeError("Could not read video duration.")
    length = min(35.0, max(20.0, probe / 5))
    starts = [0.0, probe*.2, probe*.4, probe*.6, max(0.0, probe-length)] if probe > 60 else [0.0]
    windows=[]; seen=set()
    for s in starts:
        s=min(s,max(0.0,probe-length)); key=round(s,1)
        if key in seen: continue
        seen.add(key); windows.append((s,min(probe,s+length)))
    clips=[]
    for i,(s,e) in enumerate(windows,1):
        c.execute("UPDATE jobs SET message=? WHERE id=?", (f"Rendering clip {i}/{len(windows)}…", job_id)); c.commit()
        out=OUT/f"{job_id}_{i}.mp4"
        vf="scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2"
        run(["ffmpeg","-y","-ss",str(s),"-i",str(src),"-t",str(e-s),"-vf",vf,"-c:v","libx264","-preset","veryfast","-crf","23","-c:a","aac","-movflags","+faststart",str(out)])
        clips.append({"title":f"Viral Candidate #{i}","duration":e-s,"start":s,"score":max(70,98-i*5),"source":source_label,"resolution":"1080×1920","url":f"/files/{out.name}"})
    return clips


def process_url(job_id, url):
    c = db()
    try:
        work = WORK / job_id; work.mkdir(exist_ok=True)
        c.execute("UPDATE jobs SET status=?,message=? WHERE id=?", ("processing", "Fetching video…", job_id)); c.commit()
        # Explicitly tell yt-dlp where Deno is. This fixes the EJS runtime detection
        # on the Railway container. It does not bypass YouTube rate limits or bot checks.
        cmd=["yt-dlp","--no-playlist","--js-runtimes","deno:/usr/local/bin/deno","-f","bv*+ba/b","--merge-output-format","mp4","-o",str(work / "source.%(ext)s"),url]
        run(cmd)
        files = list(work.glob("source.*"))
        if not files: raise RuntimeError("No video was returned by the source.")
        clips=render_clips(job_id, files[0], url)
        c.execute("UPDATE jobs SET status=?,message=?,clips=? WHERE id=?", ("completed",f"{len(clips)} playable clips ready",json.dumps(clips),job_id)); c.commit()
    except Exception as e:
        c.execute("UPDATE jobs SET status=?,message=?,error=? WHERE id=?", ("error","Processing failed",str(e),job_id)); c.commit()


def process_file(job_id, src, source_label):
    c=db()
    try:
        c.execute("UPDATE jobs SET status=?,message=? WHERE id=?", ("processing","Analysing uploaded video…",job_id)); c.commit()
        clips=render_clips(job_id, src, source_label)
        c.execute("UPDATE jobs SET status=?,message=?,clips=? WHERE id=?", ("completed",f"{len(clips)} playable clips ready",json.dumps(clips),job_id)); c.commit()
    except Exception as e:
        c.execute("UPDATE jobs SET status=?,message=?,error=? WHERE id=?", ("error","Processing failed",str(e),job_id)); c.commit()

@app.post("/api/analyze")
def analyze(r: Req):
    if not r.url.startswith(("http://","https://")):
        raise HTTPException(400,"Please enter a valid http/https video URL.")
    job=uuid.uuid4().hex[:12]; c=db()
    c.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?)",(job,"queued","Queued…","","[]",int(time.time()))); c.commit()
    threading.Thread(target=process_url,args=(job,r.url),daemon=True).start()
    return {"job_id":job}

@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    name=(file.filename or "video.mp4").replace("/","_").replace("\\","_")
    if not name.lower().endswith((".mp4",".mov",".mkv",".webm",".m4v",".avi")):
        raise HTTPException(400,"Please upload a supported video file.")
    job=uuid.uuid4().hex[:12]; c=db(); c.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?)",(job,"queued","Queued…","","[]",int(time.time()))); c.commit()
    work=WORK/job; work.mkdir(exist_ok=True); src=work/name
    with src.open("wb") as f:
        while chunk:=await file.read(1024*1024): f.write(chunk)
    threading.Thread(target=process_file,args=(job,src,name),daemon=True).start()
    return {"job_id":job}

@app.get("/api/job/{job_id}")
def get_job(job_id):
    c=db(); row=c.execute("SELECT * FROM jobs WHERE id=?",(job_id,)).fetchone()
    if not row: raise HTTPException(404,"Job not found")
    d=dict(row); d["clips"]=json.loads(d["clips"] or "[]"); return d

app.mount("/files", StaticFiles(directory=str(OUT)), name="files")
app.mount("/", StaticFiles(directory=str(FRONT), html=True), name="frontend")
