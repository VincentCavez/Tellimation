// SHIELD login page: email + interest in exchange for free API credit.
// On success the server sets the shield_participant cookie and the home opens.

(function() {
  'use strict';

  // Same per-browser id as home.js, so earlier play stays in the analysis PDF
  var participantId = null;
  try { participantId = localStorage.getItem('home_participant'); } catch (e) {}
  if (!participantId) {
    participantId = 'free-' + Math.random().toString(16).slice(2, 10);
    try { localStorage.setItem('home_participant', participantId); } catch (e) {}
  }

  var form = document.getElementById('register-form');
  var emailInput = document.getElementById('reg-email');
  var interestInput = document.getElementById('reg-interest');
  var errorEl = document.getElementById('reg-error');
  var submitBtn = document.getElementById('reg-submit');

  function showError(msg, field) {
    errorEl.textContent = msg;
    if (field) field.focus();
  }

  form.addEventListener('submit', function(ev) {
    ev.preventDefault();
    var email = emailInput.value.trim();
    var interest = interestInput.value.trim();
    if (!email || !emailInput.checkValidity()) {
      return showError('Please enter a valid email address.', emailInput);
    }
    if (!interest) {
      return showError('Please tell us why you are interested.', interestInput);
    }
    errorEl.textContent = '';
    submitBtn.disabled = true;

    fetch('/api/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ participant: participantId, email: email, interest: interest })
    })
      .then(function(resp) {
        return resp.json().then(function(data) {
          if (!resp.ok) throw new Error(data.error || 'Something went wrong.');
          window.location.href = '/';
        });
      })
      .catch(function(err) {
        submitBtn.disabled = false;
        showError(err.message || 'Something went wrong. Please try again.');
      });
  });
})();
