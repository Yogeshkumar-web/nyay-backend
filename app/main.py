from contextlib import asynccontextmanager
from fastapi.security import HTTPBearer
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.exceptions import AppError, app_error_handler
from app.features.users.router import router as users_router
from app.features.auth.router import router as auth_router
from app.features.cases.router import router as cases_router
from app.features.documents.router import router as documents_router
from app.features.jobs.router import router as jobs_router
from fastapi.openapi.utils import get_openapi
from app.features.extraction.router import router as extraction_router
from app.features.context.router import router as context_router
from app.features.drafts.router import router as drafts_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title="VakilSuite API",
        version="1.0.0",
        routes=app.routes,
    )
    openapi_schema["components"]["securitySchemes"] = {
        "HTTPBearer": {
            "type": "http",
            "scheme": "bearer",
        }
    }
    openapi_schema["security"] = [{"HTTPBearer": []}]
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app = FastAPI(
    title="VakilSuite API",
    version="1.0.0",
    lifespan=lifespan,
    swagger_ui_parameters={"persistAuthorization": True},
)

app.openapi = custom_openapi

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Custom error handler
app.add_exception_handler(AppError, app_error_handler)
# Routers
API_PREFIX = "/api/v1"
app.include_router(cases_router, prefix=API_PREFIX)
app.include_router(users_router, prefix=API_PREFIX)
app.include_router(auth_router, prefix=API_PREFIX)
app.include_router(documents_router, prefix=API_PREFIX)
app.include_router(jobs_router, prefix=API_PREFIX)
app.include_router(extraction_router, prefix=API_PREFIX)
app.include_router(context_router, prefix=API_PREFIX)
app.include_router(drafts_router, prefix=API_PREFIX)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok"}