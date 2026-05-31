from datetime import date

from x_fetcher.config import AppConfig, TargetConfig
from x_fetcher.fetcher import Fetcher
from x_fetcher.store import Store
from x_fetcher.x_client import XResponse


def search_page(status_id: str):
    return {
        "data": {
            "search_by_raw_query": {
                "search_timeline": {
                    "timeline": {
                        "instructions": [
                            {
                                "entries": [
                                    {
                                        "content": {
                                            "entryType": "TimelineTimelineItem",
                                            "itemContent": {
                                                "tweet_results": {
                                                    "result": {
                                                        "rest_id": status_id,
                                                        "legacy": {
                                                            "full_text": "hello",
                                                            "created_at": "Thu Jan 09 12:00:00 +0000 2025",
                                                        },
                                                    }
                                                }
                                            },
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                }
            }
        }
    }


class FakeClient:
    def __init__(self):
        self.cursors = []

    def search_timeline(self, query_id, raw_query, cursor=None):
        self.cursors.append(cursor)
        return XResponse(200, "{}", search_page("1"))


def test_search_window_resumes_from_initial_cursor(tmp_path):
    store = Store(tmp_path / "test.db")
    store.init_schema()
    client = FakeClient()
    config = AppConfig(targets=(TargetConfig("alice", date(2025, 1, 1), date(2025, 1, 15)),), max_tweets=1)
    fetcher = Fetcher(config, store, client, sleep_enabled=False)  # type: ignore[arg-type]

    tweets, last_cursor, reason = fetcher._fetch_search_window(
        "alice",
        "search-qid",
        date(2025, 1, 8),
        date(2025, 1, 15),
        initial_cursor="resume-cursor",
    )

    assert client.cursors == ["resume-cursor"]
    assert [t.status_id for t in tweets] == ["1"]
    assert last_cursor is None
    assert reason == "search returned no more cursor"
    store.close()
