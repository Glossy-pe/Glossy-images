from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Request
from fastapi.responses import FileResponse, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import os
import uuid
import shutil
import hashlib
import aiofiles
from dotenv import load_dotenv

load_dotenv()
from database.database import engine, SessionLocal, Base
from models.image import Image

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Image Management API - CRUD Extendido")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200", "https://glossy-web.mimarca.pe", "https://glossy.mimarca.pe"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(
    GZipMiddleware,
    minimum_size=1000,
)

BASE_UPLOAD_DIR = "images"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "jfif", "avif"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

# Mapa de extensión → MIME type correcto
MIME_TYPES = {
    "jpg":  "image/jpeg",
    "jpeg": "image/jpeg",
    "png":  "image/png",
    "webp": "image/webp",
    "avif": "image/avif",
    "jfif": "image/jpeg",
}

os.makedirs(BASE_UPLOAD_DIR, exist_ok=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def validate_file_extension(filename: str) -> str:
    _, ext = os.path.splitext(filename)
    ext = ext.lower().lstrip(".")
    if not ext:
        raise HTTPException(status_code=400, detail="Archivo sin extensión")
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Extensión no permitida")
    return ext


def validate_category(category: str) -> str:
    safe_category = os.path.normpath(category)
    if safe_category.startswith("..") or "/" in safe_category or "\\" in safe_category:
        raise HTTPException(status_code=400, detail="Categoría inválida")
    return safe_category


def build_etag(path: str) -> str:
    """ETag basado en tamaño + fecha de modificación (sin leer el archivo)."""
    stat = os.stat(path)
    raw = f"{stat.st_size}-{stat.st_mtime}"
    return hashlib.md5(raw.encode()).hexdigest()


def serve_image(path: str, filename: str, request: Request) -> Response:
    """
    Sirve un archivo de imagen con headers de caché correctos.
    - Cache-Control: 1 año para assets inmutables (el nombre incluye UUID).
    - ETag + Last-Modified para validación condicional.
    - Responde 304 Not Modified si el cliente ya tiene la versión actual.
    """
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

    stat = os.stat(path)
    etag = build_etag(path)
    last_modified = stat.st_mtime

    # Validación condicional — el cliente ya tiene la imagen
    if_none_match = request.headers.get("if-none-match")
    if if_none_match and if_none_match == f'"{etag}"':
        return Response(status_code=304)

    _, ext = os.path.splitext(filename)
    ext_clean = ext.lower().lstrip(".")
    media_type = MIME_TYPES.get(ext_clean, "application/octet-stream")

    # 1 año de caché — válido porque el filename incluye UUID (cambia si se reemplaza)
    response = FileResponse(
        path,
        media_type=media_type,
        filename=filename,
    )
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    response.headers["ETag"] = f'"{etag}"'
    response.headers["Last-Modified"] = str(int(last_modified))
    response.headers["Vary"] = "Accept-Encoding"

    return response


# -------------------------
# Endpoints (sin cambios en firma)
# -------------------------

@app.post("/images", status_code=201)
async def upload_image(
    category: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    safe_category = validate_category(category)
    ext = validate_file_extension(file.filename)

    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)

    if file_size > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="Archivo muy grande")

    filename = f"{uuid.uuid4()}.{ext}"
    category_dir = os.path.join(BASE_UPLOAD_DIR, safe_category)
    os.makedirs(category_dir, exist_ok=True)
    file_path = os.path.join(category_dir, filename)

    try:
        async with aiofiles.open(file_path, "wb") as f:
            while chunk := await file.read(1024 * 64):
                await f.write(chunk)
    except Exception as e:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(status_code=500, detail=str(e))

    try:
        image = Image(filename=filename, category=safe_category)
        db.add(image)
        db.commit()
        db.refresh(image)
    except Exception as e:
        if os.path.exists(file_path):
            os.remove(file_path)
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "id": image.id,
        "filename": image.filename,
        "category": image.category,
        "url": f"/images/{image.id}"
    }


@app.patch("/images/{image_id}")
def update_image(
    image_id: int,
    file: UploadFile | None = File(None),
    filename: str | None = Form(None),
    category: str | None = Form(None),
    db: Session = Depends(get_db)
):
    image = db.query(Image).filter(Image.id == image_id).first()
    if not image:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")

    if not any([file, filename, category]):
        raise HTTPException(status_code=400, detail="No se enviaron campos para actualizar")

    old_filename = image.filename
    old_category = image.category

    if filename:
        _, old_ext = os.path.splitext(old_filename)
        new_base, _ = os.path.splitext(filename)
        final_filename = f"{new_base}{old_ext}"
    else:
        final_filename = old_filename

    final_category = category if category else old_category

    old_path = os.path.join(BASE_UPLOAD_DIR, old_category, old_filename)
    new_path = os.path.join(BASE_UPLOAD_DIR, final_category, final_filename)

    try:
        os.makedirs(os.path.join(BASE_UPLOAD_DIR, final_category), exist_ok=True)

        if file:
            with open(new_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            if new_path != old_path and os.path.exists(old_path):
                os.remove(old_path)
        elif new_path != old_path and os.path.exists(old_path):
            shutil.move(old_path, new_path)

        image.filename = final_filename
        image.category = final_category
        db.commit()

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "detail": "Imagen actualizada",
        "id": image.id,
        "filename": image.filename,
        "category": image.category,
        "file_replaced": bool(file)
    }


@app.get("/images")
def get_all_images(db: Session = Depends(get_db)):
    images = db.query(Image).all()
    return [
        {
            "id": i.id,
            "filename": i.filename,
            "category": i.category,
            "created_at": i.created_at,
            "url": f"/images/{i.id}"
        }
        for i in images
    ]


@app.get("/images/{image_id}")
def get_image_by_id(image_id: int, db: Session = Depends(get_db)):
    image = db.query(Image).filter(Image.id == image_id).first()
    if not image:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")
    return {
        "id": image.id,
        "filename": image.filename,
        "category": image.category,
        "created_at": image.created_at,
        "file_url": f"/images/{image.id}/file"
    }


@app.get("/images/{image_id}/file")
def get_image_file(image_id: int, request: Request, db: Session = Depends(get_db)):
    image = db.query(Image).filter(Image.id == image_id).first()
    if not image:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")

    path = os.path.join(BASE_UPLOAD_DIR, image.category, image.filename)
    return serve_image(path, image.filename, request)


@app.delete("/images/{image_id}", status_code=200)
def delete_image_by_id(image_id: int, db: Session = Depends(get_db)):
    image = db.query(Image).filter(Image.id == image_id).first()
    if not image:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")

    path = os.path.join(BASE_UPLOAD_DIR, image.category, image.filename)

    try:
        if os.path.exists(path):
            os.remove(path)
        db.delete(image)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Error eliminando imagen")

    return {"detail": "Imagen eliminada"}