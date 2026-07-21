from connectors.tavily import TavilyConnector


def test_search_normalizes_tavily_results(monkeypatch):
    connector = TavilyConnector(api_key="test-key")

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"title": "A", "url": "https://a", "content": "Snippet"}]}

    monkeypatch.setattr("connectors.tavily.requests.post", lambda *args, **kwargs: Response())

    assert connector.search("berita terbaru", news=True) == [
        {"title": "A", "url": "https://a", "snippet": "Snippet"}
    ]


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
