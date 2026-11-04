#!/usr/bin/env python3
"""Publish @hybridboysclub's latest Instagram posts as a static feed.

Runs on GitHub Actions every 15 minutes.  It reads the account through the
Instagram API with Instagram Login, saves each post's image to media/<id>.jpg
(Instagram's own image links expire), and writes feed.json, which the Hybrid
Boys Club app reads.  Active stories are saved to stories/ and published
to the `stories` branch, which is replaced on every run.  Only content that is already public on Instagram is
published here.

The access token is never stored in plain text:
  * INSTAGRAM_TOKEN (repository secret) is the token the owner generated.
  * The token is refreshed weekly (Instagram tokens last 60 days).  The
    refreshed token is saved to token.enc, encrypted with the FEED_KEY
    secret, so the job keeps working without anyone updating the secret.
  * Setting a new INSTAGRAM_TOKEN secret always takes priority.
"""
import hashlib, json, os, subprocess, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

API = "https://graph.instagram.com"
VERSION = "v25.0"
STORY_FIELDS = "id,media_type,media_url,thumbnail_url,permalink,timestamp"
FIELDS = "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,children{media_type,media_url,thumbnail_url}"
LIMIT = 24
KEY = os.environ.get("FEED_KEY", "")
SECRET_TOKEN = os.environ.get("INSTAGRAM_TOKEN", "").strip()


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def openssl(args, data):
    return subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt", "-pass", "env:FEED_KEY"] + args,
                          input=data, capture_output=True, check=True).stdout


def load_state():
    if not os.path.exists("token.enc") or not KEY:
        return None
    try:
        return json.loads(openssl(["-d", "-base64", "-A"], open("token.enc", "rb").read()))
    except Exception as exc:  # wrong key or corrupt file
        print("Couldn't read token.enc:", exc)
        return None


def save_state(state):
    open("token.enc", "wb").write(openssl(["-base64", "-A"], json.dumps(state).encode()))


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "hbc-feed"}), timeout=60) as r:
        return r.read()


def get_json(path, **params):
    q = urllib.parse.urlencode(params)
    try:
        return json.loads(get(f"{API}/{path}?{q}"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf8", "ignore")
        try:
            msg = json.loads(body)["error"]["message"]
        except Exception:
            msg = body[:300]
        raise RuntimeError(f"Instagram returned {e.code}: {msg}") from None


def write_status(ok, message, extra=None):
    status = {"ok": ok, "message": message, "checkedAt": now_iso()}
    status.update(extra or {})
    json.dump(status, open("status.json", "w"), indent=2)


def sync_stories(token):
    """Active stories (last 24 hours) go to stories/ and stories/stories.json.
    The workflow publishes that folder to the `stories` branch, replacing it
    each time, so expired stories leave no history.  Returns None if
    Instagram won't return stories for this token."""
    try:
        items = get_json(f"{VERSION}/me/stories", fields=STORY_FIELDS, access_token=token).get("data", [])
    except RuntimeError as exc:
        print("Stories unavailable:", exc)
        return None
    os.makedirs("stories", exist_ok=True)
    out, keep = [], set()
    for st in items:
        sid, kind = st["id"], st.get("media_type")
        image_src = st.get("thumbnail_url") if kind == "VIDEO" else st.get("media_url")
        if not image_src:
            continue
        entry = {"id": sid, "mediaType": kind, "image": f"{sid}.jpg", "permalink": st.get("permalink"), "timestamp": st.get("timestamp")}
        try:
            if not os.path.exists(f"stories/{sid}.jpg"):
                open(f"stories/{sid}.jpg", "wb").write(get(image_src))
            keep.add(f"{sid}.jpg")
            if kind == "VIDEO" and st.get("media_url"):
                if not os.path.exists(f"stories/{sid}.mp4"):
                    open(f"stories/{sid}.mp4", "wb").write(get(st["media_url"]))
                keep.add(f"{sid}.mp4")
                entry["video"] = f"{sid}.mp4"
        except Exception as exc:
            print("Skipped story", sid, exc)
            continue
        out.append(entry)
    for name in os.listdir("stories"):
        if name != "stories.json" and name not in keep:
            os.remove(f"stories/{name}")
    json.dump({"stories": out, "updatedAt": now_iso()}, open("stories/stories.json", "w"), indent=2)
    print(f"{len(out)} active stories.")
    return out


def main():
    if not SECRET_TOKEN and not os.path.exists("token.enc"):
        write_status(False, "Instagram isn't connected yet.  Add the INSTAGRAM_TOKEN repository secret.")
        print("No token configured yet; nothing to do.")
        return 0

    secret_hash = hashlib.sha256(SECRET_TOKEN.encode()).hexdigest() if SECRET_TOKEN else None
    state = load_state()
    if secret_hash and (not state or state.get("sourceHash") != secret_hash):
        state = {"token": SECRET_TOKEN, "refreshedAt": 0, "sourceHash": secret_hash}
        print("Using the INSTAGRAM_TOKEN secret.")
    if not state:
        write_status(False, "The saved token couldn't be read.  Set the INSTAGRAM_TOKEN secret again.")
        return 1

    token = state["token"]
    try:
        # Weekly refresh keeps the 60-day token alive.  Instagram allows a
        # refresh once a token is at least 24 hours old.
        if KEY and time.time() - state.get("refreshedAt", 0) > 7 * 86400:
            try:
                r = get_json("refresh_access_token", grant_type="ig_refresh_token", access_token=token)
                token = r["access_token"]
                state.update(token=token, refreshedAt=int(time.time()), expiresAt=int(time.time()) + int(r.get("expires_in", 5184000)))
                print("Refreshed the access token.")
            except RuntimeError as exc:
                print("Token refresh skipped:", exc)
            save_state(state)

        me = get_json(f"{VERSION}/me", fields="username", access_token=token)
        media = get_json(f"{VERSION}/me/media", fields=FIELDS, limit=LIMIT, access_token=token).get("data", [])
    except RuntimeError as exc:
        write_status(False, str(exc))
        print("Sync failed:", exc)
        return 1

    posts, keep = [], set()
    for m in media:
        first = ((m.get("children") or {}).get("data") or [None])[0] or {}
        kind = m.get("media_type")
        if kind == "VIDEO":
            src = m.get("thumbnail_url") or m.get("media_url")
        elif kind == "CAROUSEL_ALBUM":
            src = m.get("media_url") or first.get("thumbnail_url") or first.get("media_url")
        else:
            src = m.get("media_url") or m.get("thumbnail_url")
        if not src:
            continue  # e.g. reels with licensed audio omit media_url
        path = f"media/{m['id']}.jpg"
        if not os.path.exists(path):
            try:
                open(path, "wb").write(get(src))
            except Exception as exc:
                print("Skipped image", m["id"], exc)
                continue
        keep.add(path)
        posts.append({
            "id": m["id"],
            "caption": (m.get("caption") or "")[:600],
            "mediaType": kind,
            "image": path,
            "permalink": m.get("permalink"),
            "timestamp": m.get("timestamp"),
        })

    for name in os.listdir("media"):
        p = f"media/{name}"
        if name.endswith(".jpg") and p not in keep:
            os.remove(p)

    stories = sync_stories(token)

    feed = {"username": me.get("username"), "posts": posts}
    old = json.load(open("feed.json")) if os.path.exists("feed.json") else {}
    if {k: old.get(k) for k in feed} != feed:
        feed["updatedAt"] = now_iso()
        json.dump(feed, open("feed.json", "w"), indent=2, ensure_ascii=False)
        print(f"Published {len(posts)} posts.")
    else:
        print("No new posts.")
    extra = {"posts": len(posts), "stories": None if stories is None else len(stories)}
    if state.get("expiresAt"):
        extra["tokenExpiresAt"] = datetime.fromtimestamp(state["expiresAt"], timezone.utc).strftime("%Y-%m-%d")
    write_status(True, "Synced", extra)
    return 0


if __name__ == "__main__":
    sys.exit(main())
