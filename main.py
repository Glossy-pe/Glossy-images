from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
import os
import uuid
import shutil
from dotenv import load_dotenv
load_dotenv()
from database.database import engine, SessionLocal, Base
from models.image import Image

# -------------------------
# Inicialización
# -------------------------
Base.metadata.create_all(bind=engine)

app = FastAPI()

BASE_UPLOAD_DIR = "images"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "jfif", "avif"}

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
# Upload de imagen
# -------------------------
@app.post("/upload")
async def upload_image(
    category: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    # 🔐 Normalizar categoría
    safe_category = os.path.normpath(category)
    if safe_category.startswith(".."):
        raise HTTPException(status_code=400, detail="Categoría inválida")

    # ✅ Obtener extensión REAL
    _, ext = os.path.splitext(file.filename)
    ext = ext.lower().lstrip(".")

    if not ext:
        raise HTTPException(status_code=400, detail="Archivo sin extensión")

    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Extensión no permitida")

    # ✅ Nombre final con UUID
    filename = f"{uuid.uuid4()}.{ext}"

    # Crear carpeta por categoría (organización interna en disco)
    category_dir = os.path.join(BASE_UPLOAD_DIR, safe_category)
    os.makedirs(category_dir, exist_ok=True)
    file_path = os.path.join(category_dir, filename)

    # Guardar archivo
    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    # 💾 Guardar en DB
    image = Image(
        filename=filename,
        category=safe_category,
    )
    db.add(image)
    db.commit()
    db.refresh(image)

    return {
        "id": image.id,
        "image_url": f"/images/{filename}"
    }

# -------------------------
# Delete todo
# -------------------------
@app.delete("/images/all")
def delete_all(db: Session = Depends(get_db)):
    total = db.query(Image).count()
    if total == 0:
        raise HTTPException(status_code=404, detail="No hay imágenes")

    # Eliminar todo el contenido de la carpeta base
    for item in os.listdir(BASE_UPLOAD_DIR):
        item_path = os.path.join(BASE_UPLOAD_DIR, item)
        if os.path.isdir(item_path):
            shutil.rmtree(item_path)
        else:
            os.remove(item_path)

    # Eliminar en DB
    db.query(Image).delete()
    db.commit()

    return {"detail": "Todas las imágenes eliminadas", "total": total}

# -------------------------
# Delete por categoría
# -------------------------
@app.delete("/images/category/{category}")
def delete_category(category: str, db: Session = Depends(get_db)):
    safe_category = os.path.normpath(category)
    if safe_category.startswith(".."):
        raise HTTPException(status_code=400, detail="Categoría inválida")

    images = db.query(Image).filter(Image.category == safe_category).all()
    if not images:
        raise HTTPException(status_code=404, detail="No hay imágenes en esa categoría")

    # Eliminar carpeta en disco
    category_dir = os.path.join(BASE_UPLOAD_DIR, safe_category)
    if os.path.exists(category_dir):
        shutil.rmtree(category_dir)

    # Eliminar en DB
    db.query(Image).filter(Image.category == safe_category).delete()
    db.commit()

    return {"detail": f"Categoría '{safe_category}' eliminada", "total": len(images)}

@app.get("/images")
def get_all_images(db: Session = Depends(get_db)):
    images = db.query(Image).all()

    if not images:
        return []

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
# Servir imagen
# -------------------------
@app.get("/images/{filename}")
def get_image(filename: str, db: Session = Depends(get_db)):
    # Buscar en DB para obtener la categoría (carpeta real en disco)
    image = db.query(Image).filter(Image.filename == filename).first()
    if not image:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")

    path = os.path.join(BASE_UPLOAD_DIR, image.category, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Archivo no encontrado en disco")

    return FileResponse(path)

# -------------------------
# Delete por nombre
# -------------------------
@app.delete("/images/{filename}")
def delete_image(filename: str, db: Session = Depends(get_db)):
    image = db.query(Image).filter(Image.filename == filename).first()
    if not image:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")

    # Eliminar archivo en disco
    path = os.path.join(BASE_UPLOAD_DIR, image.category, filename)
    if os.path.exists(path):
        os.remove(path)

    # Eliminar en DB
    db.delete(image)
    db.commit()

    return {"detail": f"Imagen '{filename}' eliminada"}