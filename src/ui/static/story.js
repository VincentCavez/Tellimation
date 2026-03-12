    (function() {
      const participantId = sessionStorage.getItem('participant_id');
      const childAge = sessionStorage.getItem('child_age') || '8';
      if (!participantId) {
        window.location.href = '/';
        return;
      }

      var sceneNumber = 1;

      // -- Engine setup --
      const canvas = document.getElementById('scene-canvas');
      const buf = new PixelBuffer(PW, PH);
      const renderer = new Renderer(canvas, buf);
      const animRunner = new AnimationRunner(buf, renderer);

      // -- Track current scene for drag-and-drop --
      var currentSceneRef = null;

      // -- Load initial scene from sessionStorage --
      const chosenScene = JSON.parse(sessionStorage.getItem('chosen_scene') || 'null');
      if (chosenScene) {
        renderScene(chosenScene);
      }

      function renderScene(scene) {
        currentSceneRef = scene;
        buf.clear();

        var spriteCode = scene.sprite_code || {};
        var bgEntry = spriteCode.bg || null;

        // Collect non-bg entity IDs
        var entityEids = [];
        for (var eid in spriteCode) {
          if (spriteCode.hasOwnProperty(eid) && eid !== 'bg') {
            entityEids.push(eid);
          }
        }

        // Sort by depth_order (ascending) so back entities draw first, front entities on top
        var entities = (scene.manifest && scene.manifest.entities) || [];
        var depthMap = {};
        for (var di = 0; di < entities.length; di++) {
          var de = entities[di];
          depthMap[de.id] = (de.position && de.position.depth_order != null) ? de.position.depth_order : 0;
        }
        entityEids.sort(function(a, b) { return (depthMap[a] || 0) - (depthMap[b] || 0); });

        function renderEntitiesAndFlush() {
          buf.snapshotBackground();

          var N = buf.data.length;
          var preR = new Array(N);
          var preG = new Array(N);
          var preB = new Array(N);
          var preE = new Array(N);

          for (var i = 0; i < entityEids.length; i++) {
            var prefix = entityEids[i];

            for (var j = 0; j < N; j++) {
              preR[j] = buf.data[j].r;
              preG[j] = buf.data[j].g;
              preB[j] = buf.data[j].b;
              preE[j] = buf.data[j].e;
            }

            try {
              renderSpriteEntry(prefix, spriteCode[prefix], buf);
            } catch (e) {
              console.warn('[renderScene] Failed to render', prefix, e);
            }

            buf.saveEntityLayer(prefix, preR, preG, preB, preE);
          }
          // Pre-compute distance fields for all entities (used by animations)
          buf.computeDistanceFields(20);
          // Render temporary sprites on top of normal entities
          renderTempSprites(buf);
          renderer.render();
        }

        // Render background first (may be async for image_background format)
        if (bgEntry && bgEntry.format === 'image_background') {
          var p = executeImageBackground(bgEntry, buf);
          if (p && typeof p.then === 'function') {
            p.then(renderEntitiesAndFlush);
          } else {
            renderEntitiesAndFlush();
          }
        } else {
          if (bgEntry) {
            try { renderSpriteEntry('bg', bgEntry, buf); } catch (e) {
              console.warn('[renderScene] Failed to render bg', e);
            }
          }
          renderEntitiesAndFlush();
        }
      }

      // -- Entity Drag-and-Drop --
      var drag = { active: false, entityId: null, offsetX: 0, offsetY: 0 };

      function canvasToArt(e) {
        var rect = canvas.getBoundingClientRect();
        return {
          x: Math.floor((e.clientX - rect.left) / rect.width * PW),
          y: Math.floor((e.clientY - rect.top) / rect.height * PH),
        };
      }

      function hitTestEntity(artX, artY) {
        if (artX < 0 || artX >= PW || artY < 0 || artY >= PH) return null;
        var e = buf.data[artY * PW + artX].e;
        if (!e || e.startsWith('bg')) return null;
        var dot = e.indexOf('.');
        return dot >= 0 ? e.substring(0, dot) : e;
      }

      function moveEntityPixels(entityId, dx, dy) {
        var layer = buf.entityLayers[entityId];
        if (!layer || layer.length === 0) return;
        dx = Math.round(dx);
        dy = Math.round(dy);
        if (dx === 0 && dy === 0) return;

        // 1. Erase old pixels (restore behind-colors)
        for (var i = 0; i < layer.length; i++) {
          var idx = layer[i].idx;
          buf.data[idx].r = layer[i].br;
          buf.data[idx].g = layer[i].bg;
          buf.data[idx].b = layer[i].bb;
          buf.data[idx].e = '';
        }

        // 2. Compute shifted layer
        var newLayer = [];
        for (var i = 0; i < layer.length; i++) {
          var oldIdx = layer[i].idx;
          var ox = oldIdx % PW;
          var oy = Math.floor(oldIdx / PW);
          var nx = ox + dx;
          var ny = oy + dy;
          if (nx >= 0 && nx < PW && ny >= 0 && ny < PH) {
            var newIdx = ny * PW + nx;
            newLayer.push({
              idx: newIdx,
              r: layer[i].r,
              g: layer[i].g,
              b: layer[i].b,
              e: layer[i].e,
              br: buf.data[newIdx].r,
              bg: buf.data[newIdx].g,
              bb: buf.data[newIdx].b,
            });
          }
        }

        // 3. Write shifted pixels
        for (var i = 0; i < newLayer.length; i++) {
          var idx = newLayer[i].idx;
          buf.data[idx].r = newLayer[i].r;
          buf.data[idx].g = newLayer[i].g;
          buf.data[idx].b = newLayer[i].b;
          buf.data[idx].e = newLayer[i].e;
        }

        buf.entityLayers[entityId] = newLayer;
        renderer.render();
      }

      canvas.addEventListener('mousedown', function(e) {
        if (NarrationClient.isRecording && NarrationClient.isRecording()) return;
        var art = canvasToArt(e);
        var eid = hitTestEntity(art.x, art.y);
        if (!eid) return;

        var bounds = buf.getEntityBounds(eid);
        if (!bounds) return;

        var centerX = (bounds.x1 + bounds.x2) / 2;
        var centerY = (bounds.y1 + bounds.y2) / 2;
        drag.active = true;
        drag.entityId = eid;
        drag.offsetX = art.x - centerX;
        drag.offsetY = art.y - centerY;
        drag.lastArtX = art.x;
        drag.lastArtY = art.y;
        canvas.style.cursor = 'grabbing';
        e.preventDefault();
      });

      canvas.addEventListener('mousemove', function(e) {
        if (!drag.active) {
          // Hover cursor
          var art = canvasToArt(e);
          var eid = hitTestEntity(art.x, art.y);
          canvas.style.cursor = eid ? 'grab' : '';
          return;
        }
        var art = canvasToArt(e);
        var dx = art.x - drag.lastArtX;
        var dy = art.y - drag.lastArtY;
        if (dx === 0 && dy === 0) return;
        moveEntityPixels(drag.entityId, dx, dy);
        drag.lastArtX = art.x;
        drag.lastArtY = art.y;
        e.preventDefault();
      });

      function endDrag() {
        if (!drag.active) return;
        drag.active = false;
        canvas.style.cursor = '';

        // Compute new normalized position from entity bounds
        var bounds = buf.getEntityBounds(drag.entityId);
        if (bounds && ws && ws.readyState === WebSocket.OPEN) {
          var centerX = (bounds.x1 + bounds.x2) / 2;
          var centerY = (bounds.y1 + bounds.y2) / 2;
          var normX = centerX / PW;
          var normY = centerY / PH;
          // art-grid top-left for raw_sprite format
          var artX = bounds.x1;
          var artY = bounds.y1;

          ws.send(JSON.stringify({
            type: 'entity_moved',
            entity_id: drag.entityId,
            position: { x: normX, y: normY },
            art_position: { x: artX, y: artY },
          }));
        }
        drag.entityId = null;
      }

      canvas.addEventListener('mouseup', endDrag);
      canvas.addEventListener('mouseleave', endDrag);

      // -- WebSocket --
      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const ws = new WebSocket(
        protocol + '//' + location.host + '/ws'
        + '?participant_id=' + encodeURIComponent(participantId)
        + '&child_age=' + encodeURIComponent(childAge)
      );

      var storyIndex = parseInt(sessionStorage.getItem('story_index') || '0', 10);

      // Receive binary data as ArrayBuffer (for voice audio PCM)
      ws.binaryType = 'arraybuffer';

      ws.onopen = function() {
        // Send the chosen scene so the server can initialize the narration loop
        // (the previous WS connection was closed on navigation from /selection)
        if (chosenScene) {
          ws.send(JSON.stringify({
            type: 'init_scene',
            scene: chosenScene,
            story_index: storyIndex || 0,
          }));
        }
      };

      ws.onmessage = function(event) {
        // Binary message (voice audio PCM data)
        if (event.data instanceof ArrayBuffer) {
          NarrationClient.handleVoiceBinary(event.data);
          return;
        }

        var msg = JSON.parse(event.data);

        switch (msg.type) {
          case 'transcription':
            handleTranscription(msg);
            break;

          case 'animation':
            handleAnimation(msg);
            break;

          case 'add_temp_sprite':
            handleAddTempSprite(msg);
            break;

          case 'remove_temp_sprite':
            handleRemoveTempSprite(msg);
            break;

          case 'scene_complete':
            handleSceneComplete();
            break;

          case 'branches':
            handleBranches(msg.scenes);
            break;

          case 'story_complete':
            handleStoryComplete();
            break;

          case 'new_scene':
            handleNewScene(msg.scene);
            break;

          case 'voice_start':
            // Stop any running animation loop — voice replaces animation as feedback
            animRunner.stop();
            NarrationClient.handleVoiceHeader(msg);
            break;

          case 'assessment_result':
            // No longer display text immediately — will come via correction_text or guidance_text
            break;

          case 'correction_text':
            // Display factual error correction text (after animation completes)
            if (msg.guidance_text) {
              feedbackEl.textContent = msg.guidance_text;
              feedbackEl.style.opacity = 1;
            }
            break;

          case 'guidance_text':
            // Display MISL guidance text (after animation completes)
            if (msg.guidance_text) {
              feedbackEl.textContent = msg.guidance_text;
              feedbackEl.style.opacity = 1;
            }
            break;

          case 'error':
            console.error('Server error:', msg.message);
            break;
        }
      };

      // -- Transcription feedback --
      var feedbackEl = document.getElementById('transcription-feedback');

      function handleTranscription(msg) {
        feedbackEl.textContent = msg.transcription || '';
      }

      // -- Temp sprite handlers --
      function handleAddTempSprite(msg) {
        addTempSprite(msg.id, msg.sprite);
        try {
          executeRawSprite(msg.sprite, buf);
        } catch (e) {
          console.warn('[handleAddTempSprite] Failed to render', msg.id, e);
        }
        renderer.render();
      }

      function handleRemoveTempSprite(msg) {
        removeTempSprite(msg.id);
        var prefix = msg.id;
        for (var i = 0; i < buf.data.length; i++) {
          var e = buf.data[i].e;
          if (e === prefix || e.indexOf(prefix + '.') === 0) {
            buf.data[i].r = buf.data[i]._br || 0;
            buf.data[i].g = buf.data[i]._bg || 0;
            buf.data[i].b = buf.data[i]._bb || 0;
            buf.data[i].e = '';
          }
        }
        renderer.render();
      }

      // -- Animation playback --
      function handleAnimation(msg) {
        if (msg.template) {
          animRunner.playLoop({
            template: msg.template,
            params: msg.params || {},
            particles: msg.particles || [],
            text_overlays: msg.text_overlays || [],
            duration_ms: msg.duration_ms || 1200,
          });
        } else if (msg.steps) {
          // Mode C: sequence — play steps in order, then loop
          animRunner.playLoopSequence(msg.steps);
        } else {
          // Mode D: custom code
          var overlays = msg.text_overlays || [];
          if (overlays.length > 0 && typeof drawText === 'function') {
            for (var i = 0; i < overlays.length; i++) {
              var ov = overlays[i];
              var c = ov.color || [255, 255, 255];
              drawText(buf.data, buf.width, buf.height,
                ov.text, ov.x, ov.y, c[0], c[1], c[2], ov.id, ov.scale || 1);
            }
            renderer.render();
          }
          // Register custom animation for reuse if template_name provided
          if (msg.template_name && typeof AnimationTemplates !== 'undefined') {
            try {
              var dur = msg.duration_ms || 1200;
              var codeSrc = msg.code;
              AnimationTemplates.register(msg.template_name, function(params) {
                var wrapped = codeSrc + '\nreturn animate;';
                var animFn = new Function('buf', 'PW', 'PH', 'tempSprites', wrapped)(
                  buf.data, buf.width, buf.height,
                  typeof tempSprites !== 'undefined' ? tempSprites : {}
                );
                return { animate: animFn, duration_ms: dur };
              }, dur);
            } catch (regErr) {
              console.warn('[story] Failed to register custom animation:', regErr);
            }
          }
          animRunner.playLoop(msg.code, msg.duration_ms || 1200);
        }
      }
      // Expose animRunner for narration.js to stop loops on recording
      window.animRunner = animRunner;

      // -- Scene complete → show branch picker --
      var branchPicker = document.getElementById('branch-picker');
      var branchThumbnails = document.getElementById('branch-thumbnails');
      var transcriptionBox = document.getElementById('transcription-box');
      var pttHint = document.getElementById('ptt-hint');

      function handleSceneComplete() {
        // Disable push-to-talk
        transcriptionBox.style.display = 'none';
        pttHint.style.display = 'none';
        // Show branch picker with loading state
        branchPicker.style.display = '';
        branchThumbnails.innerHTML =
          '<div class="loading-overlay">' +
          '<div class="loading-dots"><span></span><span></span><span></span><span></span></div>' +
          '<div class="loading-text">Creating new scenes...</div>' +
          '</div>';
        // Request branches from server
        ws.send(JSON.stringify({ type: 'generate_branches' }));
      }

      function handleStoryComplete() {
        // Disable push-to-talk
        transcriptionBox.style.display = 'none';
        pttHint.style.display = 'none';
        // Show story complete message instead of branch picker
        branchPicker.style.display = '';
        branchThumbnails.innerHTML =
          '<div style="text-align:center; padding:2rem;">' +
          '<h2 style="color:#2c3e50; margin-bottom:0.5rem;">The End!</h2>' +
          '<p style="color:#666; font-size:1.1rem;">Great job telling the story!</p>' +
          '</div>';
      }

      // -- Branch selection --
      var currentBranches = [];

      function handleBranches(scenes) {
        currentBranches = scenes;
        branchThumbnails.innerHTML = '';
        scenes.forEach(function(scene, index) {
          var card = ScenePicker.createThumbnailCard(scene);
          card.addEventListener('click', function() {
            selectBranch(index);
          });
          branchThumbnails.appendChild(card);
        });
      }

      function selectBranch(index) {
        var scene = currentBranches[index];
        // Hide branch picker, show PTT
        branchPicker.style.display = 'none';
        transcriptionBox.style.display = '';
        pttHint.style.display = '';
        feedbackEl.textContent = '';
        // Update scene number
        sceneNumber++;
        document.getElementById('scene-num').textContent = sceneNumber;
        // Render the new scene
        renderScene(scene);
        // Notify server
        ws.send(JSON.stringify({ type: 'select_branch', index: index }));
      }

      function handleNewScene(scene) {
        renderScene(scene);
      }

      // -- Push-to-talk (narration.js handles MediaRecorder + space bar) --
      var pttHintEl = document.getElementById('ptt-hint');
      NarrationClient.init(ws, transcriptionBox, feedbackEl, pttHintEl);
    })();
