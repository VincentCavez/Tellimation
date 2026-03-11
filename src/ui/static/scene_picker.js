// Tellimations Scene Picker
// Reusable thumbnail component for selection and story pages.
// Renders a small pixel-art preview from scene sprite_code.

var ScenePicker = (function() {
  'use strict';

  // Thumbnails are 1/2 the art grid (PW×PH → PW/2 × PH/2).
  // We render sprites at art-grid resolution then downsample by averaging
  // each 2x2 block of pixels, so nothing gets cropped.
  // Canvas is rendered at native 1:1; CSS handles display scaling.
  var THUMB_PW = Math.ceil(PW / 2);
  var THUMB_PH = Math.ceil(PH / 2);
  var DOWNSAMPLE_FACTOR = 2;

  /**
   * Downsample a full-resolution PixelBuffer (PW×PH) to 1/4 size (THUMB_PW×THUMB_PH)
   * by averaging every 2×2 block of pixels.
   */
  function downsampleBuffer(fullBuf) {
    var thumbBuf = new PixelBuffer(THUMB_PW, THUMB_PH);
    for (var ty = 0; ty < THUMB_PH; ty++) {
      for (var tx = 0; tx < THUMB_PW; tx++) {
        var sx = tx * DOWNSAMPLE_FACTOR;
        var sy = ty * DOWNSAMPLE_FACTOR;
        // Gather 2x2 block from full buffer
        var tr = 0, tg = 0, tb = 0;
        var count = 0;
        var bestEntity = '';
        for (var dy = 0; dy < DOWNSAMPLE_FACTOR; dy++) {
          for (var dx = 0; dx < DOWNSAMPLE_FACTOR; dx++) {
            var fi = (sy + dy) * fullBuf.width + (sx + dx);
            if (fi < fullBuf.data.length) {
              var p = fullBuf.data[fi];
              tr += p.r;
              tg += p.g;
              tb += p.b;
              count++;
              // Keep the most specific entity ID from the block
              if (p.e.length > bestEntity.length) bestEntity = p.e;
            }
          }
        }
        if (count > 0) {
          var ti = ty * THUMB_PW + tx;
          thumbBuf.data[ti].r = Math.round(tr / count);
          thumbBuf.data[ti].g = Math.round(tg / count);
          thumbBuf.data[ti].b = Math.round(tb / count);
          thumbBuf.data[ti].e = bestEntity;
        }
      }
    }
    return thumbBuf;
  }

  /**
   * Create a thumbnail card element for a scene.
   *
   * Canvas is rendered at native 1:1 resolution (140×90) and CSS
   * handles display scaling via image-rendering: pixelated.
   *
   * @param {Object} scene - Scene data with manifest, sprite_code, branch_summary.
   * @returns {HTMLElement} A .thumbnail-card div element.
   */
  function createThumbnailCard(scene) {

    var card = document.createElement('div');
    card.className = 'thumbnail-card';

    var canvas = document.createElement('canvas');
    card.appendChild(canvas);

    // Summary text
    var summary = document.createElement('div');
    summary.className = 'summary';
    summary.textContent = scene.branch_summary || scene.narrative_text || '';
    card.appendChild(summary);

    // Render sprites — background may be async (image_background format)
    var spriteCode = scene.sprite_code || {};

    // Separate background from entity sprites
    var bgEntry = spriteCode.bg || null;
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

    function renderToCanvas(fullBuf) {
      fullBuf.snapshotBackground();
      // Render all entity sprites (sync)
      for (var i = 0; i < entityEids.length; i++) {
        try {
          renderSpriteEntry(entityEids[i], spriteCode[entityEids[i]], fullBuf);
        } catch (e) {
          console.warn('[ScenePicker] Failed to render sprite for', entityEids[i], e);
        }
      }
      // Downsample art grid (560x360) → 280x180 by averaging 2x2 blocks
      var thumbBuf = downsampleBuffer(fullBuf);
      // Render at native 1:1 resolution — CSS handles display scaling
      // via image-rendering: pixelated (avoids fractional byte offsets
      // that caused horizontal striping with non-integer scale factors).
      canvas.width = THUMB_PW;
      canvas.height = THUMB_PH;
      canvas.style.imageRendering = 'pixelated';
      canvas.style.imageRendering = 'crisp-edges';
      var thumbCtx = canvas.getContext('2d');
      thumbCtx.imageSmoothingEnabled = false;
      var imgData = thumbCtx.createImageData(THUMB_PW, THUMB_PH);
      var px = imgData.data;
      for (var idx = 0; idx < THUMB_PW * THUMB_PH; idx++) {
        var p = thumbBuf.data[idx];
        var off = idx * 4;
        px[off] = p.r;
        px[off + 1] = p.g;
        px[off + 2] = p.b;
        px[off + 3] = 255;
      }
      thumbCtx.putImageData(imgData, 0, 0);
    }

    var fullBuf = new PixelBuffer(PW, PH);

    if (bgEntry && bgEntry.format === 'image_background') {
      // Async: load background image first, then render entities on top
      var bgPromise = executeImageBackground(bgEntry, fullBuf);
      if (bgPromise && typeof bgPromise.then === 'function') {
        bgPromise.then(function() { renderToCanvas(fullBuf); });
      } else {
        renderToCanvas(fullBuf);
      }
    } else {
      // Sync: render background (palette_grid or code) then entities
      if (bgEntry) {
        try {
          renderSpriteEntry('bg', bgEntry, fullBuf);
        } catch (e) {
          console.warn('[ScenePicker] Failed to render bg', e);
        }
      }
      renderToCanvas(fullBuf);
    }

    return card;
  }

  /**
   * Render skeleton placeholder cards.
   *
   * @param {HTMLElement} container - DOM element to append skeletons into.
   * @param {number} count - Number of skeleton cards.
   */
  function renderSkeletons(container, count) {
    for (var i = 0; i < count; i++) {
      var card = document.createElement('div');
      card.className = 'thumbnail-card skeleton-card';

      var sCanvas = document.createElement('div');
      sCanvas.className = 'skeleton-canvas';
      card.appendChild(sCanvas);

      var sText = document.createElement('div');
      sText.className = 'skeleton-text';
      card.appendChild(sText);

      var sText2 = document.createElement('div');
      sText2.className = 'skeleton-text-2';
      card.appendChild(sText2);

      container.appendChild(card);
    }
  }

  return {
    createThumbnailCard: createThumbnailCard,
    renderSkeletons: renderSkeletons,
    THUMB_PW: THUMB_PW,
    THUMB_PH: THUMB_PH,
  };
})();

if (typeof module !== 'undefined' && module.exports) {
  module.exports = ScenePicker;
}
