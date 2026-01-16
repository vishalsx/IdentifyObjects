from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# Routers

from routers import (
    identifyObjects,
    updateObjects,
    getWorklist,
    getTranslations,
    skipTranslation,
    extractFileInfo,
    thumbNail,
    getImagePool,
    createBook,
    searchBook,
    createStory,
    createprompt,
    createQuiz,
    createContest,
    import_content
)

from routers.login import router as login_router
# from db import connect_to_mongo, close_mongo_connection

# --- Import database connection ---
from db.connection import db, client


app = FastAPI(title="Identify Objects API")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# # Startup / Shutdown events
# @app.on_event("startup")
# async def startup_event():
#     await connect_to_mongo()

# @app.on_event("shutdown")
# async def shutdown_event():
#     await close_mongo_connection()

# Routers
# app.include_router(login_router, prefix="/auth", tags=["auth"])
app.include_router(login_router, prefix="/auth", tags=["Authentication"])  # ✅ matches login.py
app.include_router(identifyObjects.router)
app.include_router(updateObjects.router)
app.include_router(getWorklist.router)
app.include_router(getTranslations.router)
app.include_router(skipTranslation.router)
app.include_router(extractFileInfo.router)
app.include_router(thumbNail.router)
app.include_router(getImagePool.router)
app.include_router(createBook.router)
app.include_router(searchBook.router)
app.include_router(createStory.router)
app.include_router(createprompt.router)
app.include_router(createQuiz.router)
app.include_router(createContest.router)
app.include_router(import_content.router)

@app.get("/")
def read_root():
    return {"message": "Hello from Identify Object app"}


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
