from x_fetcher.fetcher import parse_search_page
from x_fetcher.parser import parse_tweet_result


def tweet_result(status_id="1", text="hello", created_at="Wed Jan 01 12:00:00 +0000 2025"):
    return {
        "rest_id": status_id,
        "legacy": {
            "full_text": text,
            "created_at": created_at,
            "favorite_count": 2,
            "retweet_count": 3,
            "reply_count": 4,
            "lang": "en",
        },
        "views": {"count": "5"},
    }


def test_parse_tweet_result():
    parsed = parse_tweet_result(tweet_result())

    assert parsed is not None
    assert parsed.status_id == "1"
    assert parsed.created_at_utc.isoformat() == "2025-01-01T12:00:00+00:00"
    assert parsed.likes == 2
    assert parsed.views == 5


def test_parse_search_page_extracts_tweet_and_cursor():
    data = {
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
                                            "itemContent": {"tweet_results": {"result": tweet_result("42")}},
                                        }
                                    },
                                    {"content": {"cursorType": "Bottom", "value": "cursor-1"}},
                                ]
                            }
                        ]
                    }
                }
            }
        }
    }

    tweets, cursor = parse_search_page(data)

    assert [t.status_id for t in tweets] == ["42"]
    assert cursor == "cursor-1"
