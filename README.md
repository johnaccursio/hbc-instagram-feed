# HBC Instagram feed

The latest posts from [@hybridboysclub](https://www.instagram.com/hybridboysclub/), checked every 15 minutes for the Hybrid Boys Club app's "On Instagram" widget.

- `feed.json`: post list (caption, type, image, Instagram link, time).
- `media/`: post images (Instagram's own image links expire).
- `status.json`: last sync result.
- `stories` branch: active stories (`stories.json`, images and videos).  It is replaced on every run, so expired stories leave no history.

Everything here is already public on Instagram.  The access token is kept in this repository's secrets (`INSTAGRAM_TOKEN`).  After its weekly refresh it is stored only in encrypted form (`token.enc`, encrypted with the `FEED_KEY` secret).

## Connecting or reconnecting

1. Generate an Instagram token for @hybridboysclub at developers.facebook.com: create an app, add Instagram, choose "API setup with Instagram login," then "Generate token."
2. In this repository, go to Settings › Secrets and variables › Actions › New repository secret.  Set the name to `INSTAGRAM_TOKEN` and paste the token as the value.
3. Go to Actions › Sync Instagram › Run workflow.
