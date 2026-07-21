from connectors.tavily import TavilyConnector, bias_query_for_indonesia


def test_bias_query_appends_indonesia_when_missing():
    assert bias_query_for_indonesia("harga beras") == "harga beras Indonesia"
    assert bias_query_for_indonesia("ekonomi Indonesia") == "ekonomi Indonesia"
    assert bias_query_for_indonesia("Jakarta flood") == "Jakarta flood"


def test_search_general_sends_indonesia_country_and_advanced_depth(monkeypatch):
    connector = TavilyConnector(api_key="test-key")
    captured = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"title": "A", "url": "https://a", "content": "Snippet"}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return Response()

    monkeypatch.setattr("connectors.tavily.requests.post", fake_post)
    monkeypatch.setattr("connectors.tavily.settings.tavily_country", "indonesia")
    monkeypatch.setattr("connectors.tavily.settings.tavily_search_depth", "advanced")

    assert connector.search("harga beras", news=False) == [
        {"title": "A", "url": "https://a", "snippet": "Snippet"}
    ]
    assert captured["json"]["topic"] == "general"
    assert captured["json"]["country"] == "indonesia"
    assert captured["json"]["search_depth"] == "advanced"
    assert captured["json"]["query"] == "harga beras Indonesia"


def test_search_news_omits_country_but_biases_query(monkeypatch):
    connector = TavilyConnector(api_key="test-key")
    captured = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"title": "A", "url": "https://a", "content": "Snippet"}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return Response()

    monkeypatch.setattr("connectors.tavily.requests.post", fake_post)

    assert connector.search("berita terbaru", news=True) == [
        {"title": "A", "url": "https://a", "snippet": "Snippet"}
    ]
    assert captured["json"]["topic"] == "news"
    assert "country" not in captured["json"]
    assert captured["json"]["query"] == "berita terbaru Indonesia"


def test_search_without_key_returns_empty():
    assert TavilyConnector(api_key="").search("test") == []


def test_usage_returns_tavily_payload(monkeypatch):
    connector = TavilyConnector(api_key="test-key")

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"key": {"usage": 12, "limit": 1000}}

    monkeypatch.setattr("connectors.tavily.requests.get", lambda *args, **kwargs: Response())

    assert connector.usage()["key"]["usage"] == 12
