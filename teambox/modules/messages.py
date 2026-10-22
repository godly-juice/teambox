"""SLOT: MESSAGES, CALLS and VIDEO CALLS (Marjus, 2026-10-21, team-calls-messages-monitoring-notice.txt).
Not built yet - next agent. Requirements from the plan: encrypted at rest to Lumi's public key (decrypt only on BESNIK); members read
their own conversations; the notice below must be ACCEPTED before first use (store acceptance per member with the notice's sha256);
WatchDog monitoring = AI first, a concern is verified by a third-party AI before any human sees it.
NOTICE is Marjus's text verbatim with exactly the three approved typo fixes (sctrictly->strictly, intersts->interests,
supercede->supersede) - change nothing else."""
import hashlib

NOTICE = (
    "chatting about the project outside of project work spaces is strictly prohibited to ensure fair voting. All messages, notes, "
    "everything will be monitored by the WatchDog. Admins will decide the result amongst them but this is in place to not conspire "
    "against other team members and to prohibit clustering of idea promotion based on hidden interests. Privacy will be respected and "
    "only processed by Ai unless the Ai raises a concern which will first be verified by a third party Ai before reaching any human's "
    "attention so speak freely but do not conspire based off hidden interests. Shared, personal interests because you deserve it are "
    "welcomed and promoted; we only care about 'hidden'. Hidden promotes corruption and we do not want a corrupt team member(s) as it "
    "will be catastrophic to the team and the team's interests supersede one's interests. But, one's interests are what ignite the "
    "team's interests so please do not hold back as 0 punishment will come from having self interests, on the contrary; we heavily "
    "promote it as one winning means the team is winning."
)
NOTICE_SHA256 = hashlib.sha256(NOTICE.encode("utf8")).hexdigest()

MODULE = {
    "id": "messages",
    "title": "Messages & calls",
    "nav": "Messages & calls",
    "status": "coming soon",
    "summary": "Messages, voice calls and video calls inside the team box - with a notice you must accept first.",
    "help": "Messages, calls and video calls will live here. Before first use you will be asked to read and accept this notice:",
    "notice": NOTICE,
    "notice_sha256": NOTICE_SHA256,
}
