"""FastAPI application object.

Error response shape:
    Both the validation handler and the HTTP exception handler return a JSON
    body of the form ``{"detail": <detail>}``.

    - For ``RequestValidationError`` (422), ``detail`` is a list of objects,
      each with a ``loc`` (path to the offending field), ``msg``, and
      ``type``, mirroring FastAPI's default validation error format.
    - For ``HTTPException`` (e.g. 404), ``detail`` is the exception's
      string detail message.

    Neither handler includes exception tracebacks or exception reprs in the
    response body.
"""

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.api.routes.strings import router as strings_router

app = FastAPI(title="String Utils API", version="0.1.0")

app.include_router(strings_router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": jsonable_encoder(exc.errors())},
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )
