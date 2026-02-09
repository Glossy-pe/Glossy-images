from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import FileResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import os
import uuid
import shutil
import aiofiles
from dotenv import load_dotenv

load_dotenv()
from database.database import engine, SessionLocal, Base
from models.image import Image

# -------------------------
# Inicialización
# -------------------------
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Image Management API - CRUD Extendido")

# 🌍 CORS — SIEMPRE primero
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200", "https://glossy-web.mimarca.pe"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 🗜️ Compresión GZip
app.add_middleware(
    GZipMiddleware,
    minimum_size=1000,
)

BASE_UPLOAD_DIR = "images"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "jfif", "avif"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

os.makedirs(BASE_UPLOAD_DIR, exist_ok=True)


# -------------------------
# Dependencia DB
# -------------------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# -------------------------
# Validadores Auxiliares
# -------------------------
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
        image = Image(
            filename=filename,
            category=safe_category
        )
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
        raise HTTPException(
            status_code=400,
            detail="No se enviaron campos para actualizar"
        )

    old_filename = image.filename
    old_category = image.category

    # 🔹 Resolver filename final
    if filename:
        _, old_ext = os.path.splitext(old_filename)
        new_base, _ = os.path.splitext(filename)
        final_filename = f"{new_base}{old_ext}"
    else:
        final_filename = old_filename

    # 🔹 Resolver categoría final
    final_category = category if category else old_category

    old_path = os.path.join(BASE_UPLOAD_DIR, old_category, old_filename)
    new_path = os.path.join(BASE_UPLOAD_DIR, final_category, final_filename)

    try:
        os.makedirs(
            os.path.join(BASE_UPLOAD_DIR, final_category),
            exist_ok=True
        )

        # 🔹 Reemplazar contenido del archivo
        if file:
            with open(new_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)

            # Si cambió nombre o categoría, borrar el viejo
            if new_path != old_path and os.path.exists(old_path):
                os.remove(old_path)

        # 🔹 Solo mover / renombrar
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
def get_image_by_id(
    image_id: int,
    db: Session = Depends(get_db)
):
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
def get_image_file(
    image_id: int,
    db: Session = Depends(get_db)
):
    image = db.query(Image).filter(Image.id == image_id).first()
    if not image:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")

    path = os.path.join(
        BASE_UPLOAD_DIR,
        image.category,
        image.filename
    )

    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=image.filename
    )

@app.delete("/images/{image_id}", status_code=200)
def delete_image_by_id(
    image_id: int,
    db: Session = Depends(get_db)
):
    image = db.query(Image).filter(Image.id == image_id).first()
    if not image:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")

    path = os.path.join(
        BASE_UPLOAD_DIR,
        image.category,
        image.filename
    )

    try:
        # 🔹 Borrar archivo físico (si existe)
        if os.path.exists(path):
            os.remove(path)

        # 🔹 Borrar DB
        db.delete(image)
        db.commit()

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Error eliminando imagen"
        )

    return {"detail": "Imagen eliminada"}
    safe_cat = validate_category(category)
    cat_dir = os.path.join(BASE_UPLOAD_DIR, safe_cat)
    if os.path.exists(cat_dir): shutil.rmtree(cat_dir)
    db.query(Image).filter(Image.category == safe_cat).delete()
    db.commit()
    return {"detail": f"Categoría {safe_cat} eliminada"}