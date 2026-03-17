// Tellimations Study Landing Page
// Fetches assignment data, renders decorative thumbnails, handles navigation via buttons.

(function() {
  'use strict';

  var participantNumber = sessionStorage.getItem('participant_number');
  if (!participantNumber) {
    window.location.href = '/study';
    return;
  }

  var trainingContainer = document.getElementById('training-thumbnails');
  var storyContainer = document.getElementById('story-thumbnails');
  var completed = JSON.parse(sessionStorage.getItem('study_completed') || '{}');

  function createPlaceholder() {
    var el = document.createElement('div');
    el.className = 'thumbnail-placeholder';
    var inner = document.createElement('div');
    inner.className = 'placeholder-inner';
    el.appendChild(inner);
    return el;
  }

  var assignmentData = null;

  fetch('/api/study/assignment?participant=' + encodeURIComponent(participantNumber))
    .then(function(resp) {
      if (!resp.ok) throw new Error('Failed to load assignment');
      return resp.json();
    })
    .then(function(data) {
      assignmentData = data;

      // Render training thumbnails (decorative only)
      var trainingRendered = 0;
      if (data.training_scenes) {
        data.training_scenes.forEach(function(scene) {
          if (scene.sprite_code && Object.keys(scene.sprite_code).length > 0) {
            var card = ScenePicker.createThumbnailCard(scene);
            card.classList.add('thumbnail-decorative');
            trainingContainer.appendChild(card);
          } else {
            trainingContainer.appendChild(createPlaceholder());
          }
          trainingRendered++;
        });
      }
      for (var i = trainingRendered; i < 2; i++) {
        trainingContainer.appendChild(createPlaceholder());
      }

      // Render story thumbnails in assigned order (decorative, NOT clickable)
      if (data.order && data.stories) {
        data.order.forEach(function(storyLabel) {
          var storyData = data.stories[storyLabel];
          var isComplete = completed[storyLabel] === true;

          // Thumbnail or placeholder
          var wrapper;
          if (storyData && storyData.sprite_code && Object.keys(storyData.sprite_code).length > 0) {
            wrapper = ScenePicker.createThumbnailCard(storyData);
            wrapper.classList.add('thumbnail-decorative');
          } else {
            wrapper = createPlaceholder();
          }

          // Wrap in a container for label + status
          var container = document.createElement('div');
          container.className = 'study-story-slot';
          if (isComplete) container.classList.add('completed');
          container.appendChild(wrapper);

          // Story name label
          var label = document.createElement('div');
          label.className = 'thumbnail-label';
          label.textContent = (storyData && storyData.name) || ('Story ' + storyLabel);
          container.appendChild(label);

          // Done indicator
          if (isComplete) {
            var check = document.createElement('div');
            check.className = 'study-story-check';
            check.textContent = 'Done';
            container.appendChild(check);
          }

          storyContainer.appendChild(container);
        });
      }

      updateReadyButton();
    })
    .catch(function(err) {
      console.error('[study_landing]', err);
      for (var i = 0; i < 2; i++) trainingContainer.appendChild(createPlaceholder());
      for (var j = 0; j < 4; j++) storyContainer.appendChild(createPlaceholder());
    });

  function getNextStory() {
    if (!assignmentData) return null;
    for (var i = 0; i < assignmentData.order.length; i++) {
      var label = assignmentData.order[i];
      if (!completed[label]) return label;
    }
    return null;
  }

  function navigateToStory(label) {
    if (!assignmentData) return;
    var storyData = assignmentData.stories[label];
    var isAnimated = storyData && storyData.animated;
    var sc = (storyData && storyData.scene_count) || 5;
    var name = encodeURIComponent((storyData && storyData.name) || ('Story ' + label));
    window.location.href = '/study/story?story=' + label
      + '&animated=' + (isAnimated ? 'true' : 'false')
      + '&scenes=' + sc
      + '&name=' + name;
  }

  function updateReadyButton() {
    var btn = document.getElementById('btn-ready');
    var next = getNextStory();
    if (!next) {
      btn.textContent = 'All Done!';
      btn.classList.add('btn-disabled');
    }
  }

  // Training button
  document.getElementById('btn-training').addEventListener('click', function() {
    window.location.href = '/study/story?story=training&animated=true&scenes=2&name=Training';
  });

  // Ready button — start next incomplete story in order
  document.getElementById('btn-ready').addEventListener('click', function() {
    var next = getNextStory();
    if (next) navigateToStory(next);
  });
})();
