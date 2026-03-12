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

    // Get pre-computed distance field for this entity
    var df = buf._distFields && buf._distFields[prefix];
    var haloSize = Math.round(5 + (maxHaloSize - 5) * env * pulse);
    var hr = haloColor[0], hg = haloColor[1], hb = haloColor[2];
    var haloAlphaMax = 0.7 * env * pulse;

    // Single pass: dim non-target, brighten target, draw silhouette halo
    for (var i = 0; i < buf.length; i++) {
      var p = buf[i];
      var isTarget = _isEntity(p.e, prefix);

      if (isTarget) {
        // Brighten target entity
        p.r = Math.min(255, Math.round(p._r * glow));
        p.g = Math.min(255, Math.round(p._g * glow));
        p.b = Math.min(255, Math.round(p._b * glow));
      } else if (df && df[i] > 0 && df[i] <= haloSize) {
        // Pixel is near entity contour — draw halo with smooth cubic falloff
        var falloff = 1 - df[i] / haloSize;
        var a = haloAlphaMax * falloff * falloff * falloff;
        p.r = Math.min(255, Math.round(p._r * dim * (1 - a) + hr * a));
        p.g = Math.min(255, Math.round(p._g * dim * (1 - a) + hg * a));
        p.b = Math.min(255, Math.round(p._b * dim * (1 - a) + hb * a));
      } else if (p.e && p.e !== '') {
        // Dim everything else
        p.r = Math.round(p._r * dim);
        p.g = Math.round(p._g * dim);
        p.b = Math.round(p._b * dim);
      }
    }
  };
}, 3000);

// ── I2: Nametag ──
// Large beige nametag with entity type text, connected by an undulating
// red string. The tag pivots slightly at the string attachment point.
AnimationTemplates.register('nametag', function(params) {
  var prefix = params.entityPrefix || '';
  var bgColor = params.bgColor || [235, 215, 180]; // beige
  var borderColor = params.borderColor || [180, 155, 120]; // darker beige
  var textColor = params.textColor || [80, 50, 30]; // dark brown
  var stringColor = params.stringColor || [200, 50, 40]; // red

  // Extract entity type from prefix: "cat_01" → "CAT", "big_tree_01" → "BIG TREE"
  var entityType = prefix.replace(/_\d+$/, '').replace(/_/g, ' ').toUpperCase();
  // Pre-compute text width: each char is (_FONT_W + _FONT_SPACING) * scale, minus trailing space
  var textScale = 1;
  var charW = (_FONT_W + _FONT_SPACING) * textScale;
  var textW = entityType.length * charW - _FONT_SPACING;
  var textH = _FONT_H * textScale;

  var labelPadX = 6, labelPadY = 5;
  var labelW = Math.max(40, textW + labelPadX * 2);
  var labelH = textH + labelPadY * 2;

  return function animate(buf, PW, PH, t) {
    var env = _easeEnvelope(t, 0.15, 0.15);
    if (env < 0.01) return;

    var bounds = _computeEntityBounds(buf, PW, prefix);
    if (bounds.x2 < 0) return;

    // Decide side: offset tag left or right based on available space
    var spaceLeft = bounds.x1;
    var spaceRight = PW - 1 - bounds.x2;
    var offsetRight = spaceRight >= spaceLeft;
    var holeOnLeft = offsetRight; // hole on the side facing the entity

    // Tag position: static, offset to the side, vertically at entity center
    var tagGap = 8;
    var tagCenterY = Math.max(labelH / 2 + 2, Math.min(bounds.cy, PH - labelH / 2 - 2));
    var tagX; // top-left corner X of the tag
    if (offsetRight) {
      tagX = bounds.x2 + tagGap;
    } else {
      tagX = bounds.x1 - tagGap - labelW;
    }
    var tagY = Math.round(tagCenterY - labelH / 2); // top-left corner Y

    // Hole: circle INSIDE the tag, next to the border, at mid-height
    var holeRadius = 1;
    var holeCenterLy = Math.round(labelH / 2); // local Y in tag coords
    var holeCenterLx = holeOnLeft ? (1 + holeRadius + 1) : (labelW - 2 - holeRadius); // 1px inside border
    var holeScreenX = tagX + holeCenterLx;
    var holeScreenY = tagY + holeCenterLy;

    // String endpoint: actual entity contour pixel via horizontal ray-cast
    var stringDirX = offsetRight ? 1 : -1;
    var rayCY = Math.round(bounds.cy);
    var rayStartX = Math.round(bounds.cx);
    var stringEndX = rayStartX, stringEndY = rayCY;
    var rayFoundEntity = false;
    for (var rd = 1; rd <= Math.ceil(Math.max(bounds.x2 - bounds.cx, bounds.cx - bounds.x1)) + 2; rd++) {
      var rtx = rayStartX + stringDirX * rd;
      if (rtx < 0 || rtx >= PW) break;
      var rti = rayCY * PW + rtx;
      if (buf[rti].e && buf[rti].e.startsWith(prefix)) {
        stringEndX = rtx; stringEndY = rayCY;
        rayFoundEntity = true;
      } else if (rayFoundEntity) {
        break;
      }
    }
    // Fall back to bounding box edge if ray found nothing
    if (!rayFoundEntity) {
      stringEndX = offsetRight ? bounds.x2 : bounds.x1;
      stringEndY = rayCY;
    }

    // Draw the undulating red string from hole to entity edge (only string moves)
    var stringDx = stringEndX - holeScreenX;
    var stringDy = stringEndY - holeScreenY;
    var stringLen = Math.max(1, Math.sqrt(stringDx * stringDx + stringDy * stringDy));
    var steps = Math.round(stringLen);
    if (steps > 1) {
      var snx = -stringDy / stringLen, sny = stringDx / stringLen;
      for (var si2 = 0; si2 <= steps; si2++) {
        var progress = si2 / steps;
        var waveAmp = 2 * Math.sin(progress * Math.PI) * env;
        var waveOff = waveAmp * Math.sin(progress * Math.PI * 2.5 + t * Math.PI * 4);
        var px = Math.round(holeScreenX + stringDx * progress + snx * waveOff);
        var py = Math.round(holeScreenY + stringDy * progress + sny * waveOff);
        if (px >= 0 && px < PW && py >= 0 && py < PH) {
          var si = py * PW + px;
          _blendPixel(buf, si, stringColor[0], stringColor[1], stringColor[2], env);
        }
      }
    }

    // Draw label (static, no rotation)
    for (var ly = 0; ly < labelH; ly++) {
      for (var lx = 0; lx < labelW; lx++) {
        var drawX = tagX + lx;
        var drawY = tagY + ly;
        if (drawX < 0 || drawX >= PW || drawY < 0 || drawY >= PH) continue;

        // Round corners: diagonal notch at each corner (Manhattan distance < 3)
        var cxDist = Math.min(lx, labelW - 1 - lx);
        var cyDist = Math.min(ly, labelH - 1 - ly);
        if (cxDist + cyDist < 3) continue;

        var di = drawY * PW + drawX;
        var isBorder = (ly === 0 || ly === labelH - 1 || lx === 0 || lx === labelW - 1);

        // Hole: round circle inside the tag (not touching border)
        var hdx = lx - holeCenterLx, hdy = ly - holeCenterLy;
        var holeDist = Math.sqrt(hdx * hdx + hdy * hdy);
        var isHole = holeDist <= holeRadius;
        if (isHole) continue; // leave hole transparent (shows background)

        // Hole border: dark ring around the hole
        var isHoleRing = holeDist <= holeRadius + 1 && holeDist > holeRadius;

        // Red line: from hole edge to tag border, at mid-height (1px tall)
        var isRedLine = false;
        if (ly === holeCenterLy) {
          if (holeOnLeft && lx >= 0 && lx < holeCenterLx - holeRadius) isRedLine = true;
          if (!holeOnLeft && lx > holeCenterLx + holeRadius && lx <= labelW - 1) isRedLine = true;
        }

        var cr, cg, cb;
        if (isRedLine) {
          cr = stringColor[0]; cg = stringColor[1]; cb = stringColor[2];
        } else if (isHoleRing) {
          cr = borderColor[0]; cg = borderColor[1]; cb = borderColor[2];
        } else if (isBorder) {
          cr = borderColor[0]; cg = borderColor[1]; cb = borderColor[2];
        } else {
          cr = bgColor[0]; cg = bgColor[1]; cb = bgColor[2];
        }
        _blendPixel(buf, di, cr, cg, cb, env);
      }
    }

    // Draw text inside the label (static, no rotation)
    var textStartX = tagX + Math.round((labelW - textW) / 2);
    var textStartY = tagY + labelPadY;
    var upper = entityType;
    var cx2 = textStartX;
    for (var ci = 0; ci < upper.length; ci++) {
      var ch = upper[ci];
      var glyph = _PIXEL_FONT[ch];
      if (!glyph) { cx2 += charW; continue; }
      for (var gy = 0; gy < _FONT_H; gy++) {
        for (var gx = 0; gx < _FONT_W; gx++) {
          if (!glyph[gy * _FONT_W + gx]) continue;
          for (var sy2 = 0; sy2 < textScale; sy2++) {
            for (var sx2 = 0; sx2 < textScale; sx2++) {
              var drawX2 = cx2 + gx * textScale + sx2;
              var drawY2 = textStartY + gy * textScale + sy2;
              if (drawX2 >= 0 && drawX2 < PW && drawY2 >= 0 && drawY2 < PH) {
                var ti = drawY2 * PW + drawX2;
                _blendPixel(buf, ti, textColor[0], textColor[1], textColor[2], env);
              }
            }
          }
        }
      }
      cx2 += charW;
    }
  };
}, 3000);

// ── S2: Stamp ──
// Phase 1 (0→0.667): entity lifts diagonally (up-right), black silhouette at original position.
// Phase 2 (0.667→0.833): sharp ease-in snap back to original, no bounce.
// Phase 3 (0.833→1.0): crack lines radiate from all around the entity contour, then fade.
AnimationTemplates.register('stamp', function(params) {
  var prefix = params.entityPrefix || '';
  var maxLift = params.liftPixels || 22;
  // Diagonal direction: up-right (negative dy, positive dx)
  var liftDY = -maxLift;
  var liftDX = Math.round(maxLift * 0.7);

  return function animate(buf, PW, PH, t) {
    var LIFT_END = 0.667;
    var SNAP_END = 0.833;
    var crackRange = 14;

    // ── Collect entity pixels and bounding box ──
    // Use entity layer for complete bounds (includes pixels covered by higher-z entities).
    // Only visible pixels (owned in flat buffer) go into indices for drawing.
    var minX = PW, maxX = 0, minY = PH, maxY = 0;
    var indices = [];
    var layerData = buf._entityLayers && buf._entityLayers[prefix];
    if (layerData && layerData.length > 0) {
      for (var k = 0; k < layerData.length; k++) {
        var li = layerData[k];
        var lx = li.idx % PW, ly = Math.floor(li.idx / PW);
        if (lx < minX) minX = lx; if (lx > maxX) maxX = lx;
        if (ly < minY) minY = ly; if (ly > maxY) maxY = ly;
        if (buf[li.idx].e && buf[li.idx].e.startsWith(prefix)) {
          indices.push(li.idx);
        }
      }
    } else {
      for (var i = 0; i < buf.length; i++) {
        if (buf[i].e && buf[i].e.startsWith(prefix)) {
          var x = i % PW, y = Math.floor(i / PW);
          indices.push(i);
          if (x < minX) minX = x; if (x > maxX) maxX = x;
          if (y < minY) minY = y; if (y > maxY) maxY = y;
        }
      }
    }
    if (indices.length === 0) return;
    var ecx = (minX + maxX) / 2;
    var ecy = (minY + maxY) / 2;
    var halfW = (maxX - minX) / 2;
    var halfH = (maxY - minY) / 2;

    // ── Compute displacement (progress: 0=at origin, 1=fully lifted) ──
    var prog = 0;
    if (t <= LIFT_END) {
      prog = t / LIFT_END; // linear lift
    } else if (t <= SNAP_END) {
      var lt = (t - LIFT_END) / (SNAP_END - LIFT_END);
      // Ease-in quadratic snap: accelerates toward ground, no bounce
      prog = 1 - lt * lt;
    }
    var dispX = Math.round(liftDX * prog);
    var dispY = Math.round(liftDY * prog);

    // ── Draw entity at displaced position ──
    if (dispX !== 0 || dispY !== 0) {
      // Blank visible entity pixels to behind-colors (don't touch covered pixels)
      for (var k = 0; k < indices.length; k++) {
        var idx = indices[k];
        buf[idx].r = buf[idx]._br;
        buf[idx].g = buf[idx]._bg;
        buf[idx].b = buf[idx]._bb;
      }
      // Black silhouette at visible original positions
      for (var k = 0; k < indices.length; k++) {
        var idx = indices[k];
        buf[idx].r = 0; buf[idx].g = 0; buf[idx].b = 0;
      }
      // Draw COMPLETE entity at displaced position using layer data
      // (includes pixels that were covered by higher-z entities)
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
    } else if (layerData) {
      // prog===0: entity back at original position — restore ALL entity pixels from layer
      // (ensures covered pixels are also restored to their correct entity colors)
      for (var k = 0; k < layerData.length; k++) {
        var li = layerData[k];
        buf[li.idx].r = li.r; buf[li.idx].g = li.g; buf[li.idx].b = li.b;
        buf[li.idx].e = li.e;
      }
    }
    // Without layerData: prog===0 relies on buf.restore() which shows composite correctly

    // ── Phase 3: cracks radiating from all around the entity contour ──
    if (t > SNAP_END) {
      var crackT = (t - SNAP_END) / (1 - SNAP_END); // 0→1
      var crackGrow = Math.min(1, crackT / 0.6);
      var crackFade = crackT < 0.6 ? 1.0 : 1.0 - (crackT - 0.6) / 0.4;
      var maxCrackLen = 10;

      // 12 cracks evenly spaced around the entity contour.
      // Ray-cast from center outward to find the actual entity edge per direction.
      var N = 12;
      var maxSearch = Math.ceil(Math.max(halfW, halfH)) + 2;
      for (var ci = 0; ci < N; ci++) {
        var ang = (ci / N) * 2 * Math.PI;
        var cosA = Math.cos(ang), sinA = Math.sin(ang);
        // Find outermost entity pixel in this direction via ray-cast
        var cox = -1, coy = -1;
        var foundEntity = false;
        for (var d = 1; d <= maxSearch; d++) {
          var tx = Math.round(ecx + cosA * d);
          var ty = Math.round(ecy + sinA * d);
          if (tx < 0 || tx >= PW || ty < 0 || ty >= PH) break;
          var ti = ty * PW + tx;
          if (buf[ti].e && buf[ti].e.startsWith(prefix)) {
            cox = tx; coy = ty;
            foundEntity = true;
          } else if (foundEntity) {
            break; // stop at first background pixel after entity
          }
        }
        if (!foundEntity) continue; // no entity in this direction, skip crack
        // Crack extends outward from the actual contour pixel
        var crackLen = maxCrackLen - (ci % 3);
        var len = Math.round(crackLen * crackGrow);
        var zig = (ci % 2 === 0) ? 1 : -1;
        for (var cl = 1; cl <= len; cl++) {
          var zOff = (cl % 3 === 0) ? zig : 0;
          var cpx = Math.round(cox + cosA * cl + sinA * zOff);
          var cpy = Math.round(coy + sinA * cl - cosA * zOff);
          if (cpx >= 0 && cpx < PW && cpy >= 0 && cpy < PH) {
            var cidx = cpy * PW + cpx;
            var cv = Math.round(30 * crackFade);
            buf[cidx].r = cv; buf[cidx].g = cv; buf[cidx].b = cv;
          }
        }
      }
    }
  };
}, 3000);

// ── P1: Color Pop ──
// Rainbow canon: each pixel cycles through all rainbow colors, starting
// from a hue offset based on its original color. Pixels of the same
// original hue move in sync; different hues create a canon effect.
// Non-target entities are desaturated.
AnimationTemplates.register('color_pop', function(params) {
  var prefix = params.entityPrefix || '';
  var desatStr = params.desaturationStrength != null ? params.desaturationStrength : 0.8;
  var cycleCount = 2;  // full rainbow cycles over 3s

  return function animate(buf, PW, PH, t) {
    var env = _easeEnvelope(t, 0.12, 0.12);
    if (env < 0.01) return;

    for (var i = 0; i < buf.length; i++) {
      var p = buf[i];
      if (_isEntity(p.e, prefix)) {
        var origHue = _rgbToHue(p._r, p._g, p._b);
        var hue = ((t * cycleCount + origHue) % 1 + 1) % 1;
        var rgb = _hslToRgb(hue, 0.7, 0.5);
        p.r = Math.round(p._r * (1 - env) + rgb[0] * env);
        p.g = Math.round(p._g * (1 - env) + rgb[1] * env);
        p.b = Math.round(p._b * (1 - env) + rgb[2] * env);
      } else if (p.e && p.e !== '' && !p.e.startsWith('bg.')) {
        var L = Math.round(p._r * 0.299 + p._g * 0.587 + p._b * 0.114);
        var mix = desatStr * env;
        p.r = Math.round(p._r * (1 - mix) + L * mix);
        p.g = Math.round(p._g * (1 - mix) + L * mix);
        p.b = Math.round(p._b * (1 - mix) + L * mix);
      }
    }
  };
}, 3000);

// ── S1: Reveal ──
// Occluding layer becomes semi-transparent to show hidden elements.
AnimationTemplates.register('reveal', function(params) {
  var prefix = params.entityPrefix || '';
  var revealAlpha = params.revealAlpha != null ? params.revealAlpha : 0.7;

  return function animate(buf, PW, PH, t) {
    var env = _easeEnvelope(t, 0.25, 0.25);
    var alpha = revealAlpha * env;

    // Make occluding entity more transparent to reveal what's behind
    for (var i = 0; i < buf.length; i++) {
      if (_isEntity(buf[i].e, prefix)) {
        buf[i].r = Math.round(buf[i]._r * (1 - alpha) + buf[i]._br * alpha);
        buf[i].g = Math.round(buf[i]._g * (1 - alpha) + buf[i]._bg * alpha);
        buf[i].b = Math.round(buf[i]._b * (1 - alpha) + buf[i]._bb * alpha);
      }
    }

    // White opaque outline on border pixels throughout the animation
    if (env > 0.01) {
      var bounds = _computeEntityBounds(buf, PW, prefix);
      var neighbors = [[-1,0],[1,0],[0,-1],[0,1]];
      for (var y = bounds.y1; y <= bounds.y2; y++) {
        for (var x = bounds.x1; x <= bounds.x2; x++) {
          if (x < 0 || x >= PW || y < 0 || y >= PH) continue;
          var idx = y * PW + x;
          var isEntity = _isEntity(buf[idx].e, prefix);
          if (!isEntity) continue;
          var isBorder = false;
          for (var n = 0; n < 4; n++) {
            var nx = x + neighbors[n][0], ny = y + neighbors[n][1];
            if (nx < 0 || nx >= PW || ny < 0 || ny >= PH) { isBorder = true; break; }
            var ne = buf[ny * PW + nx].e;
            if (!_isEntity(ne, prefix)) { isBorder = true; break; }
          }
          if (isBorder) {
            _blendPixel(buf, idx, 255, 255, 255, env);
          }
        }
      }
    }
  };
}, 1500);

// ── P2: Emanation ──
// Custom multi-pixel sprites (icicles, vapor, stars, dust clouds, hearts)
// emanate from the entity contour in waves. Much more visible and evocative
// than single-pixel particle dots.

// Sprite drawing function for emanation types
function _drawEmanationSprite(buf, PW, PH, type, cx, cy, size, alpha) {
  if (alpha < 0.05) return;
  var _blend = function(idx, r, g, b, a) {
    if (idx < 0 || idx >= buf.length) return;
    _blendPixel(buf, idx, r, g, b, a);
  };
  var px, py, idx;

  if (type === 'frost') {
    // Icicle: tall narrow triangle pointing down, 4px wide × 12px tall at size=1
    var iW = Math.round(4 * size), iH = Math.round(12 * size);
    var half = Math.floor(iW / 2);
    for (var dy = 0; dy < iH; dy++) {
      // Width narrows from full at top to 1 at bottom
      var rowHalf = Math.max(0, Math.round(half * (1 - dy / iH)));
      for (var dx = -rowHalf; dx <= rowHalf; dx++) {
        px = Math.round(cx + dx); py = Math.round(cy + dy);
        if (px >= 0 && px < PW && py >= 0 && py < PH) {
          idx = py * PW + px;
          var bright = 1 - dy / iH * 0.3;  // lighter at tip
          // Highlight pixel for reflet
          var isReflet = (dx === -rowHalf + 1 && dy > 1 && dy < iH - 2);
          if (isReflet) {
            _blend(idx, 255, 255, 255, alpha * 0.8);
          } else {
            _blend(idx, Math.round(180 * bright), Math.round(220 * bright), 255, alpha);
          }
        }
      }
    }
  } else if (type === 'steam') {
    // Vapor streak: wavy vertical column rising, 3px wide × 10px tall
    var sH = Math.round(10 * size), sW = Math.round(3 * size);
    for (var dy = 0; dy < sH; dy++) {
      var waveOff = Math.round(Math.sin(dy * 0.8 + cx * 0.5) * 1.5 * size);
      var rowAlpha = alpha * (1 - dy / sH * 0.6);  // fade toward top
      for (var dx = 0; dx < sW; dx++) {
        px = Math.round(cx + dx - sW / 2 + waveOff);
        py = Math.round(cy - dy);  // rises upward
        if (px >= 0 && px < PW && py >= 0 && py < PH) {
          idx = py * PW + px;
          _blend(idx, 240, 240, 245, rowAlpha);
        }
      }
    }
  } else if (type === 'sparkle') {
    // 4-pointed star: center + 4 cardinal arms + 4 diagonal stubs
    var arm = Math.round(3 * size);
    var diagArm = Math.round(2 * size);
    // Center pixel
    px = Math.round(cx); py = Math.round(cy);
    if (px >= 0 && px < PW && py >= 0 && py < PH) _blend(py * PW + px, 255, 255, 255, alpha);
    // Cardinal arms
    for (var d = 1; d <= arm; d++) {
      var armA = alpha * (1 - d / (arm + 1));
      var offsets = [[d, 0], [-d, 0], [0, d], [0, -d]];
      for (var o = 0; o < 4; o++) {
        px = Math.round(cx + offsets[o][0]); py = Math.round(cy + offsets[o][1]);
        if (px >= 0 && px < PW && py >= 0 && py < PH) _blend(py * PW + px, 255, 255, 180, armA);
      }
    }
    // Diagonal stubs
    for (var d = 1; d <= diagArm; d++) {
      var diagA = alpha * (1 - d / (diagArm + 1)) * 0.6;
      var diags = [[d, d], [-d, d], [d, -d], [-d, -d]];
      for (var o = 0; o < 4; o++) {
        px = Math.round(cx + diags[o][0]); py = Math.round(cy + diags[o][1]);
        if (px >= 0 && px < PW && py >= 0 && py < PH) _blend(py * PW + px, 255, 255, 200, diagA);
      }
    }
  } else if (type === 'dust') {
    // Dust cloud: fuzzy circle with noise — denser and larger
    var rad = Math.round(4 * size);
    for (var dy = -rad; dy <= rad; dy++) {
      for (var dx = -rad; dx <= rad; dx++) {
        var dist2 = dx * dx + dy * dy;
        if (dist2 > rad * rad) continue;
        // Pseudo-random skip for dusty look (skip 1/8 instead of 1/4)
        if (((dx * 7 + dy * 13 + Math.round(cx)) & 7) === 0) continue;
        var dist = Math.sqrt(dist2);
        var falloff = 1 - dist / rad;
        px = Math.round(cx + dx); py = Math.round(cy + dy);
        if (px >= 0 && px < PW && py >= 0 && py < PH) {
          idx = py * PW + px;
          var cr = 180 + ((dx * 3 + dy * 7) & 15);
          var cg = 165 + ((dx * 5 + dy * 11) & 15);
          var cb = 130 + ((dx * 9 + dy * 3) & 15);
          _blend(idx, cr, cg, cb, alpha * falloff);
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
    var hScale = Math.max(1, Math.round(size));
    for (var hy = 0; hy < 6; hy++) {
      for (var hx = 0; hx < 7; hx++) {
        if (!heartMap[hy][hx]) continue;
        for (var sy = 0; sy < hScale; sy++) {
          for (var sx = 0; sx < hScale; sx++) {
            px = Math.round(cx - 3 * hScale + hx * hScale + sx);
            py = Math.round(cy - 3 * hScale + hy * hScale + sy);
            if (px >= 0 && px < PW && py >= 0 && py < PH) {
              idx = py * PW + px;
              // Lighter center, deeper edges
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
  } else if (type === 'anger') {
    // Manga anger mark 💢: 4 L-shaped corners pointing INWARD, forming a + shape
    // TL=┘ TR=└ BL=┐ BR=┌, each 2px thick, gap at center
    var angerMap = [
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
    var aScale = Math.max(1, Math.round(size));
    var aW = 9, aH = 9;
    for (var ay = 0; ay < aH; ay++) {
      for (var ax = 0; ax < aW; ax++) {
        if (!angerMap[ay][ax]) continue;
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
  } else if (type === 'fear') {
    // Sweat droplet: teardrop shape, pointed top, round bottom
    var dropMap = [
      [0,0,1,0,0],
      [0,1,1,1,0],
      [0,1,1,1,0],
      [1,1,1,1,1],
      [1,1,1,1,1],
      [1,1,1,1,1],
      [0,1,1,1,0],
    ];
    var fScale = Math.max(1, Math.round(size));
    for (var fy = 0; fy < 7; fy++) {
      for (var fx = 0; fx < 5; fx++) {
        if (!dropMap[fy][fx]) continue;
        for (var sy = 0; sy < fScale; sy++) {
          for (var sx = 0; sx < fScale; sx++) {
            px = Math.round(cx - 2 * fScale + fx * fScale + sx);
            py = Math.round(cy - 3 * fScale + fy * fScale + sy);
            if (px >= 0 && px < PW && py >= 0 && py < PH) {
              idx = py * PW + px;
              // White highlight at top-left, blue body
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

AnimationTemplates.register('emanation', function(params) {
  var prefix = params.entityPrefix || '';
  var pType = params.particleType || 'steam';
  var totalSprites = _clamp(params.particleCount || 18, 8, 30);

  // Stronger tints per type — applied to entity base color during animation
  var tints = {
    steam:   { r: 50, g: -25, b: -50 },
    frost:   { r: -50, g: 0, b: 70 },
    sparkle: { r: 40, g: 40, b: 25 },
    dust:    { r: -25, g: -25, b: -40 },
    hearts:  { r: 50, g: -15, b: 15 },
    anger:   { r: 60, g: -20, b: -20 },
    fear:    { r: 40, g: 40, b: 50 },
  };
  var tint = tints[pType] || tints.steam;

  // Movement configs per type
  var moveConfigs = {
    steam:   { vy: -18, vx: 0, vxJitter: 6, vyJitter: 3, gravity: 0, sway: 1.5 },
    frost:   { vy: 10, vx: 0, vxJitter: 3, vyJitter: 2, gravity: 5, sway: 1.0 },
    sparkle: { vy: 0, vx: 0, vxJitter: 2, vyJitter: 2, gravity: 0, sway: 0 },
    dust:    { vy: 6, vx: 2, vxJitter: 4, vyJitter: 2, gravity: 3, sway: 0.8 },
    hearts:  { vy: -12, vx: 0, vxJitter: 5, vyJitter: 2, gravity: 0, sway: 2.0 },
    anger:   { vy: 0, vx: 0, vxJitter: 1, vyJitter: 1, gravity: 0, sway: 0 },
    fear:    { vy: 15, vx: 0, vxJitter: 3, vyJitter: 3, gravity: 8, sway: 0.5 },
  };
  var mc = moveConfigs[pType] || moveConfigs.steam;

  // Pre-generate sprite data (position, velocity, spawn time, size)
  var sprites = [];
  var waves = 4;
  var perWave = Math.ceil(totalSprites / waves);

  // Use seeded pseudo-random for deterministic results per animation instance
  var _seed = 12345 + prefix.length * 7;
  function _rand() { _seed = (_seed * 16807 + 0) % 2147483647; return (_seed & 0xffff) / 0xffff; }

  for (var w = 0; w < waves; w++) {
    var waveTime = w / waves * 0.7;  // waves spawn from t=0 to t=0.7
    for (var s = 0; s < perWave && sprites.length < totalSprites; s++) {
      var sizeVar = 0.7 + _rand() * 0.6;  // 0.7 to 1.3
      sprites.push({
        spawnT: waveTime + _rand() * 0.08,  // slight jitter within wave
        // spawn position will be set on first use (needs entity bounds)
        side: _rand(),       // 0-1 for position along contour
        sideType: Math.floor(_rand() * 4),  // 0=top, 1=right, 2=bottom, 3=left
        vx: mc.vx + ((_rand() - 0.5) * 2) * mc.vxJitter,
        vy: mc.vy + ((_rand() - 0.5) * 2) * mc.vyJitter,
        size: sizeVar,
        x: 0, y: 0,
        maxAge: 0.5 + _rand() * 0.3,
        flicker: (pType === 'sparkle') ? _rand() : -1,  // sparkle flicker phase
        initialized: false
      });
    }
  }

  return function animate(buf, PW, PH, t) {
    var env = _easeEnvelope(t, 0.15, 0.15);

    // Tint entity
    for (var i = 0; i < buf.length; i++) {
      if (_isEntity(buf[i].e, prefix)) {
        buf[i].r = _clamp(buf[i]._r + Math.round(tint.r * env), 0, 255);
        buf[i].g = _clamp(buf[i]._g + Math.round(tint.g * env), 0, 255);
        buf[i].b = _clamp(buf[i]._b + Math.round(tint.b * env), 0, 255);
      }
    }

    // Get bounds for spawn positions
    var bounds = _computeEntityBounds(buf, PW, prefix);
    if (bounds.x2 < 0) return;
    var bw = bounds.x2 - bounds.x1, bh = bounds.y2 - bounds.y1;

    // Update and draw each sprite
    for (var s = 0; s < sprites.length; s++) {
      var sp = sprites[s];
      if (t < sp.spawnT) continue;  // not yet spawned

      var age = t - sp.spawnT;
      if (age > sp.maxAge) continue;  // expired

      // Initialize spawn position on first frame
      if (!sp.initialized) {
        if (sp.sideType === 0) {
          sp.x = bounds.x1 + sp.side * bw; sp.y = bounds.y1 - 2;
        } else if (sp.sideType === 1) {
          sp.x = bounds.x2 + 2; sp.y = bounds.y1 + sp.side * bh;
        } else if (sp.sideType === 2) {
          sp.x = bounds.x1 + sp.side * bw; sp.y = bounds.y2 + 2;
        } else {
          sp.x = bounds.x1 - 2; sp.y = bounds.y1 + sp.side * bh;
        }
        sp.initialized = true;
      }

      // Update position
      var dt = 1 / 60;
      sp.x += sp.vx * dt;
      sp.y += sp.vy * dt;
      sp.vy += mc.gravity * dt;
      // Lateral sway
      if (mc.sway > 0) {
        sp.x += Math.sin(age * 8 + sp.side * 10) * mc.sway * dt * 15;
      }

      // Alpha envelope: fadeIn (0-20%), hold, fadeOut (last 30%)
      var lifeRatio = age / sp.maxAge;
      var spriteAlpha = env;
      if (lifeRatio < 0.2) spriteAlpha *= lifeRatio / 0.2;
      else if (lifeRatio > 0.7) spriteAlpha *= (1 - lifeRatio) / 0.3;

      // Sparkle flicker
      if (sp.flicker >= 0 && Math.sin(t * 20 + sp.flicker * 100) < -0.3) continue;

      _drawEmanationSprite(buf, PW, PH, pType, sp.x, sp.y, sp.size, spriteAlpha);
    }
  };
}, 3000);

// ── T1: Flashback ──
// Old film effect: entire scene goes B&W with projector flicker,
// vertical scratch lines, and dust specks — like a silent-era film reel.
// Scaffolds past tense — "this already happened."
AnimationTemplates.register('flashback', function(params) {

  return function animate(buf, PW, PH, t) {
    // Envelope: fade to B&W (0→0.08), hold (0.08→0.92), fade back (0.92→1)
    var desat;
    if (t < 0.08) desat = t / 0.08;
    else if (t < 0.92) desat = 1;
    else desat = 1 - (t - 0.92) / 0.08;

    // Projector brightness flicker (pseudo-random per frame)
    var frame = Math.floor(t * 180); // ~60fps × 3s
    var flickSeed = (frame * 16807 + 12345) % 2147483647;
    var flick = 1.0 + ((flickSeed % 1000) / 1000 - 0.5) * 0.08 * desat; // ±4%

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

    var numScratches = 1 + Math.floor(rng() * 3); // 1-3 scratches
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
AnimationTemplates.register('timelapse', function(params) {
  // Two full day-night cycles: day→night→day→night→day
  // 4 transitions × 1s each = 4s total. No lingering on night.
  // Each keyframe: { t, mult, tintR, tintG, tintB }
  // Pixel formula: out = clamp(_original * mult + tint, 0, 255)
  var keyframes = [
    { t: 0.000, mult: 1.00, tintR:   0, tintG:   0, tintB:   0 }, // day
    { t: 0.125, mult: 0.70, tintR:  60, tintG:  18, tintB:  38 }, // dusk (rose)
    { t: 0.250, mult: 0.22, tintR:  10, tintG:   8, tintB:  52 }, // night
    { t: 0.375, mult: 0.50, tintR:  12, tintG:  22, tintB:  88 }, // dawn (light blue)
    { t: 0.500, mult: 1.00, tintR:   0, tintG:   0, tintB:   0 }, // day
    { t: 0.625, mult: 0.70, tintR:  60, tintG:  18, tintB:  38 }, // dusk (rose)
    { t: 0.750, mult: 0.22, tintR:  10, tintG:   8, tintB:  52 }, // night
    { t: 0.875, mult: 0.50, tintR:  12, tintG:  22, tintB:  88 }, // dawn (light blue)
    { t: 1.000, mult: 1.00, tintR:   0, tintG:   0, tintB:   0 }, // day
  ];

  var isIndoor = !!params.isIndoor;
  // Two particle systems for the two night phases
  var ps1 = isIndoor ? null : new ParticleSystem(ParticlePresets.sparkle);
  var ps2 = isIndoor ? null : new ParticleSystem(ParticlePresets.sparkle);
  var stars1Spawned = false;
  var stars2Spawned = false;

  return function animate(buf, PW, PH, t) {
    // Find surrounding keyframes and interpolate linearly
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

    for (var i = 0; i < buf.length; i++) {
      buf[i].r = _clamp(Math.round(buf[i]._r * mult + tintR), 0, 255);
      buf[i].g = _clamp(Math.round(buf[i]._g * mult + tintG), 0, 255);
      buf[i].b = _clamp(Math.round(buf[i]._b * mult + tintB), 0, 255);
    }

    // Stars (sparkle preset) only for outdoor scenes, during both night phases
    if (!isIndoor) {
      // Night 1: ~t 0.15 → 0.40
      if (t >= 0.15 && !stars1Spawned) {
        for (var s = 0; s < 12; s++) ps1.spawn(Math.random() * PW, Math.random() * PH * 0.35);
        stars1Spawned = true;
      }
      if (t >= 0.15 && t <= 0.40) { ps1.update(1 / 60); ps1.draw(buf, PW, PH); }

      // Night 2: ~t 0.65 → 0.90
      if (t >= 0.65 && !stars2Spawned) {
        for (var s = 0; s < 12; s++) ps2.spawn(Math.random() * PW, Math.random() * PH * 0.35);
        stars2Spawned = true;
      }
      if (t >= 0.65 && t <= 0.90) { ps2.update(1 / 60); ps2.draw(buf, PW, PH); }
    }
  };
}, 4000);

// ── A1: Motion Lines ──
// Fast burst movements with pauses between. Direction coherent with entity
// type (birds: any direction, others: left/right). Thick, visible speed streaks.
AnimationTemplates.register('motion_lines', function(params) {
  var prefix = params.entityPrefix || '';
  var dir = params.direction || 'right';   // 'left', 'right', 'any'
  var lineLen = _clamp(params.lineLength || 20, 10, 30);
  var amp = _clamp(params.amplitude || 10, 5, 15);
  var MAX_LINES = 60;

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
    var thin = (_sv % 3 === 0);                      // ~1/3 are 1px thin lines
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
}, 3000);

// ── A2: Anticipation ──
// Entity compresses slightly, lurches forward, then freezes mid-motion.
// Like a momentum that was interrupted. Scaffolds missing/uncompleted action verbs.
AnimationTemplates.register('anticipation', function(params) {
  var prefix = params.entityPrefix || '';

  return function animate(buf, PW, PH, t) {
    // Collect entity pixels and bounding box.
    // Use entity layer for complete bounds (includes covered pixels).
    var minX = PW, maxX = 0, minY = PH, maxY = 0;
    var indices = [];
    var layerData = buf._entityLayers && buf._entityLayers[prefix];
    if (layerData && layerData.length > 0) {
      for (var k = 0; k < layerData.length; k++) {
        var li = layerData[k];
        var lx = li.idx % PW, ly = Math.floor(li.idx / PW);
        if (lx < minX) minX = lx; if (lx > maxX) maxX = lx;
        if (ly < minY) minY = ly; if (ly > maxY) maxY = ly;
        if (buf[li.idx].e && buf[li.idx].e.startsWith(prefix)) {
          indices.push(li.idx);
        }
      }
    } else {
      for (var i = 0; i < buf.length; i++) {
        if (buf[i].e && buf[i].e.startsWith(prefix)) {
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
    // Go phase (t: 0→0.5): pixel slides from px toward mirrorX.
    //   Extremity (dist=1) starts at t=0; axis (dist=0) starts at t=0.25.
    //   All pixels arrive at mirrorX at t=0.5.
    //
    // Return phase (t: 0.5→1.0): pixel slides back from mirrorX to px.
    //   Same stagger: extremity starts at t=0.5, axis at t=0.75.
    //   All pixels back at px at t=1.0.
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

      if (t <= 0.5) {
        var tStart = (1 - dist) * 0.25;
        var p = _clamp((t - tStart) / (0.5 - tStart), 0, 1);
        newX = px + (mirrorX - px) * p;
      } else {
        var tStart2 = 0.5 + (1 - dist) * 0.25;
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
}, 2000);

// ── Decomposition (not in new grammar — legacy support) ──
AnimationTemplates.register('decomposition', function(params) {
  var prefix = params.entityPrefix || '';
  var sepPx = _clamp(params.separationPixels || 8, 2, 20);

  return function animate(buf, PW, PH, t) {
    var pixels = _collectEntityPixels(buf, PW, prefix);
    if (pixels.length === 0) return;

    // Group by second-level sub-entity prefix
    var groups = {};
    for (var j = 0; j < pixels.length; j++) {
      var e = pixels[j].e;
      // Extract group: "fox_01.head.ears" → "fox_01.head"
      var parts = e.split('.');
      var groupKey;
      if (parts.length >= 2) groupKey = parts[0] + '.' + parts[1];
      else groupKey = parts[0];
      if (!groups[groupKey]) groups[groupKey] = { pixels: [], cx: 0, cy: 0 };
      groups[groupKey].pixels.push(pixels[j]);
    }

    // Compute centroids
    var entityBounds = _computeEntityBounds(buf, PW, prefix);
    for (var key in groups) {
      var g = groups[key];
      var sx = 0, sy = 0;
      for (var p = 0; p < g.pixels.length; p++) {
        sx += g.pixels[p].x;
        sy += g.pixels[p].y;
      }
      g.cx = sx / g.pixels.length;
      g.cy = sy / g.pixels.length;
    }

    // Compute separation amount
    var sep;
    if (t < 0.3) sep = sepPx * (t / 0.3);
    else if (t < 0.6) sep = sepPx;
    else sep = sepPx * (1 - (t - 0.6) / 0.4);

    _blankEntityPixels(buf, pixels);

    // Redraw each group at offset from entity center
    for (var key in groups) {
      var g = groups[key];
      var dirX = g.cx - entityBounds.cx;
      var dirY = g.cy - entityBounds.cy;
      var dist = Math.sqrt(dirX * dirX + dirY * dirY) || 1;
      var dx = Math.round(dirX / dist * sep);
      var dy = Math.round(dirY / dist * sep);

      // Pulse during hold phase
      var brightness = 1;
      if (t >= 0.3 && t < 0.6) {
        brightness = 1 + 0.2 * Math.sin((t - 0.3) / 0.3 * Math.PI * 4);
      }

      for (var p = 0; p < g.pixels.length; p++) {
        var px = g.pixels[p];
        var nx = px.x + dx, ny = px.y + dy;
        if (nx >= 0 && nx < PW && ny >= 0 && ny < PH) {
          var ni = ny * PW + nx;
          buf[ni].r = Math.min(255, Math.round(px.r * brightness));
          buf[ni].g = Math.min(255, Math.round(px.g * brightness));
          buf[ni].b = Math.min(255, Math.round(px.b * brightness));
        }
      }
    }
  };
}, 1800);

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

      _drawStarBurst(buf, PW, PH, ipx, ipy, starAlpha, 6, 4);
    }
  };
}, 1500);

// ── C1: Sequential Glow ──
AnimationTemplates.register('sequential_glow', function(params) {
  var prefixes = params.entityPrefixes || [params.entityPrefix || ''];
  var n = prefixes.length;

  return function animate(buf, PW, PH, t) {
    // Determine which entity is currently glowing
    var activeIdx = Math.min(Math.floor(t * n), n - 1);
    var phaseT = (t * n) % 1; // 0-1 within current entity's window
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
        var boost = 1 + 0.3 * glow;
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
// Entity pixels fall downward with slight horizontal drift, fading to opacity 0.
AnimationTemplates.register('disintegration', function(params) {
  var prefix = params.entityPrefix || '';

  var cachedPixels = null;
  var cachedOffsets = null; // {dx, dy, delay} per pixel — dy always positive (downward)
  var cachedBounds = null;

  return function animate(buf, PW, PH, t) {
    if (!cachedPixels) {
      cachedPixels = _collectEntityPixels(buf, PW, prefix);
      cachedBounds = _computeEntityBounds(buf, PW, prefix);
      cachedOffsets = [];
      var bh = cachedBounds.y2 - cachedBounds.y1 + 1;
      var bw = cachedBounds.x2 - cachedBounds.x1 + 1;
      for (var j = 0; j < cachedPixels.length; j++) {
        // Pixels from top of entity fall further, bottom pixels fall less
        var relY = (cachedPixels[j].y - cachedBounds.y1) / Math.max(1, bh); // 0=top, 1=bottom
        var fallDist = bh * (0.4 + 0.6 * (1 - relY)); // top pixels fall more
        cachedOffsets.push({
          dx: Math.round((Math.random() - 0.5) * bw * 0.3), // slight horizontal drift
          dy: Math.round(fallDist * (0.7 + Math.random() * 0.6)), // downward
          delay: Math.random() * 0.25 + relY * 0.15 // top pixels start detaching first
        });
      }
    }

    // Phase 1 (t 0→0.5): Pixels detach and fall downward (staggered)
    // Phase 2 (t 0.3→1.0): Pixels fade to opacity 0 (overlaps with falling)

    _blankEntityPixels(buf, cachedPixels);

    for (var j = 0; j < cachedPixels.length; j++) {
      var p = cachedPixels[j];
      var off = cachedOffsets[j];

      // Fall progress
      var fallStart = off.delay * 0.5;
      var fall = 0;
      if (t > fallStart) {
        fall = Math.min(1, (t - fallStart) / 0.55);
        fall = fall * fall; // accelerating (gravity-like)
      }

      var drawX = Math.round(p.x + off.dx * fall);
      var drawY = Math.round(p.y + off.dy * fall);

      // Fade to opacity 0 — starts shortly after detaching, staggered per pixel
      var fadeStart = fallStart + 0.15;
      var alpha = 1;
      if (t > fadeStart) {
        alpha = 1 - Math.min(1, (t - fadeStart) / 0.5);
      }

      if (alpha > 0.01 && drawX >= 0 && drawX < PW && drawY >= 0 && drawY < PH) {
        var di = drawY * PW + drawX;
        buf[di].r = Math.round(buf[di]._br * (1 - alpha) + p.r * alpha);
        buf[di].g = Math.round(buf[di]._bg * (1 - alpha) + p.g * alpha);
        buf[di].b = Math.round(buf[di]._bb * (1 - alpha) + p.b * alpha);
      }
    }
  };
}, 2000);

// ── C3: Ghost Outline ──
// Dark flat puddle at an empty spot + big "?" with black outline. Scaffolds absence.
AnimationTemplates.register('ghost_outline', function(params) {
  var prefix = params.entityPrefix || '';

  var cachedPuddleCx = null, cachedPuddleY = null;
  var cachedRx = 0, cachedRy = 0;
  var cachedEdgeOffsets = null;

  return function animate(buf, PW, PH, t) {
    if (cachedPuddleCx === null) {
      var bounds = _computeEntityBounds(buf, PW, prefix);
      var ew = bounds.x2 - bounds.x1 + 1;
      var eh = bounds.y2 - bounds.y1 + 1;
      cachedRx = Math.max(8, Math.round(ew * 0.55));
      cachedRy = Math.max(3, Math.round(eh * 0.08));

      // Find an empty ground-level spot (not overlapping any entity)
      var groundY = bounds.y2;
      var testH = 10; // vertical strip to test for emptiness
      var bestCx = null;
      // Try offsets: ±1.0×ew, ±1.5×ew, ±2.0×ew
      var offsets = [1.0, -1.0, 1.5, -1.5, 2.0, -2.0, 0.7, -0.7];
      for (var oi = 0; oi < offsets.length; oi++) {
        var testCx = Math.round(bounds.cx + offsets[oi] * ew);
        if (testCx - cachedRx < 0 || testCx + cachedRx >= PW) continue;
        // Check if this column is empty of entity pixels
        var occupied = false;
        for (var ty = Math.max(0, groundY - testH); ty <= Math.min(PH - 1, groundY + cachedRy); ty++) {
          for (var tx = testCx - 5; tx <= testCx + 5; tx++) {
            if (tx < 0 || tx >= PW) continue;
            var ti = ty * PW + tx;
            if (buf[ti].e && buf[ti].e !== '' && buf[ti].e !== 'background' && !buf[ti].e.startsWith('bg')) {
              occupied = true; break;
            }
          }
          if (occupied) break;
        }
        if (!occupied) { bestCx = testCx; break; }
      }
      // Fallback: offset 1.5× entity width to the right (or left if off-screen)
      if (bestCx === null) {
        bestCx = bounds.cx + Math.round(ew * 1.5);
        if (bestCx + cachedRx >= PW) bestCx = bounds.cx - Math.round(ew * 1.5);
      }
      cachedPuddleCx = _clamp(bestCx, cachedRx, PW - cachedRx - 1);
      cachedPuddleY = groundY;

      // Edge wobble offsets
      cachedEdgeOffsets = [];
      for (var row = 0; row < cachedRy * 2 + 1; row++) {
        cachedEdgeOffsets.push(Math.random() * Math.PI * 2);
      }
    }

    var cx = cachedPuddleCx;
    var puddleY = cachedPuddleY;
    var rx = cachedRx, ry = cachedRy;

    // Phase 1 (t 0→0.15): fade in | Phase 2 (t 0.15→0.7): wobble | Phase 3 (t 0.7→1): dissolve
    var shapeAlpha = 1;
    if (t < 0.15) {
      shapeAlpha = t / 0.15;
    } else if (t > 0.7) {
      shapeAlpha = 1 - (t - 0.7) / 0.3;
    }
    shapeAlpha = Math.max(0, Math.min(1, shapeAlpha));
    if (shapeAlpha < 0.01) return;

    var gc = [60, 65, 85]; // dark blue-grey

    // Draw flat puddle: scan-line filled ellipse with wobbling edge
    for (var dy = -ry; dy <= ry; dy++) {
      var py = puddleY + dy;
      if (py < 0 || py >= PH) continue;
      var rowFrac = dy / ry;
      var halfW = rx * Math.sqrt(Math.max(0, 1 - rowFrac * rowFrac));
      var rowIdx = (dy + ry) % cachedEdgeOffsets.length;
      var wobble = Math.sin(t * Math.PI * 5 + cachedEdgeOffsets[rowIdx]) * 2;
      halfW += wobble;
      if (halfW < 1) continue;

      for (var dx = Math.round(-halfW); dx <= Math.round(halfW); dx++) {
        var px = cx + dx;
        if (px < 0 || px >= PW) continue;
        var pi = py * PW + px;
        var edgeFrac = Math.abs(dx) / halfW;
        var isEdge = edgeFrac > 0.8;
        if (isEdge && (px + py) % 2 !== 0) continue;
        var sa = shapeAlpha * (0.6 - 0.2 * edgeFrac);
        _blendPixel(buf, pi, gc[0], gc[1], gc[2], sa);
      }
    }

    // Draw big "?" with black outline (13×19 bitmap, thick strokes)
    if (shapeAlpha > 0.15) {
      var qAlpha = Math.min(1, (shapeAlpha - 0.15) / 0.25);
      // "?" bitmap 13 wide × 19 tall
      var qMark = [
        0,0,0,1,1,1,1,1,1,1,0,0,0,
        0,0,1,1,1,1,1,1,1,1,1,0,0,
        0,1,1,1,0,0,0,0,0,1,1,1,0,
        1,1,1,0,0,0,0,0,0,0,1,1,1,
        1,1,1,0,0,0,0,0,0,0,1,1,1,
        1,1,0,0,0,0,0,0,0,0,1,1,1,
        0,0,0,0,0,0,0,0,0,1,1,1,0,
        0,0,0,0,0,0,0,0,1,1,1,0,0,
        0,0,0,0,0,0,0,1,1,1,0,0,0,
        0,0,0,0,0,0,1,1,1,0,0,0,0,
        0,0,0,0,0,1,1,1,0,0,0,0,0,
        0,0,0,0,0,1,1,1,0,0,0,0,0,
        0,0,0,0,0,1,1,1,0,0,0,0,0,
        0,0,0,0,0,1,1,1,0,0,0,0,0,
        0,0,0,0,0,0,0,0,0,0,0,0,0,
        0,0,0,0,0,0,0,0,0,0,0,0,0,
        0,0,0,0,0,0,0,0,0,0,0,0,0,
        0,0,0,0,0,1,1,1,0,0,0,0,0,
        0,0,0,0,0,1,1,1,0,0,0,0,0,
        0,0,0,0,0,0,0,0,0,0,0,0,0
      ];
      var qW = 13, qH = 20;
      // Gentle float: slow sinusoidal drift in all directions
      var floatX = Math.round(Math.sin(t * Math.PI * 2.3) * 2 + Math.cos(t * Math.PI * 1.7) * 1);
      var floatY = Math.round(Math.sin(t * Math.PI * 1.9 + 1.2) * 2 + Math.cos(t * Math.PI * 2.7) * 1);
      var qx0 = cx - Math.floor(qW / 2) + floatX;
      var qy0 = puddleY - ry - qH - 3 + floatY;
      var qa = qAlpha * shapeAlpha;
      // Pass 1: black outline (draw 8-neighbors of each "?" pixel)
      // Note: neighbors outside the bitmap bounds are still drawn on screen
      var dirs = [[-1,-1],[-1,0],[-1,1],[0,-1],[0,1],[1,-1],[1,0],[1,1]];
      for (var qy = 0; qy < qH; qy++) {
        for (var qx = 0; qx < qW; qx++) {
          if (!qMark[qy * qW + qx]) continue;
          for (var d = 0; d < 8; d++) {
            var nx = qx + dirs[d][0], ny = qy + dirs[d][1];
            // Skip only if the neighbor is itself a fill pixel (inside bitmap bounds)
            var isInsideBitmap = nx >= 0 && nx < qW && ny >= 0 && ny < qH;
            if (isInsideBitmap && qMark[ny * qW + nx]) continue;
            var sx = qx0 + nx, sy = qy0 + ny;
            if (sx >= 0 && sx < PW && sy >= 0 && sy < PH) {
              var si = sy * PW + sx;
              _blendPixel(buf, si, 0, 0, 0, qa);
            }
          }
        }
      }
      // Pass 2: white fill
      for (var qy = 0; qy < qH; qy++) {
        for (var qx = 0; qx < qW; qx++) {
          if (!qMark[qy * qW + qx]) continue;
          var sx = qx0 + qx, sy = qy0 + qy;
          if (sx >= 0 && sx < PW && sy >= 0 && sy < PH) {
            var si = sy * PW + sx;
            _blendPixel(buf, si, 255, 255, 220, qa);
          }
        }
      }
    }
  };
}, 2500);

// ── R1: Magnetism ──
AnimationTemplates.register('magnetism', function(params) {
  var prefixA = params.entityPrefixA || params.entityPrefix || '';
  var prefixB = params.entityPrefixB || '';
  // Bigger sparkles: size 3, more spread, longer life
  var ps = new ParticleSystem({
    color: [255, 255, 200], size: 3,
    maxAge: 0.45, gravity: 0, drag: 0.8,
    spreadX: 8, spreadY: 8,
    vx: 0, vy: 0, vxJitter: 20, vyJitter: 20,
    fadeIn: 0.05, fadeOut: 0.4, flicker: true,
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
        ps.burst(midX, midY, 20);
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

      // Draw rotated magnets using reverse-mapping (no holes) at 2x scale with black outline
      var mgScale = 2;
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

    if (t < 0.20) {
      // Attract — ease-in, close the contour gap over 300ms
      var attract = t / 0.20;
      attract = attract * attract; // ease-in (slow start, accelerate)
      dxA = Math.round(cachedGap / 2 * attract * ndx);
      dyA = Math.round(cachedGap / 2 * attract * ndy);
      dxB = Math.round(-cachedGap / 2 * attract * ndx);
      dyB = Math.round(-cachedGap / 2 * attract * ndy);
    } else if (t < 0.50) {
      // Push apart — ease-out, separate over 450ms
      var progress = (t - 0.20) / 0.30;
      progress = 1 - (1 - progress) * (1 - progress); // ease-out (fast start, decelerate)
      dxA = Math.round(-repelPx / 2 * progress * ndx);
      dyA = Math.round(-repelPx / 2 * progress * ndy);
      dxB = Math.round(repelPx / 2 * progress * ndx);
      dyB = Math.round(repelPx / 2 * progress * ndy);
    } else if (t < 0.70) {
      // Hold apart
      dxA = Math.round(-repelPx / 2 * ndx);
      dyA = Math.round(-repelPx / 2 * ndy);
      dxB = Math.round(repelPx / 2 * ndx);
      dyB = Math.round(repelPx / 2 * ndy);
    } else {
      // Drift back — ease-in-out
      var release = (t - 0.70) / 0.30;
      release = release * release * (3 - 2 * release); // smoothstep
      dxA = Math.round(-repelPx / 2 * (1 - release) * ndx);
      dyA = Math.round(-repelPx / 2 * (1 - release) * ndy);
      dxB = Math.round(repelPx / 2 * (1 - release) * ndx);
      dyB = Math.round(repelPx / 2 * (1 - release) * ndy);
    }

    _blankEntityPixels(buf, pixelsA);
    _blankEntityPixels(buf, pixelsB);
    _redrawEntityPixels(buf, PW, PH, pixelsA, dxA, dyA);
    _redrawEntityPixels(buf, PW, PH, pixelsB, dxB, dyB);

    // ── Bonk effect at contour collision point ──
    var bonkStart = 0.16, bonkPeak = 0.22, bonkEnd = 0.42;
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

      _drawStarBurst(buf, PW, PH, ipx, ipy, bonkAlpha, 4, 3);

      // Expanding impact ring
      var ringProgress = _clamp((t - bonkStart) / (bonkEnd - bonkStart), 0, 1);
      var ringRadius = 2 + ringProgress * 6;  // expands 2px -> 8px
      var ringAlpha = bonkAlpha * 0.7;
      var ringSteps = Math.max(16, Math.ceil(ringRadius * 6));
      for (var s = 0; s < ringSteps; s++) {
        var angle = (s / ringSteps) * Math.PI * 2;
        var rx = Math.round(ipx + Math.cos(angle) * ringRadius);
        var ry = Math.round(ipy + Math.sin(angle) * ringRadius);
        if (rx >= 0 && rx < PW && ry >= 0 && ry < PH) {
          var ri = ry * PW + rx;
          _blendPixel(buf, ri, 255, 220, 80, ringAlpha);
        }
      }
    }
  };
}, 1500);


// ── D1: Speech Bubble ──
// Elliptical speech bubble with black 1px border, a pointed horn toward the
// entity's head, and "..." (three bold dots) centered inside.
AnimationTemplates.register('speech_bubble', function(params) {
  var prefix = params.entityPrefix || '';

  return function animate(buf, PW, PH, t) {
    var env = _easeEnvelope(t, 0.2, 0.2);
    if (env < 0.01) return;

    var bounds = _computeEntityBounds(buf, PW, prefix);
    if (bounds.x2 < 0) return;

    var alpha = env;

    // Ray-cast upward from entity center to find actual top contour pixel
    var rayCX = Math.round(bounds.cx);
    var entityTopY = bounds.y1;
    var foundTop = false;
    for (var rd = 1; rd <= Math.ceil(bounds.cy - bounds.y1) + 2; rd++) {
      var rty = Math.round(bounds.cy) - rd;
      if (rty < 0) break;
      var rti = rty * PW + rayCX;
      if (buf[rti].e && buf[rti].e.startsWith(prefix)) {
        entityTopY = rty; foundTop = true;
      } else if (foundTop) { break; }
    }

    // Ellipse half-radii and horn dimensions
    var rx = 18, ry = 11;
    var hornH = 9, hornHalfW = 4, gap = 2;

    var hornTipX = rayCX;
    var hornTipY = entityTopY - gap;
    var bubbleCX = rayCX;
    var bubbleCY = hornTipY - hornH - ry;

    // Clamp bubble to canvas
    bubbleCX = Math.max(rx + 2, Math.min(PW - rx - 2, bubbleCX));
    if (bubbleCY < ry + 1) bubbleCY = ry + 1;

    var hornBaseY = bubbleCY + ry;

    // 1. Fill outer ellipse (rx+1, ry+1) black → 1px border ring
    for (var y = bubbleCY - ry - 1; y <= bubbleCY + ry + 1; y++) {
      if (y < 0 || y >= PH) continue;
      for (var x = bubbleCX - rx - 1; x <= bubbleCX + rx + 1; x++) {
        if (x < 0 || x >= PW) continue;
        var nx = (x - bubbleCX) / (rx + 1), ny = (y - bubbleCY) / (ry + 1);
        if (nx * nx + ny * ny <= 1.0) {
          var idx = y * PW + x;
          _blendPixel(buf, idx, 0, 0, 0, alpha);
        }
      }
    }

    // 2. Fill inner ellipse (rx, ry) white → interior
    for (var y = bubbleCY - ry; y <= bubbleCY + ry; y++) {
      if (y < 0 || y >= PH) continue;
      for (var x = bubbleCX - rx; x <= bubbleCX + rx; x++) {
        if (x < 0 || x >= PW) continue;
        var nx = (x - bubbleCX) / rx, ny = (y - bubbleCY) / ry;
        if (nx * nx + ny * ny <= 1.0) {
          var idx = y * PW + x;
          _blendPixel(buf, idx, 255, 255, 255, alpha);
        }
      }
    }

    // 3. Horn (white fill + black outline edges), only if room exists
    if (hornTipY > hornBaseY) {
      // Fill horn white
      for (var y = hornBaseY; y <= hornTipY; y++) {
        if (y < 0 || y >= PH) continue;
        var frac = (y - hornBaseY) / (hornTipY - hornBaseY);
        var hw = Math.round(hornHalfW * (1 - frac));
        var hcx = Math.round(bubbleCX + (hornTipX - bubbleCX) * frac);
        for (var x = hcx - hw; x <= hcx + hw; x++) {
          if (x < 0 || x >= PW) continue;
          var idx = y * PW + x;
          _blendPixel(buf, idx, 255, 255, 255, alpha);
        }
      }
      // Draw horn outline edges (black lines)
      var edgePts = [
        [bubbleCX - hornHalfW, hornBaseY, hornTipX, hornTipY],
        [bubbleCX + hornHalfW, hornBaseY, hornTipX, hornTipY]
      ];
      for (var ei = 0; ei < 2; ei++) {
        var ex0 = edgePts[ei][0], ey0 = edgePts[ei][1];
        var ex1 = edgePts[ei][2], ey1 = edgePts[ei][3];
        var edx = ex1 - ex0, edy = ey1 - ey0;
        var esteps = Math.max(Math.abs(edx), Math.abs(edy));
        for (var es = 0; es <= esteps; es++) {
          var epx = Math.round(ex0 + edx * es / esteps);
          var epy = Math.round(ey0 + edy * es / esteps);
          if (epx >= 0 && epx < PW && epy >= 0 && epy < PH) {
            var epi = epy * PW + epx;
            _blendPixel(buf, epi, 0, 0, 0, alpha);
          }
        }
      }
    }

    // 4. Draw "..." — three 3×3 black squares, centered in ellipse
    var dotTopY = bubbleCY - 1;
    var dotCXs = [bubbleCX - 5, bubbleCX, bubbleCX + 5];
    for (var di = 0; di < 3; di++) {
      for (var ddy = 0; ddy < 3; ddy++) {
        for (var ddx = 0; ddx < 3; ddx++) {
          var dpx = dotCXs[di] - 1 + ddx, dpy = dotTopY + ddy;
          if (dpx >= 0 && dpx < PW && dpy >= 0 && dpy < PH) {
            var dpi = dpy * PW + dpx;
            _blendPixel(buf, dpi, 0, 0, 0, alpha);
          }
        }
      }
    }
  };
}, 1500);

// ── D2: Thought Bubble ──
// Pixelated thought bubble (round, linked bubbles) with "..." or symbol.
// Scaffolds Internal Response and Plan (mental_verbs).
AnimationTemplates.register('thought_bubble', function(params) {
  var prefix = params.entityPrefix || '';
  // Cloud shape = union of overlapping circles
  var CC = [
    { dx:  0,  dy:  2, r: 10 },  // main body
    { dx: -8,  dy: -5, r:  7 },  // top-left bump
    { dx:  0,  dy: -9, r:  7 },  // top-center bump
    { dx:  8,  dy: -5, r:  7 },  // top-right bump
    { dx:-13,  dy:  2, r:  5 },  // left side
    { dx: 13,  dy:  2, r:  5 },  // right side
  ];
  var CLOUD_BOTTOM_DY = 12; // distance from cloud center to bottom

  function inCloud(px, py, bcx, bcy, extra) {
    for (var ci = 0; ci < CC.length; ci++) {
      var cdx = px - (bcx + CC[ci].dx), cdy = py - (bcy + CC[ci].dy);
      var r = CC[ci].r + extra;
      if (cdx * cdx + cdy * cdy <= r * r) return true;
    }
    return false;
  }

  return function animate(buf, PW, PH, t) {
    var env = _easeEnvelope(t, 0.2, 0.2);
    if (env < 0.01) return;
    var bounds = _computeEntityBounds(buf, PW, prefix);
    if (bounds.x2 < 0) return;
    var alpha = env;

    // Ray-cast upward to find entity top contour
    var rayCX = Math.round(bounds.cx);
    var entityTopY = bounds.y1;
    var foundTop = false;
    for (var rd = 1; rd <= Math.ceil(bounds.cy - bounds.y1) + 2; rd++) {
      var rty = Math.round(bounds.cy) - rd;
      if (rty < 0) break;
      var rti = rty * PW + rayCX;
      if (buf[rti].e && buf[rti].e.startsWith(prefix)) {
        entityTopY = rty; foundTop = true;
      } else if (foundTop) { break; }
    }

    var gap = 12;
    var bcx = rayCX;
    var bcy = (entityTopY - gap) - CLOUD_BOTTOM_DY;
    bcx = Math.max(19, Math.min(PW - 19, bcx));
    if (bcy < 17) bcy = 17;

    // 1. Cloud border (black) — fill outer shape
    var sxMin = Math.max(0, bcx - 20), sxMax = Math.min(PW - 1, bcx + 20);
    var syMin = Math.max(0, bcy - 17), syMax = Math.min(PH - 1, bcy + 14);
    for (var sy = syMin; sy <= syMax; sy++) {
      for (var sx = sxMin; sx <= sxMax; sx++) {
        if (inCloud(sx, sy, bcx, bcy, 1)) {
          var si = sy * PW + sx;
          _blendPixel(buf, si, 0, 0, 0, alpha);
        }
      }
    }

    // 2. Cloud interior (white fill)
    for (var sy = syMin; sy <= syMax; sy++) {
      for (var sx = sxMin; sx <= sxMax; sx++) {
        if (inCloud(sx, sy, bcx, bcy, 0)) {
          var si = sy * PW + sx;
          _blendPixel(buf, si, 255, 255, 255, alpha);
        }
      }
    }

    // 3. Trail circles just below cloud, touching each other, not reaching entity
    var cloudBottomY = bcy + CLOUD_BOTTOM_DY;
    var tr1 = 2, tr2 = 1;
    var tc1y = cloudBottomY + 1 + tr1;   // 1px gap from cloud outer border
    var tc2y = tc1y + tr1 + tr2;          // touching circle 1
    var trailCircles = [{cx: bcx, cy: tc1y, r: tr1}, {cx: bcx, cy: tc2y, r: tr2}];
    for (var ti = 0; ti < trailCircles.length; ti++) {
      var tcx = trailCircles[ti].cx;
      var tcy = trailCircles[ti].cy;
      var tr = trailCircles[ti].r;
      // outer black border
      for (var ty = tcy - tr - 1; ty <= tcy + tr + 1; ty++) {
        for (var tx = tcx - tr - 1; tx <= tcx + tr + 1; tx++) {
          if (tx < 0 || tx >= PW || ty < 0 || ty >= PH) continue;
          var tdx = tx - tcx, tdy = ty - tcy, outerR = tr + 1;
          if (tdx * tdx + tdy * tdy <= outerR * outerR) {
            var toi = ty * PW + tx;
            _blendPixel(buf, toi, 0, 0, 0, alpha);
          }
        }
      }
      // inner white fill
      for (var ty = tcy - tr; ty <= tcy + tr; ty++) {
        for (var tx = tcx - tr; tx <= tcx + tr; tx++) {
          if (tx < 0 || tx >= PW || ty < 0 || ty >= PH) continue;
          var tdx = tx - tcx, tdy = ty - tcy;
          if (tdx * tdx + tdy * tdy <= tr * tr) {
            var twi = ty * PW + tx;
            _blendPixel(buf, twi, 255, 255, 255, alpha);
          }
        }
      }
    }

    // 4. "..." three 3×3 black squares inside cloud
    var dotTopY = bcy - 1;
    var dotCXs = [bcx - 5, bcx, bcx + 5];
    for (var di = 0; di < 3; di++) {
      for (var ddy = 0; ddy < 3; ddy++) {
        for (var ddx = 0; ddx < 3; ddx++) {
          var dpx = dotCXs[di] - 1 + ddx, dpy = dotTopY + ddy;
          if (dpx >= 0 && dpx < PW && dpy >= 0 && dpy < PH) {
            var dpi = dpy * PW + dpx;
            _blendPixel(buf, dpi, 0, 0, 0, alpha);
          }
        }
      }
    }
  };
}, 1500);

// ── D3: Alert ──
// "!" sprite above entity. Signals that an important event just happened
// or that the entity is reacting to something.
// Scaffolds Initiating Event (IE) and Internal Response (IR).
AnimationTemplates.register('alert', function(params) {
  var prefix = params.entityPrefix || '';

  // "!" bitmap: 5 wide × 20 tall — same height structure as ghost_outline "?"
  // Body: rows 0-13 (3px wide bar), gap: rows 14-16, dot: rows 17-18, pad: row 19
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
  var spacing = 5; // px gap between marks

  return function animate(buf, PW, PH, t) {
    var env = _easeEnvelope(t, 0.15, 0.2);
    if (env < 0.01) return;
    var alpha = env;

    var bounds = _computeEntityBounds(buf, PW, prefix);
    if (bounds.x2 < 0) return;

    // 3 marks centered above entity
    var totalW = eW * 3 + spacing * 2;
    var x0 = Math.round(bounds.cx - totalW / 2);
    var y0 = Math.max(2, bounds.y1 - eH - 4);

    for (var mi = 0; mi < 3; mi++) {
      var mx0 = x0 + mi * (eW + spacing);

      // Pass 1: 2px black outer outline
      for (var ry = 0; ry < eH; ry++) {
        for (var rx = 0; rx < eW; rx++) {
          if (!eMark[ry * eW + rx]) continue;
          for (var oy = -2; oy <= 2; oy++) {
            for (var ox = -2; ox <= 2; ox++) {
              var px = mx0 + rx + ox, py = y0 + ry + oy;
              if (px < 0 || px >= PW || py < 0 || py >= PH) continue;
              var pi = py * PW + px;
              _blendPixel(buf, pi, 0, 0, 0, alpha);
            }
          }
        }
      }

      // Pass 2: 1px red outline
      for (var ry = 0; ry < eH; ry++) {
        for (var rx = 0; rx < eW; rx++) {
          if (!eMark[ry * eW + rx]) continue;
          for (var oy = -1; oy <= 1; oy++) {
            for (var ox = -1; ox <= 1; ox++) {
              var px = mx0 + rx + ox, py = y0 + ry + oy;
              if (px < 0 || px >= PW || py < 0 || py >= PH) continue;
              var pi = py * PW + px;
              _blendPixel(buf, pi, 210, 40, 20, alpha);
            }
          }
        }
      }

      // Pass 3: yellow fill
      for (var ry = 0; ry < eH; ry++) {
        for (var rx = 0; rx < eW; rx++) {
          if (!eMark[ry * eW + rx]) continue;
          var px = mx0 + rx, py = y0 + ry;
          if (px < 0 || px >= PW || py < 0 || py >= PH) continue;
          var pi = py * PW + px;
          _blendPixel(buf, pi, 255, 220, 30, alpha);
        }
      }
    }

    // Gentle entity pulse
    var pulse = 1 + 0.12 * env * (0.5 + 0.5 * Math.sin(t * Math.PI * 5));
    for (var i = 0; i < buf.length; i++) {
      if (_isEntity(buf[i].e, prefix)) {
        buf[i].r = Math.min(255, Math.round(buf[i]._r * pulse));
        buf[i].g = Math.min(255, Math.round(buf[i]._g * pulse));
        buf[i].b = Math.min(255, Math.round(buf[i]._b * pulse));
      }
    }
  };
}, 1200);

// ── D4: Interjection ──
// Comic-style starburst bubble (elliptical body + radiating spikes).
// The ONLY animation that displays text from the child's speech.
// Positioned above entity if entityPrefix is given, otherwise finds free space.
AnimationTemplates.register('interjection', function(params) {
  var prefix = params.entityPrefix || '';
  var word = params.word || '???';
  var numSpikes = 12;
  var spikeH = 8;  // recomputed proportionally on first animate call

  var cachedBCX = null, cachedBCY, cachedRX, cachedRY;

  function findFreeSpot(buf, PW, PH, rrx, rry) {
    var pad = spikeH + 3;
    var candidates = [
      { x: Math.round(PW / 2),    y: rry + pad },
      { x: Math.round(PW * 0.25), y: rry + pad },
      { x: Math.round(PW * 0.75), y: rry + pad },
      { x: Math.round(PW * 0.15), y: rry + pad },
      { x: Math.round(PW * 0.85), y: rry + pad },
    ];
    for (var ci = 0; ci < candidates.length; ci++) {
      var cx = _clamp(candidates[ci].x, rrx + pad, PW - rrx - pad);
      var cy = candidates[ci].y;
      var occupied = false;
      for (var ty = cy - rry - pad; ty <= cy + rry + pad && !occupied; ty++) {
        for (var tx = cx - rrx - pad; tx <= cx + rrx + pad && !occupied; tx++) {
          if (tx < 0 || tx >= PW || ty < 0 || ty >= PH) continue;
          var e = buf[ty * PW + tx].e;
          if (e && e !== '' && e !== 'bg' && !e.startsWith('bg')) occupied = true;
        }
      }
      if (!occupied) return { x: cx, y: cy };
    }
    return { x: Math.round(PW / 2), y: rry + spikeH + 3 };
  }

  return function animate(buf, PW, PH, t) {
    var env = _easeEnvelope(t, 0.1, 0.25);
    if (env < 0.01) return;
    var alpha = env;

    if (cachedBCX === null) {
      var displayText = word.toUpperCase();
      var textW = displayText.length * (_FONT_W + _FONT_SPACING) - _FONT_SPACING;
      cachedRX = Math.max(22, Math.round((textW + 12) / 2));
      cachedRY = Math.round(cachedRX * 0.6);
      spikeH = Math.max(6, Math.round(cachedRX * 0.35));  // proportional to ellipse size
      var pad = spikeH + 3;
      var placed = false;

      if (prefix && prefix !== 'none') {
        var bounds = _computeEntityBounds(buf, PW, prefix);
        if (bounds.x2 >= 0) {
          cachedBCX = _clamp(Math.round(bounds.cx), cachedRX + pad, PW - cachedRX - pad);
          // Place center so starburst bottom is 2px above entity top.
          // No lower-bound clamp — canvas clips anything above y=0 naturally.
          cachedBCY = bounds.y1 - cachedRY - spikeH - 2;
          placed = true;
        }
      }
      if (!placed) {
        var spot = findFreeSpot(buf, PW, PH, cachedRX, cachedRY);
        cachedBCX = spot.x; cachedBCY = spot.y;
      }
    }

    var bcx = cachedBCX, bcy = cachedBCY;
    var rrx = cachedRX, rry = cachedRY;

    // Inner ellipse radius in direction `angle`
    function rInner(cosA, sinA) {
      var d = Math.sqrt((rry * cosA) * (rry * cosA) + (rrx * sinA) * (rrx * sinA));
      return d > 0.001 ? (rrx * rry / d) : rrx;
    }

    // Max starburst radius in direction `angle` (inner ellipse + spike)
    function rMax(angle) {
      var cosA = Math.cos(angle), sinA = Math.sin(angle);
      var ri = rInner(cosA, sinA);
      var phase = ((angle * numSpikes / (2 * Math.PI)) % 1 + 1) % 1;
      var lin = Math.max(0, 1 - Math.abs(phase - 0.5) * 2);
      var profile = lin * lin * lin;  // concave sides (cube makes edges hollow)
      return ri + spikeH * profile;
    }

    var pad = spikeH + 3;
    var sx0 = Math.max(0, bcx - rrx - pad);
    var sx1 = Math.min(PW - 1, bcx + rrx + pad);
    var sy0 = Math.max(0, bcy - rry - pad);
    var sy1 = Math.min(PH - 1, bcy + rry + pad);

    // Single pass: yellow inside starburst, black 2px outline outside
    for (var sy = sy0; sy <= sy1; sy++) {
      for (var sx = sx0; sx <= sx1; sx++) {
        var dx = sx - bcx, dy = sy - bcy;
        var r = Math.sqrt(dx * dx + dy * dy);
        var angle = Math.atan2(dy, dx);
        var rm = rMax(angle);
        var pi = sy * PW + sx;
        if (r <= rm) {
          _blendPixel(buf, pi, 255, 210, 20, alpha);
        } else if (r <= rm + 2) {
          _blendPixel(buf, pi, 0, 0, 0, alpha);
        }
      }
    }

    // Draw text centered, then re-blend with alpha so it fades identically to the bubble
    var displayText = word.toUpperCase();
    var textW = displayText.length * (_FONT_W + _FONT_SPACING) - _FONT_SPACING;
    var ttx = Math.round(bcx - textW / 2);
    var tty = Math.round(bcy - _FONT_H / 2);
    drawText(buf, PW, PH, displayText, ttx, tty, 30, 20, 10, 'temp.interjection');
    for (var rty = Math.max(0, tty - 1); rty <= Math.min(PH - 1, tty + 7); rty++) {
      for (var rtx = Math.max(0, ttx - 1); rtx <= Math.min(PW - 1, ttx + textW + 1); rtx++) {
        var rpi = rty * PW + rtx;
        if (buf[rpi].e === 'temp.interjection') {
          buf[rpi].r = Math.round(buf[rpi]._r * (1 - alpha) + 30 * alpha);
          buf[rpi].g = Math.round(buf[rpi]._g * (1 - alpha) + 20 * alpha);
          buf[rpi].b = Math.round(buf[rpi]._b * (1 - alpha) + 10 * alpha);
        }
      }
    }
  };
}, 1500);


// ═══════════════════════════════════════════════════════════════════
// Exports
// ═══════════════════════════════════════════════════════════════════

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { AnimationTemplates, ParticleSystem, ParticlePresets };
}
