"""API routes exposing each public string-utility function as a POST endpoint."""

from fastapi import APIRouter

from src import string_utils
from src.api.schemas import (
    IsPalindromeRequest,
    IsPalindromeResponse,
    ParseConfigRequest,
    ParseConfigResponse,
    ReverseRequest,
    ReverseResponse,
    SlugifyRequest,
    SlugifyResponse,
    TitleCaseRequest,
    TitleCaseResponse,
    TruncateRequest,
    TruncateResponse,
    WordCountRequest,
    WordCountResponse,
)

router = APIRouter(prefix="/strings")


@router.post("/reverse", response_model=ReverseResponse, summary="Reverse a string")
def reverse(body: ReverseRequest) -> ReverseResponse:
    return ReverseResponse(result=string_utils.reverse(body.s))


@router.post(
    "/is-palindrome",
    response_model=IsPalindromeResponse,
    summary="Check whether a string is a palindrome",
)
def is_palindrome(body: IsPalindromeRequest) -> IsPalindromeResponse:
    return IsPalindromeResponse(result=string_utils.is_palindrome(body.s))


@router.post("/word-count", response_model=WordCountResponse, summary="Count words in a string")
def word_count(body: WordCountRequest) -> WordCountResponse:
    return WordCountResponse(result=string_utils.word_count(body.s))


@router.post("/truncate", response_model=TruncateResponse, summary="Truncate a string")
def truncate(body: TruncateRequest) -> TruncateResponse:
    return TruncateResponse(result=string_utils.truncate(body.s, body.max_length, body.suffix))


@router.post(
    "/parse-config",
    response_model=ParseConfigResponse,
    summary="Parse key=value configuration text into a dict",
)
def parse_config(body: ParseConfigRequest) -> ParseConfigResponse:
    return ParseConfigResponse(result=string_utils.parse_config(body.text))


@router.post("/slugify", response_model=SlugifyResponse, summary="Convert a string into a slug")
def slugify(body: SlugifyRequest) -> SlugifyResponse:
    return SlugifyResponse(result=string_utils.slugify(body.s))


@router.post(
    "/title-case",
    response_model=TitleCaseResponse,
    summary="Convert a string to title case",
)
def title_case(body: TitleCaseRequest) -> TitleCaseResponse:
    return TitleCaseResponse(result=string_utils.title_case(body.s))
