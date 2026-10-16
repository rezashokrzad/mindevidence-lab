# Google Sign-In Setup

MindEvidence Lab uses **Google as its only identity provider**. The flow is the server-side OAuth 2.0 authorization-code flow with OpenID Connect, PKCE, `state`, and `nonce`. The browser never sees the client secret or any Google token. The backend verifies the ID token and sets its own HttpOnly session cookie.

Only the identity scopes **`openid email profile`** are requested. No Gmail or other Google API access is requested.

## 1. Create the OAuth client in Google Cloud

1. Open https://console.cloud.google.com/ and create a project (or pick one), e.g. *MindEvidence Lab*.
2. Go to **APIs & Services → OAuth consent screen** (in newer consoles: **Google Auth Platform → Branding / Audience / Data access**).
   - **User type / Audience:**
     - *Internal* if everyone signs in with your organization's Google Workspace accounts.
     - Otherwise *External*. While the app is in **Testing** status, add each student as a **Test user**. Only test users can sign in, and Google allows up to 100.
   - **App name, support email, developer contact:** fill in.
   - **Scopes (Data access):** add only `openid`, `.../auth/userinfo.email`, and `.../auth/userinfo.profile`. These are non-sensitive, so no Google verification review is needed.
3. Go to **APIs & Services → Credentials → Create credentials → OAuth client ID**.
   - **Application type:** *Web application*.
   - **Authorized JavaScript origins:** *leave empty.* This field is only used when browser JavaScript talks to Google directly. In this app the browser goes through the backend, so nothing is needed here. Adding `http://localhost:5173` is harmless but not required.
   - **Authorized redirect URIs:** add exactly:
     ```
     http://localhost:8000/api/auth/callback
     ```
     It must match `GOOGLE_REDIRECT_URI` character for character: same scheme, host, and port, and no trailing slash. `localhost` and `127.0.0.1` count as different hosts.
4. Click **Create**, then copy the **Client ID** and **Client secret**.

## 2. Configure the backend

In `backend/.env` (git-ignored; see `backend/.env.example`):

```dotenv
GOOGLE_CLIENT_ID=1234567890-abc.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=<paste from Google Cloud; never commit, never put in frontend code>
GOOGLE_REDIRECT_URI=http://localhost:8000/api/auth/callback
SESSION_SECRET=<random, at least 32 characters>
COOKIE_SECURE=false
FRONTEND_URL=http://localhost:5173/
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

Generate `SESSION_SECRET` with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Optional access control:

| Variable | Effect |
| --- | --- |
| `ALLOWED_EMAIL_DOMAINS=university.edu` | Only verified emails in these domains may sign in |
| `ALLOWED_EMAILS=ta@gmail.com,...` | Explicit addresses (case-insensitive) |
| `INSTRUCTOR_GOOGLE_SUBS=1098...` | These Google subject ids get the `instructor` role |

With both allowlists empty, any Google account with a verified email can sign in. The role is recalculated at each sign-in. Roles are assigned by Google `sub` (stable), never by email.

To find someone's `sub`, have them sign in once, then run:

```bash
psql -h localhost -U mindevidence -d mindevidence -c "SELECT google_sub, email FROM auth.users"
```

Run migrations (`0003_auth_users` adds the user tables) and start the API, from `backend/`:

```bash
.venv/Scripts/alembic upgrade head
```
```bash
.venv/Scripts/python -m uvicorn app.main:create_app --factory --reload --port 8000
```

`GET http://localhost:8000/api/health` should report `"sign_in": "google"`.

## 3. Sign in locally

1. Serve the frontend on **port 5173** and open **http://localhost:5173**. Use `localhost`, not `127.0.0.1`: the API is at `localhost:8000`, and keeping the same host name keeps the session cookie same-site.
2. Click **Sign in with Google**, choose an account (a test user if the app is in Testing), and approve.
3. You are returned to the app, signed in. **Sign out** revokes the session on the server.

### Troubleshooting

| Symptom | Cause |
| --- | --- |
| Google shows `redirect_uri_mismatch` | The URI in Google Cloud ≠ `GOOGLE_REDIRECT_URI` (check port, host, trailing slash) |
| Google shows "access blocked … not completed verification" or "app is in testing" | Add the account as a Test user, or publish the app |
| Banner: "sign-in attempt expired or did not start on this page" (`invalid_state`) | You took over 10 minutes, used a different host (`127.0.0.1` vs `localhost`), or opened an old callback URL |
| Banner: "not authorized" | The email is outside `ALLOWED_EMAIL_DOMAINS` / `ALLOWED_EMAILS` |
| Banner: "not configured on the server" | `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` are empty |
| Signed in, but every API call returns 401 | Frontend and API on different hosts (`127.0.0.1` vs `localhost`), so the cookie isn't sent |

## 4. Production requirements (HTTPS)

The backend **refuses to start** with `APP_ENV=production` unless all of the following hold:

- `COOKIE_SECURE=true`. The session cookie becomes `__Host-me_session`: Secure, HttpOnly, SameSite=Lax, `Path=/`, and no Domain.
- `GOOGLE_REDIRECT_URI`, `FRONTEND_URL`, and every `CORS_ORIGINS` entry use `https://`.
- `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are set.
- `SESSION_SECRET` is at least 32 characters and not a placeholder. This applies in every environment.

Also:

1. **Serve over HTTPS only**, with TLS terminated at a reverse proxy and HTTP redirected to HTTPS. Google only accepts `https://` redirect URIs for non-localhost hosts.
2. **Add the production redirect URI** in Google Cloud, e.g. `https://lab.example.edu/api/auth/callback`, and set `GOOGLE_REDIRECT_URI` to match.
3. **Serve the frontend and API from the same site**, ideally the same origin (`https://lab.example.edu/` and `https://lab.example.edu/api/`), so the SameSite=Lax cookie is sent with API requests.
4. **Restrict sign-in** with `ALLOWED_EMAIL_DOMAINS` or `ALLOWED_EMAILS`, or make the consent screen *Internal* for a Workspace domain.
5. **Keep secrets in the deployment's secret store** (environment variables), never in the repository or the frontend. Rotating `SESSION_SECRET` signs everyone out. Rotate the Google client secret in Google Cloud if it is exposed.
6. Behind a proxy, run uvicorn with `--proxy-headers` and `--forwarded-allow-ips` set to the proxy, so request scheme and host are correct.

## What is stored

`auth.users` stores:
- `google_sub`, the stable identity;
- `email`, `email_verified`, and `display_name`;
- `role` (`student` or `instructor`), `is_active`, `created_at`, `updated_at`, and `last_login_at`.

`auth.login_sessions` stores:
- an HMAC of the session token (never the token itself) and a per-session CSRF token;
- `created_at`, `expires_at` (`SESSION_TTL_HOURS`, default 12), `last_seen_at`, and `revoked_at`.

Google access tokens are discarded immediately, and no refresh token is requested (`access_type=online`).

## What has and has not been tested

- **Tested automatically (`backend/tests/test_auth*.py`):**
  - The full flow runs against an offline fake of Google's token and certificate endpoints. ID tokens are real RS256 JWTs, verified by Google's `google-auth` library.
  - These are covered: state, nonce, and PKCE; signature, issuer, audience, expiry, and nonce rejection; identity by `sub`; allowlists; roles; cookie flags (development and production); CSRF; Origin checks; expiry, revocation, and sign-out; protection of every route; and ownership.
- **Tested manually in a browser:** the signed-out state, the redirect URL to Google, the signed-in UI with a backend-created session, and sign-out (CSRF-protected POST, server-side revocation, replayed cookie refused).
- **Not tested:** a real round trip through Google's consent screen. That needs your Google Cloud client credentials. Do it once after step 3 above.


## Optional: Gmail summaries (second redirect URI)

Only needed if you set `EMAIL_TOKEN_ENCRYPTION_KEY` and want the **Email reports** feature.

1. In Google Cloud, enable the **Gmail API** for the project.
2. On the OAuth consent screen, add the scope `https://www.googleapis.com/auth/gmail.send`. While the app is in *Testing*, add each user as a test user. This is a sensitive scope, so a public app would need Google verification.
3. On the web client, add a second authorized redirect URI: `http://localhost:8000/api/reports/email/callback` (it must equal `GMAIL_REDIRECT_URI`).
4. Generate a key and put it in `backend/.env`: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

**Not tested:** a real Gmail send. The flow, encryption, revocation and limits are tested against offline fakes of Google's endpoints.
