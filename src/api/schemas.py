"""Pydantic request and response models for the string-utility endpoints."""

from pydantic import BaseModel, Field


class ReverseRequest(BaseModel):
    s: str = Field(..., description="The string to reverse.")


class ReverseResponse(BaseModel):
    result: str = Field(..., description="The reversed string.")


class IsPalindromeRequest(BaseModel):
    s: str = Field(..., description="The string to check for palindrome-ness.")


class IsPalindromeResponse(BaseModel):
    result: bool = Field(
        ...,
        description=("Whether the string is a palindrome (case-insensitive, ignoring spaces)."),
    )


class WordCountRequest(BaseModel):
    s: str = Field(..., description="The string whose words will be counted.")


class WordCountResponse(BaseModel):
    result: int = Field(..., description="The number of words in the string.")


class TruncateRequest(BaseModel):
    s: str = Field(..., description="The string to truncate.")
    max_length: int = Field(..., description="The maximum length of the resulting string.")
    suffix: str = Field(
        "...",
        description="The suffix appended to the string when it is truncated.",
    )


class TruncateResponse(BaseModel):
    result: str = Field(..., description="The truncated string.")


class ParseConfigRequest(BaseModel):
    text: str = Field(
        ...,
        description=("Key=value configuration text to parse, ignoring '#' comments."),
    )


class ParseConfigResponse(BaseModel):
    result: dict[str, str] = Field(
        ..., description="The parsed configuration as a mapping of keys to values."
    )


class SlugifyRequest(BaseModel):
    s: str = Field(..., description="The string to convert into a slug.")


class SlugifyResponse(BaseModel):
    result: str = Field(..., description="The lowercase, URL-safe slug generated from the string.")


class TitleCaseRequest(BaseModel):
    s: str = Field(..., description="The string to convert to title case.")


class TitleCaseResponse(BaseModel):
    result: str = Field(
        ...,
        description=(
            "The string with each word capitalised, keeping short joining "
            "words lowercase unless first."
        ),
    )
