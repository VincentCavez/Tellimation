(function() {
      const apiKey = sessionStorage.getItem('api_key');
      const participantId = sessionStorage.getItem('participant_id');
      const childAge = sessionStorage.getItem('child_age') || '8';
      if (!apiKey || !participantId) {
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

      function updateProgress(sceneIndex, totalScenes, stepName) {
        sceneProgressMap[sceneIndex] = stepWeights[stepName] || 0;
        var total = 0;
        for (var key in sceneProgressMap) total += sceneProgressMap[key];
        var overallPct = Math.min(total / totalScenes, 99);
        progressBar.style.width = overallPct + '%';
        progressLabel.textContent = stepLabels[stepName] || 'Working...';
      }

      function showProgress() {
        progressContainer.style.display = '';
        container.style.display = 'none';
        progressBar.style.width = '2%';
        progressLabel.textContent = 'Preparing your stories...';
      }

      function hideProgress() {
        progressContainer.style.display = 'none';
        container.style.display = '';
      }

      // Connect WebSocket
      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const ws = new WebSocket(
        protocol + '//' + location.host + '/ws'
        + '?api_key=' + encodeURIComponent(apiKey)
        + '&participant_id=' + encodeURIComponent(participantId)
        + '&child_age=' + encodeURIComponent(childAge)
      );

      ws.onopen = function() {
        showProgress();
        ws.send(JSON.stringify({ type: 'generate_initial_scenes' }));
      };

      ws.onmessage = function(event) {
        if (event.data instanceof ArrayBuffer || event.data instanceof Blob) return;
        var msg = JSON.parse(event.data);

        if (msg.type === 'generation_progress') {
          updateProgress(msg.scene_index, msg.total_scenes, 'manifest');
        }

        if (msg.type === 'generation_step') {
          updateProgress(msg.scene_index, msg.total_scenes, msg.step);
        }

        if (msg.type === 'scene_ready') {
          // Scene ready — show thumbnail and make it clickable immediately
          branches.push(msg.scene);
          hideProgress();
          appendThumbnail(msg.scene, branches.length - 1);
        }

        if (msg.type === 'initial_scenes_done') {
          hideProgress();
          btnOneMore.style.display = '';
        }

        if (msg.type === 'initial_scenes') {
          // Backward compat: bulk delivery (from-disk case)
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
      };

      ws.onerror = function() {
        hideProgress();
        container.style.display = '';
        container.innerHTML = '<p style="color:var(--red)">Connection error. Please reload.</p>';
      };

      function appendThumbnail(scene, index) {
        var card = ScenePicker.createThumbnailCard(scene);
        card.addEventListener('click', function() { selectScene(index); });
        cardElements[index] = card;
        container.appendChild(card);
      }

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
        ws.send(JSON.stringify({ type: 'select_scene', index: index }));
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
