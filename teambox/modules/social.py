"""Comments + emoji reactions, shared by the home feed and the weekly team feed.
A thing that can be commented on is a dict with "comments": [...] and "reactions": {emoji: [member slugs]}.
Reactions are a fixed set of emoji (no free text), one toggle per member per emoji."""
import secrets
import time

EMOJI = ["\U0001F44D", "❤️", "\U0001F602", "\U0001F389", "\U0001F62E", "\U0001F525", "\U0001F440", "\U0001F64F"]
COMMENT_MAX = 4000
COMMENTS_PER_THING = 500


class SocialError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def now_s():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def add_comment(thing, slug, email, text, max_chars=COMMENT_MAX):
    text = str(text or "").strip()
    if not text:
        raise SocialError(400, "write something first")
    if len(text) > max_chars:
        raise SocialError(400, "a comment can be at most %d characters" % max_chars)
    lst = thing.setdefault("comments", [])
    if len(lst) >= COMMENTS_PER_THING:
        raise SocialError(409, "this has %d comments already (the most)" % COMMENTS_PER_THING)
    c = {"id": secrets.token_hex(5), "author": slug, "author_email": email, "text": text, "at": now_s()}
    lst.append(c)
    return c


def delete_comment(thing, cid, slug, admin):
    lst = thing.get("comments") or []
    c = next((x for x in lst if x.get("id") == cid), None)
    if not c:
        raise SocialError(404, "no such comment")
    if c["author"] != slug and not admin:
        raise SocialError(403, "only the comment's author or an admin can delete it")
    thing["comments"] = [x for x in lst if x.get("id") != cid]
    return True


def toggle_reaction(thing, slug, emoji):
    if emoji not in EMOJI:
        raise SocialError(400, "pick one of the offered emoji")
    r = thing.setdefault("reactions", {})
    who = r.setdefault(emoji, [])
    if slug in who:
        who.remove(slug)
        on = False
    else:
        who.append(slug)
        on = True
    if not who:
        r.pop(emoji, None)
    return on


def public_view(thing, slug, names):
    """Comments + reactions as the UI needs them (display names, 'mine' flags) - never other members' emails."""
    return {
        "comments": [{"id": c["id"], "author": c["author"], "author_name": names(c["author"]), "text": c["text"], "at": c["at"],
                      "mine": c["author"] == slug} for c in thing.get("comments") or []],
        "reactions": [{"emoji": e, "count": len(thing.get("reactions", {}).get(e, [])),
                       "mine": slug in thing.get("reactions", {}).get(e, [])} for e in EMOJI],
    }
