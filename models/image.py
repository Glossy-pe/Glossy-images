from sqlalchemy import Column, Integer, String, DateTime, func
from database.database import Base

class Image(Base):
    __tablename__ = "images"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, nullable=False, unique=True)
    category = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, server_default=func.now())