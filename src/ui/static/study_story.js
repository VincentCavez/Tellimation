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
  var _tutorialActive = (storyKey === 'training' && currentScene === 1);
  var _tutorialStarted = false;

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

      case 'study_log':
        console.log('[' + msg.tag + '] ' + msg.text);
        if (msg.tag === 'MISTAKES') window._gotMistakes = true;
        if (msg.tag === 'OPTIONS') window._gotOptions = true;
        if (window._gotMistakes && window._gotOptions) {
          NarrationClient.assessmentDone();
          window._gotMistakes = false;
          window._gotOptions = false;
        }
        break;

      case 'error':
        console.error('[study_story] Server error:', msg.message);
        break;
    }
  };

  ws.onclose = function() {
  };

  // --- Narration client init (PTT recording only) ---
  NarrationClient.init(
    ws,
    null,
    null,
    document.getElementById('ptt-hint')
  );

  // --- Scene loading ---
  var _nextSceneTimer = null;

  var _sceneTransitioning = false;

  function loadScene(sceneNum) {
    sceneNumEl.textContent = sceneNum;

    // Stop any running animation and ignore incoming ones during transition
    _sceneTransitioning = true;
    if (window.animRunner) {
      window.animRunner.stopLoop();
      // Detach the old runner so late callbacks don't corrupt the new scene's buffer
      window.animRunner.buf = null;
      window.animRunner.renderer = null;
    }

    // Clear any pending next-scene timer
    if (_nextSceneTimer) { clearTimeout(_nextSceneTimer); _nextSceneTimer = null; }

    // Hide buttons initially, show after 60s delay (unless tutorial is active)
    btnNextScene.style.display = 'none';
    btnFinish.style.display = 'none';
    if (!_tutorialActive) {
      _nextSceneTimer = setTimeout(function() {
        if (sceneNum < sceneCount) {
          btnNextScene.style.display = '';
        } else {
          btnFinish.style.display = '';
        }
      }, 60000);
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
        // Keep blocking animations briefly after scene load to let server
        // flush any pending animations from the previous scene
        setTimeout(function() { _sceneTransitioning = false; }, 500);
      })
      .catch(function(err) {
        console.error('[study_story] Failed to load scene:', err);
      });
  }

  // --- Render scene (matches main story.js pattern) ---
  function renderScene(scene) {
    currentSceneRef = scene;

    // HD format: draw images directly on canvas, bypass pixel buffer
    if (scene.format === 'hd') {
      renderSceneHD(scene);
      return;
    }

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
      // Start tutorial for non-HD scenes if needed
      if (_tutorialActive && !_tutorialStarted) {
        _tutorialStarted = true;
        startTutorial();
      }
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

  // --- HD entity contour data (precomputed at load time) ---
  // hdEntityData[entityId] = { mask, contour, bounds, distField }
  //   mask:      Uint8Array(W*H), 1 = entity pixel, 0 = transparent
  //   contour:   Array of {x, y, idx} — border pixels (opaque with a transparent neighbor)
  //   bounds:    {x1, y1, x2, y2} — bounding box
  //   distField: Uint8Array(W*H), 0 = inside entity, 1..N = distance from contour, 255 = far
  var hdEntityData = {};
  window.hdEntityData = hdEntityData;

  function computeEntityContour(entityId, img, w, h) {
    // Draw entity image on offscreen canvas to read pixel data
    var off = document.createElement('canvas');
    off.width = w;
    off.height = h;
    var offCtx = off.getContext('2d');
    offCtx.drawImage(img, 0, 0);
    var imgData = offCtx.getImageData(0, 0, w, h);
    var px = imgData.data;
    var total = w * h;

    // Build mask (1 = opaque pixel)
    var mask = new Uint8Array(total);
    var x1 = w, y1 = h, x2 = -1, y2 = -1;
    for (var i = 0; i < total; i++) {
      var a = px[i * 4 + 3];
      if (a <= 10) continue; // fully transparent
      var r = px[i * 4], g = px[i * 4 + 1], b = px[i * 4 + 2];
      // Remove white halo: semi-transparent white-ish pixels from bg removal
      if (a < 200 && r > 200 && g > 200 && b > 200) {
        px[i * 4 + 3] = 0;
        continue;
      }
      mask[i] = 1;
      var mx = i % w, my = (i - mx) / w;
      if (mx < x1) x1 = mx;
      if (mx > x2) x2 = mx;
      if (my < y1) y1 = my;
      if (my > y2) y2 = my;
    }

    // Find contour: opaque pixels with at least one transparent 4-neighbor
    var contour = [];
    for (var i = 0; i < total; i++) {
      if (!mask[i]) continue;
      var cx = i % w, cy = (i - cx) / w;
      if ((cx > 0     && !mask[i - 1]) ||
          (cx < w - 1 && !mask[i + 1]) ||
          (cy > 0     && !mask[i - w]) ||
          (cy < h - 1 && !mask[i + w])) {
        contour.push({ x: cx, y: cy, idx: i });
      }
    }

    // BFS distance field from contour outward (max 20px)
    var maxDist = 20;
    var distField = new Uint8Array(total);
    distField.fill(255);
    // Mark entity pixels as 0
    for (var i = 0; i < total; i++) {
      if (mask[i]) distField[i] = 0;
    }
    // Seed BFS from contour neighbors
    var queue = [];
    for (var c = 0; c < contour.length; c++) {
      var ci = contour[c].idx;
      var cx = ci % w, cy = (ci - cx) / w;
      if (cx > 0     && distField[ci - 1] === 255) { distField[ci - 1] = 1; queue.push(ci - 1); }
      if (cx < w - 1 && distField[ci + 1] === 255) { distField[ci + 1] = 1; queue.push(ci + 1); }
      if (cy > 0     && distField[ci - w] === 255) { distField[ci - w] = 1; queue.push(ci - w); }
      if (cy < h - 1 && distField[ci + w] === 255) { distField[ci + w] = 1; queue.push(ci + w); }
    }
    var dist = 1;
    while (queue.length > 0 && dist < maxDist) {
      dist++;
      var next = [];
      for (var q = 0; q < queue.length; q++) {
        var ci = queue[q];
        var cx = ci % w, cy = (ci - cx) / w;
        if (cx > 0     && distField[ci - 1] === 255) { distField[ci - 1] = dist; next.push(ci - 1); }
        if (cx < w - 1 && distField[ci + 1] === 255) { distField[ci + 1] = dist; next.push(ci + 1); }
        if (cy > 0     && distField[ci - w] === 255) { distField[ci - w] = dist; next.push(ci - w); }
        if (cy < h - 1 && distField[ci + w] === 255) { distField[ci + w] = dist; next.push(ci + w); }
      }
      queue = next;
    }

    // Write back cleaned image (white halo pixels zeroed out)
    offCtx.putImageData(imgData, 0, 0);

    hdEntityData[entityId] = {
      mask: mask,
      contour: contour,
      bounds: { x1: x1, y1: y1, x2: x2, y2: y2 },
      distField: distField,
      width: w,
      height: h,
      cleanCanvas: off,
    };
  }

  // --- Capture bg-only pixels at half resolution (before entities are drawn) ---
  var HD_SCALE = 1; // no downscale: full resolution animation buffer
  function captureBgPixels(fullW, fullH) {
    var aw = Math.ceil(fullW / HD_SCALE);
    var ah = Math.ceil(fullH / HD_SCALE);
    var off = document.createElement('canvas');
    off.width = aw;
    off.height = ah;
    var offCtx = off.getContext('2d');
    offCtx.imageSmoothingEnabled = true;
    offCtx.drawImage(canvas, 0, 0, aw, ah);
    return offCtx.getImageData(0, 0, aw, ah).data;
  }

  // --- Build PixelBuffer from HD canvas at half resolution ---
  function buildHDPixelBuffer(fullW, fullH, bgPixels) {
    var aw = Math.ceil(fullW / HD_SCALE); // animation buffer width
    var ah = Math.ceil(fullH / HD_SCALE); // animation buffer height

    // Downscale the canvas into an offscreen canvas
    var off = document.createElement('canvas');
    off.width = aw;
    off.height = ah;
    var offCtx = off.getContext('2d');
    offCtx.imageSmoothingEnabled = true;
    offCtx.drawImage(canvas, 0, 0, aw, ah);
    var imgData = offCtx.getImageData(0, 0, aw, ah);
    var px = imgData.data;
    var total = aw * ah;

    var hdBuf = new PixelBuffer(aw, ah);

    // Fill pixel data
    for (var i = 0; i < total; i++) {
      var p = hdBuf.data[i];
      p.r = px[i * 4];
      p.g = px[i * 4 + 1];
      p.b = px[i * 4 + 2];
      p.e = 'bg';
    }

    // Downsample entity masks and stamp IDs
    var entityIds = scene_entity_order || [];
    var downMasks = {};
    for (var ei = 0; ei < entityIds.length; ei++) {
      var eid = entityIds[ei];
      var ed = hdEntityData[eid];
      if (!ed) continue;
      var dm = new Uint8Array(total);
      for (var y = 0; y < ah; y++) {
        for (var x = 0; x < aw; x++) {
          // Check if any pixel in the source 2×2 block is opaque
          var sx = x * HD_SCALE, sy = y * HD_SCALE;
          var hit = false;
          for (var dy = 0; dy < HD_SCALE && !hit; dy++) {
            for (var dx = 0; dx < HD_SCALE && !hit; dx++) {
              var fi = (sy + dy) * fullW + (sx + dx);
              if (fi < ed.mask.length && ed.mask[fi]) hit = true;
            }
          }
          if (hit) {
            var di = y * aw + x;
            dm[di] = 1;
            hdBuf.data[di].e = eid;
          }
        }
      }
      downMasks[eid] = dm;
    }

    // Compute distance fields at half resolution from downsampled masks
    hdBuf.distFields = {};
    for (var eid in downMasks) {
      var mask = downMasks[eid];
      var field = new Uint8Array(total);
      field.fill(255);
      for (var i = 0; i < total; i++) { if (mask[i]) field[i] = 0; }
      // Find contour
      var edgeQueue = [];
      for (var i = 0; i < total; i++) {
        if (field[i] !== 0) continue;
        var cx = i % aw, cy = (i - cx) / aw;
        if ((cx > 0      && field[i - 1]  !== 0) ||
            (cx < aw - 1 && field[i + 1]  !== 0) ||
            (cy > 0      && field[i - aw] !== 0) ||
            (cy < ah - 1 && field[i + aw] !== 0)) {
          edgeQueue.push(i);
        }
      }
      // BFS
      var queue = [];
      for (var q = 0; q < edgeQueue.length; q++) {
        var ci = edgeQueue[q], cx = ci % aw, cy = (ci - cx) / aw;
        if (cx > 0      && field[ci - 1]  === 255) { field[ci - 1]  = 1; queue.push(ci - 1); }
        if (cx < aw - 1 && field[ci + 1]  === 255) { field[ci + 1]  = 1; queue.push(ci + 1); }
        if (cy > 0      && field[ci - aw] === 255) { field[ci - aw] = 1; queue.push(ci - aw); }
        if (cy < ah - 1 && field[ci + aw] === 255) { field[ci + aw] = 1; queue.push(ci + aw); }
      }
      var dist = 1, maxDist = 20;
      while (queue.length > 0 && dist < maxDist) {
        dist++;
        var next = [];
        for (var q = 0; q < queue.length; q++) {
          var ci = queue[q], cx = ci % aw, cy = (ci - cx) / aw;
          if (cx > 0      && field[ci - 1]  === 255) { field[ci - 1]  = dist; next.push(ci - 1); }
          if (cx < aw - 1 && field[ci + 1]  === 255) { field[ci + 1]  = dist; next.push(ci + 1); }
          if (cy > 0      && field[ci - aw] === 255) { field[ci - aw] = dist; next.push(ci - aw); }
          if (cy < ah - 1 && field[ci + aw] === 255) { field[ci + aw] = dist; next.push(ci + aw); }
        }
        queue = next;
      }
      hdBuf.distFields[eid] = field;
    }
    hdBuf.data._distFields = hdBuf.distFields;

    // Build entityLayers — use each entity's own image (not the composited canvas)
    // so that overlapping pixels get the correct per-entity colors
    for (var ei = 0; ei < entityIds.length; ei++) {
      var eid = entityIds[ei];
      var dm = downMasks[eid];
      var ed = window.hdEntityData && window.hdEntityData[eid];
      if (!dm) continue;
      var layer = [];
      if (ed && ed.cleanCanvas) {
        var entOff = document.createElement('canvas');
        entOff.width = aw; entOff.height = ah;
        var entCtx = entOff.getContext('2d');
        entCtx.imageSmoothingEnabled = true;
        entCtx.drawImage(ed.cleanCanvas, 0, 0, aw, ah);
        var entPx = entCtx.getImageData(0, 0, aw, ah).data;
        for (var i = 0; i < total; i++) {
          if (dm[i]) {
            var pi = i * 4;
            var ea = entPx[pi + 3];
            if (ea > 10) {
              layer.push({ idx: i, r: entPx[pi], g: entPx[pi + 1], b: entPx[pi + 2], e: eid });
            } else {
              layer.push({ idx: i, r: hdBuf.data[i].r, g: hdBuf.data[i].g, b: hdBuf.data[i].b, e: eid });
            }
          }
        }
      } else {
        for (var i = 0; i < total; i++) {
          if (dm[i]) {
            layer.push({ idx: i, r: hdBuf.data[i].r, g: hdBuf.data[i].g, b: hdBuf.data[i].b, e: eid });
          }
        }
      }
      hdBuf.entityLayers[eid] = layer;
    }
    hdBuf.data._entityLayers = hdBuf.entityLayers;

    // Set background snapshot from bg-only pixels (without entities)
    if (bgPixels) {
      for (var i = 0; i < total; i++) {
        var p = hdBuf.data[i];
        p._br = bgPixels[i * 4];
        p._bg = bgPixels[i * 4 + 1];
        p._bb = bgPixels[i * 4 + 2];
        p._be = 'bg';
      }
    } else {
      hdBuf.snapshotBackground();
    }

    return hdBuf;
  }

  // --- HD Renderer (half-res buffer → full-res canvas via upscale) ---
  var hdRenderer = null;

  function createHDRenderer(fullW, fullH) {
    var aw = Math.ceil(fullW / HD_SCALE);
    var ah = Math.ceil(fullH / HD_SCALE);
    // Offscreen canvas at buffer resolution for putImageData
    var offCanvas = document.createElement('canvas');
    offCanvas.width = aw;
    offCanvas.height = ah;
    var offCtx = offCanvas.getContext('2d');
    var offImgData = offCtx.createImageData(aw, ah);

    return {
      canvas: canvas,
      buf: null,
      render: function() {
        var b = this.buf;
        if (!b) return;
        var out = offImgData.data;
        var n = aw * ah;
        for (var i = 0; i < n; i++) {
          var p = b.data[i];
          out[i * 4]     = p.r;
          out[i * 4 + 1] = p.g;
          out[i * 4 + 2] = p.b;
          out[i * 4 + 3] = 255;
        }
        offCtx.putImageData(offImgData, 0, 0);
        // Upscale to main canvas
        var mainCtx = canvas.getContext('2d');
        mainCtx.imageSmoothingEnabled = true;
        mainCtx.drawImage(offCanvas, 0, 0, fullW, fullH);
      }
    };
  }

  // Track entity draw order for the current scene
  var scene_entity_order = [];

  // --- HD scene rendering: draw images directly on canvas ---
  function renderSceneHD(scene) {
    var ctx = canvas.getContext('2d');

    // Switch canvas from pixel-art to HD mode
    canvas.classList.remove('pixel-art');
    canvas.style.imageRendering = 'auto';

    // Reset entity data for new scene
    hdEntityData = {};
    window.hdEntityData = hdEntityData;
    window._hdSceneImages = null; // will store {bg, entities} for full-res redraw
    scene_entity_order = (scene.entity_urls || []).map(function(e) { return e.id; });

    var bgImg = new Image();
    bgImg.onload = function() {
      // Adapt canvas to image dimensions
      var w = bgImg.width, h = bgImg.height;
      canvas.width = w;
      canvas.height = h;
      ctx.imageSmoothingEnabled = true;
      ctx.drawImage(bgImg, 0, 0);

      // Capture bg-only pixels BEFORE drawing entities (for animation background snapshot)
      var bgOnlyPixels = captureBgPixels(w, h);

      // Overlay entity images (all same size as background, just superpose)
      var entities = scene.entity_urls || [];
      if (entities.length === 0) {
        setupHDAnimations(w, h, bgOnlyPixels);
        return;
      }

      // Load all entity images, then draw in order + compute contours
      var images = new Array(entities.length);
      var loaded = 0;
      entities.forEach(function(ent, idx) {
        var entImg = new Image();
        entImg.onload = function() {
          images[idx] = entImg;
          // Precompute contour data from the raw entity image
          computeEntityContour(ent.id, entImg, w, h);
          loaded++;
          if (loaded === entities.length) {
            for (var i = 0; i < entities.length; i++) {
              var ed = hdEntityData[entities[i].id];
              ctx.drawImage(ed && ed.cleanCanvas ? ed.cleanCanvas : images[i], 0, 0);
            }
            // Store images for full-res redraw after animations
            window._hdSceneImages = { bg: bgImg, entities: entities, images: images };
            // Build pixel buffer + animation runner for HD
            setupHDAnimations(w, h, bgOnlyPixels);
          }
        };
        entImg.onerror = function() {
          console.warn('[renderSceneHD] Failed to load entity', ent.id, ent.url);
          loaded++;
          if (loaded === entities.length) {
            for (var i = 0; i < entities.length; i++) {
              var ed = hdEntityData[entities[i].id];
              ctx.drawImage(ed && ed.cleanCanvas ? ed.cleanCanvas : images[i], 0, 0);
            }
            setupHDAnimations(w, h, bgOnlyPixels);
          }
        };
        entImg.src = ent.url;
      });
    };
    bgImg.onerror = function() {
      console.error('[renderSceneHD] Failed to load background', scene.background_url);
    };
    bgImg.src = scene.background_url;
  }

  function setupHDAnimations(w, h, bgPixels) {
    var aw = Math.ceil(w / HD_SCALE);
    var ah = Math.ceil(h / HD_SCALE);
    var hdBuf = buildHDPixelBuffer(w, h, bgPixels);
    hdRenderer = createHDRenderer(w, h);
    hdRenderer.buf = hdBuf;

    // Rewire the animation runner to use HD buffer + renderer + 24fps
    buf = hdBuf;
    renderer = hdRenderer;
    animRunner.buf = hdBuf;
    animRunner.renderer = hdRenderer;
    animRunner.frameInterval = 1000 / 24; // 24fps
    // Redraw full-res scene after each animation ends
    animRunner.onAnimationFinish = function() {
      var sceneImgs = window._hdSceneImages;
      if (!sceneImgs) return;
      var mainCtx = canvas.getContext('2d');
      mainCtx.drawImage(sceneImgs.bg, 0, 0);
      var ents = sceneImgs.entities;
      for (var i = 0; i < ents.length; i++) {
        var ed = hdEntityData[ents[i].id];
        mainCtx.drawImage(ed && ed.cleanCanvas ? ed.cleanCanvas : sceneImgs.images[i], 0, 0);
      }
    };
    window.animRunner = animRunner;

    console.log('[HD] Animation buffer ready:', aw + 'x' + ah,
      '(downscaled from ' + w + 'x' + h + ')',
      'entities:', Object.keys(hdEntityData).join(', '));

    // Start tutorial if this is training scene 1
    if (_tutorialActive && !_tutorialStarted) {
      _tutorialStarted = true;
      startTutorial();
    }
  }

  // --- Tutorial mode (training scene 1) ---
  // NOTE: _tutorialActive and _tutorialStarted declared earlier (before setupHDAnimations)

  // Entity targets are resolved dynamically from the loaded scene's entity order.
  // Fallback: use scene_entity_order[0], [1], [2] etc.
  function _ent(idx) { return scene_entity_order[idx] || scene_entity_order[0] || ''; }

  // Get entity asset URL for ghost_outline
  function _entityAssetUrl(idx) {
    var eid = _ent(idx);
    var imgs = window._hdSceneImages;
    if (imgs && imgs.entities) {
      for (var i = 0; i < imgs.entities.length; i++) {
        if (imgs.entities[i].id === eid) return imgs.entities[i].url;
      }
    }
    return '';
  }

  var TUTORIAL_STEPS = [
    { text: "Welcome to the practice! Let me show you the animations I can do. When you see one, it means I want you to say or repeat something about the picture! No need to remember them, I just want you to see them at least once!", audio: "/oral-instructions/tutorial_01.wav", animation: null },
    { text: "If I want you to talk about a character, I will shine a spotlight on them, like this!", audio: "/oral-instructions/tutorial_02.wav", animFn: function() { return { template: 'spotlight', params: { entityPrefix: _ent(0) } }; } },
    { text: "If I am not sure who or what you are talking about, things will glow one by one!", audio: "/oral-instructions/tutorial_03.wav", animFn: function() { return { template: 'sequential_glow', params: { entityPrefixes: scene_entity_order.slice() } }; } },
    { text: "I can also show a name tag floating above them if they need a name, or want to be called by it!", audio: "/oral-instructions/tutorial_04.wav", animFn: function() { return { template: 'nametag', params: { entityPrefix: _ent(1), labelText: '?' } }; } },
    { text: "When I want you to describe how someone is moving, I show lines of movement!", audio: "/oral-instructions/tutorial_05.wav", animFn: function() { return { template: 'motion_lines', params: { entityPrefix: _ent(2), direction: 'right' } }; } },
    { text: "I can also flip them around to show they are in action!", audio: "/oral-instructions/tutorial_06.wav", animFn: function() { return { template: 'flip', params: { entityPrefix: _ent(0) } }; } },
    { text: "If I want you to describe what something looks like, I will make its colors pop!", audio: "/oral-instructions/tutorial_07.wav", animFn: function() { return { template: 'color_pop', params: { entityPrefix: _ent(1) } }; } },
    { text: "When I want you to tell me how someone is feeling, I show little particles around them!", audio: "/oral-instructions/tutorial_08.wav", animFn: function() { return { template: 'emanation', params: { entityPrefix: _ent(1), particleType: 'hearts' } }; } },
    { text: "If something is hiding behind another thing, I can make it see-through so you can see!", audio: "/oral-instructions/tutorial_09.wav", animFn: function() { return { template: 'reveal', params: { entityPrefix: _ent(2) } }; } },
    { text: "I can also stamp a character to show where they are standing!", audio: "/oral-instructions/tutorial_10.wav", animFn: function() { return { template: 'stamp', params: { entityPrefix: _ent(0) } }; } },
    { text: "If I want you to use the past tense, the picture will look like an old movie!", audio: "/oral-instructions/tutorial_11.wav", animFn: function() { return { template: 'flashback', params: { isIndoor: false } }; } },
    { text: "And if I want you to talk about the future, you will see a day-and-night effect!", audio: "/oral-instructions/tutorial_12.wav", animFn: function() { return { template: 'timelapse', params: { isIndoor: false } }; } },
    { text: "When two characters are connected, they will be pulled toward each other!", audio: "/oral-instructions/tutorial_13.wav", animFn: function() { return { template: 'magnetism', params: { entityPrefixA: _ent(0), entityPrefixB: _ent(1) } }; } },
    { text: "And if they should be apart, they will push away from each other!", audio: "/oral-instructions/tutorial_14.wav", animFn: function() { return { template: 'repel', params: { entityPrefixA: _ent(1), entityPrefixB: _ent(2) } }; } },
    { text: "When one thing causes something to happen to another, you will see a push!", audio: "/oral-instructions/tutorial_15.wav", animFn: function() { return { template: 'causal_push', params: { entityPrefixA: _ent(2), entityPrefixB: _ent(1) } }; } },
    { text: "When something should not be mentioned, it will break apart into tiny pieces!", audio: "/oral-instructions/tutorial_16.wav", animFn: function() { return { template: 'disintegration', params: { entityPrefix: _ent(2) } }; } },
    { text: "And if something is missing, you will see a ghostly shape!", audio: "/oral-instructions/tutorial_17.wav", animFn: function() { return { template: 'ghost_outline', params: { entityPrefix: _ent(2), ghostImageUrl: _entityAssetUrl(2) } }; }, hideEntity: 2 },
    { text: "If I want a character to say something, a speech bubble will appear!", audio: "/oral-instructions/tutorial_18.wav", animFn: function() { return { template: 'speech_bubble', params: { entityPrefix: _ent(0), text: 'Hello!' } }; } },
    { text: "If I want a character to think something, a thought bubble will appear!", audio: "/oral-instructions/tutorial_19.wav", animFn: function() { return { template: 'thought_bubble', params: { entityPrefix: _ent(1), text: '...' } }; } },
    { text: "When something important or surprising happens, you will see an exclamation mark!", audio: "/oral-instructions/tutorial_20.wav", animFn: function() { return { template: 'alert', params: { entityPrefix: _ent(2) } }; } },
    { text: "And when a word is really special, it will burst out like in a comic book!", audio: "/oral-instructions/tutorial_21.wav", animFn: function() { return { template: 'interjection', params: { entityPrefix: _ent(0), word: 'Wow!' } }; } },
    { text: "Great job! Now you know all my animations. Let's practice with the next scene!", audio: "/oral-instructions/tutorial_22.wav", animation: null },
  ];

  var tutorialPanel = document.getElementById('tutorial-panel');
  var tutorialText = document.getElementById('tutorial-text');
  var tutorialBtns = document.getElementById('tutorial-btns');
  var btnTutorialReplay = document.getElementById('btn-tutorial-replay');
  var btnTutorialContinue = document.getElementById('btn-tutorial-continue');
  var _tutorialStep = 0;
  var _tutorialWordTimers = [];
  var _tutorialAudio = null;

  function clearTutorialTimers() {
    for (var i = 0; i < _tutorialWordTimers.length; i++) clearTimeout(_tutorialWordTimers[i]);
    _tutorialWordTimers = [];
  }

  function revealTutorialText(text, audioDuration, onDone) {
    clearTutorialTimers();
    var words = text.split(/\s+/);
    tutorialText.innerHTML = '';
    var totalChars = 0;
    for (var i = 0; i < words.length; i++) {
      totalChars += words[i].length;
      var span = document.createElement('span');
      span.className = 'instr-word';
      span.textContent = words[i] + ' ';
      tutorialText.appendChild(span);
    }
    var spans = tutorialText.querySelectorAll('.instr-word');
    var secPerChar = (audioDuration > 0 && totalChars > 0) ? audioDuration / totalChars : 0.045;
    var elapsed = 0;
    for (var i = 0; i < spans.length; i++) {
      var charLen = Math.max(1, spans[i].textContent.trim().length);
      (function(s, delay) {
        _tutorialWordTimers.push(setTimeout(function() { s.classList.add('visible'); }, delay));
      })(spans[i], elapsed * 1000);
      elapsed += charLen * secPerChar;
    }
    // Callback after all words revealed
    if (onDone) {
      _tutorialWordTimers.push(setTimeout(onDone, elapsed * 1000 + 200));
    }
  }

  // Save original pixel buffer references for restore after ghost_outline
  var _savedBuf = null;

  // Redraw HD scene excluding a specific entity (for ghost_outline demo)
  function _redrawSceneWithout(entityId) {
    var sceneImgs = window._hdSceneImages;
    if (!sceneImgs) return;
    // Save the original buffer before replacing it
    _savedBuf = animRunner.buf;
    var mainCtx = canvas.getContext('2d');
    mainCtx.drawImage(sceneImgs.bg, 0, 0);
    var ents = sceneImgs.entities;
    for (var i = 0; i < ents.length; i++) {
      if (ents[i].id === entityId) continue; // skip this entity
      var ed = hdEntityData[ents[i].id];
      mainCtx.drawImage(ed && ed.cleanCanvas ? ed.cleanCanvas : sceneImgs.images[i], 0, 0);
    }
    // Rebuild pixel buffer from the modified canvas so animation snapshot excludes the entity
    var w = canvas.width, h = canvas.height;
    var bgPixels = captureBgPixels(w, h);
    var hdBuf = buildHDPixelBuffer(w, h, bgPixels);
    hdRenderer.buf = hdBuf;
    buf = hdBuf;
    animRunner.buf = hdBuf;
    animRunner.renderer = hdRenderer;
  }

  // Restore the full scene (all entities + original pixel buffer) after hiding one
  function _restoreFullScene() {
    if (!_savedBuf) return;
    // Restore original pixel buffer
    buf = _savedBuf;
    hdRenderer.buf = _savedBuf;
    animRunner.buf = _savedBuf;
    animRunner.renderer = hdRenderer;
    _savedBuf = null;
    // Redraw full scene on canvas
    var sceneImgs = window._hdSceneImages;
    if (!sceneImgs) return;
    var mainCtx = canvas.getContext('2d');
    mainCtx.drawImage(sceneImgs.bg, 0, 0);
    var ents = sceneImgs.entities;
    for (var i = 0; i < ents.length; i++) {
      var ed = hdEntityData[ents[i].id];
      mainCtx.drawImage(ed && ed.cleanCanvas ? ed.cleanCanvas : sceneImgs.images[i], 0, 0);
    }
  }

  function playTutorialStep(stepIdx) {
    _tutorialStep = stepIdx;
    tutorialBtns.style.display = 'none';
    var step = TUTORIAL_STEPS[stepIdx];
    var anim = step.animFn ? step.animFn() : step.animation;

    // Stop any previous audio
    if (_tutorialAudio) { _tutorialAudio.pause(); _tutorialAudio = null; }

    // Restore full scene if previous step hid an entity
    _restoreFullScene();

    function doRevealAndAnim(audioDuration) {
      revealTutorialText(step.text, audioDuration, function() {
        if (anim) {
          if (step.hideEntity != null) {
            _redrawSceneWithout(_ent(step.hideEntity));
          }
          animRunner.play({
            template: anim.template,
            params: anim.params || {},
            duration_ms: (anim.params && anim.params.duration_ms) || 2500,
          }).then(function() {
            if (step.hideEntity != null) {
              _restoreFullScene();
            }
            showTutorialButtons(stepIdx);
          });
        } else {
          showTutorialButtons(stepIdx);
        }
      });
    }

    if (step.audio) {
      _tutorialAudio = new Audio(step.audio);
      var fired = false;
      function onReady() {
        if (fired) return;
        fired = true;
        var dur = _tutorialAudio.duration || 0;
        _tutorialAudio.play().catch(function() {});
        doRevealAndAnim(dur);
      }
      _tutorialAudio.addEventListener('canplaythrough', onReady, { once: true });
      // If already loaded (cached)
      if (_tutorialAudio.readyState >= 4) onReady();
    } else {
      doRevealAndAnim(0);
    }
  }

  function showTutorialButtons(stepIdx) {
    tutorialBtns.style.display = 'flex';
    btnTutorialContinue.textContent = (stepIdx >= TUTORIAL_STEPS.length - 1) ? 'Finish' : 'Continue';
  }

  function startTutorial() {
    // Hide PTT and buttons
    document.getElementById('ptt-hint').style.display = 'none';
    btnNextScene.style.display = 'none';
    btnFinish.style.display = 'none';
    if (_nextSceneTimer) { clearTimeout(_nextSceneTimer); _nextSceneTimer = null; }

    tutorialPanel.style.display = '';
    playTutorialStep(0);
  }

  function endTutorial() {
    if (_tutorialAudio) { _tutorialAudio.pause(); _tutorialAudio = null; }
    _tutorialActive = false;
    tutorialPanel.style.display = 'none';
    // Show PTT hint and Next Scene button
    document.getElementById('ptt-hint').style.display = '';
    btnNextScene.style.display = '';
  }

  if (btnTutorialReplay) {
    btnTutorialReplay.addEventListener('click', function() {
      playTutorialStep(_tutorialStep);
    });
  }
  if (btnTutorialContinue) {
    btnTutorialContinue.addEventListener('click', function() {
      if (_tutorialStep >= TUTORIAL_STEPS.length - 1) {
        endTutorial();
      } else {
        playTutorialStep(_tutorialStep + 1);
      }
    });
  }

  // --- Test: play all 20 animations sequentially ---
  var ALL_TEMPLATES = [
    'spotlight', 'nametag', 'reveal', 'stamp',
    'color_pop', 'emanation', 'flashback', 'timelapse',
    'motion_lines', 'flip',
    'magnetism', 'repel', 'causal_push',
    'sequential_glow', 'disintegration', 'ghost_outline',
    'speech_bubble', 'thought_bubble', 'alert', 'interjection'
  ];
  var SINGLE_ENTITY = [
    'spotlight', 'nametag', 'stamp', 'color_pop', 'reveal',
    'emanation', 'motion_lines', 'flip',
    'disintegration', 'ghost_outline', 'speech_bubble', 'thought_bubble', 'alert', 'interjection'
  ];
  var TWO_ENTITY = ['magnetism', 'repel', 'causal_push'];
  var SCENE_WIDE = ['timelapse', 'flashback'];
  var MULTI_ENTITY = ['sequential_glow'];

  var stopPlayAll = false;

  window.playAllAnimations = async function() {
    if (!animRunner || !animRunner.buf) {
      console.error('Animation buffer not ready');
      return;
    }
    var entities = scene_entity_order;
    var entityA = entities[0] || '';
    var entityB = entities.length > 1 ? entities[1] : entities[0] || '';
    stopPlayAll = false;

    console.log('--- Playing all 20 animations ---');
    for (var i = 0; i < ALL_TEMPLATES.length; i++) {
      if (stopPlayAll) { console.log('Stopped by user'); break; }
      var name = ALL_TEMPLATES[i];
      var params = {};

      if (SINGLE_ENTITY.indexOf(name) >= 0) params.entityPrefix = entityA;
      if (TWO_ENTITY.indexOf(name) >= 0) {
        params.entityPrefixA = entityA;
        params.entityPrefixB = entityB;
      }
      if (MULTI_ENTITY.indexOf(name) >= 0) params.entityPrefixes = entities;
      if (SCENE_WIDE.indexOf(name) >= 0) params.isIndoor = false;
      if (name === 'emanation') params.particleType = 'sparkle';
      if (name === 'motion_lines') params.direction = 'right';
      if (name === 'speech_bubble' || name === 'thought_bubble') params.text = 'Hello!';
      if (name === 'interjection') params.word = 'Wow!';

      var spec = { template: name, params: params };
      console.log((i + 1) + '/20: ' + name);
      try {
        await animRunner.play(spec);
      } catch (err) {
        console.error(name + ' error:', err);
      }
      if (!stopPlayAll) await new Promise(function(r) { setTimeout(r, 500); });
    }
    console.log('--- All animations complete ---');
  };

  window.stopAnimations = function() {
    stopPlayAll = true;
    animRunner.stopLoop();
  };

  // --- Handle WS messages ---
  function handleAnimation(msg) {
    if (!isAnimated) return;
    if (_sceneTransitioning) return;  // Ignore animations during scene transition
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
