// Tellimations Study Story Page
// Loads pre-generated scenes sequentially.
// User speaks via PTT (Space), voice is transcribed & analysed.
// System never produces oral output. User controls scene progression.

(function() {
  'use strict';

  // --- Session data ---
  var participantNumber = sessionStorage.getItem('participant_number');
  if (!participantNumber) {
    window.location.href = '/study';
    return;
  }

  var storyKey = new URLSearchParams(window.location.search).get('story');
  var isAnimated = new URLSearchParams(window.location.search).get('animated') === 'true';
  var sceneCount = parseInt(new URLSearchParams(window.location.search).get('scenes') || '5', 10);
  var storyName = decodeURIComponent(new URLSearchParams(window.location.search).get('name') || 'Story');

  if (!storyKey) {
    window.location.href = '/study/landing';
    return;
  }

  var currentScene = 1;

  // --- UI elements ---
  var storyNameEl = document.getElementById('story-name');
  var sceneNumEl = document.getElementById('scene-num');
  var sceneTotalEl = document.getElementById('scene-total');
  var btnNextScene = document.getElementById('btn-next-scene');
  var btnFinish = document.getElementById('btn-finish-story');

  storyNameEl.textContent = storyName;
  sceneTotalEl.textContent = sceneCount;

  // --- Engine setup ---
  var canvas = document.getElementById('scene-canvas');
  var buf = new PixelBuffer(PW, PH);
  var renderer = new Renderer(canvas, buf);
  var animRunner = new AnimationRunner(buf, renderer);
  window.animRunner = animRunner;

  var currentSceneRef = null;

  // --- WebSocket (for PTT transcription + animations, NO system voice) ---
  var protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  var wsUrl = protocol + '//' + location.host + '/ws/study'
    + '?participant=' + encodeURIComponent(participantNumber)
    + '&story=' + encodeURIComponent(storyKey)
    + '&animated=' + (isAnimated ? '1' : '0');
  var ws = new WebSocket(wsUrl);
  ws.binaryType = 'arraybuffer';

  ws.onopen = function() {
    loadScene(currentScene);
  };

  ws.onmessage = function(event) {
    // Ignore all binary (system voice audio) — no oral support from system
    if (event.data instanceof ArrayBuffer) return;

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

      // Ignore voice_start, scene_complete — user controls progression
      case 'voice_start':
      case 'scene_complete':
        break;

      case 'error':
        console.error('[study_story] Server error:', msg.message);
        break;
    }
  };

  ws.onclose = function() {
    console.log('[study_story] WebSocket closed');
  };

  // --- Narration client init (PTT recording + transcription only) ---
  NarrationClient.init(
    ws,
    document.getElementById('transcription-box'),
    document.getElementById('transcription-feedback'),
    document.getElementById('ptt-hint')
  );

  // --- Scene loading ---
  function loadScene(sceneNum) {
    sceneNumEl.textContent = sceneNum;

    // Show/hide buttons
    if (sceneNum < sceneCount) {
      btnNextScene.style.display = '';
      btnFinish.style.display = 'none';
    } else {
      btnNextScene.style.display = 'none';
      btnFinish.style.display = '';
    }

    fetch('/api/study/scene?story=' + encodeURIComponent(storyKey) + '&scene=' + sceneNum)
      .then(function(resp) {
        if (!resp.ok) throw new Error('Failed to load scene ' + sceneNum);
        return resp.json();
      })
      .then(function(scene) {
        renderScene(scene);
        // Tell server we loaded this scene (for transcription context)
        ws.send(JSON.stringify({
          type: 'study_scene_loaded',
          story: storyKey,
          scene_number: sceneNum,
          scene: scene,
        }));
      })
      .catch(function(err) {
        console.error('[study_story] Failed to load scene:', err);
      });
  }

  // --- Render scene (matches main story.js pattern) ---
  function renderScene(scene) {
    currentSceneRef = scene;
    buf.clear();

    var spriteCode = scene.sprite_code || {};
    var bgEntry = spriteCode.bg || null;

    var entityEids = [];
    for (var eid in spriteCode) {
      if (spriteCode.hasOwnProperty(eid) && eid !== 'bg') {
        entityEids.push(eid);
      }
    }

    // Sort by depth_order
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
      buf.computeDistanceFields(20);
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

  // --- Handle WS messages ---
  function handleTranscription(msg) {
    var el = document.getElementById('transcription-feedback');
    if (el) el.textContent = msg.text || '';
    var box = document.getElementById('transcription-box');
    if (box) {
      box.classList.remove('recording');
      box.classList.add('visible');
    }
  }

  function handleAnimation(msg) {
    if (!isAnimated) return;
    if (msg.template) {
      // Mode A/B: template-based animation — play in loop
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
    } else if (msg.code) {
      // Mode D: custom code
      try {
        animRunner.playLoop(msg.code, msg.duration_ms || 1200);
      } catch (e) {
        console.warn('[animation] Error running animation:', e);
      }
    }
  }

  function handleAddTempSprite(msg) {
    if (!isAnimated) return;
    if (msg.sprite && msg.id) {
      addTempSprite(msg.id, msg.sprite);
      renderTempSprites(buf);
      renderer.render();
    }
  }

  function handleRemoveTempSprite(msg) {
    if (!isAnimated) return;
    if (msg.id) {
      removeTempSprite(msg.id);
      if (currentSceneRef) renderScene(currentSceneRef);
    }
  }

  // --- Buttons ---
  btnNextScene.addEventListener('click', function() {
    currentScene++;
    loadScene(currentScene);
  });

  btnFinish.addEventListener('click', function() {
    var completed = JSON.parse(sessionStorage.getItem('study_completed') || '{}');
    completed[storyKey] = true;
    sessionStorage.setItem('study_completed', JSON.stringify(completed));
    window.location.href = '/study/landing';
  });
})();
