# Installing the team box on xOS (the RTX 3060 PC)

The app is plain Python 3 (standard library only) plus `git`, `bubblewrap` and `prlimit`. It never holds Marjus's files or secrets.
The one secret it needs is the **team gateway secret** from BESNIK. That secret is read from a file and never goes into the repo.

> Not done yet (Director): nothing below has been run on the real box. Every step was tested in the `archlinux` WSL distro on BESNIK
> (Python 3.14, git 2.56, bubblewrap 0.12), except systemd and cloudflared, which were not run.

## 1. Packages

```sh
sudo pacman -Syu --needed python git bubblewrap util-linux cloudflared
```
`util-linux` provides `prlimit`. `cloudflared` is in Arch's `extra` repo. Nothing else is required: no pip, no node.
Check that unprivileged user namespaces work, because the jail needs them (this is the Arch default):
```sh
sysctl kernel.unprivileged_userns_clone 2>/dev/null; cat /proc/sys/user/max_user_namespaces   # must be > 0
sudo -u nobody bwrap --unshare-all --ro-bind /usr /usr --symlink usr/bin /bin --symlink usr/lib /lib --symlink usr/lib /lib64 --proc /proc --dev /dev true && echo JAIL-OK
```

## 2. User, folders, code

```sh
sudo useradd --system --create-home --home-dir /var/lib/teambox --shell /usr/bin/nologin teambox
sudo install -d -o teambox -g teambox -m 0700 /var/lib/teambox
sudo install -d -o root -g teambox -m 0750 /etc/teambox
sudo git clone <this repo> /opt/teambox        # or copy D:\Atlas\teambox (without data/, shots/, config.json)
sudo chown -R root:root /opt/teambox           # the app can not modify its own code
```

## 3. Config + gateway secret

```sh
sudo cp /opt/teambox/config.example.json /etc/teambox/config.json
sudo chown root:teambox /etc/teambox/config.json && sudo chmod 0640 /etc/teambox/config.json
sudo nano /etc/teambox/config.json
```
Fill in:
- `admin_emails`: Marjus's Access email(s). Admins can do everything, including promote.
- `access.team_domain` and `access.aud`: from the Access application (step 5). Keep `require_jwt: true`, so that a forged email header from any local process gives nothing.
- `gateway.url`: `http://<BESNIK LAN IP>:11540`.
- `gateway.secret_file`: `/etc/teambox/gateway.secret`.
- Keep `dev_mode: false`. The app refuses to start in dev mode unless `TEAMBOX_ALLOW_DEV=1` is set; never set it on this box.

Copy the gateway secret over **by hand**, e.g. on a USB stick or by typing it. It is the content of `D:\Atlas\memory\team-gateway.secret` on BESNIK.
```sh
sudo install -o root -g teambox -m 0640 /dev/stdin /etc/teambox/gateway.secret    # paste, then Ctrl-D
```

## 4. BESNIK side (team gateway). Only Marjus or the Director does this, in Lumi Control / team-gateway.json:
- In `D:\Atlas\memory\team-gateway.json`, set `"use_lan": true`, set `"lan_bind": "<BESNIK LAN IP>"`, and add the team box's LAN IP to `allow_ips`. Then restart the gateway the usual way (explorer.exe).
- In Lumi Control → Router settings, add each team member's **Access email** as an account, with their caps and allowed models. The team box sends `X-Team-Account = <member's email>`.
- Share the models that the team may use.
- Admins come through the gateway too, but the gateway refuses the owner's own account by design (`admin_not_via_gateway`). To chat or build on the team box, Marjus needs a separate team account (e.g. a second email), or he uses BESNIK's own admin portal.
- Firewall: BESNIK should accept 11540 only from the team box's IP.

## 5. Cloudflare: the team box's own tunnel + Access

```sh
sudo -u cloudflared cloudflared tunnel login            # or create the tunnel in the dashboard and copy the credentials json
cloudflared tunnel create teambox
sudo cp /opt/teambox/systemd/teambox.yml.example /etc/cloudflared/teambox.yml   # fill in the tunnel UUID + credentials path
```
- **DNS:** `team.xseed.ing` currently points at **BESNIK's** tunnel (TEAM-PORTAL-REPORT). Move it only when the box is ready:
  `cloudflared tunnel route dns --overwrite-dns teambox team.xseed.ing`. Then remove the `team.xseed.ing` ingress lines from BESNIK's
  `tunnel-config.yml` and restart BESNIK's cloudflared the safe way (Director, with Marjus's OK).
- **Access applications** (Zero Trust → Access → Applications):
  1. `team.xseed.ing` (whole host): an email one-time-PIN login with an allow-list of team emails and Marjus. Copy its **AUD tag** into `access.aud`, and your `<team>.cloudflareaccess.com` into `access.team_domain`.
  2. `team.xseed.ing/git` → policy **Bypass** (everyone). Git clients can not do a browser login; on this path the per-member token is the authentication.
     (Without it, members need `cloudflared access login https://team.xseed.ing` plus a git credential helper. That is possible, but awkward.)
  3. Optional: `team.xseed.ing/how-it-works` and `team.xseed.ing/api/public/` → **Bypass**, so that a member's own AI can read the public explanation.
     `/static/` must then be bypassed as well (the page loads its CSS/JS from there).

## 6. systemd

```sh
sudo cp /opt/teambox/systemd/teambox.service /opt/teambox/systemd/cloudflared-teambox.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now teambox.service
curl -s http://127.0.0.1:8740/healthz            # {"ok": true}
sudo journalctl -u teambox -n 20                  # "team box on http://127.0.0.1:8740 (jail: ok)"  <- must say jail: ok
sudo systemctl enable --now cloudflared-teambox.service
```
`teambox.service` caps the whole app at 8 GB RAM and 2048 tasks (cgroup). On top of that, every jailed test has its own prlimit caps
(memory, processes, file size, CPU) and a wall-clock timeout from `config.json` → `jail`. Do not add `RestrictNamespaces=` or
`PrivateUsers=` to the unit, because bubblewrap needs user namespaces. `systemd_scope` (one cgroup per test via `systemd-run --user`) is
left off, because the `teambox` system user has no user manager. Turning it on would need `loginctl enable-linger teambox` plus
`XDG_RUNTIME_DIR`. This has not been tested.

## 6b. Feeds: the home feed uploads and the Tuesday 14:00 team feed

Config: `"feeds"` in `/etc/teambox/config.json` (all optional; defaults in `config.example.json`):
- `timezone` (default `America/Chicago`), `publish_weekday` (0 = Monday, so 1 = Tuesday), `publish_time` (`"14:00"`). The time is
  computed in that timezone whatever the box's system clock zone is, and follows daylight saving (14:00 CDT = 19:00 UTC, 14:00 CST = 20:00 UTC).
  The box needs the tz database: `pacman -S --needed tzdata` (normally already installed).
- `max_file_mb` (95) per file and `member_quota_mb` (1000) per member. Cloudflare refuses request bodies over 100 MB on the Free/Pro plans,
  so do not raise `max_file_mb` above ~95 behind the tunnel. Uploads are streamed to disk, never held in memory.
- Uploaded files live in `/var/lib/teambox/feed/files/` under random names (outside `/opt/teambox/static`), the team feed's read-only
  copies in `/var/lib/teambox/team-feed/`. Both are covered by the normal backup of `/var/lib/teambox`. Watch the disk:
  `du -sh /var/lib/teambox/feed`.

**The publish runs inside the app** (`"scheduler": true`, the default): a thread checks every `tick_s` seconds and once at start-up,
so if the box was off at 14:00 it publishes as soon as `teambox.service` is back (the week is marked "published late"). Each Tuesday is
recorded in `/var/lib/teambox/team-feed/publishes.json` and published once, under a thread lock and a file lock.

**Optional: also (or instead) a systemd timer.** Safe to run next to the in-app scheduler, because of the same lock and record:
```sh
sudo cp /opt/teambox/systemd/teambox-feed.service /opt/teambox/systemd/teambox-feed.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now teambox-feed.timer
systemctl list-timers teambox-feed.timer                  # next run: Tue 14:00 America/Chicago (+ a no-op check every 15 min)
sudo systemctl start teambox-feed.service && journalctl -u teambox-feed -n 5   # {"published": null, "next": "..."} when nothing is due
```
If you change `feeds.timezone`/`publish_time`, change `OnCalendar=` in the timer too (or rely on the 15-minute check). To use only the
timer, set `"scheduler": false`.

Testing it without waiting for Tuesday: an admin opens Team feed → "Publish now" (labelled "Early publish" in the feed; the next Tuesday
publishes only what is new). The test suite simulates the clock: `python3 -m unittest -v tests.test_feeds`.

## 7. Check from outside (after DNS moves)
1. In a private window, open `https://team.xseed.ing`. You should get the Access email login, then the tour on first visit.
2. Open `https://team.xseed.ing/how-it-works`. This is the public page.
3. Sign in as a member and go to Your caps. You should see the gateway's numbers. If you see "could not be reached" instead, check the LAN, the BESNIK `allow_ips` and the secret.
4. Profile → make a token, then `git clone https://team.xseed.ing/git/<id>/my-sandbox.git` (the password is the token), commit, and push. The build shows **tests pass**.
5. Home: post something with a picture and a csv; a teammate sees it, comments and reacts. Team feed: "Your work that goes out next time" lists your build.
6. Run the self-test on the box: `cd /opt/teambox && sudo -u teambox env PATH=/usr/bin python3 -m unittest tests.test_teambox tests.test_feeds`. It uses its own temporary folder and fake models, and switches dev mode on only inside its own test process.

## 8. Messages & calls (Lumi's key, the export door, TURN)

Messages and calls stay switched off (the page says why) until the box has **Lumi's public key**.

**a. Lumi's key - on BESNIK, once (Director):**
```bat
python D:\Atlas\app\lumi_comms_key.py
```
It writes `D:\Atlas\memory\lumi-comms\` (ACL: the normal user only) with `lumi-comms-private.pem` (never leaves BESNIK) and
`export.token` (never leaves BESNIK). It prints only the public key, its fingerprint (kid) and the export token's sha256, plus a
`"comms": {...}` block. It refuses to run a second time, because a new key would make every stored message unreadable for Lumi.
Back the folder up offline (USB stick in a drawer): if the private key is lost, Lumi can never read the old messages again.

**b. The box's config:** paste the printed `lumi_public_key`, `lumi_kid` and `export_token_sha256` into `comms` in
`/etc/teambox/config.json`, then `sudo systemctl restart teambox`. Publish the fingerprint to the team (for example in the Tuesday
feed): every member's Messages page shows "Lumi's key: xxxx xxxx xxxx xxxx", and the browser warns if it ever changes.

**c. The export door (BESNIK pulls ciphertext):** the review script calls `https://team.xseed.ing/export/comms` with
`Authorization: Bearer <export token>`. The box keeps only the token's sha256 and hands out ciphertext + Lumi's wrapped key +
metadata, nothing it could read. In Cloudflare Access, add an application for `team.xseed.ing/export` with a **Service Auth** policy
and create a **service token**. Put its id/secret on BESNIK in `D:\Atlas\memory\lumi-comms\cf-access.json`:
`{"client_id": "...", "client_secret": "..."}`. Then run a dry check:
```bat
python D:\Atlas\app\lumi_comms_review.py --box https://team.xseed.ing --ai1 fake-strict --ai2 fake-careful --once --inbox %TEMP%\dry-concerns.jsonl
```
That review is a **stub**: the fake AIs are keyword rules, real models are not wired in (and call recordings are not transcribed yet).
Agreed concerns go to `D:\Atlas\portal\data\watchdog-concerns.jsonl`, a new file. The live portal does not show it yet.

**d. TURN (calls across different networks):** calls are browser to browser. Two members on different home networks or mobile data
usually need a relay. Install coturn on the box, or on any small public VPS (the media stays DTLS-SRTP encrypted end to end; TURN only
relays packets it can not read):
```sh
sudo pacman -S --needed coturn
head -c 32 /dev/urandom | base64 | sudo install -o root -g teambox -m 0640 /dev/stdin /etc/teambox/turn.secret
sudo cp /etc/teambox/turn.secret /etc/turnserver/turn.secret     # coturn reads it too
sudo tee /etc/turnserver/turnserver.conf >/dev/null <<'EOF'
listening-port=3478
tls-listening-port=5349
fingerprint
use-auth-secret
static-auth-secret=PASTE-THE-SAME-SECRET
realm=turn.xseed.ing
total-quota=50
no-cli
no-multicast-peers
denied-peer-ip=10.0.0.0-10.255.255.255
denied-peer-ip=172.16.0.0-172.31.255.255
denied-peer-ip=192.168.0.0-192.168.255.255
cert=/etc/letsencrypt/live/turn.xseed.ing/fullchain.pem
pkey=/etc/letsencrypt/live/turn.xseed.ing/privkey.pem
EOF
sudo systemctl enable --now turnserver
```
TURN can not go through the Cloudflare tunnel (it is UDP/TCP media, not HTTP). It needs its own public address: forward UDP+TCP 3478,
TCP 5349 and the relay range (UDP 49152-65535) on the router, or use a VPS. Then set `comms.ice_servers` (STUN) and `comms.turn.urls` in the config.
The box hands each member short-lived TURN credentials (`comms.turn.ttl_s`, coturn's `use-auth-secret` scheme), never the secret.
The `denied-peer-ip` lines stop the relay from being used to reach the LAN (and so BESNIK). Without TURN, calls work only when both
browsers can reach each other directly (same LAN, most home routers with STUN).

**e. Limits that stay:** calls are a mesh, so 4 people at most (`comms.max_call`). Bigger group calls need an SFU (e.g. LiveKit or
mediasoup) - not built. The recording is made by the caller's browser: a modified browser could skip it. Everyone in the call then sees
"no recording has reached the team box yet", and the admin page lists the call as "NO recording received".

## Updating / backup
- Code: `cd /opt/teambox && sudo git pull && sudo systemctl restart teambox`. When it restarts, any running build is marked "interrupted".
- Data: everything is under `/var/lib/teambox` (members, sandboxes, builds, team-main, tokens-index). Back it up as one folder.
