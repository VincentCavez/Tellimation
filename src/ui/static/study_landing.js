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
          if (scene.thumbnail_url) {
            var card = document.createElement('div');
            card.className = 'thumbnail-card thumbnail-decorative';
            var img = document.createElement('img');
            img.src = scene.thumbnail_url;
            img.alt = scene.name || 'Training';
            img.style.width = '100%';
            img.style.height = '100%';
            img.style.objectFit = 'cover';
            img.style.borderRadius = '8px';
            card.appendChild(img);
            trainingContainer.appendChild(card);
          } else if (scene.sprite_code && Object.keys(scene.sprite_code).length > 0) {
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
          if (storyData && storyData.format === 'hd' && storyData.thumbnail_url) {
            wrapper = document.createElement('div');
            wrapper.className = 'thumbnail-card thumbnail-decorative';
            var img = document.createElement('img');
            img.src = storyData.thumbnail_url;
            img.style.width = '100%';
            img.style.height = '100%';
            img.style.objectFit = 'cover';
            img.style.borderRadius = '8px';
            wrapper.appendChild(img);
          } else if (storyData && storyData.sprite_code && Object.keys(storyData.sprite_code).length > 0) {
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

    // First visit: training not done yet → dim stories and disable Ready
    var trainingDone = completed['training'] === true;
    if (!trainingDone) {
      // Dim story slots
      var slots = document.querySelectorAll('#story-thumbnails .study-story-slot');
      for (var i = 0; i < slots.length; i++) {
        slots[i].style.opacity = '0.5';
      }
      // Disable Ready button
      btn.style.opacity = '0.2';
      btn.style.pointerEvents = 'none';
    }

    // Training done → dim training block, disable Practice button
    if (trainingDone) {
      var trainingSlots = document.querySelectorAll('#training-thumbnails .study-story-slot, #training-thumbnails .thumbnail-placeholder');
      for (var j = 0; j < trainingSlots.length; j++) {
        trainingSlots[j].style.opacity = '0.5';
      }
      var btnTraining = document.getElementById('btn-training');
      if (btnTraining) {
        btnTraining.style.opacity = '0.2';
        btnTraining.style.pointerEvents = 'none';
      }
    }
  }

  // ── Instruction system ──
  var instrShown = sessionStorage.getItem('instructions_shown') === 'true';
  var postTrainingShown = sessionStorage.getItem('post_training_shown') === 'true';
  var instrParagraphs = [];
  var instrAudio = [];
  var instrIndex = 0;
  var currentAudio = null;
  var postTrainingParagraph = '';
  var postTrainingAudioUrl = '';
  var betweenStoriesParagraph = '';
  var betweenStoriesAudioUrl = '';
  var endParagraph = '';
  var endAudioUrl = '';

  var wordTimers = [];

  function clearWordTimers() {
    for (var i = 0; i < wordTimers.length; i++) clearTimeout(wordTimers[i]);
    wordTimers = [];
  }

  var totalCharCount = 0; // total chars across all 4 instructions
  var totalAudioDuration = 0; // total audio duration across all 4 instructions
  var audioDurations = []; // per-instruction audio durations
  var durationsLoaded = 0;

  function computeCharRate() {
    if (totalAudioDuration > 0 && totalCharCount > 0) {
      return totalAudioDuration / totalCharCount; // seconds per character
    }
    return 0.05; // fallback: 50ms per char
  }

  function revealWords(slot, instrIdx) {
    clearWordTimers();
    var spans = slot.querySelectorAll('.instr-word');
    if (spans.length === 0) return;
    var secPerChar = computeCharRate();
    var elapsed = 0;
    for (var i = 0; i < spans.length; i++) {
      var charLen = Math.max(1, spans[i].textContent.trim().length);
      (function(s, delay) {
        wordTimers.push(setTimeout(function() { s.classList.add('visible'); }, delay));
      })(spans[i], elapsed * 1000);
      elapsed += charLen * secPerChar;
    }
  }

  function showInstruction(idx) {
    var slotId = 'instr-slot-' + idx;
    var slot = document.getElementById(slotId);
    if (!slot || idx >= instrParagraphs.length) return;

    // Build word spans
    var words = instrParagraphs[idx].split(/\s+/);
    slot.innerHTML = '';
    for (var i = 0; i < words.length; i++) {
      var span = document.createElement('span');
      span.className = 'instr-word';
      span.textContent = words[i] + ' ';
      slot.appendChild(span);
    }
    slot.style.display = 'block';

    // Show parent panel
    if (idx < 2) {
      document.getElementById('instruction-left').style.display = 'flex';
    } else {
      document.getElementById('instruction-right').style.display = 'flex';
    }

    // Play audio and sync word reveal
    playInstructionAudio(idx, slot);
  }

  function playInstructionAudio(idx, slot) {
    if (currentAudio) { currentAudio.pause(); currentAudio = null; }
    clearWordTimers();
    if (idx < instrAudio.length) {
      currentAudio = new Audio(instrAudio[idx]);
      currentAudio.addEventListener('canplaythrough', function() {
        revealWords(slot, idx);
      }, { once: true });
      if (currentAudio.readyState >= 4) {
        revealWords(slot, idx);
      }
      currentAudio.play().catch(function() {
        var spans = slot.querySelectorAll('.instr-word');
        for (var i = 0; i < spans.length; i++) spans[i].classList.add('visible');
      });
    }
  }

  function replayCurrentInstruction() {
    var slotId = 'instr-slot-' + instrIndex;
    var slot = document.getElementById(slotId);
    if (!slot) return;
    // Reset word visibility
    var spans = slot.querySelectorAll('.instr-word');
    for (var i = 0; i < spans.length; i++) spans[i].classList.remove('visible');
    playInstructionAudio(instrIndex, slot);
  }

  function initInstructions() {
    // Always fetch — we need the data for post-training too
    fetch('/api/study/instructions')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        postTrainingParagraph = data.post_training_paragraph || '';
        postTrainingAudioUrl = data.post_training_audio || '';
        betweenStoriesParagraph = data.between_stories_paragraph || '';
        betweenStoriesAudioUrl = data.between_stories_audio || '';
        endParagraph = data.end_paragraph || '';
        endAudioUrl = data.end_audio || '';

        // If initial instructions already shown, check for transitional messages
        if (instrShown) {
          checkPostTraining();
          checkBetweenStories();
          checkAllDone();
          return;
        }

        instrParagraphs = data.paragraphs.slice(0, 4);
        instrAudio = data.audio.slice(0, 4);
        if (instrParagraphs.length === 0) return;

        // Compute total char count across all instructions
        totalCharCount = 0;
        for (var pi = 0; pi < instrParagraphs.length; pi++) {
          totalCharCount += instrParagraphs[pi].replace(/\s+/g, '').length;
        }

        // Preload all audio to get durations, then start
        var pending = instrAudio.length;
        audioDurations = new Array(instrAudio.length);
        for (var ai = 0; ai < instrAudio.length; ai++) {
          (function(index) {
            var a = new Audio(instrAudio[index]);
            a.addEventListener('loadedmetadata', function() {
              audioDurations[index] = a.duration;
              totalAudioDuration += a.duration;
              pending--;
              if (pending === 0) startInstructions();
            });
            // Fallback timeout
            setTimeout(function() {
              if (!audioDurations[index]) {
                audioDurations[index] = 5;
                totalAudioDuration += 5;
                pending--;
                if (pending === 0) startInstructions();
              }
            }, 3000);
          })(ai);
        }

        function startInstructions() {
        // Show first instruction
        showInstruction(0);
        instrIndex = 0;

        // Hide right panel buttons until we get there
        var rightBtnRow = document.querySelector('#instruction-right .instruction-btn-row');
        if (rightBtnRow) rightBtnRow.style.display = 'none';

        updateNextButton();

        // Next buttons (one per panel)
        var nextBtns = document.querySelectorAll('.btn-next-panel');
        for (var ni = 0; ni < nextBtns.length; ni++) {
          nextBtns[ni].addEventListener('click', function() {
            instrIndex++;
            if (instrIndex < instrParagraphs.length) {
              showInstruction(instrIndex);
              updateNextButton();
              // When moving to right panel, hide left buttons, show right buttons
              if (instrIndex === 2) {
                var leftBtnRow = document.querySelector('#instruction-left .instruction-btn-row');
                if (leftBtnRow) leftBtnRow.style.display = 'none';
                if (rightBtnRow) rightBtnRow.style.display = 'flex';
              }
            } else {
              // All done, hide right buttons
              if (rightBtnRow) rightBtnRow.style.display = 'none';
              sessionStorage.setItem('instructions_shown', 'true');
            }
          });
        }

        // Replay buttons
        var replayBtns = document.querySelectorAll('.btn-replay-panel');
        for (var ri = 0; ri < replayBtns.length; ri++) {
          replayBtns[ri].addEventListener('click', function() {
            replayCurrentInstruction();
          });
        }
        } // end startInstructions
      });
  }

  function updateNextButton() {
    var btns = document.querySelectorAll('.btn-next-panel');
    for (var i = 0; i < btns.length; i++) {
      if (instrIndex >= instrParagraphs.length - 1) {
        btns[i].textContent = 'Got it!';
      } else {
        btns[i].textContent = 'Next';
      }
    }
  }

  // ── Post-training instruction (5th paragraph) ──
  function checkPostTraining() {
    if (postTrainingShown || !postTrainingParagraph) return;
    var trainingDone = completed['training'] === true;
    if (!trainingDone) return;

    showCenterMessage(postTrainingParagraph, postTrainingAudioUrl, function() {
      sessionStorage.setItem('post_training_shown', 'true');
      postTrainingShown = true;
    });
  }

  // ── Between-stories instruction (6th paragraph, shown after stories 1-3) ──
  function countCompletedStories() {
    var count = 0;
    if (!assignmentData) return count;
    for (var i = 0; i < assignmentData.order.length; i++) {
      if (completed[assignmentData.order[i]] === true) count++;
    }
    return count;
  }

  function checkBetweenStories() {
    if (!betweenStoriesParagraph) return;
    if (!postTrainingShown) return; // post-training takes priority
    var trainingDone = completed['training'] === true;
    if (!trainingDone) return;

    // Need assignmentData to count stories — retry if not loaded yet
    if (!assignmentData) {
      setTimeout(checkBetweenStories, 200);
      return;
    }

    var storiesDone = countCompletedStories();
    var lastSeenCount = parseInt(sessionStorage.getItem('stories_shown_count') || '0', 10);

    // Show between-stories message if a new story was completed (stories 1-3, not 4)
    if (storiesDone > lastSeenCount && storiesDone >= 1 && storiesDone <= 3) {
      showCenterMessage(betweenStoriesParagraph, betweenStoriesAudioUrl, function() {
        sessionStorage.setItem('stories_shown_count', String(storiesDone));
      });
    }
  }

  // ── End of study (all 4 stories done) ──
  function checkAllDone() {
    if (!endParagraph) return;
    if (!postTrainingShown) return;
    var trainingDone = completed['training'] === true;
    if (!trainingDone) return;
    if (sessionStorage.getItem('end_shown') === 'true') return;

    // Need assignmentData to count stories
    if (!assignmentData) {
      setTimeout(checkAllDone, 200);
      return;
    }

    var storiesDone = countCompletedStories();
    var totalStories = assignmentData.order ? assignmentData.order.length : 4;

    if (storiesDone >= totalStories) {
      // Triple the falling pixels
      var pixelInterval = window._pixelInterval;
      if (pixelInterval) clearInterval(pixelInterval);
      window._maxPixels = (window._maxPixels || 8) * 3;
      window._pixelInterval = setInterval(window._createPixel, 200);

      showCenterMessage(endParagraph, endAudioUrl, function() {
        sessionStorage.setItem('end_shown', 'true');
      });
    }
  }

  function showCenterMessage(text, audioUrl, onDismiss) {
    var panel = document.getElementById('instruction-center');
    var slot = document.getElementById('instr-slot-4');
    if (!panel || !slot) return;

    // Build word spans
    var words = text.split(/\s+/);
    slot.innerHTML = '';
    for (var i = 0; i < words.length; i++) {
      var span = document.createElement('span');
      span.className = 'instr-word';
      span.textContent = words[i] + ' ';
      slot.appendChild(span);
    }
    slot.style.display = 'block';
    panel.style.display = 'flex';

    // Play audio and reveal words
    function playAndReveal() {
      if (currentAudio) { currentAudio.pause(); currentAudio = null; }
      clearWordTimers();
      var spans = slot.querySelectorAll('.instr-word');
      for (var k = 0; k < spans.length; k++) spans[k].classList.remove('visible');

      if (audioUrl) {
        currentAudio = new Audio(audioUrl);
        currentAudio.addEventListener('loadedmetadata', function() {
          var dur = currentAudio.duration || 5;
          var charCount = text.replace(/\s+/g, '').length;
          var secPerChar = dur / Math.max(1, charCount);
          var elapsed = 0;
          for (var j = 0; j < spans.length; j++) {
            var charLen = Math.max(1, spans[j].textContent.trim().length);
            (function(s, delay) {
              wordTimers.push(setTimeout(function() { s.classList.add('visible'); }, delay));
            })(spans[j], elapsed * 1000);
            elapsed += charLen * secPerChar;
          }
        });
        currentAudio.play().catch(function() {
          for (var j = 0; j < spans.length; j++) spans[j].classList.add('visible');
        });
      } else {
        for (var j = 0; j < spans.length; j++) spans[j].classList.add('visible');
      }
    }

    playAndReveal();

    // Dismiss button
    var dismissBtn = document.getElementById('btn-dismiss-post-training');
    if (dismissBtn) {
      // Remove old listeners by cloning
      var newBtn = dismissBtn.cloneNode(true);
      dismissBtn.parentNode.replaceChild(newBtn, dismissBtn);
      newBtn.addEventListener('click', function() {
        if (currentAudio) { currentAudio.pause(); currentAudio = null; }
        clearWordTimers();
        panel.style.display = 'none';
        if (onDismiss) onDismiss();
      });
    }

    // Replay button
    var replayBtn = panel.querySelector('.btn-replay-panel');
    if (replayBtn) {
      var newReplay = replayBtn.cloneNode(true);
      replayBtn.parentNode.replaceChild(newReplay, replayBtn);
      newReplay.addEventListener('click', function() {
        playAndReveal();
      });
    }
  }

  initInstructions();

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
