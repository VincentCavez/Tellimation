(function() {
      const participantId = sessionStorage.getItem('participant_id');
      const childAge = sessionStorage.getItem('child_age') || '8';
      if (!participantId) {
        window.location.href = '/';
        return;
      }

      const container = document.getElementById('thumbnails');
      const btnOneMore = document.getElementById('btn-one-more');
      const selTitle = document.getElementById('sel-title');
      const selSub = document.getElementById('sel-sub');
      const progressContainer = document.getElementById('progress-container');
      const progressBar = document.getElementById('progress-bar');
      const progressLabel = document.getElementById('progress-label');
      var branches = [];
      var cardElements = {};
      var pendingSelectIndex = null;

      // Step weights for progress calculation
      var stepWeights = {
        starting: 5,
        manifest: 20,
        images: 45,
        masks: 85,
        assembly: 95
      };
      var stepLabels = {
        starting: 'Getting creative...',
        manifest: 'Imagining a story...',
        images: 'Drawing the scene...',
        masks: 'Adding details...',
        assembly: 'Almost ready...'
      };
      var sceneProgressMap = {};

      // Record this scene's generation step weight and update the overall progress bar and label.
      function updateProgress(sceneIndex, totalScenes, stepName) {
        sceneProgressMap[sceneIndex] = stepWeights[stepName] || 0;
        var total = 0;
        for (var key in sceneProgressMap) total += sceneProgressMap[key];
        var overallPct = Math.min(total / totalScenes, 99);
        progressBar.style.width = overallPct + '%';
        progressLabel.textContent = stepLabels[stepName] || 'Working...';
      }

      // Show the generation progress bar and hide the scene cards.
      function showProgress() {
        progressContainer.style.display = '';
        container.style.display = 'none';
        progressBar.style.width = '2%';
        progressLabel.textContent = 'Preparing your stories...';
      }

      // Hide the progress bar and show the scene cards again.
      function hideProgress() {
        progressContainer.style.display = 'none';
        container.style.display = '';
      }

      // Connect WebSocket
      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      var ws = null;
      var defaultScenesLoaded = false;

      // Open the WebSocket to the server with the participant id and child age.
      function connectWs() {
        ws = new WebSocket(
          protocol + '//' + location.host + '/ws'
          + '?participant_id=' + encodeURIComponent(participantId)
          + '&child_age=' + encodeURIComponent(childAge)
        );
        ws.onopen = function() {
          if (!defaultScenesLoaded) {
            showProgress();
            ws.send(JSON.stringify({ type: 'generate_initial_scenes' }));
          }
        };
        ws.onmessage = onWsMessage;
        ws.onerror = onWsError;
      }

      // Try loading pre-generated default scenes first
      fetch('/api/default-scenes')
        .then(function(res) {
          if (res.ok) return res.json();
          throw new Error('no defaults');
        })
        .then(function(scenes) {
          if (Array.isArray(scenes) && scenes.length > 0) {
            defaultScenesLoaded = true;
            branches = scenes;
            hideProgress();
            container.innerHTML = '';
            cardElements = {};
            branches.forEach(function(scene, i) { appendThumbnail(scene, i); });
            // Still connect WS for select_scene
            connectWs();
          } else {
            connectWs();
          }
        })
        .catch(function() {
          connectWs();
        });

      // Handle a server WebSocket message (e.g. generation progress, generated scenes); binary frames are ignored.
      function onWsMessage(event) {
        if (event.data instanceof ArrayBuffer || event.data instanceof Blob) return;
        var msg = JSON.parse(event.data);

        if (msg.type === 'generation_progress') {
          updateProgress(msg.scene_index, msg.total_scenes, 'manifest');
        }

        if (msg.type === 'generation_step') {
          updateProgress(msg.scene_index, msg.total_scenes, msg.step);
        }

        if (msg.type === 'scene_ready') {
          branches.push(msg.scene);
          hideProgress();
          appendThumbnail(msg.scene, branches.length - 1);
        }

        if (msg.type === 'initial_scenes_done') {
          hideProgress();
          btnOneMore.style.display = '';
        }

        if (msg.type === 'initial_scenes') {
          branches = msg.scenes;
          var isDisk = !!msg.from_disk;
          if (isDisk) {
            selTitle.innerHTML = 'Welcome back! <span class="accent">Continue</span> a story';
            selSub.textContent = 'Pick a previous story to continue, or start a new one';
          }
          hideProgress();
          container.innerHTML = '';
          cardElements = {};
          branches.forEach(function(scene, i) { appendThumbnail(scene, i); });
          btnOneMore.style.display = isDisk ? 'none' : '';
        }

        if (msg.type === 'one_more_scene') {
          branches.push(msg.scene);
          var newIdx = msg.index !== undefined ? msg.index : branches.length - 1;
          appendThumbnail(msg.scene, newIdx);
        }

        if (msg.type === 'scene_selected_ready') {
          sessionStorage.setItem('chosen_scene', JSON.stringify(msg.scene));
          sessionStorage.setItem('scene_index', String(pendingSelectIndex || 0));
          window.location.href = '/story';
        }

        if (msg.type === 'error') {
          console.error('Server error:', msg.message);
          hideProgress();
          if (pendingSelectIndex !== null) {
            var card = cardElements[pendingSelectIndex];
            if (card) {
              card.style.opacity = '';
              card.style.pointerEvents = '';
            }
            pendingSelectIndex = null;
          }
          container.style.display = '';
          container.innerHTML = '<p style="color:var(--red)">' + msg.message + '</p>';
        }
      }

      // Show a reload prompt when the WebSocket connection fails.
      function onWsError() {
        hideProgress();
        container.style.display = '';
        container.innerHTML = '<p style="color:var(--red)">Connection error. Please reload.</p>';
      }

      // Add a clickable thumbnail card for a scene that selects it on click.
      function appendThumbnail(scene, index) {
        var card = ScenePicker.createThumbnailCard(scene);
        card.addEventListener('click', function() { selectScene(index); });
        cardElements[index] = card;
        container.appendChild(card);
      }

      // Mark the scene as chosen (once), store its story index and send the selection to the server.
      function selectScene(index) {
        if (pendingSelectIndex !== null) return;
        pendingSelectIndex = index;
        var scene = branches[index];
        if (scene._story_index) {
          sessionStorage.setItem('story_index', String(scene._story_index));
        } else {
          sessionStorage.removeItem('story_index');
        }
        var card = cardElements[index];
        if (card) {
          card.style.opacity = '0.6';
          card.style.pointerEvents = 'none';
        }
        // Send scene data along for default-scenes case (server may not have it)
        var msg = { type: 'select_scene', index: index };
        if (defaultScenesLoaded) {
          msg.scene = scene;
        }
        ws.send(JSON.stringify(msg));
      }

      btnOneMore.addEventListener('click', function() {
        btnOneMore.disabled = true;
        btnOneMore.textContent = 'Generating...';
        ws.send(JSON.stringify({ type: 'generate_one_more' }));
        var origHandler = ws.onmessage;
        ws.onmessage = function(event) {
          origHandler(event);
          if (event.data instanceof ArrayBuffer || event.data instanceof Blob) return;
          var m = JSON.parse(event.data);
          if (m.type === 'one_more_scene' || m.type === 'error') {
            btnOneMore.disabled = false;
            btnOneMore.textContent = '+ I want to see one more';
          }
        };
      });
    })();
