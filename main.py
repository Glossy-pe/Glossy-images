from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import FileResponse
from fastapi.middleware.gzip import GZIPMiddleware
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

app = FastAPI()

# ✅ Middleware de compresión
app.add_middleware(GZIPMiddleware, minimum_size=1000)

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
# Validar extensión
# -------------------------
def validate_file_extension(filename: str) -> str:
    """Retorna la extensión validada o lanza excepción"""
    _, ext = os.path.splitext(filename)
    ext = ext.lower().lstrip(".")
    
    if not ext:
        raise HTTPException(status_code=400, detail="Archivo sin extensión")
    
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Extensión no permitida")
    
    return ext

# -------------------------
# Validar categoría
# -------------------------
def validate_category(category: str) -> str:
    """Normaliza y valida la categoría"""
    safe_category = os.path.normpath(category)
    if safe_category.startswith("..") or "/" in safe_category or "\\" in safe_category:
        raise HTTPException(status_code=400, detail="Categoría inválida")
    return safe_category

# -------------------------
# Upload de imagen (OPTIMIZADO)
# -------------------------
@app.post("/upload")
async def upload_image(
    category: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    # 🔐 Validaciones
    safe_category = validate_category(category)
    ext = validate_file_extension(file.filename)
    
    # ✅ Validar tamaño del archivo
    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)
    
    if file_size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413, 
            detail=f"Archivo muy grande. Máximo: {MAX_FILE_SIZE // (1024*1024)}MB"
        )
    
    # ✅ Nombre final con UUID
    filename = f"{uuid.uuid4()}.{ext}"
    
    # Crear carpeta por categoría
    category_dir = os.path.join(BASE_UPLOAD_DIR, safe_category)
    os.makedirs(category_dir, exist_ok=True)
    file_path = os.path.join(category_dir, filename)
    
    # ✅ Guardar archivo con streaming asíncrono (optimizado)
    try:
        async with aiofiles.open(file_path, "wb") as f:
            while chunk := await file.read(1024 * 64):  # 64KB chunks
                await f.write(chunk)
    except Exception as e:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(status_code=500, detail=f"Error guardando archivo: {str(e)}")
    
    # 💾 Guardar en DB
    try:
        image = Image(
            filename=filename,
            category=safe_category,
        )
        db.add(image)
        db.commit()
        db.refresh(image)
    except Exception as e:
        if os.path.exists(file_path):
            os.remove(file_path)
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error en base de datos: {str(e)}")
    
    # ✅ MANTIENE EL FORMATO ORIGINAL DE RESPUESTA
    return {
        "id": image.id,
        "image_url": f"/images/{filename}"
    }

# -------------------------
# Delete todo (SIN CAMBIOS EN RESPUESTA)
# -------------------------
@app.delete("/images/all")
def delete_all(db: Session = Depends(get_db)):
    total = db.query(Image).count()
    if total == 0:
        raise HTTPException(status_code=404, detail="No hay imágenes")

    # Eliminar todo el contenido de la carpeta base
    try:
        for item in os.listdir(BASE_UPLOAD_DIR):
            item_path = os.path.join(BASE_UPLOAD_DIR, item)
            if os.path.isdir(item_path):
                shutil.rmtree(item_path)
            else:
                os.remove(item_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error eliminando archivos: {str(e)}")

    # Eliminar en DB
    try:
        db.query(Image).delete()
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error en base de datos: {str(e)}")

    # ✅ MANTIENE EL FORMATO ORIGINAL DE RESPUESTA
    return {"detail": "Todas las imágenes eliminadas", "total": total}

# -------------------------
# Delete por categoría (SIN CAMBIOS EN RESPUESTA)
# -------------------------
@app.delete("/images/category/{category}")
def delete_category(category: str, db: Session = Depends(get_db)):
    safe_category = validate_category(category)

    images = db.query(Image).filter(Image.category == safe_category).all()
    if not images:
        raise HTTPException(status_code=404, detail="No hay imágenes en esa categoría")

    # Eliminar carpeta en disco
    category_dir = os.path.join(BASE_UPLOAD_DIR, safe_category)
    if os.path.exists(category_dir):
        try:
            shutil.rmtree(category_dir)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error eliminando carpeta: {str(e)}")

    # Eliminar en DB
    try:
        db.query(Image).filter(Image.category == safe_category).delete()
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error en base de datos: {str(e)}")

    # ✅ MANTIENE EL FORMATO ORIGINAL DE RESPUESTA
    return {"detail": f"Categoría '{safe_category}' eliminada", "total": len(images)}

# -------------------------
# Listar imágenes (SIN CAMBIOS EN RESPUESTA)
# -------------------------
@app.get("/images")
def get_all_images(db: Session = Depends(get_db)):
    # ✅ Optimizado con limit para evitar cargar miles de registros
    # Pero mantiene el mismo formato de respuesta
    images = db.query(Image).limit(1000).all()  # Límite razonable

    if not images:
        return []

    # ✅ MANTIENE EL FORMATO ORIGINAL DE RESPUESTA
    return [
        {
            "id": image.id,
            "filename": image.filename,
            "category": image.category,
            "url": f"/images/{image.filename}"
        }
        for image in images
    ]

# -------------------------
# Servir imagen (OPTIMIZADO CON CACHÉ)
# -------------------------
@app.get("/images/{filename}")
def get_image(filename: str, db: Session = Depends(get_db)):
    # ✅ Validar filename para seguridad
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Nombre de archivo inválido")
    
    # Buscar en DB para obtener la categoría (carpeta real en disco)
    image = db.query(Image).filter(Image.filename == filename).first()
    if not image:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")

    path = os.path.join(BASE_UPLOAD_DIR, image.category, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Archivo no encontrado en disco")

    # ✅ OPTIMIZACIÓN: Agregar headers de caché sin cambiar el cuerpo
    return FileResponse(
        path,
        media_type=f"image/{os.path.splitext(filename)[1].lstrip('.')}",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": f'"{image.id}-{filename}"',
        }
    )

# -------------------------
# Delete por nombre (SIN CAMBIOS EN RESPUESTA)
# -------------------------
@app.delete("/images/{filename}")
def delete_image(filename: str, db: Session = Depends(get_db)):
    # ✅ Validar filename
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Nombre de archivo inválido")
    
    image = db.query(Image).filter(Image.filename == filename).first()
    if not image:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")

    # Eliminar archivo en disco
    path = os.path.join(BASE_UPLOAD_DIR, image.category, filename)
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error eliminando archivo: {str(e)}")

    # Eliminar en DB
    try:
        db.delete(image)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error en base de datos: {str(e)}")

    # ✅ MANTIENE EL FORMATO ORIGINAL DE RESPUESTA
    return {"detail": f"Imagen '{filename}' eliminada"}