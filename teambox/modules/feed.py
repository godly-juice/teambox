"""SLOT: the HOME FEED (Marjus, 2026-10-21: "in the home page, add a feed where right now its just comments ppl can post but also
they should be able to attach files to that post, videos, pictures, notepad, spreadbooks, etc.").
Not built yet - next agent. Suggested shape: data/feed/<id>.json posts {author slug, text, attachments[{name, type, size, sha256}]},
files in data/feed/files/<sha256> (size/type caps, served with Content-Disposition + nosniff, never executed), comments + reactions."""

MODULE = {
    "id": "feed",
    "title": "Home feed",
    "nav": "Home feed",
    "status": "coming soon",
    "summary": "Posts from the team with attachments: videos, pictures, notes, spreadsheets.",
    "help": "The home feed is where anyone on the team can post a note and attach files (videos, pictures, notes, spreadsheets). "
            "It is not switched on yet - this page will turn into the feed when it is.",
}
