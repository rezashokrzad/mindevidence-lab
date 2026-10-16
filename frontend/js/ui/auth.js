// Header sign-in state and the sign-in result banner.
// The UI never sees tokens or secrets: the session is an HttpOnly cookie set by the backend.

const AUTH_ERROR_MESSAGES = {
  access_denied: 'Sign-in was cancelled.',
  invalid_state: 'The sign-in attempt expired or did not start on this page. Please try again.',
  invalid_request: 'Google returned an incomplete response. Please try again.',
  token_exchange_failed: 'Google sign-in could not be completed. Please try again.',
  invalid_id_token: 'Google\'s response could not be verified, so you were not signed in.',
  email_not_verified: 'Your Google account email address is not verified.',
  not_authorized: 'This Google account is not authorized to use MindEvidence Lab. Ask your instructor for access.',
  account_disabled: 'This account has been disabled. Contact your instructor.',
  auth_not_configured: 'Google sign-in is not configured on the server yet (see docs/google-oauth-setup.md).',
  google_error: 'Google reported an error during sign-in. Please try again.',
};

export function authErrorMessage(code) {
  return AUTH_ERROR_MESSAGES[code] ?? 'Sign-in failed. Please try again.';
}

function initials(name, email) {
  const source = (name || email || '?').trim();
  const parts = source.split(/\s+/).filter(Boolean);
  return (parts.length > 1 ? parts[0][0] + parts.at(-1)[0] : source.slice(0, 2)).toUpperCase();
}

export function initAuthUi({ store, actions }) {
  const area = document.getElementById('auth-area');
  const checking = document.getElementById('auth-checking');
  const signInBtn = document.getElementById('google-signin');
  const chip = document.getElementById('user-chip');
  const banner = document.getElementById('auth-banner');
  const bannerText = document.getElementById('auth-banner-text');

  signInBtn.addEventListener('click', () => actions.signIn());
  document.getElementById('signout-btn').addEventListener('click', () => actions.signOut());
  document.getElementById('auth-banner-close').addEventListener('click', () => store.set({ authNotice: null }));

  store.watch((s) => [s.auth], (s) => {
    const { status, user, signInAvailable } = s.auth;
    area.dataset.state = status;
    checking.hidden = status !== 'checking';
    signInBtn.hidden = status !== 'signed_out';
    signInBtn.disabled = !signInAvailable;
    signInBtn.title = signInAvailable ? '' : 'Google sign-in is not configured on the server';
    chip.hidden = status !== 'signed_in';
    if (status === 'unavailable') {
      checking.hidden = false;
      checking.textContent = 'Sign-in unavailable (API unreachable)';
    } else {
      checking.textContent = 'Checking sign-in…';
    }
    if (status === 'signed_in' && user) {
      document.getElementById('user-initials').textContent = initials(user.display_name, user.email);
      document.getElementById('user-name').textContent = user.display_name || user.email;
      document.getElementById('user-email').textContent = user.email;
      document.getElementById('user-role').textContent = user.role;
    }
  });

  store.watch((s) => [s.authNotice], (s) => {
    banner.hidden = !s.authNotice;
    if (s.authNotice) {
      bannerText.textContent = s.authNotice.text;
      banner.classList.toggle('info', s.authNotice.kind === 'info');
    }
  });
}
