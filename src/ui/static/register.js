// SHIELD login page: Sign in with Google + interest, in exchange for free API credit.
// The server verifies the Google token, registers first-time visitors (who must
// say why they are interested) and sets the session cookie; then the home opens.

(function() {
  'use strict';

  var form = document.getElementById('register-form');
  var interestInput = document.getElementById('reg-interest');
  var errorEl = document.getElementById('reg-error');
  var googleEl = document.getElementById('reg-google');
  var continueBtn = document.getElementById('reg-continue');
  var clientId = form.getAttribute('data-client-id');

  // Google token kept so a first-time visitor who forgot the interest field
  // can fill it and continue without signing in again (it is valid 1 hour)
  var credential = null;

  // Display msg in the error element (empty string clears it).
  function showError(msg) { errorEl.textContent = msg; }

  // Send the Google credential and interest to the server; redirect to the home page on success, otherwise show the error.
  function submit() {
    showError('');
    continueBtn.disabled = true;
    fetch('/api/auth/google', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ credential: credential, interest: interestInput.value.trim() })
    })
      .then(function(resp) {
        return resp.json().then(function(data) {
          if (resp.ok) { window.location.href = '/'; return; }
          continueBtn.disabled = false;
          if (data.need_interest) {
            continueBtn.hidden = false;
            interestInput.focus();
          }
          showError(data.error || 'Something went wrong. Please try again.');
        });
      })
      .catch(function() {
        continueBtn.disabled = false;
        showError('Something went wrong. Please try again.');
      });
  }

  continueBtn.addEventListener('click', function() {
    if (!interestInput.value.trim()) {
      showError('Please tell us why you are interested in SHIELD.');
      interestInput.focus();
      return;
    }
    submit();
  });

  // Called by the Google Identity Services script once it has loaded
  window.onGoogleLibraryLoad = function() {
    if (!clientId || clientId.indexOf('{{') === 0) {
      showError('Sign-in is not available yet. Please come back later.');
      return;
    }
    google.accounts.id.initialize({
      client_id: clientId,
      callback: function(resp) { credential = resp.credential; submit(); }
    });
    google.accounts.id.renderButton(googleEl, {
      theme: 'filled_blue', size: 'large', shape: 'pill', text: 'continue_with', locale: 'en'
    });
  };
})();
