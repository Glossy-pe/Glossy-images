from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import os

# Siempre construir desde variables de entorno
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")

# Validar que existan las variables críticas
if not all([DB_USER, DB_PASSWORD, DB_HOST, DB_NAME]):
    raise ValueError("Faltan variables de entorno de base de datos requeridas")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# ✅ Engine optimizado para PostgreSQL en producción
engine = create_engine(
    DATABASE_URL,
    # Pool de conexiones optimizado
    pool_size=10,              # 10 conexiones permanentes
    max_overflow=20,           # Hasta 20 conexiones adicionales bajo carga
    pool_timeout=30,           # Timeout de 30 segundos para obtener conexión
    pool_recycle=3600,         # Reciclar conexiones cada hora (evita "server closed connection")
    pool_pre_ping=True,        # Verificar que la conexión esté viva antes de usarla
    
    # Opciones de ejecución
    echo=False,                # No loggear queries SQL (cambiar a True solo en dev)
    future=True,               # Usar SQLAlchemy 2.0 style
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()