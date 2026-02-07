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


# -------------------------
# CREATE: Upload de imagen
# -------------------------
@app.post("/upload")
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
        if os.path.exists(file_path): os.remove(file_path)
        raise HTTPException(status_code=500, detail=str(e))

    try:
        image = Image(filename=filename, category=safe_category)
        db.add(image)
        db.commit()
        db.refresh(image)
    except Exception as e:
        if os.path.exists(file_path): os.remove(file_path)
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    return {"id": image.id, "image_url": f"/images/{filename}"}


# -------------------------
# UPDATE: Renombrar Categoría
# -------------------------
@app.patch("/categories/rename")
def rename_category(
    old_name: str = Form(...),
    new_name: str = Form(...),
    db: Session = Depends(get_db)
):
    """Cambia el nombre de una categoría (carpeta y registros)"""
    safe_old = validate_category(old_name)
    safe_new = validate_category(new_name)

    old_dir = os.path.join(BASE_UPLOAD_DIR, safe_old)
    new_dir = os.path.join(BASE_UPLOAD_DIR, safe_new)

    if not os.path.exists(old_dir):
        raise HTTPException(status_code=404, detail="Categoría origen no existe")
    if os.path.exists(new_dir):
        raise HTTPException(status_code=400, detail="La categoría destino ya existe")

    # 1. Renombrar carpeta
    try:
        os.rename(old_dir, new_dir)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error renombrando carpeta: {str(e)}")

    # 2. Actualizar DB
    try:
        db.query(Image).filter(Image.category == safe_old).update({"category": safe_new})
        db.commit()
    except Exception as e:
        os.rename(new_dir, old_dir)  # Revertir carpeta si falla DB
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error en DB: {str(e)}")

    return {"detail": f"Categoría '{safe_old}' renombrada a '{safe_new}'"}


# -------------------------
# UPDATE: Renombrar Archivo de Imagen
# -------------------------
@app.patch("/images/{image_id}/rename")
def rename_image_file(
    image_id: int,
    new_filename: str = Form(...),
    db: Session = Depends(get_db)
):
    """Cambia el nombre del archivo físico de una imagen específica"""
    image = db.query(Image).filter(Image.id == image_id).first()
    if not image:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")

    # Asegurar que mantenga la extensión original por seguridad
    _, old_ext = os.path.splitext(image.filename)
    new_base, new_ext = os.path.splitext(new_filename)
    
    # Si el usuario no envió extensión o envió una distinta, forzamos la original
    final_new_filename = f"{new_base}{old_ext}"

    old_path = os.path.join(BASE_UPLOAD_DIR, image.category, image.filename)
    new_path = os.path.join(BASE_UPLOAD_DIR, image.category, final_new_filename)

    if os.path.exists(new_path):
        raise HTTPException(status_code=400, detail="Ya existe un archivo con ese nombre")

    try:
        if os.path.exists(old_path):
            os.rename(old_path, new_path)
        
        image.filename = final_new_filename
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    return {"detail": "Archivo renombrado", "new_filename": final_new_filename}


# -------------------------
# UPDATE: Reemplazar contenido (Mantenimiento de nombre)
# -------------------------
@app.put("/images/{image_id}/replace")
async def replace_image_content(
    image_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """Reemplaza el archivo físico por uno nuevo manteniendo el registro y nombre"""
    image = db.query(Image).filter(Image.id == image_id).first()
    if not image:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")

    # Validar que la extensión sea compatible
    validate_file_extension(file.filename)

    file_path = os.path.join(BASE_UPLOAD_DIR, image.category, image.filename)

    try:
        # Sobrescribir el archivo existente
        async with aiofiles.open(file_path, "wb") as f:
            while chunk := await file.read(1024 * 64):
                await f.write(chunk)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reemplazando archivo: {str(e)}")

    return {"detail": "Contenido de imagen reemplazado", "filename": image.filename}


# -------------------------
# READ & DELETE (Existentes Optimizados)
# -------------------------
@app.get("/images")
def get_all_images(db: Session = Depends(get_db)):
    images = db.query(Image).all()
    return [{"id": i.id, "filename": i.filename, "category": i.category, "url": f"/images/{i.filename}", "created_at": i.created_at} for i in images]

@app.get("/images/{filename}")
def get_image_file(filename: str, db: Session = Depends(get_db)):
    image = db.query(Image).filter(Image.filename == filename).first()
    if not image: raise HTTPException(status_code=404)
    path = os.path.join(BASE_UPLOAD_DIR, image.category, filename)
    return FileResponse(path)

@app.delete("/images/{image_id}")
def delete_image_by_id(image_id: int, db: Session = Depends(get_db)):
    image = db.query(Image).filter(Image.id == image_id).first()
    if not image: raise HTTPException(status_code=404)
    
    path = os.path.join(BASE_UPLOAD_DIR, image.category, image.filename)
    if os.path.exists(path): os.remove(path)
    
    db.delete(image)
    db.commit()
    return {"detail": "Imagen eliminada"}

@app.delete("/images/category/{category}")
def delete_category(category: str, db: Session = Depends(get_db)):
    safe_cat = validate_category(category)
    cat_dir = os.path.join(BASE_UPLOAD_DIR, safe_cat)
    if os.path.exists(cat_dir): shutil.rmtree(cat_dir)
    db.query(Image).filter(Image.category == safe_cat).delete()
    db.commit()
    return {"detail": f"Categoría {safe_cat} eliminada"}