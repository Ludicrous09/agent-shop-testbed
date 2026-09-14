from src import string_utils


def test_reverse(client):
    response = client.post("/strings/reverse", json={"s": "hello"})

    assert response.status_code == 200
    assert response.json() == {"result": string_utils.reverse("hello")}


def test_reverse_missing_field_returns_422(client):
    response = client.post("/strings/reverse", json={})

    assert response.status_code == 422


def test_is_palindrome(client):
    response = client.post("/strings/is-palindrome", json={"s": "A man a plan a canal Panama"})

    assert response.status_code == 200
    assert response.json() == {"result": string_utils.is_palindrome("A man a plan a canal Panama")}


def test_is_palindrome_missing_field_returns_422(client):
    response = client.post("/strings/is-palindrome", json={})

    assert response.status_code == 422


def test_word_count(client):
    response = client.post("/strings/word-count", json={"s": "the quick brown fox"})

    assert response.status_code == 200
    assert response.json() == {"result": string_utils.word_count("the quick brown fox")}


def test_word_count_missing_field_returns_422(client):
    response = client.post("/strings/word-count", json={})

    assert response.status_code == 422


def test_truncate(client):
    response = client.post(
        "/strings/truncate", json={"s": "hello world", "max_length": 8, "suffix": "..."}
    )

    assert response.status_code == 200
    assert response.json() == {"result": string_utils.truncate("hello world", 8, "...")}


def test_truncate_missing_field_returns_422(client):
    response = client.post("/strings/truncate", json={"s": "hello world"})

    assert response.status_code == 422


def test_parse_config(client):
    text = "# comment\nkey = value\nother=thing\n"
    response = client.post("/strings/parse-config", json={"text": text})

    assert response.status_code == 200
    assert response.json() == {"result": string_utils.parse_config(text)}


def test_parse_config_missing_field_returns_422(client):
    response = client.post("/strings/parse-config", json={})

    assert response.status_code == 422


def test_slugify(client):
    response = client.post("/strings/slugify", json={"s": "Hello, World!  Foo_Bar"})

    assert response.status_code == 200
    assert response.json() == {"result": string_utils.slugify("Hello, World!  Foo_Bar")}


def test_slugify_missing_field_returns_422(client):
    response = client.post("/strings/slugify", json={})

    assert response.status_code == 422


def test_title_case(client):
    response = client.post("/strings/title-case", json={"s": "the lord of the rings"})

    assert response.status_code == 200
    assert response.json() == {"result": string_utils.title_case("the lord of the rings")}


def test_title_case_missing_field_returns_422(client):
    response = client.post("/strings/title-case", json={})

    assert response.status_code == 422
