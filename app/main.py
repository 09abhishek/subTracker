from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routes.user import router as user_router
from .routes.auth import router as auth_router
from .routes.account import router as account_router
from .routes.categories import router as categories_router
from .routes.ledgers import router as ledgers_router
from .routes.analytics import router as analytics_router

# Adding FastAPI app instance
app = FastAPI(
    title="SubTracker API",
    description="API for processing and managing personal finance ledger files",
    version="1.0.0"
)

# Allow CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Adding Routes
app.include_router(user_router)
app.include_router(auth_router)
app.include_router(account_router)
app.include_router(categories_router)
app.include_router(ledgers_router)
app.include_router(analytics_router)

# Default Route
@app.get("/")
async def read_root():
    return {
        "detail": "Welcome to SubTracker API",
        "swagger": "http://localhost:8000/docs"
    }