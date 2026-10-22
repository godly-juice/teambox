"""SLOT: the weekly TEAM FEED (Marjus, 2026-10-21: "every week, on tuesday, at 2pm all work and models created needs to become public
within the team so everyone can see what everyone did with a comment button ... and emoji reaction button").
Not built yet - next agent. Suggested shape: a scheduler tick (server thread or systemd timer) at Tuesday 14:00 box-local time
publishes a snapshot of every member's builds (title, summary, diff stats, test verdict) and saved model versions to
data/team-feed/<yyyy-ww>.json; comments + emoji reactions per item. Until a publish, builds stay visible only to their owner + admins."""

MODULE = {
    "id": "team-feed",
    "title": "Team feed (weekly)",
    "nav": "Team feed",
    "status": "coming soon",
    "summary": "Every Tuesday at 14:00 everyone's work and model versions become visible to the whole team, with comments and emoji reactions.",
    "help": "Every Tuesday at 2 pm, all work and models created that week become public within the team, so everyone can see what everyone "
            "did, comment on it and react with an emoji. Until then your builds are visible only to you and the admins. "
            "Not switched on yet.",
}
