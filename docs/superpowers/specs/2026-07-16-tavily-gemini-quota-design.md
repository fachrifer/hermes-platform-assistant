# Tavily Search and Gemini Quota Design

## Goal

Add web search for Nyx through Tavily and add a `/quota` Telegram command that
reports Tavily credit usage and the available Gemini quota/connectivity status.

## Tavily Search

- Read `TAVILY_API_KEY` from environment configuration.
- Use Tavily's web search endpoint for general and recent-information queries.
- Return title, URL, and snippet for each result so Nyx can cite sources.
- Use Tavily's news topic when the request is explicitly about current news.
- Fail gracefully when the key is missing, the request fails, or the API returns
  a quota error.

## Quota Command

`/quota` reports two independent sections:

- Tavily: plan, current-cycle usage, limit, and remaining credits from the
  official `/usage` endpoint.
- Gemini: configured model and live API status; when a service account and
  project ID are configured, query official Google Cloud quota metadata and
  monitoring metrics. If an exact remaining value is unavailable, state that
  explicitly and provide the relevant console path rather than estimating it.

## Gemini Credential Isolation

- Keep the existing Gmail/Calendar OAuth credentials unchanged.
- Read a separate service account JSON using `GEMINI_QUOTA_CREDENTIALS`.
- Scope it to the project in `GEMINI_QUOTA_PROJECT_ID`.
- Use least-privilege Google Cloud roles: Service Usage Viewer and Monitoring
  Viewer.
- The service account file remains under the synced `credentials/` directory
  and is never logged or sent to Tavily/Gemini requests.

## Integration Boundaries

- Add a `TavilyConnector` responsible only for Tavily HTTP calls and response
  normalization.
- Keep quota formatting in a small service/helper consumed by Telegram.
- Add `/quota` to the Telegram command registry and help text.
- Do not make ordinary chat dependent on Tavily availability.

## Verification

- Test Tavily response normalization, missing-key behavior, and quota response
  formatting without making network calls.
- Test `/quota` output for configured and unavailable providers.
- Run the full test suite and verify the container starts healthy.
