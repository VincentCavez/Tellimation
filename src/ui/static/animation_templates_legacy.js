'use strict';
// ═══════════════════════════════════════════════════════════════════
// LEGACY animation templates — study 1 (spring 2026) visuals.
//
// nametag (I2), reveal (S1) and ghost_outline (C3) were replaced by
// silhouette, peek and missing_piece in animation_templates.js. These
// implementations are kept ONLY so the study-1 stimuli can be regenerated
// byte-comparable (simulation.html, scripts/study_gen/video_ui.html).
// They are NOT loaded by the live story/study clients, and the grammar
// and prompts hold no reference to them.
// ═══════════════════════════════════════════════════════════════════

// ── I2: Nametag ──
// Large beige nametag with entity type text, connected by an undulating
// red string. The tag pivots slightly at the string attachment point.
AnimationTemplates.register('nametag', _perTargetWrapper(function(params) {
  var prefix = params.entityPrefix || '';
  var bgColor = params.bgColor || [235, 215, 180]; // beige
  var borderColor = params.borderColor || [120, 95, 60]; // dark brown border
  var textColor = params.textColor || [80, 50, 30]; // dark brown
  var stringColor = params.stringColor || [200, 50, 40]; // red

  // Label text: use explicit labelText param if provided, otherwise empty (prompt to name)
  var entityType = (params.labelText != null && params.labelText !== '') ? params.labelText : '';
  // Pre-compute text width: each char is (_FONT_W + _FONT_SPACING) * scale, minus trailing space
  var textScale = 3;
  var charW = (_FONT_W + _FONT_SPACING) * textScale;
  var textW = entityType.length > 0 ? entityType.length * charW - _FONT_SPACING : 0;
  var textH = _FONT_H * textScale;

  var labelPadX = 24, labelPadY = 20;
  var labelW = Math.max(110, textW + labelPadX * 2);
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
    var tagGap = 24;
    var tagCenterY = Math.max(labelH / 2 + 2, Math.min(bounds.cy, PH - labelH / 2 - 2));
    var tagX; // top-left corner X of the tag
    if (offsetRight) {
      tagX = bounds.x2 + tagGap;
    } else {
      tagX = bounds.x1 - tagGap - labelW;
    }
    var tagY = Math.round(tagCenterY - labelH / 2); // top-left corner Y

    // Hole: circle INSIDE the tag, next to the border, at mid-height
    var holeRadius = 3;
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
      if (buf[rti].e && _isEntity(buf[rti].e, prefix)) {
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
        for (var st = -1; st <= 1; st++) {
          var spx = px + Math.round(snx * st);
          var spy = py + Math.round(sny * st);
          if (spx >= 0 && spx < PW && spy >= 0 && spy < PH) {
            var si = spy * PW + spx;
            _blendPixel(buf, si, stringColor[0], stringColor[1], stringColor[2], env);
          }
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
        if (cxDist + cyDist < 9) continue;

        var di = drawY * PW + drawX;
        var borderThick = 5;
        var isBorder = (ly < borderThick || ly >= labelH - borderThick || lx < borderThick || lx >= labelW - borderThick);

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
}), 3000);



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



// ── C3: Ghost Outline ──
// Dark flat puddle at an empty spot + big "?" with black outline. Scaffolds absence.
AnimationTemplates.register('ghost_outline', function(params) {
  var prefix = params.entityPrefix || '';
  // ghostImageUrl: URL to the entity asset from another scene (full-size RGBA PNG)
  var ghostImageUrl = params.ghostImageUrl || '';

  var cachedPuddleCx = null, cachedPuddleY = null;
  var cachedRx = 0, cachedRy = 0;
  var cachedEdgeOffsets = null;

  // Ghost silhouette data (loaded from image)
  var ghostLoading = false, ghostReady = false;
  var ghostContour = null; // [{x, y}] relative to ghost bounding box
  var ghostMask = null;    // [{x, y}] all opaque pixels
  var ghostW = 0, ghostH = 0;
  var ghostScale = 1;

  function loadGhostImage() {
    if (ghostLoading || !ghostImageUrl) return;
    ghostLoading = true;
    var img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = function() {
      var off = document.createElement('canvas');
      off.width = img.width; off.height = img.height;
      var ctx = off.getContext('2d');
      ctx.drawImage(img, 0, 0);
      var data = ctx.getImageData(0, 0, img.width, img.height).data;

      // Find bounding box of opaque pixels
      var x1 = img.width, y1 = img.height, x2 = 0, y2 = 0;
      for (var y = 0; y < img.height; y++) {
        for (var x = 0; x < img.width; x++) {
          var a = data[(y * img.width + x) * 4 + 3];
          if (a > 30) {
            if (x < x1) x1 = x; if (x > x2) x2 = x;
            if (y < y1) y1 = y; if (y > y2) y2 = y;
          }
        }
      }
      if (x2 <= x1) { ghostLoading = false; return; }

      ghostW = x2 - x1 + 1;
      ghostH = y2 - y1 + 1;

      // Build mask (opaque pixels) and contour (edge pixels)
      ghostMask = [];
      ghostContour = [];
      for (var y = y1; y <= y2; y++) {
        for (var x = x1; x <= x2; x++) {
          var a = data[(y * img.width + x) * 4 + 3];
          if (a > 30) {
            ghostMask.push({ x: x - x1, y: y - y1 });
            // Check if it's an edge pixel (has a transparent neighbor)
            var isEdge = false;
            var dirs = [[-1,0],[1,0],[0,-1],[0,1]];
            for (var d = 0; d < 4; d++) {
              var nx = x + dirs[d][0], ny = y + dirs[d][1];
              if (nx < 0 || nx >= img.width || ny < 0 || ny >= img.height) { isEdge = true; break; }
              if (data[(ny * img.width + nx) * 4 + 3] <= 30) { isEdge = true; break; }
            }
            if (isEdge) ghostContour.push({ x: x - x1, y: y - y1 });
          }
        }
      }
      ghostReady = true;
    };
    img.src = ghostImageUrl;
  }

  return function animate(buf, PW, PH, t) {
    // Start loading ghost image on first frame
    if (ghostImageUrl && !ghostLoading && !ghostReady) loadGhostImage();

    if (cachedPuddleCx === null) {
      var bounds = _computeEntityBounds(buf, PW, prefix);
      var ew = bounds.x2 - bounds.x1 + 1;
      var eh = bounds.y2 - bounds.y1 + 1;
      cachedRx = Math.max(8, Math.round(ew * 0.55));
      cachedRy = Math.max(3, Math.round(eh * 0.08));

      // Find an empty ground-level spot (not overlapping any entity)
      var groundY = bounds.y2;
      var testH = 10;
      var bestCx = null;
      var offsets = [1.0, -1.0, 1.5, -1.5, 2.0, -2.0, 0.7, -0.7];
      for (var oi = 0; oi < offsets.length; oi++) {
        var testCx = Math.round(bounds.cx + offsets[oi] * ew);
        if (testCx - cachedRx < 0 || testCx + cachedRx >= PW) continue;
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
      if (bestCx === null) {
        bestCx = bounds.cx + Math.round(ew * 1.5);
        if (bestCx + cachedRx >= PW) bestCx = bounds.cx - Math.round(ew * 1.5);
      }
      cachedPuddleCx = _clamp(bestCx, cachedRx, PW - cachedRx - 1);
      cachedPuddleY = groundY;

      cachedEdgeOffsets = [];
      for (var row = 0; row < cachedRy * 2 + 1; row++) {
        cachedEdgeOffsets.push(Math.random() * Math.PI * 2);
      }

      // Scale ghost to match buffer resolution vs source image (1:1 natural size)
      if (ghostReady && ghostH > 0) {
        ghostScale = PW / 1376;  // source images are 1376×768
      }
    }

    // Recompute ghost scale if it loaded after first frame
    if (ghostReady && ghostScale === 1 && ghostH > 0) {
      var bounds2 = _computeEntityBounds(buf, PW, prefix);
      var eh2 = bounds2.y2 - bounds2.y1 + 1;
      var ew2 = bounds2.x2 - bounds2.x1 + 1;
      ghostScale = PW / 1376;
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

    var gc = params.puddleColor || [60, 65, 85];

    // Draw flat puddle
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

    // Draw ghost silhouette (or fallback "?" if no image)
    if (shapeAlpha > 0.15) {
      var qa = Math.min(1, (shapeAlpha - 0.15) / 0.25) * shapeAlpha;

      // Gentle float
      var floatX = Math.round(Math.sin(t * Math.PI * 2.3) * 3 + Math.cos(t * Math.PI * 1.7) * 2);
      var floatY = Math.round(Math.sin(t * Math.PI * 1.9 + 1.2) * 3 + Math.cos(t * Math.PI * 2.7) * 2);

      if (ghostReady && ghostContour) {
        // Draw ghost entity silhouette — semi-transparent fill + bright contour
        var gw = Math.round(ghostW * ghostScale);
        var gh = Math.round(ghostH * ghostScale);
        var gx0 = cx - Math.round(gw / 2) + floatX;
        var gy0 = puddleY - ry - gh - 6 + floatY;

        // Semi-transparent fill (ghostly)
        for (var mi = 0; mi < ghostMask.length; mi++) {
          var mx = gx0 + Math.round(ghostMask[mi].x * ghostScale);
          var my = gy0 + Math.round(ghostMask[mi].y * ghostScale);
          if (mx >= 0 && mx < PW && my >= 0 && my < PH) {
            var mpi = my * PW + mx;
            _blendPixel(buf, mpi, 40, 50, 70, qa * 0.45);
          }
        }

        // Bright contour outline
        for (var ci = 0; ci < ghostContour.length; ci++) {
          var ex = gx0 + Math.round(ghostContour[ci].x * ghostScale);
          var ey = gy0 + Math.round(ghostContour[ci].y * ghostScale);
          if (ex >= 0 && ex < PW && ey >= 0 && ey < PH) {
            var epi = ey * PW + ex;
            _blendPixel(buf, epi, 160, 180, 210, qa * 0.85);
          }
        }
      } else {
        // Fallback: "?" bitmap (13×20) if no ghost image
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
        var qx0 = cx - Math.floor(qW / 2) + floatX;
        var qy0 = puddleY - ry - qH - 3 + floatY;
        var dirs = [[-1,-1],[-1,0],[-1,1],[0,-1],[0,1],[1,-1],[1,0],[1,1]];
        for (var qy = 0; qy < qH; qy++) {
          for (var qx = 0; qx < qW; qx++) {
            if (!qMark[qy * qW + qx]) continue;
            for (var d = 0; d < 8; d++) {
              var nx = qx + dirs[d][0], ny = qy + dirs[d][1];
              var isInsideBitmap = nx >= 0 && nx < qW && ny >= 0 && ny < qH;
              if (isInsideBitmap && qMark[ny * qW + nx]) continue;
              var sx = qx0 + nx, sy = qy0 + ny;
              if (sx >= 0 && sx < PW && sy >= 0 && sy < PH) {
                _blendPixel(buf, sy * PW + sx, 0, 0, 0, qa);
              }
            }
          }
        }
        for (var qy = 0; qy < qH; qy++) {
          for (var qx = 0; qx < qW; qx++) {
            if (!qMark[qy * qW + qx]) continue;
            var sx = qx0 + qx, sy = qy0 + qy;
            if (sx >= 0 && sx < PW && sy >= 0 && sy < PH) {
              _blendPixel(buf, sy * PW + sx, 255, 255, 220, qa);
            }
          }
        }
      }
    }
  };
}, 2500);

