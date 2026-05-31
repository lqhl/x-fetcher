from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from curl_cffi import requests as cffi_requests

from .config import SecretsConfig
from .models import UserProfile


BEARER = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"

FEATURES: dict[str, Any] = {
    "rweb_tipjar_consumption_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "premium_content_api_read_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "rweb_video_timestamps_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
}

PROFILE_FEATURES: dict[str, Any] = {
    "hidden_profile_subscriptions_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "highlights_tweets_tab_ui_enabled": True,
    "responsive_web_twitter_article_notes_tab_enabled": True,
    "subscriptions_feature_can_gift_premium": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
}


class XClientError(RuntimeError):
    pass


@dataclass
class XResponse:
    status_code: int
    text: str
    data: dict[str, Any] | None = None


class XClient:
    def __init__(self, secrets: SecretsConfig):
        self.secrets = secrets
        self.session = cffi_requests.Session()
        self.session.headers.update(
            {
                "authorization": f"Bearer {BEARER}",
                "Cookie": f"auth_token={secrets.auth_token}; ct0={secrets.ct0}",
                "x-csrf-token": secrets.ct0,
                "x-twitter-active-user": "yes",
                "x-twitter-client-language": "en",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

    def web_get(self, url: str, **kwargs: Any):
        headers = dict(self.session.headers)
        headers.pop("authorization", None)
        return cffi_requests.get(url, headers=headers, impersonate="chrome131", **kwargs)

    def extract_query_id(self, text: str, operation_name: str) -> str:
        escaped = re.escape(operation_name)
        patterns = [
            rf'"queryId":"([^"]+)"[^}}]*"operationName":"{escaped}"',
            rf'"operationName":"{escaped}"[^}}]*"queryId":"([^"]+)"',
            rf'queryId:"([^"]+)"[^}}]*operationName:"{escaped}"',
            rf'operationName:"{escaped}"[^}}]*queryId:"([^"]+)"',
        ]
        for pattern in patterns:
            m = re.search(pattern, text)
            if m:
                return m.group(1)
        return ""

    def find_operation_query_id(self, screen_name: str, operation_name: str) -> str:
        r = self.web_get(f"https://x.com/{screen_name}")
        qid = self.extract_query_id(r.text, operation_name)
        if qid:
            return qid

        js_urls = re.findall(r'src="(https://abs\.twimg\.com/responsive-web/client-web/[^"]+\.js)"', r.text)
        for url in js_urls[:12]:
            r2 = self.web_get(url, timeout=10)
            if r2.status_code != 200:
                continue
            qid = self.extract_query_id(r2.text, operation_name)
            if qid:
                return qid
        raise XClientError(f"Cannot find GraphQL query id for {operation_name}")

    def get_user_profile(self, screen_name: str) -> UserProfile:
        try:
            qid = self.find_operation_query_id(screen_name, "UserByScreenName")
        except XClientError:
            qid = "32pL5BWePUg4I7rFppjjKA"
        r = self.session.get(
            f"https://x.com/i/api/graphql/{qid}/UserByScreenName",
            params={
                "variables": json.dumps({"screen_name": screen_name, "withSafetyModeUserFields": True}),
                "features": json.dumps(PROFILE_FEATURES),
            },
            impersonate="chrome131",
        )
        if r.status_code in (401, 403):
            raise XClientError(f"X authentication failed (HTTP {r.status_code}); refresh auth_token and ct0")
        if r.status_code != 200:
            raise XClientError(f"UserByScreenName HTTP {r.status_code}: {r.text[:200]}")
        user = r.json().get("data", {}).get("user", {}).get("result", {})
        rest_id = str(user.get("rest_id") or "")
        if not rest_id:
            raise XClientError(f"Cannot find user id for @{screen_name}")
        legacy = user.get("legacy") or {}
        return UserProfile(
            screen_name=str(legacy.get("screen_name") or screen_name),
            rest_id=rest_id,
            display_name=str(legacy.get("name") or ""),
            profile_created_at=str(legacy.get("created_at") or ""),
        )

    def user_tweets(self, query_id: str, user_id: str, cursor: str | None = None, count: int = 40) -> XResponse:
        variables = {
            "userId": user_id,
            "count": count,
            "includePromotedContent": False,
            "withQuickPromoteEligibilityTweetFields": True,
            "withVoice": True,
            "withV2Timeline": True,
        }
        if cursor:
            variables["cursor"] = cursor
        r = self.session.get(
            f"https://x.com/i/api/graphql/{query_id}/UserTweets",
            params={"variables": json.dumps(variables), "features": json.dumps(FEATURES)},
            impersonate="chrome131",
        )
        return XResponse(r.status_code, r.text, r.json() if r.status_code == 200 else None)

    def search_timeline(self, query_id: str, raw_query: str, cursor: str | None = None, count: int = 20) -> XResponse:
        variables = {"rawQuery": raw_query, "count": count, "querySource": "typed_query", "product": "Latest"}
        if cursor:
            variables["cursor"] = cursor
        r = self.session.post(
            f"https://x.com/i/api/graphql/{query_id}/SearchTimeline",
            json={"variables": variables, "features": FEATURES},
            impersonate="chrome131",
        )
        return XResponse(r.status_code, r.text, r.json() if r.status_code == 200 else None)
