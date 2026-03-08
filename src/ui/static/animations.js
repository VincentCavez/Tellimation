// Tellimations Animation Engine
// Executes animation code on PixelBuffer via requestAnimationFrame

// ---------------------------------------------------------------------------
// AnimationRunner
// ---------------------------------------------------------------------------

class AnimationRunner {
  constructor(pixelBuffer, renderer) {
    this.buf = pixelBuffer;
    this.renderer = renderer;
    this.isPlaying = false;
    this._rafId = null;
    this._resolve = null;
  }

  /**
   * Play an animation — accepts either a code string or a template spec object.
   * @param {string|Object} codeOrSpec — JS code string OR { template, params, particles, duration_ms }
   * @param {number} [durationMs=1200] — duration (ignored if spec provides duration_ms)
   */
  play(codeOrSpec, durationMs = 1200) {
    // Template spec path
    if (typeof codeOrSpec === 'object' && codeOrSpec !== null && codeOrSpec.template) {
      return this._playSpec(codeOrSpec);
    }

    // Compile code string to function
    // tempSprites is exposed so animation code can add/remove sprites mid-animation
    let animFn;
    try {
      const wrapped = `
        ${codeOrSpec}
        return animate;
      `;
      animFn = new Function('buf', 'PW', 'PH', 'tempSprites', wrapped)(
        this.buf.data, this.buf.width, this.buf.height,
        typeof tempSprites !== 'undefined' ? tempSprites : {}
      );
    } catch (e1) {
      try {
        animFn = new Function('buf', 'PW', 'PH', 't', 'tempSprites', codeOrSpec);
      } catch (e2) {
        console.error('[AnimationRunner] Failed to compile animation:', e2);
        return Promise.resolve();
      }
    }

    return this._playFunction(animFn, durationMs);
  }

  /**
   * Play a template spec by building it via AnimationTemplates.
   */
  _playSpec(spec) {
    if (typeof AnimationTemplates === 'undefined') {
      console.error('[AnimationRunner] AnimationTemplates not loaded');
      return Promise.resolve();
    }
    // Render text overlays into pixel buffer before snapshot
    var overlays = spec.text_overlays || [];
    if (overlays.length > 0 && typeof drawText === 'function') {
      for (var i = 0; i < overlays.length; i++) {
        var ov = overlays[i];
        var c = ov.color || [255, 255, 255];
        drawText(this.buf.data, this.buf.width, this.buf.height,
          ov.text, ov.x, ov.y, c[0], c[1], c[2], ov.id, ov.scale || 1);
      }
      this.renderer.render();
    }
    var built = AnimationTemplates.build(spec);
    return this._playFunction(built.animate, built.duration_ms);
  }

  /**
   * Core animation loop — plays a pre-compiled animate function.
   */
  _playFunction(animFn, durationMs) {
    if (this.isPlaying) {
      this._finish();
    }

    return new Promise((resolve) => {
      this._resolve = resolve;

      this.buf.snapshot();
      this.isPlaying = true;
      const startTime = performance.now();

      const tick = (now) => {
        if (!this.isPlaying) return;

        const elapsed = now - startTime;
        const t = Math.min(elapsed / durationMs, 1);

        this.buf.restore();
        // Re-render temp sprites so animation code can see/interact with them
        if (typeof renderTempSprites === 'function') {
          renderTempSprites(this.buf);
        }

        try {
          animFn(this.buf.data, this.buf.width, this.buf.height, t);
        } catch (err) {
          console.error('[AnimationRunner] Runtime error at t=' + t.toFixed(3) + ':', err);
          this._finish();
          return;
        }

        this.renderer.render();

        if (t < 1) {
          this._rafId = requestAnimationFrame(tick);
        } else {
          this._finish();
        }
      };

      this._rafId = requestAnimationFrame(tick);
    });
  }

  stop() {
    if (this.isPlaying) {
      this._finish();
    }
  }

  _finish() {
    this.isPlaying = false;
    if (this._rafId !== null) {
      cancelAnimationFrame(this._rafId);
      this._rafId = null;
    }
    // Restore original pixel state
    this.buf.restore();
    // Re-render temp sprites on top after restore
    if (typeof renderTempSprites === 'function') {
      renderTempSprites(this.buf);
    }
    this.renderer.render();

    if (this._resolve) {
      const r = this._resolve;
      this._resolve = null;
      r();
    }
  }
}

// ---------------------------------------------------------------------------
// Fallback Animation Library
// ---------------------------------------------------------------------------
// Each function returns a code string compatible with AnimationRunner.play().
// The generated code defines: function animate(buf, PW, PH, t) { ... }

const FallbackAnimations = {

  /**
   * colorPop — desaturates everything except the target entity, which gets
   * a glowing brightness boost. Maps to PROPERTY_COLOR errors.
   */
  colorPop(entityPrefix) {
    return `
function animate(buf, PW, PH, t) {
  // Ease in then ease out
  var strength = t < 0.15 ? t / 0.15 : t > 0.85 ? (1 - t) / 0.15 : 1;
  for (var i = 0; i < buf.length; i++) {
    var p = buf[i];
    var isTarget = p.e === '${entityPrefix}' || p.e.indexOf('${entityPrefix}.') === 0;
    if (isTarget) {
      // Brighten with a gentle pulse
      var glow = 1 + 0.3 * strength * (0.7 + 0.3 * Math.sin(t * Math.PI * 6));
      p.r = Math.min(255, Math.round(p._r * glow));
      p.g = Math.min(255, Math.round(p._g * glow));
      p.b = Math.min(255, Math.round(p._b * glow));
    } else if (p.e !== 'sky' && p.e !== 'ground' && p.e !== '') {
      // Desaturate non-target entities
      var L = Math.round(p._r * 0.299 + p._g * 0.587 + p._b * 0.114);
      var mix = strength * 0.8;
      p.r = Math.round(p._r * (1 - mix) + L * mix);
      p.g = Math.round(p._g * (1 - mix) + L * mix);
      p.b = Math.round(p._b * (1 - mix) + L * mix);
    }
  }
}`;
  },

  /**
   * shake — rapid horizontal jitter of the target entity pixels.
   * Maps to IDENTITY errors (vibrating pulse / jelloing).
   */
  shake(entityPrefix) {
    return `
function animate(buf, PW, PH, t) {
  // Ease: ramp up then down
  var env = t < 0.1 ? t / 0.1 : t > 0.8 ? (1 - t) / 0.2 : 1;
  var offset = Math.round(Math.sin(t * Math.PI * 20) * 3 * env);
  if (offset === 0) return;

  // Collect target pixel positions
  var pixels = [];
  for (var i = 0; i < buf.length; i++) {
    var e = buf[i].e;
    if (e === '${entityPrefix}' || e.indexOf('${entityPrefix}.') === 0) {
      pixels.push(i);
    }
  }

  // Blank original positions — restore background color underneath
  for (var j = 0; j < pixels.length; j++) {
    var idx = pixels[j];
    buf[idx].r = buf[idx]._br || 0;
    buf[idx].g = buf[idx]._bg || 0;
    buf[idx].b = buf[idx]._bb || 0;
  }

  // Redraw shifted
  for (var j = 0; j < pixels.length; j++) {
    var idx = pixels[j];
    var x = idx % PW;
    var y = (idx - x) / PW;
    var nx = x + offset;
    if (nx >= 0 && nx < PW) {
      var ni = y * PW + nx;
      buf[ni].r = buf[idx]._r;
      buf[ni].g = buf[idx]._g;
      buf[ni].b = buf[idx]._b;
      buf[ni].e = buf[idx].e;
    }
  }
}`;
  },

  /**
   * pulse — rhythmic scale-like brightness pulsing of the target entity.
   * Maps to QUANTITY errors (sequential pulse).
   */
  pulse(entityPrefix) {
    return `
function animate(buf, PW, PH, t) {
  // Three distinct pulses over the duration
  var pulseCount = 3;
  var phase = (t * pulseCount) % 1;
  var brightness = 0.5 + 0.5 * Math.sin(phase * Math.PI);
  // Overall envelope
  var env = t < 0.1 ? t / 0.1 : t > 0.9 ? (1 - t) / 0.1 : 1;
  brightness = 1 + (brightness - 0.5) * env * 0.8;

  for (var i = 0; i < buf.length; i++) {
    var e = buf[i].e;
    if (e === '${entityPrefix}' || e.indexOf('${entityPrefix}.') === 0) {
      buf[i].r = Math.min(255, Math.round(buf[i]._r * brightness));
      buf[i].g = Math.min(255, Math.round(buf[i]._g * brightness));
      buf[i].b = Math.min(255, Math.round(buf[i]._b * brightness));
    }
  }
}`;
  },

  /**
   * isolate — dims everything except the target, which stays fully bright.
   * Maps to QUANTITY/ISOLATION errors.
   */
  isolate(entityPrefix) {
    return `
function animate(buf, PW, PH, t) {
  // Smooth ease in/out for the dimming
  var dim;
  if (t < 0.2) dim = t / 0.2;
  else if (t > 0.8) dim = (1 - t) / 0.2;
  else dim = 1;
  dim *= 0.7; // max dimming amount

  for (var i = 0; i < buf.length; i++) {
    var e = buf[i].e;
    var isTarget = e === '${entityPrefix}' || e.indexOf('${entityPrefix}.') === 0;
    if (!isTarget) {
      var factor = 1 - dim;
      buf[i].r = Math.round(buf[i]._r * factor);
      buf[i].g = Math.round(buf[i]._g * factor);
      buf[i].b = Math.round(buf[i]._b * factor);
    }
  }
}`;
  },

  /**
   * bounce — vertical bounce of the target entity pixels.
   * Maps to SPATIAL errors (settle animation).
   */
  bounce(entityPrefix) {
    return `
function animate(buf, PW, PH, t) {
  // Damped bounce: 3 bounces decaying
  var bounceT = t * 3; // 3 bounces over duration
  var decay = 1 - t;   // amplitude decays over time
  var offset = Math.round(Math.abs(Math.sin(bounceT * Math.PI)) * -8 * decay);
  if (offset === 0 && t > 0.05) return;

  // Collect target pixels (sorted by y descending so we can shift without overlap issues)
  var pixels = [];
  for (var i = 0; i < buf.length; i++) {
    var e = buf[i].e;
    if (e === '${entityPrefix}' || e.indexOf('${entityPrefix}.') === 0) {
      pixels.push(i);
    }
  }

  // Sort by y ascending (for upward shift, process top-to-bottom)
  pixels.sort(function(a, b) { return a - b; });

  // Blank original positions — restore background color underneath
  for (var j = 0; j < pixels.length; j++) {
    var idx = pixels[j];
    buf[idx].r = buf[idx]._br || 0;
    buf[idx].g = buf[idx]._bg || 0;
    buf[idx].b = buf[idx]._bb || 0;
  }

  // Redraw shifted vertically
  for (var j = 0; j < pixels.length; j++) {
    var idx = pixels[j];
    var x = idx % PW;
    var y = (idx - x) / PW;
    var ny = y + offset;
    if (ny >= 0 && ny < PH) {
      var ni = ny * PW + x;
      buf[ni].r = buf[idx]._r;
      buf[ni].g = buf[idx]._g;
      buf[ni].b = buf[idx]._b;
      buf[ni].e = buf[idx].e;
    }
  }
}`;
  },
};

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { AnimationRunner, FallbackAnimations };
}
