// SHIELD home page (signed-in visitors; the server redirects others to /login)
// Every story (2 practices + A-D) is directly playable, in any order.

(function() {
  'use strict';

  // The visitor's participant id comes from their Google account (session
  // cookie); the server also derives it there for the logs and the PDF.
  var accountEl = document.getElementById('home-account');
  fetch('/api/me')
    .then(function(resp) {
      if (resp.status === 401) { window.location.href = '/login'; return null; }
      return resp.json();
    })
    .then(function(me) {
      if (!me) return;
      sessionStorage.setItem('participant_number', me.participant);
      accountEl.textContent = '';
      accountEl.append('Signed in as ');
      var who = document.createElement('b');
      who.textContent = me.email;
      accountEl.append(who, ' \u00b7 $' + me.credit_left.toFixed(2) + ' of free credit left \u00b7 ');
      var out = document.createElement('button');
      out.type = 'button';
      out.textContent = 'Sign out';
      out.addEventListener('click', function() {
        fetch('/api/logout', { method: 'POST' }).then(function() { window.location.href = '/login'; });
      });
      accountEl.appendChild(out);
    })
    .catch(function(err) { console.error('[home] /api/me', err); });

  document.getElementById('btn-download').href = '/api/analysis.pdf';

  var trainingContainer = document.getElementById('training-thumbnails');
  var storyContainer = document.getElementById('story-thumbnails');
  var completed = JSON.parse(sessionStorage.getItem('study_completed') || '{}');

  // Create an empty thumbnail placeholder element.
  function createPlaceholder() {
    var el = document.createElement('div');
    el.className = 'thumbnail-placeholder';
    return el;
  }

  // Create a thumbnail card from scene data (image URL, sprite code, or placeholder fallback).
  function createThumbnail(data) {
    if (data && data.thumbnail_url) {
      var card = document.createElement('div');
      card.className = 'thumbnail-card';
      var img = document.createElement('img');
      img.src = data.thumbnail_url;
      img.alt = data.name || '';
      img.style.width = '100%';
      img.style.height = '100%';
      img.style.objectFit = 'cover';
      img.style.borderRadius = '8px';
      card.appendChild(img);
      return card;
    }
    if (data && data.sprite_code && Object.keys(data.sprite_code).length > 0) {
      return ScenePicker.createThumbnailCard(data);
    }
    return createPlaceholder();
  }

  // One clickable slot: thumbnail + name (+ "Done" once finished this session)
  function createSlot(data, doneKey, buildUrl) {
    var slot = document.createElement('div');
    slot.className = 'study-story-slot home-slot';
    slot.appendChild(createThumbnail(data));

    var label = document.createElement('div');
    label.className = 'thumbnail-label';
    label.textContent = data.name;
    slot.appendChild(label);

    if (doneKey && completed[doneKey] === true) {
      var check = document.createElement('div');
      check.className = 'study-story-check';
      check.textContent = 'Done';
      slot.appendChild(check);
    }

    slot.addEventListener('click', function() {
      window.location.href = buildUrl() + '&animated=true'
        + '&name=' + encodeURIComponent(data.name) + '&from=home';
    });
    return slot;
  }

  fetch('/api/study/catalog')
    .then(function(resp) {
      if (!resp.ok) throw new Error('Failed to load catalog');
      return resp.json();
    })
    .then(function(data) {
      // Practices: each is one scene of the "training" story (scene N = practice N)
      (data.training_scenes || []).forEach(function(scene, idx) {
        var n = idx + 1;
        // Both practices share the "training" completion key, so no Done marker here
        trainingContainer.appendChild(createSlot(scene, null, function() {
          return '/study/story?story=training&start=' + n + '&scenes=' + n;
        }));
      });

      ['A', 'B', 'C', 'D'].forEach(function(label) {
        var story = (data.stories || {})[label];
        if (!story) return;
        storyContainer.appendChild(createSlot(story, label, function() {
          return '/study/story?story=' + label + '&scenes=' + (story.scene_count || 5);
        }));
      });
    })
    .catch(function(err) {
      console.error('[home]', err);
      for (var i = 0; i < 2; i++) trainingContainer.appendChild(createPlaceholder());
      for (var j = 0; j < 4; j++) storyContainer.appendChild(createPlaceholder());
    });
})();
