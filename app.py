"""登記情報解析 Web アプリケーション"""

import os
import sys
import io
import json
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from parser import parse_touki_pdf

app = FastAPI(title="登記情報パーサー")

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/parse")
async def parse_pdf(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        return JSONResponse({"error": "PDFファイルのみ対応しています"}, status_code=400)

    # 一時ファイルに保存して解析
    suffix = ".pdf"
    tmp_path = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    try:
        contents = await file.read()
        tmp_path.write_bytes(contents)
        result = parse_touki_pdf(str(tmp_path))
        result["ファイル名"] = file.filename
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": f"解析エラー: {str(e)}"}, status_code=500)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
