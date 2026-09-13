import pytest
from pydantic import ValidationError

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


def test_reverse_request_valid():
    model = ReverseRequest(s="hello")
    assert model.s == "hello"


def test_reverse_request_missing_field():
    with pytest.raises(ValidationError):
        ReverseRequest()


def test_reverse_request_wrong_type():
    with pytest.raises(ValidationError):
        ReverseRequest(s=123)


def test_reverse_response_valid():
    model = ReverseResponse(result="olleh")
    assert model.result == "olleh"


def test_reverse_response_wrong_type():
    with pytest.raises(ValidationError):
        ReverseResponse(result=123)


def test_is_palindrome_request_valid():
    model = IsPalindromeRequest(s="racecar")
    assert model.s == "racecar"


def test_is_palindrome_request_missing_field():
    with pytest.raises(ValidationError):
        IsPalindromeRequest()


def test_is_palindrome_request_wrong_type():
    with pytest.raises(ValidationError):
        IsPalindromeRequest(s=123)


def test_is_palindrome_response_valid():
    model = IsPalindromeResponse(result=True)
    assert model.result is True


def test_is_palindrome_response_wrong_type():
    with pytest.raises(ValidationError):
        IsPalindromeResponse(result="not-a-bool")


def test_word_count_request_valid():
    model = WordCountRequest(s="hello world")
    assert model.s == "hello world"


def test_word_count_request_missing_field():
    with pytest.raises(ValidationError):
        WordCountRequest()


def test_word_count_request_wrong_type():
    with pytest.raises(ValidationError):
        WordCountRequest(s=123)


def test_word_count_response_valid():
    model = WordCountResponse(result=2)
    assert model.result == 2


def test_word_count_response_wrong_type():
    with pytest.raises(ValidationError):
        WordCountResponse(result="two")


def test_truncate_request_valid():
    model = TruncateRequest(s="hello world", max_length=5)
    assert model.s == "hello world"
    assert model.max_length == 5
    assert model.suffix == "..."


def test_truncate_request_custom_suffix():
    model = TruncateRequest(s="hello world", max_length=5, suffix="~")
    assert model.suffix == "~"


def test_truncate_request_missing_field():
    with pytest.raises(ValidationError):
        TruncateRequest(s="hello world")


def test_truncate_request_wrong_type():
    with pytest.raises(ValidationError):
        TruncateRequest(s="hello world", max_length="five")


def test_truncate_response_valid():
    model = TruncateResponse(result="he...")
    assert model.result == "he..."


def test_truncate_response_wrong_type():
    with pytest.raises(ValidationError):
        TruncateResponse(result=123)


def test_parse_config_request_valid():
    model = ParseConfigRequest(text="key=value")
    assert model.text == "key=value"


def test_parse_config_request_missing_field():
    with pytest.raises(ValidationError):
        ParseConfigRequest()


def test_parse_config_request_wrong_type():
    with pytest.raises(ValidationError):
        ParseConfigRequest(text=123)


def test_parse_config_response_valid():
    model = ParseConfigResponse(result={"key": "value"})
    assert model.result == {"key": "value"}


def test_parse_config_response_wrong_type():
    with pytest.raises(ValidationError):
        ParseConfigResponse(result="not-a-dict")


def test_slugify_request_valid():
    model = SlugifyRequest(s="Hello World")
    assert model.s == "Hello World"


def test_slugify_request_missing_field():
    with pytest.raises(ValidationError):
        SlugifyRequest()


def test_slugify_request_wrong_type():
    with pytest.raises(ValidationError):
        SlugifyRequest(s=123)


def test_slugify_response_valid():
    model = SlugifyResponse(result="hello-world")
    assert model.result == "hello-world"


def test_slugify_response_wrong_type():
    with pytest.raises(ValidationError):
        SlugifyResponse(result=123)


def test_title_case_request_valid():
    model = TitleCaseRequest(s="the lord of the rings")
    assert model.s == "the lord of the rings"


def test_title_case_request_missing_field():
    with pytest.raises(ValidationError):
        TitleCaseRequest()


def test_title_case_request_wrong_type():
    with pytest.raises(ValidationError):
        TitleCaseRequest(s=123)


def test_title_case_response_valid():
    model = TitleCaseResponse(result="The Lord of the Rings")
    assert model.result == "The Lord of the Rings"


def test_title_case_response_wrong_type():
    with pytest.raises(ValidationError):
        TitleCaseResponse(result=123)
