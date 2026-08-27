'use strict';
// ═══════════════════════════════════════════════════════════════════
// Section 4: Animation Template Factories (8 families, 20 animations)
//   I=Identity, P=Property, A=Action, S=Space,
//   T=Time, R=Relation, Q=Quantity, D=Discourse
// ═══════════════════════════════════════════════════════════════════

// ── I1: Spotlight ──
// Scene darkens, target entity pulses gently with luminous halo that
// follows the entity's actual silhouette (not its bounding box).
// Uses pre-computed distance field from PixelBuffer.computeDistanceFields().
AnimationTemplates.register('spotlight', function(params) {
  var prefix = params.entityPrefix || '';
  var sceneMode = !prefix || prefix === '';
  console.log('[spotlight] prefix="' + prefix + '" sceneMode=' + sceneMode);
  var dimStrength = params.dimStrength != null ? params.dimStrength : 0.7;
  var glowStrength = params.glowStrength != null ? params.glowStrength : 0.35;
  var haloColor = params.haloColor || [255, 240, 180]; // warm yellow
  var maxHaloSize = params.maxHaloSize || 14;

  return function animate(buf, PW, PH, t) {
    var env = _easeEnvelope(t, 0.15, 0.15);
    // Gentle pulse: slow sine wave (3 full cycles over 3s)
    var pulse = 0.6 + 0.4 * Math.sin(t * Math.PI * 6);
    var glow = 1 + glowStrength * env * pulse;
    var dim = 1 - dimStrength * env;

    // Halo only in entity mode (no meaningful contour for bg)
    var df = sceneMode ? null : _getDistField(buf, prefix);
    var haloSize = Math.round(5 + (maxHaloSize - 5) * env * pulse);
    var hr = haloColor[0], hg = haloColor[1], hb = haloColor[2];
    var haloAlphaMax = 0.7 * env * pulse;

    for (var i = 0; i < buf.length; i++) {
      var p = buf[i];

      if (_isTargetPixel(p, prefix, sceneMode)) {
        // Brighten target (entity or bg in scene mode)
        p.r = Math.min(255, Math.round(p._r * glow));
        p.g = Math.min(255, Math.round(p._g * glow));
        p.b = Math.min(255, Math.round(p._b * glow));
      } else if (!sceneMode && df && df[i] > 0 && df[i] <= haloSize) {
        // Halo around entity contour (entity mode only)
        var falloff = 1 - df[i] / haloSize;
        var a = haloAlphaMax * falloff * falloff * falloff;
        p.r = Math.min(255, Math.round(p._r * dim * (1 - a) + hr * a));
        p.g = Math.min(255, Math.round(p._g * dim * (1 - a) + hg * a));
        p.b = Math.min(255, Math.round(p._b * dim * (1 - a) + hb * a));
      } else if (_isNonTargetPixel(p, prefix, sceneMode) || (!sceneMode && p.e && p.e !== '')) {
        // Dim non-target
        p.r = Math.round(p._r * dim);
        p.g = Math.round(p._g * dim);
        p.b = Math.round(p._b * dim);
      }
    }
  };
}, 3000);

// ── I2: Silhouette ──
// The entity turns into a flat dark silhouette in place, holds with a gentle
// breathing pulse, then snaps back to full color with a brief flash — asking
// "who or what is this?". No text, no added sprite.
AnimationTemplates.register('silhouette', _perTargetWrapper(function(params) {
  var prefix = params.entityPrefix || '';
  var holdMs = params.holdMs != null ? params.holdMs : 1200;
  var pulseAmplitude = params.pulseAmplitude != null ? params.pulseAmplitude : 0.05;
  var cycles = Math.max(1, Math.round(params.cycles != null ? params.cycles : 1));
  var sc = params.silhouetteColor || [15, 15, 25];

  // Phase fractions within ONE cycle, derived from real-ms proportions.
  var FADE_MS = 150, FLASH_MS = 120, PAUSE_MS = 400;
  var cycleMs = FADE_MS + holdMs + FLASH_MS + PAUSE_MS;
  var fFade = FADE_MS / cycleMs;
  var fHoldEnd = (FADE_MS + holdMs) / cycleMs;
  var fFlashEnd = (FADE_MS + holdMs + FLASH_MS) / cycleMs;

  return function animate(buf, PW, PH, t) {
    // Position within the current cycle (0..1)
    var ct = (t * cycles) % 1;
    if (t >= 1) ct = 1;

    // Collect entity pixels + bounds (layer preferred: includes covered pixels)
    var minX = PW, maxX = 0, minY = PH, maxY = 0;
    var indices = [];
    var layerData = _getEntityLayer(buf, prefix);
    if (layerData && layerData.length > 0) {
      for (var k = 0; k < layerData.length; k++) {
        var li = layerData[k];
        var lx = li.idx % PW, ly = Math.floor(li.idx / PW);
        if (lx < minX) minX = lx; if (lx > maxX) maxX = lx;
        if (ly < minY) minY = ly; if (ly > maxY) maxY = ly;
        if (buf[li.idx].e && _isEntity(buf[li.idx].e, prefix)) indices.push(li.idx);
      }
    } else {
      for (var i = 0; i < buf.length; i++) {
        if (buf[i].e && _isEntity(buf[i].e, prefix)) {
          var x = i % PW, y = Math.floor(i / PW);
          indices.push(i);
          if (x < minX) minX = x; if (x > maxX) maxX = x;
          if (y < minY) minY = y; if (y > maxY) maxY = y;
        }
      }
    }
    if (indices.length === 0) return;
    var cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;

    if (ct < fHoldEnd) {
      // ── Fade-in + hold: flat silhouette, breathing (shrink-only pulse so the
      // nearest-pixel redraw never leaves holes) ──
      var fillProg = ct < fFade ? ct / fFade : 1;
      var holdProg = ct < fFade ? 0 : (ct - fFade) / (fHoldEnd - fFade);
      var scale = 1 - pulseAmplitude * (0.5 + 0.5 * Math.sin(holdProg * Math.PI * 4));

      // Blank visible entity pixels to behind-colors
      for (var k = 0; k < indices.length; k++) {
        var idx = indices[k];
        buf[idx].r = buf[idx]._br;
        buf[idx].g = buf[idx]._bg;
        buf[idx].b = buf[idx]._bb;
      }
      // Draw the silhouette at scaled positions around the anchor
      var source = layerData && layerData.length > 0 ? layerData : indices;
      var useLayers = !!(layerData && layerData.length > 0);
      for (var k = 0; k < source.length; k++) {
        var idx = useLayers ? source[k].idx : source[k];
        var px = idx % PW, py = Math.floor(idx / PW);
        var nx = Math.round(cx + (px - cx) * scale);
        var ny = Math.round(cy + (py - cy) * scale);
        if (nx < 0 || nx >= PW || ny < 0 || ny >= PH) continue;
        var nidx = ny * PW + nx;
        // During fade-in, blend original color toward the flat silhouette color
        buf[nidx].r = Math.round(buf[nidx]._r * (1 - fillProg) + sc[0] * fillProg);
        buf[nidx].g = Math.round(buf[nidx]._g * (1 - fillProg) + sc[1] * fillProg);
        buf[nidx].b = Math.round(buf[nidx]._b * (1 - fillProg) + sc[2] * fillProg);
      }
    } else if (ct < fFlashEnd) {
      // ── Instant reveal + brightness flash (colors already restored by the
      // engine each frame; just add a decaying white boost) ──
      var flashProg = (ct - fHoldEnd) / (fFlashEnd - fHoldEnd);
      var boost = (1 - flashProg) * 0.55;
      for (var k = 0; k < indices.length; k++) {
        _blendPixel(buf, indices[k], 255, 255, 255, boost);
      }
    }
    // else: pause — draw nothing, the engine restores the original frame
  };
}), 2500);

// ── S2: Stamp ──
// Phase 1 (0→0.3): entity lifts upward, black silhouette at original position.
// Phase 2 (0.3→0.5): entity pauses in the air.
// Phase 3 (0.5→0.65): sharp snap back to origin, no bounce.
// Phase 4 (0.65→0.85): crack radiations from landing point, then fade.
AnimationTemplates.register('stamp', _perTargetWrapper(function(params) {
  var prefix = params.entityPrefix || '';
  var maxLift = params.liftPixels || 44;
  var crackCount = params.crackCount != null ? params.crackCount : 12;

  return function animate(buf, PW, PH, t) {
    // ── Collect entity pixels and bounding box ──
    var minX = PW, maxX = 0, minY = PH, maxY = 0;
    var indices = [];
    var layerData = _getEntityLayer(buf, prefix);
    if (layerData && layerData.length > 0) {
      for (var k = 0; k < layerData.length; k++) {
        var li = layerData[k];
        var lx = li.idx % PW, ly = Math.floor(li.idx / PW);
        if (lx < minX) minX = lx; if (lx > maxX) maxX = lx;
        if (ly < minY) minY = ly; if (ly > maxY) maxY = ly;
        if (buf[li.idx].e && _isEntity(buf[li.idx].e, prefix)) {
          indices.push(li.idx);
        }
      }
    } else {
      for (var i = 0; i < buf.length; i++) {
        if (buf[i].e && _isEntity(buf[i].e, prefix)) {
          var x = i % PW, y = Math.floor(i / PW);
          indices.push(i);
          if (x < minX) minX = x; if (x > maxX) maxX = x;
          if (y < minY) minY = y; if (y > maxY) maxY = y;
        }
      }
    }
    if (indices.length === 0) return;
    var ecx = Math.round((minX + maxX) / 2);
    var ecy = maxY; // bottom of entity = landing point

    // ── Compute vertical lift (prog: 0=origin, 1=fully lifted) ──
    var prog = 0;
    if (t < 0.3) {
      // Phase 1: lift up (ease-out)
      var lt = t / 0.3;
      prog = 1 - (1 - lt) * (1 - lt) * (1 - lt);
    } else if (t < 0.5) {
      // Phase 2: hold in the air
      prog = 1;
    } else if (t < 0.65) {
      // Phase 3: snap back (ease-in, accelerating)
      var lt = (t - 0.5) / 0.15;
      prog = 1 - lt * lt;
    } else {
      prog = 0;
    }
    var dispX = 0;
    var dispY = Math.round(-maxLift * prog);

    // ── Draw entity at displaced position ──
    if (dispX !== 0 || dispY !== 0) {
      // Blank visible pixels to behind-colors
      for (var k = 0; k < indices.length; k++) {
        var idx = indices[k];
        buf[idx].r = buf[idx]._br;
        buf[idx].g = buf[idx]._bg;
        buf[idx].b = buf[idx]._bb;
      }
      // Dark silhouette at original position (opacity 0.6)
      for (var k = 0; k < indices.length; k++) {
        var idx = indices[k];
        buf[idx].r = Math.round(buf[idx].r * 0.4);
        buf[idx].g = Math.round(buf[idx].g * 0.4);
        buf[idx].b = Math.round(buf[idx].b * 0.4);
      }
      // Draw entity at displaced position
      if (layerData) {
        for (var k = 0; k < layerData.length; k++) {
          var li = layerData[k];
          var py = Math.floor(li.idx / PW), px = li.idx % PW;
          var nx = px + dispX, ny = py + dispY;
          if (nx >= 0 && nx < PW && ny >= 0 && ny < PH) {
            var nidx = ny * PW + nx;
            buf[nidx].r = li.r; buf[nidx].g = li.g; buf[nidx].b = li.b;
          }
        }
      } else {
        for (var k = 0; k < indices.length; k++) {
          var idx = indices[k];
          var py = Math.floor(idx / PW), px = idx % PW;
          var nx = px + dispX, ny = py + dispY;
          if (nx >= 0 && nx < PW && ny >= 0 && ny < PH) {
            var nidx = ny * PW + nx;
            buf[nidx].r = buf[idx]._r; buf[nidx].g = buf[idx]._g; buf[nidx].b = buf[idx]._b;
          }
        }
      }
    } else if (layerData && t < 0.3) {
      // Restore all entity pixels from layer at origin
      for (var k = 0; k < layerData.length; k++) {
        var li = layerData[k];
        buf[li.idx].r = li.r; buf[li.idx].g = li.g; buf[li.idx].b = li.b;
        buf[li.idx].e = li.e;
      }
    }

    // ── Phase 4: crack radiations on landing ──
    if (t >= 0.62 && t < 0.85) {
      var crackT = (t - 0.62) / 0.23;
      var crackAlpha = crackT < 0.4 ? crackT / 0.4 : 1.0 - (crackT - 0.4) / 0.6;
      var crackLen = Math.round(crackT * 40);
      var N = crackCount;
      for (var ci = 0; ci < N; ci++) {
        var ang = (ci / N) * Math.PI; // only lower half (ground-level)
        var cosA = Math.cos(ang), sinA = Math.sin(ang);
        for (var d = 3; d < crackLen; d++) {
          var cpx = Math.round(ecx + cosA * d);
          var cpy = Math.round(ecy + sinA * d * 0.3); // flattened vertically
          if (cpx < 0 || cpx >= PW || cpy < 0 || cpy >= PH) break;
          var cidx = cpy * PW + cpx;
          var cv = Math.round(50 * crackAlpha);
          _blendPixel(buf, cidx, cv, cv, cv, crackAlpha * 0.6);
        }
      }
    }
  };
}), 3000);

// ── P1: Color Pop ──
// Sequential color group reveal: quantize entity pixels into ≤7 dominant
// color groups (6 hue sectors + 1 neutral), then cycle through groups
// at 400ms each, looping until the animation ends.
// Active group shows boosted saturation; inactive groups are desaturated.
AnimationTemplates.register('color_pop', function(params) {
  var prefix = params.entityPrefix || '';
  var sceneMode = !prefix || prefix === '';
  var desatStr = params.desaturationStrength != null ? params.desaturationStrength : 0.8;
  var satBoost = params.saturationBoost != null ? params.saturationBoost : 0.3;

  var GROUP_MS = 400; // fixed: 400ms per group, not configurable
  var DURATION_MS = 3000;

  // Cached on first frame
  var cachedGroupIndex = null;
  var groupCount = 0;

  return function animate(buf, PW, PH, t) {
    var env = _easeEnvelope(t, 0.10, 0.10);
    if (env < 0.01) return;

    // === INIT (first frame): 3D k-means on (hue, saturation, lightness) ===
    if (cachedGroupIndex === null) {
      cachedGroupIndex = new Int8Array(buf.length);
      for (var k = 0; k < cachedGroupIndex.length; k++) cachedGroupIndex[k] = -1;

      var MAX_K = 7;
      // Weights: hue is dominant, sat and lum help separate same-hue variants
      var W_HUE = 1.0, W_SAT = 0.5, W_LUM = 0.5;

      // Step 1: collect HSL of eligible pixels
      var pixels = []; // {i, h, s, l}
      for (var i = 0; i < buf.length; i++) {
        var p = buf[i];
        if (!_isTargetPixel(p, prefix, sceneMode)) continue;
        var r = p._r, g = p._g, bl = p._b;
        var mx = Math.max(r, g, bl), mn = Math.min(r, g, bl);
        var satV = (mx === 0) ? 0 : (mx - mn) / mx;
        if (mx < 40 || satV < 0.15) continue;
        var hue = _rgbToHue(r, g, bl);
        var lum = (mx + mn) / 510; // HSL lightness 0..1
        var satL = 0;
        if (mx !== mn) satL = (lum <= 0.5) ? (mx - mn) / (mx + mn) : (mx - mn) / (510 - mx - mn);
        pixels.push({ i: i, h: hue, s: satL, l: lum });
      }

      if (pixels.length > 0) {
        // Step 2: linearize hue circle
        pixels.sort(function(a, b) { return a.h - b.h; });
        var bestGap = 0, gapEnd = 0;
        for (var j = 0; j < pixels.length; j++) {
          var next = (j + 1) % pixels.length;
          var gap = (j === pixels.length - 1)
            ? (1 - pixels[j].h + pixels[0].h)
            : (pixels[next].h - pixels[j].h);
          if (gap > bestGap) { bestGap = gap; gapEnd = next; }
        }
        var hueOffset = pixels[gapEnd].h;
        // Store linearized hue back
        for (var j = 0; j < pixels.length; j++) {
          pixels[j].lh = (pixels[j].h - hueOffset + 1) % 1;
        }

        // Step 3: subsample for k-means (max 800 points)
        var SAMPLE = Math.min(pixels.length, 800);
        var step = pixels.length / SAMPLE;
        var samples = [];
        for (var j = 0; j < SAMPLE; j++) {
          var px = pixels[Math.floor(j * step)];
          samples.push({ lh: px.lh, s: px.s, l: px.l });
        }

        // Weighted distance function
        function hslDist2(a_lh, a_s, a_l, b_lh, b_s, b_l) {
          var dh = (a_lh - b_lh) * W_HUE;
          var ds = (a_s - b_s) * W_SAT;
          var dl = (a_l - b_l) * W_LUM;
          return dh * dh + ds * ds + dl * dl;
        }

        // Step 4: k-means with elbow method
        var bestK = 1, bestCentroids = [{ lh: 0.5, s: 0.5, l: 0.5 }];
        var prevVariance = Infinity;

        for (var tryK = 2; tryK <= Math.min(MAX_K, pixels.length); tryK++) {
          // Init centroids: pick evenly spaced samples
          var centroids = [];
          for (var c = 0; c < tryK; c++) {
            var si = Math.floor((c + 0.5) * SAMPLE / tryK);
            centroids.push({ lh: samples[si].lh, s: samples[si].s, l: samples[si].l });
          }

          // Run k-means (max 30 iterations)
          for (var iter = 0; iter < 30; iter++) {
            var sumsH = new Float32Array(tryK);
            var sumsS = new Float32Array(tryK);
            var sumsL = new Float32Array(tryK);
            var counts = new Uint32Array(tryK);
            for (var j = 0; j < SAMPLE; j++) {
              var minD = Infinity, best = 0;
              for (var c = 0; c < tryK; c++) {
                var d = hslDist2(samples[j].lh, samples[j].s, samples[j].l,
                                 centroids[c].lh, centroids[c].s, centroids[c].l);
                if (d < minD) { minD = d; best = c; }
              }
              sumsH[best] += samples[j].lh;
              sumsS[best] += samples[j].s;
              sumsL[best] += samples[j].l;
              counts[best]++;
            }
            var moved = false;
            for (var c = 0; c < tryK; c++) {
              if (counts[c] > 0) {
                var nh = sumsH[c] / counts[c], ns = sumsS[c] / counts[c], nl = sumsL[c] / counts[c];
                if (Math.abs(nh - centroids[c].lh) > 0.001 ||
                    Math.abs(ns - centroids[c].s) > 0.001 ||
                    Math.abs(nl - centroids[c].l) > 0.001) moved = true;
                centroids[c] = { lh: nh, s: ns, l: nl };
              }
            }
            if (!moved) break;
          }

          // Compute within-cluster variance
          var variance = 0;
          for (var j = 0; j < SAMPLE; j++) {
            var minD = Infinity;
            for (var c = 0; c < tryK; c++) {
              var d = hslDist2(samples[j].lh, samples[j].s, samples[j].l,
                               centroids[c].lh, centroids[c].s, centroids[c].l);
              if (d < minD) minD = d;
            }
            variance += minD;
          }

          // Elbow: stop if adding a cluster reduces variance by less than 15%
          if (prevVariance < Infinity && variance > prevVariance * 0.85) break;
          bestK = tryK;
          bestCentroids = centroids;
          prevVariance = variance;
        }

        // Sort centroids by linearized hue
        bestCentroids.sort(function(a, b) { return a.lh - b.lh; });

        // Step 5: assign ALL pixels to nearest centroid
        groupCount = bestK;
        for (var j = 0; j < pixels.length; j++) {
          var minD = Infinity, best = 0;
          for (var c = 0; c < bestK; c++) {
            var d = hslDist2(pixels[j].lh, pixels[j].s, pixels[j].l,
                             bestCentroids[c].lh, bestCentroids[c].s, bestCentroids[c].l);
            if (d < minD) { minD = d; best = c; }
          }
          cachedGroupIndex[pixels[j].i] = best;
        }

        // Remove empty groups
        var gCounts = new Uint32Array(groupCount);
        for (var i = 0; i < cachedGroupIndex.length; i++) {
          if (cachedGroupIndex[i] >= 0) gCounts[cachedGroupIndex[i]]++;
        }
        var remap = new Int8Array(groupCount);
        var newId = 0;
        for (var g = 0; g < groupCount; g++) remap[g] = (gCounts[g] > 0) ? newId++ : -1;
        groupCount = newId;
        for (var i = 0; i < cachedGroupIndex.length; i++) {
          if (cachedGroupIndex[i] >= 0) cachedGroupIndex[i] = remap[cachedGroupIndex[i]];
        }
      } else {
        groupCount = 0;
      }
    }

    // === PER-FRAME: which group is active? (400ms per group, looping) ===
    // Hard cuts between groups, no fade between cycles.
    var msNow = t * DURATION_MS;
    var cycleDur = groupCount * GROUP_MS;
    var cycleMs = msNow % cycleDur;
    var activeGroup = Math.min(groupCount - 1, Math.floor(cycleMs / GROUP_MS));

    // === PER-PIXEL ===
    for (var i = 0; i < buf.length; i++) {
      var p = buf[i];
      var gid = cachedGroupIndex[i];

      if (gid >= 0) {
        var L = Math.round(p._r * 0.299 + p._g * 0.587 + p._b * 0.114);
        var activity = (gid === activeGroup) ? 1 : 0;

        // Desaturate inactive pixels
        var desat = env * (1 - activity);

        // Boost saturation of active group via HSL manipulation
        if (activity > 0.01) {
          var or_ = p._r, og = p._g, ob = p._b;
          var mx = Math.max(or_, og, ob), mn = Math.min(or_, og, ob);
          var lum = (mx + mn) / 510; // 0..1
          var s = 0;
          if (mx !== mn) s = (lum <= 0.5) ? (mx - mn) / (mx + mn) : (mx - mn) / (510 - mx - mn);
          var h = _rgbToHue(or_, og, ob);
          var sNew = Math.min(1, s + satBoost * activity * env);
          var boosted = _hslToRgb(h, sNew, lum);
          p.r = Math.min(255, Math.round(boosted[0] * (1 - desat) + L * desat));
          p.g = Math.min(255, Math.round(boosted[1] * (1 - desat) + L * desat));
          p.b = Math.min(255, Math.round(boosted[2] * (1 - desat) + L * desat));
        } else {
          p.r = Math.round(p._r * (1 - desat) + L * desat);
          p.g = Math.round(p._g * (1 - desat) + L * desat);
          p.b = Math.round(p._b * (1 - desat) + L * desat);
        }
      } else if (_isTargetPixel(p, prefix, sceneMode)) {
        // Target pixel not in any group (outlines/low-sat): desaturate
        var L = Math.round(p._r * 0.299 + p._g * 0.587 + p._b * 0.114);
        p.r = Math.round(p._r * (1 - env) + L * env);
        p.g = Math.round(p._g * (1 - env) + L * env);
        p.b = Math.round(p._b * (1 - env) + L * env);
      } else if (_isNonTargetPixel(p, prefix, sceneMode)) {
        // Non-target: desaturate
        var L = Math.round(p._r * 0.299 + p._g * 0.587 + p._b * 0.114);
        var mix = desatStr * env;
        p.r = Math.round(p._r * (1 - mix) + L * mix);
        p.g = Math.round(p._g * (1 - mix) + L * mix);
        p.b = Math.round(p._b * (1 - mix) + L * mix);
      }
    }
  };
}, 3000);

// ── S1: Peek ──
// The occluding entity lifts like a flap hinged on one edge: its layer
// squeezes horizontally toward the hinge (with a slight darkening simulating
// rotation), exposing the behind-pixels. Optionally halos a hidden entity
// (entityPrefixB) while open. Closes back and pauses.
AnimationTemplates.register('peek', function(params) {
  var prefix = params.entityPrefix || '';
  var prefixB = params.entityPrefixB || '';
  var hingeSide = params.hingeSide || 'auto';
  var openness = params.openness != null ? params.openness : 0.8;
  var holdMs = params.holdMs != null ? params.holdMs : 1200;
  var glowStrength = params.glowStrength != null ? params.glowStrength : 0.5;

  var OPEN_MS = 300, CLOSE_MS = 300, PAUSE_MS = 400;
  var totalMs = OPEN_MS + holdMs + CLOSE_MS + PAUSE_MS;
  var fOpen = OPEN_MS / totalMs;
  var fHoldEnd = (OPEN_MS + holdMs) / totalMs;
  var fCloseEnd = (OPEN_MS + holdMs + CLOSE_MS) / totalMs;

  var resolvedHinge = null; // 'left' | 'right', computed once from the buffer

  return function animate(buf, PW, PH, t) {
    // Opening progress 0..1 (ease-out on open, ease-in on close)
    var prog;
    if (t < fOpen) {
      var ot = t / fOpen;
      prog = 1 - (1 - ot) * (1 - ot);
    } else if (t < fHoldEnd) {
      prog = 1;
    } else if (t < fCloseEnd) {
      var ctc = (t - fHoldEnd) / (fCloseEnd - fHoldEnd);
      prog = 1 - ctc * ctc;
    } else {
      return; // pause — engine restores the original frame
    }
    if (prog <= 0.001) return;

    // Collect occluder pixels + bounds
    var minX = PW, maxX = 0;
    var indices = [];
    var layerData = _getEntityLayer(buf, prefix);
    if (layerData && layerData.length > 0) {
      for (var k = 0; k < layerData.length; k++) {
        var li = layerData[k];
        var lx = li.idx % PW;
        if (lx < minX) minX = lx; if (lx > maxX) maxX = lx;
        if (buf[li.idx].e && _isEntity(buf[li.idx].e, prefix)) indices.push(li.idx);
      }
    } else {
      for (var i = 0; i < buf.length; i++) {
        if (buf[i].e && _isEntity(buf[i].e, prefix)) {
          var x = i % PW;
          indices.push(i);
          if (x < minX) minX = x; if (x > maxX) maxX = x;
        }
      }
    }
    if (indices.length === 0) return;

    // Resolve hinge side once
    if (resolvedHinge === null) {
      if (hingeSide === 'left' || hingeSide === 'right') {
        resolvedHinge = hingeSide;
      } else if (prefixB) {
        // Hinge on the side of the occluder farthest from the hidden element
        var bBounds = _computeEntityBounds(buf, PW, prefixB);
        if (bBounds.x2 >= 0) {
          resolvedHinge = bBounds.cx < (minX + maxX) / 2 ? 'right' : 'left';
        }
      }
      if (resolvedHinge === null) {
        // No hidden element: hinge on the side with LESS free space toward the
        // frame edge, so the flap opens toward open space
        var spaceLeft = minX, spaceRight = PW - 1 - maxX;
        resolvedHinge = spaceLeft <= spaceRight ? 'left' : 'right';
      }
    }
    var hingeX = resolvedHinge === 'left' ? minX : maxX;

    var scaleX = 1 - openness * prog;
    var darken = 1 - 0.2 * prog;

    // Blank visible occluder pixels to behind-colors (this IS the exposure)
    for (var k = 0; k < indices.length; k++) {
      var idx = indices[k];
      buf[idx].r = buf[idx]._br;
      buf[idx].g = buf[idx]._bg;
      buf[idx].b = buf[idx]._bb;
    }

    // Redraw the occluder squeezed toward the hinge (shrink-only → no holes)
    var source = layerData && layerData.length > 0 ? layerData : indices;
    var useLayers = !!(layerData && layerData.length > 0);
    for (var k = 0; k < source.length; k++) {
      var srcIdx, srcR, srcG, srcB;
      if (useLayers) {
        var li = source[k];
        srcIdx = li.idx; srcR = li.r; srcG = li.g; srcB = li.b;
      } else {
        srcIdx = source[k];
        srcR = buf[srcIdx]._r; srcG = buf[srcIdx]._g; srcB = buf[srcIdx]._b;
      }
      var px = srcIdx % PW, py = Math.floor(srcIdx / PW);
      var nx = Math.round(hingeX + (px - hingeX) * scaleX);
      if (nx < 0 || nx >= PW) continue;
      var nidx = py * PW + nx;
      buf[nidx].r = Math.round(srcR * darken);
      buf[nidx].g = Math.round(srcG * darken);
      buf[nidx].b = Math.round(srcB * darken);
    }

    // Halo on the hidden element while exposed (same distance-field technique
    // as spotlight, glow-only: no scene dimming)
    if (prefixB && prog > 0.3) {
      var df = _getDistField(buf, prefixB);
      if (df) {
        var haloSize = 10;
        var alphaMax = 0.7 * glowStrength * prog;
        for (var i = 0; i < buf.length; i++) {
          if (df[i] > 0 && df[i] <= haloSize) {
            var falloff = 1 - df[i] / haloSize;
            var a = alphaMax * falloff * falloff;
            _blendPixel(buf, i, 255, 240, 180, a);
          } else if (df[i] === 0 && _isEntity(buf[i].e || '', prefixB)) {
            var boost = 1 + 0.25 * glowStrength * prog;
            buf[i].r = Math.min(255, Math.round(buf[i].r * boost));
            buf[i].g = Math.min(255, Math.round(buf[i].g * boost));
            buf[i].b = Math.min(255, Math.round(buf[i].b * boost));
          }
        }
      }
    }
  };
}, 2500);

// ── P2: Emanation ──
// Custom multi-pixel sprites (icicles, vapor, stars, dust clouds, hearts)
// emanate from the entity contour in waves. Much more visible and evocative
// than single-pixel particle dots.

// Sprite drawing function for emanation types
function _drawEmanationSprite(buf, PW, PH, type, cx, cy, size, alpha) {
  if (alpha < 0.05) return;
  // HD scale: all sprites ×3 with per-instance random variation
  var hdSize = size * 3;
  var _blend = function(idx, r, g, b, a) {
    if (idx < 0 || idx >= buf.length) return;
    _blendPixel(buf, idx, r, g, b, a);
  };
  var px, py, idx;

  if (type === 'frost') {
    // Snowflake: 6-armed star with center dot
    var arm = Math.round(5 * hdSize);
    var thick = Math.max(1, Math.round(hdSize * 0.5));
    // Center dot
    for (var dy = -thick; dy <= thick; dy++) {
      for (var dx = -thick; dx <= thick; dx++) {
        if (dx * dx + dy * dy > (thick + 1) * (thick + 1)) continue;
        px = Math.round(cx + dx); py = Math.round(cy + dy);
        if (px >= 0 && px < PW && py >= 0 && py < PH)
          _blend(py * PW + px, 255, 255, 255, alpha);
      }
    }
    // 6 arms at 60° intervals with small branches
    for (var a = 0; a < 6; a++) {
      var ang = a * Math.PI / 3;
      var cosA = Math.cos(ang), sinA = Math.sin(ang);
      for (var d = 1; d <= arm; d++) {
        var armA = alpha * (1 - d / (arm + 1) * 0.4);
        var ax = Math.round(cx + cosA * d), ay = Math.round(cy + sinA * d);
        // Main arm pixel + thickness
        for (var tw = -Math.max(0, thick - 1); tw <= Math.max(0, thick - 1); tw++) {
          var tpx = ax + Math.round(-sinA * tw), tpy = ay + Math.round(cosA * tw);
          if (tpx >= 0 && tpx < PW && tpy >= 0 && tpy < PH)
            _blend(tpy * PW + tpx, 200, 230, 255, armA);
        }
        // Branch at ~40% and ~70% of arm length
        if (d === Math.round(arm * 0.4) || d === Math.round(arm * 0.7)) {
          var branchLen = Math.round((arm - d) * 0.6);
          var brAng1 = ang + Math.PI / 6, brAng2 = ang - Math.PI / 6;
          for (var bd = 1; bd <= branchLen; bd++) {
            var ba = alpha * (1 - bd / (branchLen + 1) * 0.5);
            var bx1 = Math.round(ax + Math.cos(brAng1) * bd);
            var by1 = Math.round(ay + Math.sin(brAng1) * bd);
            var bx2 = Math.round(ax + Math.cos(brAng2) * bd);
            var by2 = Math.round(ay + Math.sin(brAng2) * bd);
            if (bx1 >= 0 && bx1 < PW && by1 >= 0 && by1 < PH)
              _blend(by1 * PW + bx1, 180, 220, 255, ba);
            if (bx2 >= 0 && bx2 < PW && by2 >= 0 && by2 < PH)
              _blend(by2 * PW + bx2, 180, 220, 255, ba);
          }
        }
      }
    }
  } else if (type === 'steam') {
    // Tall wispy vapor column — gentle wide undulation
    var sH = Math.round(18 * hdSize), sW = Math.round(3 * hdSize);
    for (var dy = 0; dy < sH; dy++) {
      // Slow gentle wave (low frequency)
      var waveOff = Math.round(Math.sin(dy * 0.15 + cx * 0.3) * 2.5 * hdSize);
      // Width tapers toward top
      var taper = 1 - dy / sH * 0.5;
      var rowW = Math.max(1, Math.round(sW * taper));
      var rowAlpha = alpha * (1 - dy / sH * 0.7);
      for (var dx = 0; dx < rowW; dx++) {
        px = Math.round(cx + dx - rowW / 2 + waveOff);
        py = Math.round(cy - dy);
        if (px >= 0 && px < PW && py >= 0 && py < PH) {
          idx = py * PW + px;
          _blend(idx, 240, 240, 245, rowAlpha);
        }
      }
    }
  } else if (type === 'sparkle') {
    var arm = Math.round(5 * hdSize);
    var diagArm = Math.round(3.5 * hdSize);
    px = Math.round(cx); py = Math.round(cy);
    if (px >= 0 && px < PW && py >= 0 && py < PH) _blend(py * PW + px, 255, 255, 255, alpha);
    for (var d = 1; d <= arm; d++) {
      var armA = alpha * (1 - d / (arm + 1));
      var offsets = [[d, 0], [-d, 0], [0, d], [0, -d]];
      for (var o = 0; o < 4; o++) {
        px = Math.round(cx + offsets[o][0]); py = Math.round(cy + offsets[o][1]);
        if (px >= 0 && px < PW && py >= 0 && py < PH) _blend(py * PW + px, 255, 255, 180, armA);
      }
    }
    for (var d = 1; d <= diagArm; d++) {
      var diagA = alpha * (1 - d / (diagArm + 1)) * 0.6;
      var diags = [[d, d], [-d, d], [d, -d], [-d, -d]];
      for (var o = 0; o < 4; o++) {
        px = Math.round(cx + diags[o][0]); py = Math.round(cy + diags[o][1]);
        if (px >= 0 && px < PW && py >= 0 && py < PH) _blend(py * PW + px, 255, 255, 200, diagA);
      }
    }
  } else if (type === 'hearts') {
    // Single heart — used standalone (not via emanation)
    var heartMap = [
      [0,1,1,0,1,1,0],
      [1,1,1,1,1,1,1],
      [1,1,1,1,1,1,1],
      [0,1,1,1,1,1,0],
      [0,0,1,1,1,0,0],
      [0,0,0,1,0,0,0]
    ];
    var hScale = Math.max(2, Math.round(hdSize));
    for (var hy = 0; hy < 6; hy++) {
      for (var hx = 0; hx < 7; hx++) {
        if (!heartMap[hy][hx]) continue;
        for (var sy = 0; sy < hScale; sy++) {
          for (var sx = 0; sx < hScale; sx++) {
            px = Math.round(cx - 3 * hScale + hx * hScale + sx);
            py = Math.round(cy - 3 * hScale + hy * hScale + sy);
            if (px >= 0 && px < PW && py >= 0 && py < PH) {
              idx = py * PW + px;
              var isCenter = (hy >= 1 && hy <= 3 && hx >= 2 && hx <= 4);
              if (isCenter) {
                _blend(idx, 255, 120, 150, alpha);
              } else {
                _blend(idx, 230, 60, 100, alpha);
              }
            }
          }
        }
      }
    }
  } else if (type === 'hearts') {
    // Pixel art heart: 7×6 pattern
    var heartMap = [
      [0,1,1,0,1,1,0],
      [1,1,1,1,1,1,1],
      [1,1,1,1,1,1,1],
      [0,1,1,1,1,1,0],
      [0,0,1,1,1,0,0],
      [0,0,0,1,0,0,0]
    ];
    var hScale = Math.max(2, Math.round(hdSize));
    for (var hy = 0; hy < 6; hy++) {
      for (var hx = 0; hx < 7; hx++) {
        if (!heartMap[hy][hx]) continue;
        for (var sy = 0; sy < hScale; sy++) {
          for (var sx = 0; sx < hScale; sx++) {
            px = Math.round(cx - 3 * hScale + hx * hScale + sx);
            py = Math.round(cy - 3 * hScale + hy * hScale + sy);
            if (px >= 0 && px < PW && py >= 0 && py < PH) {
              idx = py * PW + px;
              var isCenter = (hy >= 1 && hy <= 3 && hx >= 2 && hx <= 4);
              if (isCenter) {
                _blend(idx, 255, 120, 150, alpha);
              } else {
                _blend(idx, 230, 60, 100, alpha);
              }
            }
          }
        }
      }
    }
  } else if (type === 'veins') {
    var veinsMap = [
      [0,0,0,1,0,1,0,0,0],
      [0,0,0,1,0,1,0,0,0],
      [0,0,0,1,0,1,0,0,0],
      [1,1,1,1,0,1,1,1,1],
      [0,0,0,0,0,0,0,0,0],
      [1,1,1,1,0,1,1,1,1],
      [0,0,0,1,0,1,0,0,0],
      [0,0,0,1,0,1,0,0,0],
      [0,0,0,1,0,1,0,0,0],
    ];
    var aScale = Math.max(2, Math.round(hdSize));
    var aW = 9, aH = 9;
    for (var ay = 0; ay < aH; ay++) {
      for (var ax = 0; ax < aW; ax++) {
        if (!veinsMap[ay][ax]) continue;
        for (var sy = 0; sy < aScale; sy++) {
          for (var sx = 0; sx < aScale; sx++) {
            px = Math.round(cx - 4 * aScale + ax * aScale + sx);
            py = Math.round(cy - 4 * aScale + ay * aScale + sy);
            if (px >= 0 && px < PW && py >= 0 && py < PH) {
              idx = py * PW + px;
              _blend(idx, 220, 40, 40, alpha);
            }
          }
        }
      }
    }
  } else if (type === 'drops') {
    var dropMap = [
      [0,0,1,0,0],
      [0,1,1,1,0],
      [0,1,1,1,0],
      [1,1,1,1,1],
      [1,1,1,1,1],
      [1,1,1,1,1],
      [0,1,1,1,0],
    ];
    var fScale = Math.max(2, Math.round(hdSize));
    for (var fy = 0; fy < 7; fy++) {
      for (var fx = 0; fx < 5; fx++) {
        if (!dropMap[fy][fx]) continue;
        for (var sy = 0; sy < fScale; sy++) {
          for (var sx = 0; sx < fScale; sx++) {
            px = Math.round(cx - 2 * fScale + fx * fScale + sx);
            py = Math.round(cy - 3 * fScale + fy * fScale + sy);
            if (px >= 0 && px < PW && py >= 0 && py < PH) {
              idx = py * PW + px;
              var isHighlight = (fy <= 1 && fx <= 2) || (fy === 2 && fx === 1);
              if (isHighlight) {
                _blend(idx, 200, 230, 255, alpha);
              } else {
                _blend(idx, 100, 160, 230, alpha);
              }
            }
          }
        }
      }
    }
  }
}

// ── P2a–P2f: Emanation variants ──
// Shared factory: builds an emanation animation given particleType and tint.
function _buildEmanation(params, pType, defaultTint) {
  var prefix = params.entityPrefix || '';
  var totalSprites = _clamp(params.particleCount || 18, 8, 40);
  var paramTint = params.tint;
  var tintSat = params.tintSaturation != null ? params.tintSaturation : 0.5;
  var tint;
  if (paramTint && Array.isArray(paramTint) && paramTint.length === 3) {
    tint = { r: paramTint[0], g: paramTint[1], b: paramTint[2] };
  } else {
    tint = { r: defaultTint[0], g: defaultTint[1], b: defaultTint[2] };
  }

  var moveConfigs = {
    steam:   { vy: -18, vx: 0, vxJitter: 6, vyJitter: 3, gravity: 0, sway: 1.5 },
    frost:   { vy: 8, vx: 0, vxJitter: 5, vyJitter: 2, gravity: 2, sway: 1.5 },
    sparkle: { vy: 0, vx: 0, vxJitter: 2, vyJitter: 2, gravity: 0, sway: 0 },
    hearts:  { vy: -12, vx: 0, vxJitter: 5, vyJitter: 2, gravity: 0, sway: 2.0 },
    veins:   { vy: 0, vx: 0, vxJitter: 1, vyJitter: 1, gravity: 0, sway: 0 },
    drops:   { vy: 15, vx: 0, vxJitter: 3, vyJitter: 3, gravity: 8, sway: 0.5 },
  };
  var mc = moveConfigs[pType] || moveConfigs.steam;

  var sprites = [];
  var waves = 4;
  var perWave = Math.ceil(totalSprites / waves);
  var _seed = 12345 + prefix.length * 7;
  function _rand() { _seed = (_seed * 16807 + 0) % 2147483647; return (_seed & 0xffff) / 0xffff; }

  for (var w = 0; w < waves; w++) {
    var waveTime = w / waves * 0.7;
    for (var s = 0; s < perWave && sprites.length < totalSprites; s++) {
      var sizeVar = 0.7 + _rand() * 0.6;
      var st = _rand();
      var spSideType = st < 0.4 ? 4 : Math.floor(_rand() * 4);
      sprites.push({
        spawnT: waveTime + _rand() * 0.08,
        side: _rand(), side2: _rand(), sideType: spSideType,
        vx: mc.vx + ((_rand() - 0.5) * 2) * mc.vxJitter,
        vy: mc.vy + ((_rand() - 0.5) * 2) * mc.vyJitter,
        size: sizeVar, x: 0, y: 0,
        maxAge: 0.5 + _rand() * 0.3,
        flicker: (pType === 'sparkle') ? _rand() : -1,
        initialized: false
      });
    }
  }

  return function animate(buf, PW, PH, t) {
    var env = _easeEnvelope(t, 0.15, 0.15);
    var ts = tintSat * env;
    for (var i = 0; i < buf.length; i++) {
      if (_isEntity(buf[i].e, prefix)) {
        buf[i].r = _clamp(buf[i]._r + Math.round(tint.r * ts), 0, 255);
        buf[i].g = _clamp(buf[i]._g + Math.round(tint.g * ts), 0, 255);
        buf[i].b = _clamp(buf[i]._b + Math.round(tint.b * ts), 0, 255);
      }
    }
    var bounds = _computeEntityBounds(buf, PW, prefix);
    if (bounds.x2 < 0) return;
    var bw = bounds.x2 - bounds.x1, bh = bounds.y2 - bounds.y1;
    for (var s = 0; s < sprites.length; s++) {
      var sp = sprites[s];
      if (t < sp.spawnT) continue;
      var age = t - sp.spawnT;
      if (age > sp.maxAge) continue;
      if (!sp.initialized) {
        if (sp.sideType === 0) { sp.x = bounds.x1 + sp.side * bw; sp.y = bounds.y1 - 2; }
        else if (sp.sideType === 1) { sp.x = bounds.x2 + 2; sp.y = bounds.y1 + sp.side * bh; }
        else if (sp.sideType === 2) { sp.x = bounds.x1 + sp.side * bw; sp.y = bounds.y2 + 2; }
        else if (sp.sideType === 3) { sp.x = bounds.x1 - 2; sp.y = bounds.y1 + sp.side * bh; }
        else { sp.x = bounds.x1 + sp.side * bw; sp.y = bounds.y1 + sp.side2 * bh; }
        sp.initialized = true;
      }
      var dt = 1 / 60;
      sp.x += sp.vx * 3 * dt; sp.y += sp.vy * 3 * dt; sp.vy += mc.gravity * 3 * dt;
      if (mc.sway > 0) sp.x += Math.sin(age * 8 + sp.side * 10) * mc.sway * dt * 15;
      var lifeRatio = age / sp.maxAge;
      var spriteAlpha = env;
      if (lifeRatio < 0.2) spriteAlpha *= lifeRatio / 0.2;
      else if (lifeRatio > 0.7) spriteAlpha *= (1 - lifeRatio) / 0.3;
      if (sp.flicker >= 0 && Math.sin(t * 20 + sp.flicker * 100) < -0.3) continue;
      _drawEmanationSprite(buf, PW, PH, pType, sp.x, sp.y, sp.size, spriteAlpha);
    }
  };
}

AnimationTemplates.register('emanation_shame', _perTargetWrapper(function(params) {
  return _buildEmanation(params, 'steam', [80, -40, -80]);
}), 2500);

AnimationTemplates.register('emanation_cold', _perTargetWrapper(function(params) {
  return _buildEmanation(params, 'frost', [-80, 0, 110]);
}), 2500);

AnimationTemplates.register('emanation_joy', _perTargetWrapper(function(params) {
  return _buildEmanation(params, 'sparkle', [60, 60, 40]);
}), 2500);

AnimationTemplates.register('emanation_love', _perTargetWrapper(function(params) {
  return _buildEmanation(params, 'hearts', [80, -25, 25]);
}), 2500);

AnimationTemplates.register('emanation_anger', _perTargetWrapper(function(params) {
  return _buildEmanation(params, 'veins', [100, -35, -35]);
}), 2500);

AnimationTemplates.register('emanation_fear', _perTargetWrapper(function(params) {
  return _buildEmanation(params, 'drops', [60, 60, 80]);
}), 2500);

// ── T1: Flashback ──
// Old film effect: entire scene goes B&W with projector flicker,
// vertical scratch lines, and dust specks — like a silent-era film reel.
// Scaffolds past tense — "this already happened."
AnimationTemplates.register('flashback', function(params) {
  var flickerIntensity = params.flickerIntensity != null ? params.flickerIntensity : 0.08;
  var scratchCount = params.scratchCount != null ? params.scratchCount : 3;

  return function animate(buf, PW, PH, t) {
    // Envelope: fade to B&W (0→0.08), hold (0.08→0.92), fade back (0.92→1)
    var desat;
    if (t < 0.08) desat = t / 0.08;
    else if (t < 0.92) desat = 1;
    else desat = 1 - (t - 0.92) / 0.08;

    // Projector brightness flicker (pseudo-random per frame)
    var frame = Math.floor(t * 180); // ~60fps × 3s
    var flickSeed = (frame * 16807 + 12345) % 2147483647;
    var flick = 1.0 + ((flickSeed % 1000) / 1000 - 0.5) * flickerIntensity * desat;

    // Desaturate ALL pixels + apply flicker
    for (var i = 0; i < buf.length; i++) {
      var L = buf[i]._r * 0.299 + buf[i]._g * 0.587 + buf[i]._b * 0.114;
      var r = buf[i]._r * (1 - desat) + L * desat;
      var g = buf[i]._g * (1 - desat) + L * desat;
      var b = buf[i]._b * (1 - desat) + L * desat;
      buf[i].r = _clamp(Math.round(r * flick), 0, 255);
      buf[i].g = _clamp(Math.round(g * flick), 0, 255);
      buf[i].b = _clamp(Math.round(b * flick), 0, 255);
    }

    if (desat < 0.3) return; // artifacts only when sufficiently B&W
    var alpha = Math.min((desat - 0.3) / 0.2, 1); // fade artifacts in

    // --- Vertical scratch lines ---
    // Change scratches every ~3 frames for a flickering look
    var scratchGroup = Math.floor(frame / 3);
    var seed = (scratchGroup * 7919 + 31337) % 2147483647;
    function rng() { seed = (seed * 16807 + 31) % 2147483647; return (seed & 0x7fffffff) / 0x7fffffff; }

    var numScratches = Math.max(1, Math.min(5, scratchCount === 0 ? 0 : 1 + Math.floor(rng() * scratchCount)));
    for (var s = 0; s < numScratches; s++) {
      var sx = Math.floor(rng() * PW);
      var scratchW = rng() < 0.7 ? 1 : 2; // mostly 1px wide
      var scratchA = (0.25 + rng() * 0.45) * alpha;
      var yStart = Math.floor(rng() * PH * 0.15);
      var yEnd = PH - Math.floor(rng() * PH * 0.15);
      var wobbleAmp = rng() * 1.5;
      var wobbleFreq = 3 + rng() * 4;

      for (var y = yStart; y < yEnd; y++) {
        var wx = sx + Math.round(wobbleAmp * Math.sin(y / PH * wobbleFreq * Math.PI));
        for (var w = 0; w < scratchW; w++) {
          var px = wx + w;
          if (px >= 0 && px < PW) {
            var idx = y * PW + px;
            _blendPixel(buf, idx, 0, 0, 0, scratchA);
          }
        }
      }
    }

    // --- Dust specks / black spots ---
    seed = (scratchGroup * 3571 + 99991) % 2147483647;
    var numSpecks = 4 + Math.floor(rng() * 8); // 4-11 specks per window
    for (var d = 0; d < numSpecks; d++) {
      var dx = Math.floor(rng() * PW);
      var dy = Math.floor(rng() * PH);
      var speckR = 1 + Math.floor(rng() * 2); // 1-2px radius
      var speckA = (0.35 + rng() * 0.4) * alpha;

      for (var sy = -speckR; sy <= speckR; sy++) {
        for (var sxx = -speckR; sxx <= speckR; sxx++) {
          if (sxx * sxx + sy * sy <= speckR * speckR) {
            var py = dy + sy, pxx = dx + sxx;
            if (py >= 0 && py < PH && pxx >= 0 && pxx < PW) {
              var si = py * PW + pxx;
              _blendPixel(buf, si, 0, 0, 0, speckA);
            }
          }
        }
      }
    }
  };
}, 3000);

// ── T2: Timelapse ──
// Single day→night→day cycle with smooth tinting, moon, and twinkling stars.
AnimationTemplates.register('timelapse', function(params) {
  var isIndoor = !!params.isIndoor;

  // Keyframes: single cycle day → dusk → night → dawn → day
  var keyframes = [
    { t: 0.000, mult: 1.00, tintR:   0, tintG:   0, tintB:   0 }, // day
    { t: 0.200, mult: 0.70, tintR:  60, tintG:  18, tintB:  38 }, // dusk (rose)
    { t: 0.400, mult: 0.22, tintR:  10, tintG:   8, tintB:  52 }, // night
    { t: 0.600, mult: 0.22, tintR:  10, tintG:   8, tintB:  52 }, // night (hold)
    { t: 0.800, mult: 0.50, tintR:  12, tintG:  22, tintB:  88 }, // dawn (light blue)
    { t: 1.000, mult: 1.00, tintR:   0, tintG:   0, tintB:   0 }, // day
  ];

  // Pre-generate deterministic star positions
  var NUM_STARS = 30;
  var starData = [];
  var _rng = 42;
  for (var si = 0; si < NUM_STARS; si++) {
    _rng = (_rng * 16807 + 7) % 2147483647;
    var sx = _rng % 280;
    _rng = (_rng * 16807 + 7) % 2147483647;
    var sy = _rng % 90; // upper half only
    _rng = (_rng * 16807 + 7) % 2147483647;
    var sPhase = (_rng % 1000) / 1000 * 6.28;
    _rng = (_rng * 16807 + 7) % 2147483647;
    var sSize = 1 + (_rng % 2);
    starData.push({ x: sx, y: sy, phase: sPhase, size: sSize });
  }

  return function animate(buf, PW, PH, t) {
    // Interpolate keyframes
    var kA = keyframes[0], kB = keyframes[keyframes.length - 1];
    for (var k = 0; k < keyframes.length - 1; k++) {
      if (t >= keyframes[k].t && t <= keyframes[k + 1].t) {
        kA = keyframes[k];
        kB = keyframes[k + 1];
        break;
      }
    }
    var span = kB.t - kA.t;
    var f = span > 0 ? (t - kA.t) / span : 1;

    var mult  = kA.mult  + (kB.mult  - kA.mult)  * f;
    var tintR = kA.tintR + (kB.tintR - kA.tintR) * f;
    var tintG = kA.tintG + (kB.tintG - kA.tintG) * f;
    var tintB = kA.tintB + (kB.tintB - kA.tintB) * f;

    // Apply color grading to all pixels
    for (var i = 0; i < buf.length; i++) {
      buf[i].r = _clamp(Math.round(buf[i]._r * mult + tintR), 0, 255);
      buf[i].g = _clamp(Math.round(buf[i]._g * mult + tintG), 0, 255);
      buf[i].b = _clamp(Math.round(buf[i]._b * mult + tintB), 0, 255);
    }

    // Night intensity for stars/moon (0 during day, 1 during night)
    var nightI = _clamp(1 - mult / 0.5, 0, 1);

    if (!isIndoor && nightI > 0.3) {
      var starAlpha = (nightI - 0.3) / 0.7;

      // Twinkling stars
      for (var si = 0; si < NUM_STARS; si++) {
        var sd = starData[si];
        var twinkle = 0.4 + 0.6 * (0.5 + 0.5 * Math.sin(t * 20 + sd.phase));
        var a = starAlpha * twinkle;
        if (a < 0.1) continue;
        // Scale star position to current buffer size
        var spx = Math.round(sd.x * PW / 280);
        var spy = Math.round(sd.y * PH / 180);
        for (var dy = 0; dy < sd.size; dy++) {
          for (var dx = 0; dx < sd.size; dx++) {
            var px = spx + dx, py = spy + dy;
            if (px >= 0 && px < PW && py >= 0 && py < PH) {
              _blendPixel(buf, py * PW + px, 255, 255, 240, a);
            }
          }
        }
      }

      // Moon (upper-right area)
      if (nightI > 0.5) {
        var moonAlpha = (nightI - 0.5) / 0.5;
        var moonCX = Math.round(PW * 0.82);
        var moonCY = Math.round(PH * 0.12);
        var moonR = Math.round(PH * 0.06);
        for (var my = moonCY - moonR - 1; my <= moonCY + moonR + 1; my++) {
          for (var mx = moonCX - moonR - 1; mx <= moonCX + moonR + 1; mx++) {
            if (mx < 0 || mx >= PW || my < 0 || my >= PH) continue;
            var mdx = mx - moonCX, mdy = my - moonCY;
            var md = Math.sqrt(mdx * mdx + mdy * mdy);
            if (md <= moonR) {
              _blendPixel(buf, my * PW + mx, 255, 255, 220, moonAlpha * 0.9);
            } else if (md <= moonR + 1) {
              // Soft glow edge
              _blendPixel(buf, my * PW + mx, 255, 255, 200, moonAlpha * 0.3);
            }
          }
        }
      }
    }
  };
}, 4000);

// ── A1: Motion Lines ──
// Fast burst movements with pauses between. Direction coherent with entity
// type (birds: any direction, others: left/right). Thick, visible speed streaks.
AnimationTemplates.register('motion_lines', _perTargetWrapper(function(params) {
  var prefix = params.entityPrefix || '';
  var dir = params.direction || 'right';   // 'left', 'right', 'any'
  var lineLen = _clamp(params.lineLength || 90, 50, 150);
  var amp = _clamp(params.amplitude || 50, 25, 75);
  var MAX_LINES = 80;

  // Streak colors: alternating grey shades
  var streakColors = [
    [160, 160, 160],  // mid grey
    [240, 240, 240],  // near white
    [100, 100, 100],  // dark grey
    [200, 200, 200],  // light grey
    [80, 80, 80],     // charcoal
    [220, 220, 220],  // pale grey
    [130, 130, 130],  // medium grey
  ];

  // Pre-generate per-streak variation: spacing 5-15px, random length, some thin
  var streakVariation = [];
  var _sv = 12345;
  for (var sv = 0; sv < MAX_LINES; sv++) {
    _sv = (_sv * 16807 + 31) % 2147483647;
    var gap = 5 + (_sv % 11);                        // 5–15px spacing
    _sv = (_sv * 16807 + 31) % 2147483647;
    var lenMult = 0.5 + (_sv % 1000) / 1000 * 1.0;  // 0.5× to 1.5× length
    _sv = (_sv * 16807 + 31) % 2147483647;
    var thin = (_sv % 5 === 0);                      // ~1/5 are 1px thin lines
    _sv = (_sv * 16807 + 31) % 2147483647;
    var alphaScale = thin ? (0.4 + (_sv % 1000) / 1000 * 0.3) : 1.0;
    streakVariation.push({ gap: gap, lenMult: lenMult, thin: thin, alphaScale: alphaScale });
  }

  // Build burst patterns based on direction mode
  var bursts;
  if (dir === 'any') {
    // Flying entities: varied directions
    bursts = [
      { start: 0.05, end: 0.17, dx: amp, dy: -Math.round(amp * 0.5) },
      { start: 0.28, end: 0.40, dx: -Math.round(amp * 0.8), dy: Math.round(amp * 0.3) },
      { start: 0.52, end: 0.64, dx: Math.round(amp * 0.6), dy: Math.round(amp * 0.7) },
      { start: 0.75, end: 0.87, dx: -amp, dy: -Math.round(amp * 0.4) },
    ];
  } else {
    // Ground entities: left/right alternation
    var sign = (dir === 'left') ? -1 : 1;
    bursts = [
      { start: 0.05, end: 0.17, dx: amp * sign, dy: 0 },
      { start: 0.28, end: 0.40, dx: -amp * sign, dy: 0 },
      { start: 0.52, end: 0.64, dx: Math.round(amp * 0.9) * sign, dy: 0 },
      { start: 0.75, end: 0.87, dx: -Math.round(amp * 0.8) * sign, dy: 0 },
    ];
  }

  // Cache outermost silhouette pixels per row/col (computed once on first frame)
  var silhouetteCache = null;

  return function animate(buf, PW, PH, t) {
    var bounds = _computeEntityBounds(buf, PW, prefix);
    if (bounds.x2 <= 0) return;

    // Pre-compute outermost entity pixels per row and column on first frame
    if (!silhouetteCache) {
      silhouetteCache = { byRow: {}, byCol: {} };
      for (var i = 0; i < buf.length; i++) {
        var e = buf[i].e;
        if (!_isEntity(e, prefix)) continue;
        var ex = i % PW, ey = (i - ex) / PW;
        if (!silhouetteCache.byRow[ey]) {
          silhouetteCache.byRow[ey] = { minX: ex, maxX: ex };
        } else {
          if (ex < silhouetteCache.byRow[ey].minX) silhouetteCache.byRow[ey].minX = ex;
          if (ex > silhouetteCache.byRow[ey].maxX) silhouetteCache.byRow[ey].maxX = ex;
        }
        if (!silhouetteCache.byCol[ex]) {
          silhouetteCache.byCol[ex] = { minY: ey, maxY: ey };
        } else {
          if (ey < silhouetteCache.byCol[ex].minY) silhouetteCache.byCol[ex].minY = ey;
          if (ey > silhouetteCache.byCol[ex].maxY) silhouetteCache.byCol[ex].maxY = ey;
        }
      }
    }

    // Determine current burst and shift amount
    var shiftX = 0, shiftY = 0;
    var activeBurst = null;
    for (var b = 0; b < bursts.length; b++) {
      var burst = bursts[b];
      if (t >= burst.start && t <= burst.end) {
        activeBurst = burst;
        var burstT = (t - burst.start) / (burst.end - burst.start);
        var ease = 1 - (1 - burstT) * (1 - burstT);
        shiftX = Math.round(burst.dx * ease);
        shiftY = Math.round(burst.dy * ease);
        break;
      }
    }

    // Shift entity if in a burst
    if (shiftX !== 0 || shiftY !== 0) {
      var pixels = _collectEntityPixels(buf, PW, prefix);
      _blankEntityPixels(buf, pixels);
      _redrawEntityPixels(buf, PW, PH, pixels, shiftX, shiftY);
    }

    // Draw speed streaks ONLY during bursts, using silhouette contour
    if (activeBurst) {
      var burstProgress = (t - activeBurst.start) / (activeBurst.end - activeBurst.start);
      var bDx = activeBurst.dx, bDy = activeBurst.dy;
      var bLen = Math.sqrt(bDx * bDx + bDy * bDy);
      if (bLen < 1) return;
      // Streak direction: opposite of movement
      var streakDirX = -bDx / bLen;
      var streakDirY = -bDy / bLen;
      // Perpendicular direction for spreading lines
      var perpX = -streakDirY;
      var perpY = streakDirX;

      var streakAlpha = 0.8 * Math.sin(burstProgress * Math.PI);

      // Build trailing silhouette points from per-row/col extremes
      var trailingPts = [];
      if (Math.abs(bDx) >= Math.abs(bDy)) {
        // Primarily horizontal movement: use per-row extremes
        var rows = Object.keys(silhouetteCache.byRow);
        for (var ri = 0; ri < rows.length; ri++) {
          var ry = parseInt(rows[ri]);
          var row = silhouetteCache.byRow[ry];
          // Trailing side: left edge if moving right, right edge if moving left
          trailingPts.push({ x: bDx > 0 ? row.minX : row.maxX, y: ry });
        }
        // Sort by y
        trailingPts.sort(function(a, b2) { return a.y - b2.y; });
      } else {
        // Primarily vertical movement: use per-col extremes
        var cols = Object.keys(silhouetteCache.byCol);
        for (var ci = 0; ci < cols.length; ci++) {
          var cx = parseInt(cols[ci]);
          var col = silhouetteCache.byCol[cx];
          // Trailing side: top edge if moving down, bottom edge if moving up
          trailingPts.push({ x: cx, y: bDy > 0 ? col.minY : col.maxY });
        }
        // Sort by x
        trailingPts.sort(function(a, b2) { return a.x - b2.x; });
      }

      // Fallback to bounding box if no silhouette points (1px steps = dense coverage)
      if (trailingPts.length < 3) {
        trailingPts = [];
        var entityH = bounds.y2 - bounds.y1;
        var entityW = bounds.x2 - bounds.x1;
        if (Math.abs(bDx) >= Math.abs(bDy)) {
          for (var fy = 0; fy <= entityH; fy++) {
            trailingPts.push({ x: bDx > 0 ? bounds.x1 : bounds.x2, y: bounds.y1 + fy });
          }
        } else {
          for (var fx = 0; fx <= entityW; fx++) {
            trailingPts.push({ x: bounds.x1 + fx, y: bDy > 0 ? bounds.y1 : bounds.y2 });
          }
        }
      }

      // Walk contour with pixel-based spacing (5–15px per line)
      var ptIdx = 0;
      var lineIdx = 0;
      while (ptIdx < trailingPts.length && lineIdx < MAX_LINES) {
        var sv2 = streakVariation[lineIdx];
        var pt = trailingPts[ptIdx];
        var startX = pt.x + shiftX;
        var startY = pt.y + shiftY;

        var sc = streakColors[lineIdx % streakColors.length];
        var curLen = Math.round(lineLen * sv2.lenMult * (0.6 + burstProgress * 0.4));
        var lineAlpha = streakAlpha * sv2.alphaScale;

        for (var d = 0; d < curLen; d++) {
          var fadeD = 1 - d / curLen;
          var dAlpha = lineAlpha * fadeD;
          // Thin lines stay 1px; normal lines taper 3→2→1
          var thickness;
          if (sv2.thin) {
            thickness = 1;
          } else {
            thickness = d < curLen * 0.3 ? 3 : (d < curLen * 0.7 ? 2 : 1);
          }
          var halfT = Math.floor(thickness / 2);

          for (var tw = -halfT; tw <= halfT; tw++) {
            var sx = Math.round(startX + streakDirX * d + perpX * tw * 0.7);
            var sy = Math.round(startY + streakDirY * d + perpY * tw * 0.7);
            if (sx >= 0 && sx < PW && sy >= 0 && sy < PH) {
              var si = sy * PW + sx;
              _blendPixel(buf, si, sc[0], sc[1], sc[2], dAlpha);
            }
          }
        }

        ptIdx += sv2.gap;
        lineIdx++;
      }
    }
  };
}), 3000);

// ── A2: Anticipation ──
// Entity compresses slightly, lurches forward, then freezes mid-motion.
// Like a momentum that was interrupted. Scaffolds missing/uncompleted action verbs.
AnimationTemplates.register('flip', _perTargetWrapper(function(params) {
  var prefix = params.entityPrefix || '';
  var speed = params.speed != null ? params.speed : 1.0;

  return function animate(buf, PW, PH, t) {
    // Apply speed scaling: remap t so the flip happens faster/slower
    t = Math.min(1, t * speed);
    // Collect entity pixels and bounding box.
    // Use entity layer for complete bounds (includes covered pixels).
    var minX = PW, maxX = 0, minY = PH, maxY = 0;
    var indices = [];
    var layerData = _getEntityLayer(buf, prefix);
    if (layerData && layerData.length > 0) {
      for (var k = 0; k < layerData.length; k++) {
        var li = layerData[k];
        var lx = li.idx % PW, ly = Math.floor(li.idx / PW);
        if (lx < minX) minX = lx; if (lx > maxX) maxX = lx;
        if (ly < minY) minY = ly; if (ly > maxY) maxY = ly;
        if (buf[li.idx].e && _isEntity(buf[li.idx].e, prefix)) {
          indices.push(li.idx);
        }
      }
    } else {
      for (var i = 0; i < buf.length; i++) {
        if (buf[i].e && _isEntity(buf[i].e, prefix)) {
          var x = i % PW, y = Math.floor(i / PW);
          indices.push(i);
          if (x < minX) minX = x; if (x > maxX) maxX = x;
          if (y < minY) minY = y; if (y > maxY) maxY = y;
        }
      }
    }
    if (indices.length === 0) return;

    var cx = (minX + maxX) / 2;
    var halfW = Math.max(cx - minX, maxX - cx);
    if (halfW === 0) return;

    // Blank visible entity pixels to behind-colors (don't touch covered pixels)
    for (var k = 0; k < indices.length; k++) {
      var idx = indices[k];
      buf[idx].r = buf[idx]._br;
      buf[idx].g = buf[idx]._bg;
      buf[idx].b = buf[idx]._bb;
    }

    // Draw COMPLETE entity at interpolated x positions using layer data.
    // dist = |px - cx| / halfW  (0 = on axis, 1 = at extremity)
    // mirrorX = 2*cx - px  (horizontal mirror)
    //
    // Go phase (t: 0→0.333): pixel slides from px toward mirrorX.
    //   Extremity (dist=1) starts at t=0; axis (dist=0) starts at t=0.083.
    //   All pixels arrive at mirrorX at t=0.333.
    //
    // Hold phase (t: 0.333→0.667): entity stays mirrored.
    //
    // Return phase (t: 0.667→1.0): pixel slides back from mirrorX to px.
    //   Same stagger: extremity starts at t=0.667, axis at t=0.75.
    //   All pixels back at px at t=1.0.
    var FLIP_END = 0.333;
    var HOLD_END = 0.667;
    var drawSource = layerData || [];
    var useLayers = !!layerData;
    if (!useLayers) drawSource = indices;

    for (var k = 0; k < drawSource.length; k++) {
      var srcIdx, srcR, srcG, srcB;
      if (useLayers) {
        var li = drawSource[k];
        srcIdx = li.idx; srcR = li.r; srcG = li.g; srcB = li.b;
      } else {
        srcIdx = drawSource[k];
        srcR = buf[srcIdx]._r; srcG = buf[srcIdx]._g; srcB = buf[srcIdx]._b;
      }
      var px = srcIdx % PW, py = Math.floor(srcIdx / PW);
      var dist = Math.abs(px - cx) / halfW;
      var mirrorX = 2 * cx - px;
      var newX;

      if (t <= FLIP_END) {
        // Flip: staggered from extremity to axis
        var tStart = (1 - dist) * 0.083;
        var p = _clamp((t - tStart) / (FLIP_END - tStart), 0, 1);
        newX = px + (mirrorX - px) * p;
      } else if (t <= HOLD_END) {
        // Hold mirrored
        newX = mirrorX;
      } else {
        // Return: staggered from extremity to axis
        var tStart2 = HOLD_END + (1 - dist) * 0.083;
        var p2 = _clamp((t - tStart2) / (1.0 - tStart2), 0, 1);
        newX = mirrorX + (px - mirrorX) * p2;
      }

      var nx = Math.round(newX);
      if (nx >= 0 && nx < PW) {
        var nidx = py * PW + nx;
        buf[nidx].r = srcR;
        buf[nidx].g = srcG;
        buf[nidx].b = srcB;
      }
    }
  };
}), 2000);

// ── R3: Causal Push ──
// Element A rushes toward element B + impact burst at collision.
// Scaffolds "A causes B" — consequence, causal connectors (because, so).
AnimationTemplates.register('causal_push', function(params) {
  var prefixA = params.entityPrefixA || params.entityPrefix || '';
  var prefixB = params.entityPrefixB || '';
  var knockPx = _clamp(params.knockPixels || 15, 5, 30);
  // Cached contour gap data
  var cachedRushDist = null;
  var cachedNdx = 0, cachedNdy = 0;
  var cachedImpactX = 0, cachedImpactY = 0;

  return function animate(buf, PW, PH, t) {
    var boundsA = _computeEntityBounds(buf, PW, prefixA);
    var boundsB = prefixB ? _computeEntityBounds(buf, PW, prefixB) : null;
    if (!boundsB || boundsA.x2 < 0) return;

    var pixelsA = _collectEntityPixels(buf, PW, prefixA);
    var pixelsB = _collectEntityPixels(buf, PW, prefixB);

    // Compute true contour gap on first frame (cached)
    if (cachedRushDist === null) {
      var cg = _computeContourGap(pixelsA, pixelsB, boundsA, boundsB);
      cachedNdx = cg.ndx;
      cachedNdy = cg.ndy;
      cachedRushDist = cg.gap;
      cachedImpactX = cg.impactX;
      cachedImpactY = cg.impactY;
    }

    var dxA = 0, dyA = 0, dxB = 0, dyB = 0;

    if (t < 0.35) {
      // A rushes toward B — ease-in acceleration
      var progress = t / 0.35;
      dxA = Math.round(cachedRushDist * progress * progress * cachedNdx);
      dyA = Math.round(cachedRushDist * progress * progress * cachedNdy);
    } else if (t < 0.40) {
      // Impact: A holds at contact, jitter both
      var jitter = Math.round((Math.random() - 0.5) * 3);
      dxA = Math.round(cachedRushDist * cachedNdx + jitter * cachedNdx);
      dyA = Math.round(cachedRushDist * cachedNdy + jitter * cachedNdy);
      dxB = Math.round(jitter * cachedNdx);
      dyB = Math.round(jitter * cachedNdy);
    } else {
      // Post-impact: A slides back gently, B gets knocked away
      var postT = (t - 0.40) / 0.60;
      // A slides back smoothly to origin
      var slideBack = 1 - postT;
      dxA = Math.round(cachedRushDist * slideBack * cachedNdx);
      dyA = Math.round(cachedRushDist * slideBack * cachedNdy);
      // B knocked away in A→B direction with damped bounce
      var decayB = (1 - postT);
      var bounceB = Math.sin(postT * Math.PI * 3) * decayB;
      dxB = Math.round(knockPx * bounceB * cachedNdx);
      dyB = Math.round(knockPx * bounceB * cachedNdy);
    }

    _blankEntityPixels(buf, pixelsA);
    _blankEntityPixels(buf, pixelsB);
    _redrawEntityPixels(buf, PW, PH, pixelsA, dxA, dyA);
    _redrawEntityPixels(buf, PW, PH, pixelsB, dxB, dyB);

    // Star burst at impact
    if (t >= 0.30 && t < 0.55) {
      // Impact point on B's contour (moves with B)
      var ipx = cachedImpactX + dxB;
      var ipy = cachedImpactY + dyB;
      var starAlpha;
      if (t < 0.37) {
        starAlpha = (t - 0.30) / 0.07;
      } else {
        starAlpha = 1 - (t - 0.37) / 0.18;
      }
      starAlpha = Math.max(0, Math.min(1, starAlpha));

      _drawStarBurst(buf, PW, PH, ipx, ipy, starAlpha, 30, 20);
    }
  };
}, 1500);

// ── C1: Sequential Glow ──
AnimationTemplates.register('sequential_glow', function(params) {
  var prefixes = params.entityPrefixes || [params.entityPrefix || ''];
  var n = prefixes.length;

  var loops = 2; // number of full cycles through all entities

  return function animate(buf, PW, PH, t) {
    // Two full loops: remap t so we cycle through all entities twice
    var loopT = (t * loops) % 1; // 0-1 within current loop
    var activeIdx = Math.min(Math.floor(loopT * n), n - 1);
    var phaseT = (loopT * n) % 1; // 0-1 within current entity's window
    var glow = 0.5 + 0.5 * Math.sin(phaseT * Math.PI);

    for (var i = 0; i < buf.length; i++) {
      var p = buf[i];
      if (!p.e || p.e === '') continue;

      var isActive = false;
      for (var a = 0; a <= activeIdx; a++) {
        if (_isEntity(p.e, prefixes[a])) {
          if (a === activeIdx) {
            isActive = true;
          }
          break;
        }
      }

      if (isActive) {
        // Glow the active entity
        var boost = 1 + 0.5 * glow;
        p.r = Math.min(255, Math.round(p._r * boost));
        p.g = Math.min(255, Math.round(p._g * boost));
        p.b = Math.min(255, Math.round(p._b * boost));
      } else {
        // Check if this is any of the listed entities (dim them)
        var isListed = false;
        for (var k = 0; k < n; k++) {
          if (_isEntity(p.e, prefixes[k])) {
            isListed = true; break;
          }
        }
        if (isListed) {
          p.r = Math.round(p._r * 0.6);
          p.g = Math.round(p._g * 0.6);
          p.b = Math.round(p._b * 0.6);
        }
      }
    }
  };
}, 1600);

// ── C2: Disintegration ──
// Entity pixels pixelate in blocks then scatter as particles, falling with drift.
// Phase 1 (0→0.15): brief red flash on entity.
// Phase 2 (0.15→1.0): progressive pixelation + particle scatter + fade.
AnimationTemplates.register('disintegration', function(params) {
  var prefix = params.entityPrefix || '';
  var sceneMode = !prefix || prefix === '';
  var driftAmount = params.driftAmount != null ? params.driftAmount : 0.3;
  var fallSpeed = params.fallSpeed != null ? params.fallSpeed : 1.0;

  var cachedPixels = null;
  var cachedBounds = null;
  var cachedBlockData = null; // per-block dissolution data

  return function animate(buf, PW, PH, t) {
    if (!cachedPixels) {
      cachedPixels = _collectEntityPixels(buf, PW, prefix, sceneMode);
      cachedBounds = _computeEntityBounds(buf, PW, prefix, sceneMode);

      // Pre-compute block-level dissolution data
      var bw = cachedBounds.x2 - cachedBounds.x1 + 1;
      var bh = cachedBounds.y2 - cachedBounds.y1 + 1;
      var blockSize = Math.max(2, Math.round(Math.min(bw, bh) / 12));
      cachedBlockData = [];

      // Group pixels into blocks
      var blockMap = {};
      for (var j = 0; j < cachedPixels.length; j++) {
        var p = cachedPixels[j];
        var bx = Math.floor((p.x - cachedBounds.x1) / blockSize);
        var by = Math.floor((p.y - cachedBounds.y1) / blockSize);
        var key = bx + ',' + by;
        if (!blockMap[key]) {
          var relY = (p.y - cachedBounds.y1) / Math.max(1, bh);
          blockMap[key] = {
            pixels: [],
            delay: Math.random() * 0.3 + relY * 0.2,
            dx: Math.round((Math.random() - 0.5) * bw * driftAmount),
            dy: Math.round(bh * (0.3 + 0.7 * (1 - relY)) * fallSpeed * (0.5 + Math.random() * 0.5)),
            avgR: 0, avgG: 0, avgB: 0
          };
        }
        blockMap[key].pixels.push(j);
        blockMap[key].avgR += p.r;
        blockMap[key].avgG += p.g;
        blockMap[key].avgB += p.b;
      }
      for (var key in blockMap) {
        var bd = blockMap[key];
        var n = bd.pixels.length;
        bd.avgR = Math.round(bd.avgR / n);
        bd.avgG = Math.round(bd.avgG / n);
        bd.avgB = Math.round(bd.avgB / n);
        cachedBlockData.push(bd);
      }
    }

    // Phase 1: brief flash
    if (t < 0.15) {
      var flashI = Math.sin(t / 0.15 * Math.PI) * 0.3;
      for (var j = 0; j < cachedPixels.length; j++) {
        var p = cachedPixels[j];
        var idx = p.y * PW + p.x;
        buf[idx].r = _clamp(Math.round(p.r + 80 * flashI), 0, 255);
        buf[idx].g = _clamp(Math.round(p.g - 20 * flashI), 0, 255);
        buf[idx].b = _clamp(Math.round(p.b - 20 * flashI), 0, 255);
      }
      return;
    }

    // Phase 2: pixelate + scatter
    var dt = (t - 0.15) / 0.85;

    // Blank entity
    _blankEntityPixels(buf, cachedPixels);

    // Draw each block
    for (var bi = 0; bi < cachedBlockData.length; bi++) {
      var bd = cachedBlockData[bi];
      var localT = Math.max(0, (dt - bd.delay * 0.5) / (1 - bd.delay * 0.5));
      if (localT <= 0) {
        // Block hasn't started dissolving yet — draw normally
        for (var pi = 0; pi < bd.pixels.length; pi++) {
          var p = cachedPixels[bd.pixels[pi]];
          var idx = p.y * PW + p.x;
          if (idx >= 0 && idx < buf.length) {
            buf[idx].r = p.r; buf[idx].g = p.g; buf[idx].b = p.b;
          }
        }
        continue;
      }

      // Fall progress (gravity-like quadratic)
      var fall = Math.min(1, localT);
      fall = fall * fall;

      // Fade
      var alpha = Math.max(0, 1 - localT * 1.2);
      if (alpha <= 0.01) continue;

      // Draw block at displaced position
      var offX = Math.round(bd.dx * fall);
      var offY = Math.round(bd.dy * fall);

      for (var pi = 0; pi < bd.pixels.length; pi++) {
        var p = cachedPixels[bd.pixels[pi]];
        var drawX = p.x + offX;
        var drawY = p.y + offY;
        if (drawX >= 0 && drawX < PW && drawY >= 0 && drawY < PH) {
          var di = drawY * PW + drawX;
          buf[di].r = _clamp(Math.round(buf[di]._br * (1 - alpha) + p.r * alpha), 0, 255);
          buf[di].g = _clamp(Math.round(buf[di]._bg * (1 - alpha) + p.g * alpha), 0, 255);
          buf[di].b = _clamp(Math.round(buf[di]._bb * (1 - alpha) + p.b * alpha), 0, 255);
        }
      }
    }
  };
}, 2000);

// ── C3: Missing Piece ──
// A jigsaw-piece-shaped hole opens in the scene where an absent entity would
// be, exposing a flat dark backing; the matching piece — bearing the absent
// entity's silhouette (pieceImageUrl, injected by the caller) — hovers at the
// nearest frame edge, then fades, and the hole closes. Scaffolds absence.
AnimationTemplates.register('missing_piece', function(params) {
  var prefix = params.entityPrefix || '';
  var holeScale = params.holeScale != null ? params.holeScale : 1.2;
  var edgePulse = params.edgePulse != null ? params.edgePulse : 0.5;
  var pieceOpacity = params.pieceOpacity != null ? params.pieceOpacity : 0.8;
  var holdMs = params.holdMs != null ? params.holdMs : 1500;
  var pieceImageUrl = params.pieceImageUrl || '';

  var OPEN_MS = 200, CLOSE_MS = 200, PAUSE_MS = 400;
  var totalMs = OPEN_MS + holdMs + CLOSE_MS + PAUSE_MS;
  var fOpen = OPEN_MS / totalMs;
  var fHoldEnd = (OPEN_MS + holdMs) / totalMs;
  var fCloseEnd = (OPEN_MS + holdMs + CLOSE_MS) / totalMs;

  var BACKING = [45, 50, 65];   // flat pixel backing inside the hole
  var EDGE = [160, 180, 210];   // pulsing edge color

  // Silhouette of the absent entity, loaded from its cut-out PNG (same
  // pipeline as the old ghost_outline: bounding box, mask, contour)
  var imgLoading = false, imgReady = false;
  var mask = null, contour = null, imgW = 0, imgH = 0;

  function loadPieceImage() {
    if (imgLoading || !pieceImageUrl) return;
    imgLoading = true;
    var img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = function() {
      var off = document.createElement('canvas');
      off.width = img.width; off.height = img.height;
      var ctx = off.getContext('2d');
      ctx.drawImage(img, 0, 0);
      var data = ctx.getImageData(0, 0, img.width, img.height).data;
      var x1 = img.width, y1 = img.height, x2 = 0, y2 = 0;
      for (var y = 0; y < img.height; y++) {
        for (var x = 0; x < img.width; x++) {
          if (data[(y * img.width + x) * 4 + 3] > 30) {
            if (x < x1) x1 = x; if (x > x2) x2 = x;
            if (y < y1) y1 = y; if (y > y2) y2 = y;
          }
        }
      }
      if (x2 <= x1) { imgLoading = false; return; }
      imgW = x2 - x1 + 1; imgH = y2 - y1 + 1;
      mask = []; contour = [];
      var dirs = [[-1,0],[1,0],[0,-1],[0,1]];
      for (var y = y1; y <= y2; y++) {
        for (var x = x1; x <= x2; x++) {
          if (data[(y * img.width + x) * 4 + 3] <= 30) continue;
          mask.push({ x: x - x1, y: y - y1 });
          for (var d = 0; d < 4; d++) {
            var nx = x + dirs[d][0], ny = y + dirs[d][1];
            if (nx < 0 || nx >= img.width || ny < 0 || ny >= img.height
                || data[(ny * img.width + nx) * 4 + 3] <= 30) {
              contour.push({ x: x - x1, y: y - y1 });
              break;
            }
          }
        }
      }
      imgReady = true;
    };
    img.src = pieceImageUrl;
  }

  // Procedural jigsaw mask: base rectangle + a round tab on the top edge and a
  // round notch cut into the right edge. Returns true when (px,py) — local
  // coords with (0,0) at the rect top-left — is inside the piece shape.
  function inPiece(px, py, w, h) {
    var tabR = Math.max(3, Math.round(Math.min(w, h) * 0.18));
    // Tab: sticks out above the top edge, centered
    var tcx = w / 2, tcy = 0;
    var dtx = px - tcx, dty = py - tcy;
    if (py < 0) return dtx * dtx + dty * dty <= tabR * tabR;
    if (px < 0 || px >= w || py >= h) return false;
    // Notch: bitten out of the right edge, centered vertically
    var ncx = w, ncy = h / 2;
    var dnx = px - ncx, dny = py - ncy;
    if (dnx * dnx + dny * dny <= tabR * tabR) return false;
    return true;
  }

  var cachedHole = null; // { x0, y0, w, h }

  function placeHole(buf, PW, PH, holeW, holeH) {
    // The entity is absent from this scene: sweep a grid of candidate spots
    // (same spirit as the old ghost_outline empty-spot search) and take the
    // one covering the FEWEST entity pixels — a hole opening over a present
    // character would break the "where the absent entity would be" metaphor.
    var xFracs = [0.5, 0.32, 0.68, 0.18, 0.82, 0.08, 0.92];
    var yFracs = [0.62, 0.45, 0.75];
    var best = null, bestCount = Infinity;
    for (var yi = 0; yi < yFracs.length; yi++) {
      for (var xi = 0; xi < xFracs.length; xi++) {
        var x0 = Math.round(PW * xFracs[xi] - holeW / 2);
        var y0 = Math.round(PH * yFracs[yi] - holeH / 2);
        x0 = _clamp(x0, 2, PW - holeW - 2);
        y0 = _clamp(y0, 2, PH - holeH - 2);
        var count = 0;
        for (var ty = y0; ty < y0 + holeH; ty += 2) {
          for (var tx = x0; tx < x0 + holeW; tx += 2) {
            var e = buf[ty * PW + tx].e;
            if (e && e !== '' && e !== 'background' && e.indexOf('bg') !== 0) count++;
          }
        }
        if (count < bestCount) {
          bestCount = count;
          best = { x0: x0, y0: y0, w: holeW, h: holeH };
          if (count === 0) return best;
        }
      }
    }
    return best;
  }

  return function animate(buf, PW, PH, t) {
    if (pieceImageUrl && !imgLoading && !imgReady) loadPieceImage();

    // Opening progress
    var prog;
    if (t < fOpen) {
      prog = t / fOpen;
    } else if (t < fHoldEnd) {
      prog = 1;
    } else if (t < fCloseEnd) {
      prog = 1 - (t - fHoldEnd) / (fCloseEnd - fHoldEnd);
    } else {
      return; // pause — engine restores the original frame
    }
    if (prog <= 0.001) return;

    // Hole size: from the absent entity's cut-out when loaded, else a default
    if (cachedHole === null) {
      var scale = PW / 1376; // source cut-outs are 1376x768
      var baseW = imgReady ? Math.round(imgW * scale) : Math.round(PW * 0.16);
      var baseH = imgReady ? Math.round(imgH * scale) : Math.round(PH * 0.28);
      var holeW = Math.max(16, Math.round(baseW * holeScale));
      var holeH = Math.max(16, Math.round(baseH * holeScale));
      cachedHole = placeHole(buf, PW, PH, holeW, holeH);
    }
    var hole = cachedHole;

    // ── Hole: flat backing growing from the center, jigsaw-shaped ──
    var hw = hole.w, hh = hole.h;
    var hcx = hole.x0 + hw / 2, hcy = hole.y0 + hh / 2;
    var pulse = 0.5 + 0.5 * Math.sin(t * Math.PI * 8);
    var edgeA = (0.35 + 0.65 * edgePulse * pulse) * prog;
    var tabR = Math.max(3, Math.round(Math.min(hw, hh) * 0.18));

    for (var py = -tabR; py < hh; py++) {
      var sy = hole.y0 + py;
      if (sy < 0 || sy >= PH) continue;
      for (var px = 0; px <= hw + 1; px++) {
        var sx = hole.x0 + px;
        if (sx < 0 || sx >= PW) continue;
        // Grow from center: only pixels within prog of the half-extent
        var fx = Math.abs(sx - hcx) / (hw / 2 + tabR);
        var fy = Math.abs(sy - hcy) / (hh / 2 + tabR);
        if (Math.max(fx, fy) > prog) continue;
        if (!inPiece(px, py, hw, hh)) continue;
        var idx = sy * PW + sx;
        // Edge if any 4-neighbour is outside the piece shape
        var isEdge = !inPiece(px - 1, py, hw, hh) || !inPiece(px + 1, py, hw, hh)
                  || !inPiece(px, py - 1, hw, hh) || !inPiece(px, py + 1, hw, hh);
        if (isEdge) {
          _blendPixel(buf, idx, EDGE[0], EDGE[1], EDGE[2], edgeA);
        } else if (prog > 0.98) {
          // Fully open: flat opaque backing — a hole, not a tinted veil
          buf[idx].r = BACKING[0]; buf[idx].g = BACKING[1]; buf[idx].b = BACKING[2];
        } else {
          _blendPixel(buf, idx, BACKING[0], BACKING[1], BACKING[2], 0.92 * prog);
        }
      }
    }

    // ── Floating piece at the nearest frame edge, with the entity silhouette ──
    var pieceA = pieceOpacity * prog;
    if (pieceA > 0.03) {
      var pw2 = Math.round(hw * 0.6), ph2 = Math.round(hh * 0.6);
      var margin = 6;
      var pieceX = hcx < PW / 2 ? margin : PW - pw2 - margin;
      var bob = Math.round(Math.sin(t * Math.PI * 4) * 3);
      var pieceY = _clamp(Math.round(hcy - ph2 / 2) + bob, 2, PH - ph2 - 2);

      for (var py = -Math.round(ph2 * 0.18); py < ph2; py++) {
        var sy = pieceY + py;
        if (sy < 0 || sy >= PH) continue;
        for (var px = 0; px <= pw2 + 1; px++) {
          var sx = pieceX + px;
          if (sx < 0 || sx >= PW) continue;
          if (!inPiece(px, py, pw2, ph2)) continue;
          var idx = sy * PW + sx;
          var isEdge = !inPiece(px - 1, py, pw2, ph2) || !inPiece(px + 1, py, pw2, ph2)
                    || !inPiece(px, py - 1, pw2, ph2) || !inPiece(px, py + 1, pw2, ph2);
          if (isEdge) {
            _blendPixel(buf, idx, EDGE[0], EDGE[1], EDGE[2], pieceA);
          } else {
            _blendPixel(buf, idx, 70, 76, 96, pieceA * 0.9);
          }
        }
      }

      // Entity silhouette inside the piece (dark fill + light contour)
      if (imgReady && mask) {
        var fitScale = Math.min((pw2 * 0.8) / imgW, (ph2 * 0.8) / imgH);
        var gw = Math.round(imgW * fitScale), gh = Math.round(imgH * fitScale);
        var gx0 = pieceX + Math.round((pw2 - gw) / 2);
        var gy0 = pieceY + Math.round((ph2 - gh) / 2);
        for (var mi = 0; mi < mask.length; mi++) {
          var mx = gx0 + Math.round(mask[mi].x * fitScale);
          var my = gy0 + Math.round(mask[mi].y * fitScale);
          if (mx >= 0 && mx < PW && my >= 0 && my < PH) {
            _blendPixel(buf, my * PW + mx, 25, 28, 40, pieceA * 0.85);
          }
        }
        for (var ci = 0; ci < contour.length; ci++) {
          var ex = gx0 + Math.round(contour[ci].x * fitScale);
          var ey = gy0 + Math.round(contour[ci].y * fitScale);
          if (ex >= 0 && ex < PW && ey >= 0 && ey < PH) {
            _blendPixel(buf, ey * PW + ex, EDGE[0], EDGE[1], EDGE[2], pieceA * 0.8);
          }
        }
      } else {
        // Fallback: a "?" would reintroduce a symbol — keep the piece plain.
      }
    }
  };
}, 3000);

// ── R1: Magnetism ──
AnimationTemplates.register('magnetism', function(params) {
  var prefixA = params.entityPrefixA || params.entityPrefix || '';
  var prefixB = params.entityPrefixB || '';
  // Large sparkles with wide spread
  var ps = new ParticleSystem({
    color: [255, 255, 200], size: 5,
    maxAge: 0.6, gravity: 0, drag: 0.6,
    spreadX: 16, spreadY: 16,
    vx: 0, vy: 0, vxJitter: 40, vyJitter: 40,
    fadeIn: 0.05, fadeOut: 0.5, flicker: true,
  });
  var sparkled = false;
  // Cached diagonal movement data (computed once on first frame)
  var cachedMoveA = null, cachedMoveB = null;
  var cachedNdx = 0, cachedNdy = 0; // unit direction A→B

  return function animate(buf, PW, PH, t) {
    var boundsA = _computeEntityBounds(buf, PW, prefixA);
    var boundsB = prefixB ? _computeEntityBounds(buf, PW, prefixB) : null;
    if (!boundsB) return;

    var pixelsA = _collectEntityPixels(buf, PW, prefixA);
    var pixelsB = _collectEntityPixels(buf, PW, prefixB);

    // Compute true contour gap on first frame (cached)
    if (cachedMoveA === null) {
      var cg = _computeContourGap(pixelsA, pixelsB, boundsA, boundsB);
      cachedNdx = cg.ndx;
      cachedNdy = cg.ndy;
      cachedMoveA = cg.gap / 2;
      cachedMoveB = cg.gap / 2;
    }

    var dxA = 0, dyA = 0, dxB = 0, dyB = 0;

    if (t < 0.4) {
      // Attract toward each other (diagonal)
      var progress = t / 0.4;
      dxA = Math.round(cachedMoveA * progress * cachedNdx);
      dyA = Math.round(cachedMoveA * progress * cachedNdy);
      dxB = Math.round(-cachedMoveB * progress * cachedNdx);
      dyB = Math.round(-cachedMoveB * progress * cachedNdy);
    } else if (t < 0.7) {
      // Hold touching
      dxA = Math.round(cachedMoveA * cachedNdx);
      dyA = Math.round(cachedMoveA * cachedNdy);
      dxB = Math.round(-cachedMoveB * cachedNdx);
      dyB = Math.round(-cachedMoveB * cachedNdy);

      // Sparkle at contact point (bigger burst)
      if (!sparkled) {
        var midX = Math.round((boundsA.cx + dxA + boundsB.cx + dxB) / 2);
        var midY = Math.round((boundsA.cy + dyA + boundsB.cy + dyB) / 2);
        ps.burst(midX, midY, 35);
        sparkled = true;
      }
    } else {
      // Drift back (diagonal)
      var release = (t - 0.7) / 0.3;
      dxA = Math.round(cachedMoveA * (1 - release) * cachedNdx);
      dyA = Math.round(cachedMoveA * (1 - release) * cachedNdy);
      dxB = Math.round(-cachedMoveB * (1 - release) * cachedNdx);
      dyB = Math.round(-cachedMoveB * (1 - release) * cachedNdy);
    }

    _blankEntityPixels(buf, pixelsA);
    _blankEntityPixels(buf, pixelsB);
    _redrawEntityPixels(buf, PW, PH, pixelsA, dxA, dyA);
    _redrawEntityPixels(buf, PW, PH, pixelsB, dxB, dyB);

    // Draw U-shaped horseshoe magnets centered on each entity, rotated to face each other
    var magnetAlpha = _easeEnvelope(t, 0.1, 0.15);
    if (magnetAlpha > 0.05) {
      // Magnet bitmap (opening RIGHT), 9w × 10h
      // 0=transparent, 1=blue, 2=dkBlue, 3=red, 4=dkRed, 5=grey, 6=white
      var MG = [
        [0,0,0,0,0,0,5,6,0],
        [0,0,2,1,1,1,5,6,0],
        [0,2,1,1,1,1,1,0,0],
        [2,1,1,0,0,0,0,0,0],
        [2,1,0,0,0,0,0,0,0],
        [4,3,0,0,0,0,0,0,0],
        [4,3,3,0,0,0,0,0,0],
        [0,4,3,3,3,3,3,0,0],
        [0,0,4,3,3,3,5,6,0],
        [0,0,0,0,0,0,5,6,0],
      ];
      var MGW = 9, MGH = 10;
      // Rotation center of bitmap (center of the U gap)
      var mcx = 3.5, mcy = 4.5;
      var pal = [
        null,
        [50, 130, 230],   // 1: blue
        [30,  80, 170],   // 2: dark blue
        [220, 50,  70],   // 3: red
        [160, 30,  50],   // 4: dark red
        [140, 140, 148],  // 5: grey
        [220, 222, 228],  // 6: white
      ];

      // Compute angle from A toward B (accounts for diagonal shifting)
      var aCx = boundsA.cx + dxA, aCy = boundsA.cy + dyA;
      var bCx = boundsB.cx + dxB, bCy = boundsB.cy + dyB;
      var angleA = Math.atan2(bCy - aCy, bCx - aCx); // A's opening faces B
      var angleB = angleA + Math.PI;                    // B's opening faces A
      var cosA = Math.cos(angleA), sinA = Math.sin(angleA);
      var cosB = Math.cos(angleB), sinB = Math.sin(angleB);

      // Draw rotated magnets using reverse-mapping (no holes) at 4x scale with black outline
      var mgScale = 4;
      var halfExt = Math.ceil(Math.sqrt(MGW * MGW + MGH * MGH) * mgScale / 2) + 3;
      var cardinals = [[0,-1],[0,1],[-1,0],[1,0]];
      for (var ent = 0; ent < 2; ent++) {
        var ex = ent === 0 ? aCx : bCx;
        var ey = ent === 0 ? aCy : bCy;
        var cosE = ent === 0 ? cosA : cosB;
        var sinE = ent === 0 ? sinA : sinB;
        for (var spy = -halfExt; spy <= halfExt; spy++) {
          for (var spx = -halfExt; spx <= halfExt; spx++) {
            // Inverse rotation: screen offset → bitmap coords
            var bx = (spx * cosE + spy * sinE) / mgScale + mcx;
            var by = (-spx * sinE + spy * cosE) / mgScale + mcy;
            var ibx = Math.floor(bx), iby = Math.floor(by);
            var inBounds = ibx >= 0 && ibx < MGW && iby >= 0 && iby < MGH;
            var ci = inBounds ? MG[iby][ibx] : 0;
            var cr, cg, cb;
            if (ci !== 0) {
              // Filled pixel
              var co = pal[ci];
              cr = Math.round(co[0] * magnetAlpha);
              cg = Math.round(co[1] * magnetAlpha);
              cb = Math.round(co[2] * magnetAlpha);
            } else {
              // Outline: draw black if any cardinal neighbor is filled (works even out-of-bounds)
              var hasNeighbor = false;
              for (var ni = 0; ni < 4 && !hasNeighbor; ni++) {
                var nnbx = ibx + cardinals[ni][0], nnby = iby + cardinals[ni][1];
                if (nnbx >= 0 && nnbx < MGW && nnby >= 0 && nnby < MGH && MG[nnby][nnbx] !== 0) hasNeighbor = true;
              }
              if (!hasNeighbor) continue;
              cr = 0; cg = 0; cb = 0;
            }
            _setPixel(buf, PW, PH, Math.round(ex + spx), Math.round(ey + spy), cr, cg, cb);
          }
        }
      }
    }

    ps.update(1 / 60);
    ps.draw(buf, PW, PH);
  };
}, 1500);

// ── R2: Repel ──
// Two elements push apart from each other, like same-polarity magnets.
// Exact symmetric of Magnetism (R1).
// Scaffolds incorrect grouping — "A and B went home" but only A left.
AnimationTemplates.register('repel', function(params) {
  var prefixA = params.entityPrefixA || params.entityPrefix || '';
  var prefixB = params.entityPrefixB || '';
  var repelPx = _clamp(params.repelPixels || 22, 2, 40);
  // Cached contour gap data
  var cachedGap = null, cachedNdx = 0, cachedNdy = 0;
  var cachedImpactX = 0, cachedImpactY = 0;

  return function animate(buf, PW, PH, t) {
    var boundsA = _computeEntityBounds(buf, PW, prefixA);
    var boundsB = prefixB ? _computeEntityBounds(buf, PW, prefixB) : null;
    if (!boundsB) return;

    var pixelsA = _collectEntityPixels(buf, PW, prefixA);
    var pixelsB = _collectEntityPixels(buf, PW, prefixB);

    // Compute true contour gap on first frame (cached)
    if (cachedGap === null) {
      var cg = _computeContourGap(pixelsA, pixelsB, boundsA, boundsB);
      cachedNdx = cg.ndx; cachedNdy = cg.ndy;
      cachedGap = cg.gap;
      cachedImpactX = cg.impactX; cachedImpactY = cg.impactY;
    }

    var ndx = cachedNdx, ndy = cachedNdy;
    var dxA = 0, dyA = 0, dxB = 0, dyB = 0;

    // Repel distance: 2× the original distance from center to entity
    // (distance from midpoint to entity center + gap/2, doubled)
    var distAB = Math.sqrt(
      (boundsA.cx - boundsB.cx) * (boundsA.cx - boundsB.cx) +
      (boundsA.cy - boundsB.cy) * (boundsA.cy - boundsB.cy)
    );
    var repelDist = distAB;  // push each entity by the full AB distance away

    // Phase 1 (0→0.333): Approach — linear, like magnetism (1s)
    // Phase 2 (0.333→0.5): Contact + propulsion (0.5s)
    // Phase 3 (0.5→0.667): Hold far apart (0.5s)
    // Phase 4 (0.667→1.0): Return (1s)
    var APPROACH_END = 0.333;
    var PROPEL_END = 0.5;
    var HOLD_END = 0.667;

    if (t < APPROACH_END) {
      // Approach — linear, close the contour gap
      var attract = t / APPROACH_END;
      dxA = Math.round(cachedGap / 2 * attract * ndx);
      dyA = Math.round(cachedGap / 2 * attract * ndy);
      dxB = Math.round(-cachedGap / 2 * attract * ndx);
      dyB = Math.round(-cachedGap / 2 * attract * ndy);
    } else if (t < PROPEL_END) {
      // Propulsion — ease-out, fast push to 2× original distance
      var progress = (t - APPROACH_END) / (PROPEL_END - APPROACH_END);
      progress = 1 - (1 - progress) * (1 - progress); // ease-out
      dxA = Math.round(-repelDist / 2 * progress * ndx);
      dyA = Math.round(-repelDist / 2 * progress * ndy);
      dxB = Math.round(repelDist / 2 * progress * ndx);
      dyB = Math.round(repelDist / 2 * progress * ndy);
    } else if (t < HOLD_END) {
      // Hold far apart
      dxA = Math.round(-repelDist / 2 * ndx);
      dyA = Math.round(-repelDist / 2 * ndy);
      dxB = Math.round(repelDist / 2 * ndx);
      dyB = Math.round(repelDist / 2 * ndy);
    } else {
      // Return — smoothstep
      var release = (t - HOLD_END) / (1.0 - HOLD_END);
      release = release * release * (3 - 2 * release);
      dxA = Math.round(-repelDist / 2 * (1 - release) * ndx);
      dyA = Math.round(-repelDist / 2 * (1 - release) * ndy);
      dxB = Math.round(repelDist / 2 * (1 - release) * ndx);
      dyB = Math.round(repelDist / 2 * (1 - release) * ndy);
    }

    _blankEntityPixels(buf, pixelsA);
    _blankEntityPixels(buf, pixelsB);
    _redrawEntityPixels(buf, PW, PH, pixelsA, dxA, dyA);
    _redrawEntityPixels(buf, PW, PH, pixelsB, dxB, dyB);

    // ── Bonk effect at contour collision point ──
    var bonkStart = 0.30, bonkPeak = 0.36, bonkEnd = 0.50;
    if (t >= bonkStart && t <= bonkEnd) {
      var bonkAlpha;
      if (t < bonkPeak) {
        bonkAlpha = (t - bonkStart) / (bonkPeak - bonkStart);
      } else {
        bonkAlpha = 1 - (t - bonkPeak) / (bonkEnd - bonkPeak);
      }
      bonkAlpha = _clamp(bonkAlpha, 0, 1);

      // Impact point from contour gap (moves with B during push)
      var ipx = cachedImpactX + dxB;
      var ipy = cachedImpactY + dyB;

      _drawStarBurst(buf, PW, PH, ipx, ipy, bonkAlpha, 40, 28);

      // Expanding impact ring
      var ringProgress = _clamp((t - bonkStart) / (bonkEnd - bonkStart), 0, 1);
      var ringRadius = 10 + ringProgress * 35;  // expands 10px -> 45px
      var ringAlpha = bonkAlpha * 0.7;
      var ringThick = 3;
      var ringSteps = Math.max(16, Math.ceil(ringRadius * 6));
      for (var s = 0; s < ringSteps; s++) {
        var angle = (s / ringSteps) * Math.PI * 2;
        var cosAngle = Math.cos(angle), sinAngle = Math.sin(angle);
        for (var rt = -ringThick; rt <= ringThick; rt++) {
          var rx = Math.round(ipx + cosAngle * (ringRadius + rt));
          var ry = Math.round(ipy + sinAngle * (ringRadius + rt));
          if (rx >= 0 && rx < PW && ry >= 0 && ry < PH) {
            var ri = ry * PW + rx;
            _blendPixel(buf, ri, 255, 220, 80, ringAlpha);
          }
        }
      }
    }
  };
}, 1500);


// ── D1: Speech Bubble ──
// Rounded-rectangle speech bubble with pointed tail, animated "..." dots.
AnimationTemplates.register('speech_bubble', _perTargetWrapper(function(params) {
  var prefix = params.entityPrefix || '';
  var bubbleText = params.bubbleText || '...';

  return function animate(buf, PW, PH, t) {
    var env = _easeEnvelope(t, 0.15, 0.15);
    if (env < 0.01) return;

    var bounds = _computeEntityBounds(buf, PW, prefix);
    if (bounds.x2 < 0) return;

    // Scale-based pop-in/out
    var scale = env < 1 ? Math.min(1.0, env * 1.1) : (t > 0.85 ? _clamp(1 - (t - 0.85) / 0.15, 0, 1) : 1.0);
    if (scale < 0.05) return;
    var alpha = _clamp(env, 0, 1);

    var rayCX = Math.round(bounds.cx);
    var entityTopY = bounds.y1;

    // Bubble dimensions (scaled)
    var bw = Math.round(100 * scale), bh = Math.round(50 * scale);
    var tailH = Math.round(15 * scale);
    var gap = 6;
    var cornerR = Math.round(10 * scale);

    var tailTipX = rayCX;
    var tailTipY = entityTopY - gap;
    var bcx = _clamp(rayCX, bw / 2 + 2, PW - bw / 2 - 2);
    var bcy = tailTipY - tailH - bh / 2;
    if (bcy < bh / 2 + 2) bcy = bh / 2 + 2;

    var left = Math.round(bcx - bw / 2), right = Math.round(bcx + bw / 2);
    var top = Math.round(bcy - bh / 2), bottom = Math.round(bcy + bh / 2);

    // Helper: is point inside rounded rect?
    function inRoundRect(px, py, extra) {
      var l = left - extra, r = right + extra, tp = top - extra, bt = bottom + extra;
      var cr = cornerR + extra;
      if (px < l || px > r || py < tp || py > bt) return false;
      // Check corners
      if (px < l + cr && py < tp + cr) {
        var dx = px - (l + cr), dy = py - (tp + cr);
        return dx * dx + dy * dy <= cr * cr;
      }
      if (px > r - cr && py < tp + cr) {
        var dx = px - (r - cr), dy = py - (tp + cr);
        return dx * dx + dy * dy <= cr * cr;
      }
      if (px < l + cr && py > bt - cr) {
        var dx = px - (l + cr), dy = py - (bt - cr);
        return dx * dx + dy * dy <= cr * cr;
      }
      if (px > r - cr && py > bt - cr) {
        var dx = px - (r - cr), dy = py - (bt - cr);
        return dx * dx + dy * dy <= cr * cr;
      }
      return true;
    }

    // Helper: is point inside tail triangle?
    function inTail(px, py, extra) {
      var tw = Math.round(12 * scale) + extra;
      var tBaseY = bottom;
      var tTipY = tailTipY + extra;
      if (py < tBaseY || py > tTipY) return false;
      var frac = (py - tBaseY) / Math.max(1, tTipY - tBaseY);
      var hw = tw * (1 - frac);
      var cx2 = Math.round(bcx + (tailTipX - bcx) * frac);
      return px >= cx2 - hw && px <= cx2 + hw;
    }

    // Render bubble: border (black) → fill (white) → tail
    var padX = bw / 2 + 4, padY = bh / 2 + tailH + gap + 4;
    var sx0 = Math.max(0, Math.round(bcx - padX));
    var sx1 = Math.min(PW - 1, Math.round(bcx + padX));
    var sy0 = Math.max(0, Math.round(bcy - padY));
    var sy1 = Math.min(PH - 1, tailTipY + 2);

    // Pass 1: black border (1px thick)
    for (var y = sy0; y <= sy1; y++) {
      for (var x = sx0; x <= sx1; x++) {
        if (inRoundRect(x, y, 1) || inTail(x, y, 1)) {
          _blendPixel(buf, y * PW + x, 0, 0, 0, alpha);
        }
      }
    }

    // Pass 2: white fill
    for (var y = sy0; y <= sy1; y++) {
      for (var x = sx0; x <= sx1; x++) {
        if (inRoundRect(x, y, 0) || inTail(x, y, 0)) {
          _blendPixel(buf, y * PW + x, 255, 255, 255, alpha);
        }
      }
    }

    // Pass 3: animated dots — show 1-3 dots cycling (2 full loops)
    var dotPhase = Math.floor(t * 8) % 4;
    var numDots = dotPhase === 0 ? 1 : (dotPhase === 1 ? 2 : 3);
    var dotR = Math.round(4 * scale);
    var dotSpacing = Math.round(18 * scale);
    var dotsW = (numDots - 1) * dotSpacing;
    for (var di = 0; di < numDots; di++) {
      var dx = Math.round(bcx - dotsW / 2 + di * dotSpacing);
      var dy = Math.round(bcy + 2 * scale); // slightly below center
      for (var y = dy - dotR; y <= dy + dotR; y++) {
        for (var x = dx - dotR; x <= dx + dotR; x++) {
          if (x < 0 || x >= PW || y < 0 || y >= PH) continue;
          var ddx = x - dx, ddy = y - dy;
          if (ddx * ddx + ddy * ddy <= dotR * dotR) {
            _blendPixel(buf, y * PW + x, 40, 40, 40, alpha);
          }
        }
      }
    }
  };
}), 1500);

// ── D2: Thought Bubble ──
// Cloud-shaped thought bubble with trailing circles, animated "..." dots.
AnimationTemplates.register('thought_bubble', _perTargetWrapper(function(params) {
  var prefix = params.entityPrefix || '';
  var bubbleText = params.bubbleText || '...';

  // Cloud shape = union of overlapping circles (relative to cloud center)
  var CC = [
    { dx:  0,  dy:  4, r: 28 },  // main body
    { dx:-22,  dy:-12, r: 19 },  // top-left bump
    { dx:  0,  dy:-24, r: 19 },  // top-center bump
    { dx: 22,  dy:-12, r: 19 },  // top-right bump
    { dx:-35,  dy:  4, r: 14 },  // left side
    { dx: 35,  dy:  4, r: 14 },  // right side
  ];

  function inCloud(px, py, bcx, bcy, extra) {
    for (var ci = 0; ci < CC.length; ci++) {
      var cdx = px - (bcx + CC[ci].dx), cdy = py - (bcy + CC[ci].dy);
      var r = CC[ci].r + extra;
      if (cdx * cdx + cdy * cdy <= r * r) return true;
    }
    return false;
  }

  return function animate(buf, PW, PH, t) {
    var env = _easeEnvelope(t, 0.15, 0.15);
    if (env < 0.01) return;
    var bounds = _computeEntityBounds(buf, PW, prefix);
    if (bounds.x2 < 0) return;

    var scale = env < 1 ? Math.min(1.0, env * 1.1) : (t > 0.85 ? _clamp(1 - (t - 0.85) / 0.15, 0, 1) : 1.0);
    if (scale < 0.05) return;
    var alpha = _clamp(env, 0, 1);

    var rayCX = Math.round(bounds.cx);
    var entityTopY = bounds.y1;

    var gap = 8;
    var cloudBottomDY = 32;
    var bcx = _clamp(rayCX, 55, PW - 55);
    var bcy = entityTopY - gap - cloudBottomDY - 10;
    if (bcy < 48) bcy = 48;

    // 1. Cloud border (black, 1px)
    var sxMin = Math.max(0, bcx - 55), sxMax = Math.min(PW - 1, bcx + 55);
    var syMin = Math.max(0, bcy - 48), syMax = Math.min(PH - 1, bcy + 38);
    for (var sy = syMin; sy <= syMax; sy++) {
      for (var sx = sxMin; sx <= sxMax; sx++) {
        if (inCloud(sx, sy, bcx, bcy, 1)) {
          _blendPixel(buf, sy * PW + sx, 0, 0, 0, alpha);
        }
      }
    }

    // 2. Cloud interior (white)
    for (var sy = syMin; sy <= syMax; sy++) {
      for (var sx = sxMin; sx <= sxMax; sx++) {
        if (inCloud(sx, sy, bcx, bcy, 0)) {
          _blendPixel(buf, sy * PW + sx, 255, 255, 255, alpha);
        }
      }
    }

    // 3. Trail circles: 2 small circles between cloud and entity
    var cloudBottom = bcy + cloudBottomDY;
    var tr1R = 5, tr2R = 3;
    var tc1y = cloudBottom + 4 + tr1R;
    var tc2y = tc1y + tr1R + tr2R + 2;
    var trailCircles = [{cx: bcx, cy: tc1y, r: tr1R}, {cx: bcx, cy: tc2y, r: tr2R}];
    for (var ti = 0; ti < trailCircles.length; ti++) {
      var tcx = trailCircles[ti].cx, tcy = trailCircles[ti].cy, tr = trailCircles[ti].r;
      // border
      for (var ty = tcy - tr - 1; ty <= tcy + tr + 1; ty++) {
        for (var tx = tcx - tr - 1; tx <= tcx + tr + 1; tx++) {
          if (tx < 0 || tx >= PW || ty < 0 || ty >= PH) continue;
          var tdx = tx - tcx, tdy = ty - tcy;
          if (tdx * tdx + tdy * tdy <= (tr + 1) * (tr + 1)) {
            _blendPixel(buf, ty * PW + tx, 0, 0, 0, alpha);
          }
        }
      }
      // fill
      for (var ty = tcy - tr; ty <= tcy + tr; ty++) {
        for (var tx = tcx - tr; tx <= tcx + tr; tx++) {
          if (tx < 0 || tx >= PW || ty < 0 || ty >= PH) continue;
          var tdx = tx - tcx, tdy = ty - tcy;
          if (tdx * tdx + tdy * tdy <= tr * tr) {
            _blendPixel(buf, ty * PW + tx, 255, 255, 255, alpha);
          }
        }
      }
    }

    // 4. Animated dots inside cloud (2 full loops)
    var dotPhase = Math.floor(t * 8) % 4;
    var numDots = dotPhase === 0 ? 1 : (dotPhase === 1 ? 2 : 3);
    var dotR = 4;
    var dotSpacing = 16;
    var dotsW = (numDots - 1) * dotSpacing;
    for (var di = 0; di < numDots; di++) {
      var dx = Math.round(bcx - dotsW / 2 + di * dotSpacing);
      var dy = bcy + 2;
      for (var y = dy - dotR; y <= dy + dotR; y++) {
        for (var x = dx - dotR; x <= dx + dotR; x++) {
          if (x < 0 || x >= PW || y < 0 || y >= PH) continue;
          var ddx = x - dx, ddy = y - dy;
          if (ddx * ddx + ddy * ddy <= dotR * dotR) {
            _blendPixel(buf, y * PW + x, 40, 40, 40, alpha);
          }
        }
      }
    }
  };
}), 1500);

// ── D3: Alert ──
// Bouncing "!" marks above entity with pop-in effect and entity pulse.
AnimationTemplates.register('alert', _perTargetWrapper(function(params) {
  var prefix = params.entityPrefix || '';
  var markCount = params.markCount != null ? Math.max(1, Math.min(3, params.markCount)) : 3;
  var color = params.color || [255, 220, 30];

  // "!" bitmap: 5 wide × 20 tall
  var eMark = [
    0,1,1,1,0,
    0,1,1,1,0,
    0,1,1,1,0,
    0,1,1,1,0,
    0,1,1,1,0,
    0,1,1,1,0,
    0,1,1,1,0,
    0,1,1,1,0,
    0,1,1,1,0,
    0,1,1,1,0,
    0,1,1,1,0,
    0,1,1,1,0,
    0,1,1,1,0,
    0,1,1,1,0,
    0,0,0,0,0,
    0,0,0,0,0,
    0,0,0,0,0,
    0,1,1,1,0,
    0,1,1,1,0,
    0,0,0,0,0,
  ];
  var eW = 5, eH = 20;
  var alertScale = 3;
  var spacing = 15;

  return function animate(buf, PW, PH, t) {
    var env = _easeEnvelope(t, 0.1, 0.15);
    if (env < 0.01) return;

    // Pop-in scale
    var scale = t < 0.1 ? Math.min(1.2, env * 1.5) : (t < 0.15 ? 1.2 - 0.2 * ((t - 0.1) / 0.05) : 1.0);
    if (t > 0.85) scale = _clamp(1 - (t - 0.85) / 0.15, 0, 1);
    var alpha = _clamp(env, 0, 1);

    var bounds = _computeEntityBounds(buf, PW, prefix);
    if (bounds.x2 < 0) return;

    var sW = eW * alertScale, sH = eH * alertScale;
    var totalW = sW * markCount + spacing * (markCount - 1);
    var baseCX = Math.round(bounds.cx);
    var baseY0 = Math.max(2, bounds.y1 - sH - 12);

    // Bounce effect
    var bounce = Math.abs(Math.sin(t * Math.PI * 6)) * 8 * env;

    for (var mi = 0; mi < markCount; mi++) {
      var x0 = Math.round(baseCX - totalW / 2) + mi * (sW + spacing);
      var markBounce = Math.round(bounce + Math.sin(t * 8 + mi) * 3);
      var y0 = baseY0 - markBounce;

      // Pass 1: black outline (4px)
      for (var ry = 0; ry < eH; ry++) {
        for (var rx = 0; rx < eW; rx++) {
          if (!eMark[ry * eW + rx]) continue;
          for (var sy = 0; sy < alertScale; sy++) {
            for (var sx = 0; sx < alertScale; sx++) {
              var bx = x0 + rx * alertScale + sx, by = y0 + ry * alertScale + sy;
              for (var oy = -4; oy <= 4; oy++) {
                for (var ox = -4; ox <= 4; ox++) {
                  if (ox * ox + oy * oy > 20) continue;
                  var px = bx + ox, py = by + oy;
                  if (px < 0 || px >= PW || py < 0 || py >= PH) continue;
                  _blendPixel(buf, py * PW + px, 0, 0, 0, alpha);
                }
              }
            }
          }
        }
      }

      // Pass 2: color fill
      for (var ry = 0; ry < eH; ry++) {
        for (var rx = 0; rx < eW; rx++) {
          if (!eMark[ry * eW + rx]) continue;
          for (var sy = 0; sy < alertScale; sy++) {
            for (var sx = 0; sx < alertScale; sx++) {
              var px = x0 + rx * alertScale + sx, py = y0 + ry * alertScale + sy;
              if (px < 0 || px >= PW || py < 0 || py >= PH) continue;
              _blendPixel(buf, py * PW + px, color[0], color[1], color[2], alpha);
            }
          }
        }
      }
    }

    // Entity pulse
    var pulse = 1 + 0.12 * env * (0.5 + 0.5 * Math.sin(t * Math.PI * 5));
    for (var i = 0; i < buf.length; i++) {
      if (_isEntity(buf[i].e, prefix)) {
        buf[i].r = Math.min(255, Math.round(buf[i]._r * pulse));
        buf[i].g = Math.min(255, Math.round(buf[i]._g * pulse));
        buf[i].b = Math.min(255, Math.round(buf[i]._b * pulse));
      }
    }
  };
}), 1200);

// ── D4: Interjection ──
// Comic-style starburst with irregular spikes. Displays word from child's speech.
// Pop-in effect. 3 layers: black outline → yellow border → white fill.
AnimationTemplates.register('interjection', _perTargetWrapper(function(params) {
  var prefix = params.entityPrefix || '';
  var word = params.word || '???';

  // Deterministic spike pattern
  var NUM_SPIKES = 18;
  var spikeHeights = [];
  var _rng = 31415;
  for (var si = 0; si < NUM_SPIKES; si++) {
    _rng = (_rng * 16807 + 7) % 2147483647;
    var base = (si % 2 === 0) ? 1.0 : 0.45;
    var jitter = (_rng % 1000) / 1000 * 0.4 - 0.2;
    spikeHeights.push(_clamp(base + jitter, 0.25, 1.0));
  }

  var cachedBCX = null, cachedBCY, cachedRX, cachedRY, cachedSpikeH;

  return function animate(buf, PW, PH, t) {
    var env = _easeEnvelope(t, 0.1, 0.2);
    if (env < 0.01) return;
    var alpha = env;

    // Pop-in: overshoot then settle
    var scale;
    if (t < 0.08) {
      scale = env * 1.3; // overshoot
    } else if (t < 0.15) {
      scale = 1.3 - 0.3 * ((t - 0.08) / 0.07); // settle
    } else if (t > 0.8) {
      scale = _clamp(1 - (t - 0.8) / 0.2, 0, 1);
    } else {
      scale = 1.0;
    }
    if (scale < 0.05) return;

    if (cachedBCX === null) {
      var displayText = word.toUpperCase();
      var textScale = 3;
      var textW = displayText.length * (_FONT_W + _FONT_SPACING) * textScale - _FONT_SPACING;
      cachedRX = Math.max(80, Math.round((textW + 50) / 2));
      cachedRY = Math.round(cachedRX * 0.55);
      cachedSpikeH = Math.max(25, Math.round(cachedRX * 0.4));
      var pad = cachedSpikeH + 8;
      var placed = false;

      if (prefix && prefix !== 'none') {
        var bounds = _computeEntityBounds(buf, PW, prefix);
        if (bounds.x2 >= 0) {
          cachedBCX = _clamp(Math.round(bounds.cx), cachedRX + pad, PW - cachedRX - pad);
          cachedBCY = bounds.y1 - cachedRY - cachedSpikeH - 6;
          placed = true;
        }
      }
      if (!placed) {
        cachedBCX = Math.round(PW / 2);
        cachedBCY = Math.round(cachedRY + cachedSpikeH + 10);
      }
    }

    var bcx = cachedBCX, bcy = cachedBCY;
    var rrx = Math.round(cachedRX * scale), rry = Math.round(cachedRY * scale);
    var spikeH = Math.round(cachedSpikeH * scale);

    function rInner(angle) {
      var cosA = Math.cos(angle), sinA = Math.sin(angle);
      var d = Math.sqrt((rry * cosA) * (rry * cosA) + (rrx * sinA) * (rrx * sinA));
      return d > 0.001 ? (rrx * rry / d) : rrx;
    }

    function rBurst(angle) {
      var ri = rInner(angle);
      var norm = ((angle / (2 * Math.PI)) % 1 + 1) % 1;
      var fi = norm * NUM_SPIKES;
      var i0 = Math.floor(fi) % NUM_SPIKES;
      var i1 = (i0 + 1) % NUM_SPIKES;
      var frac = fi - Math.floor(fi);
      var smooth = frac * frac * (3 - 2 * frac);
      var h = spikeHeights[i0] * (1 - smooth) + spikeHeights[i1] * smooth;
      var pointy = Math.max(0, 1 - Math.abs(frac - 0.5) * 2);
      return ri + spikeH * h * pointy * pointy;
    }

    var pad = spikeH + 10;
    var sx0 = Math.max(0, bcx - rrx - pad);
    var sx1 = Math.min(PW - 1, bcx + rrx + pad);
    var sy0 = Math.max(0, bcy - rry - pad);
    var sy1 = Math.min(PH - 1, bcy + rry + pad);

    var BLACK_W = 5, YELLOW_W = 8;

    // 3-layer burst: black → yellow → white
    for (var sy = sy0; sy <= sy1; sy++) {
      for (var sx = sx0; sx <= sx1; sx++) {
        var dx = sx - bcx, dy = sy - bcy;
        var r = Math.sqrt(dx * dx + dy * dy);
        var angle = Math.atan2(dy, dx);
        var rm = rBurst(angle);
        var pi = sy * PW + sx;

        if (r <= rm + BLACK_W) {
          if (r <= rm - YELLOW_W) {
            _blendPixel(buf, pi, 255, 255, 255, alpha);
          } else if (r <= rm) {
            _blendPixel(buf, pi, 255, 210, 20, alpha);
          } else {
            _blendPixel(buf, pi, 0, 0, 0, alpha);
          }
        }
      }
    }

    // Text (scaled 3×) with outline
    var displayText = word.toUpperCase();
    var textScale = 3;
    var charW = (_FONT_W + _FONT_SPACING) * textScale;
    var textW = displayText.length * charW - _FONT_SPACING;
    var textH = _FONT_H * textScale;
    var ttx = Math.round(bcx - textW / 2);
    var tty = Math.round(bcy - textH / 2);

    // Black outline
    var outlineOffsets = [[-2,0],[2,0],[0,-2],[0,2],[-2,-2],[2,-2],[-2,2],[2,2]];
    for (var oi = 0; oi < outlineOffsets.length; oi++) {
      var ox = outlineOffsets[oi][0], oy = outlineOffsets[oi][1];
      var cx2 = ttx + ox;
      for (var ci = 0; ci < displayText.length; ci++) {
        var ch = displayText[ci];
        var glyph = _PIXEL_FONT[ch];
        if (!glyph) { cx2 += charW; continue; }
        for (var gy = 0; gy < _FONT_H; gy++) {
          for (var gx = 0; gx < _FONT_W; gx++) {
            if (!glyph[gy * _FONT_W + gx]) continue;
            for (var sy2 = 0; sy2 < textScale; sy2++) {
              for (var sx2 = 0; sx2 < textScale; sx2++) {
                var drawX = cx2 + gx * textScale + sx2;
                var drawY = tty + oy + gy * textScale + sy2;
                if (drawX >= 0 && drawX < PW && drawY >= 0 && drawY < PH) {
                  _blendPixel(buf, drawY * PW + drawX, 0, 0, 0, alpha);
                }
              }
            }
          }
        }
        cx2 += charW;
      }
    }

    // Yellow fill
    var cx2 = ttx;
    for (var ci = 0; ci < displayText.length; ci++) {
      var ch = displayText[ci];
      var glyph = _PIXEL_FONT[ch];
      if (!glyph) { cx2 += charW; continue; }
      for (var gy = 0; gy < _FONT_H; gy++) {
        for (var gx = 0; gx < _FONT_W; gx++) {
          if (!glyph[gy * _FONT_W + gx]) continue;
          for (var sy2 = 0; sy2 < textScale; sy2++) {
            for (var sx2 = 0; sx2 < textScale; sx2++) {
              var drawX = cx2 + gx * textScale + sx2;
              var drawY = tty + gy * textScale + sy2;
              if (drawX >= 0 && drawX < PW && drawY >= 0 && drawY < PH) {
                _blendPixel(buf, drawY * PW + drawX, 240, 190, 30, alpha);
              }
            }
          }
        }
      }
      cx2 += charW;
    }
  };
}), 1500);


// ═══════════════════════════════════════════════════════════════════
// Exports
// ═══════════════════════════════════════════════════════════════════

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { AnimationTemplates, ParticleSystem, ParticlePresets };
}
