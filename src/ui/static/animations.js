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
    this._looping = false;
    this._loopTimeout = null;
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

  /**
   * Play an animation in a loop: play → wait 2× duration → play → ...
   * Loops until stopLoop() is called.
   */
  playLoop(codeOrSpec, durationMs) {
    this.stopLoop();
    this._looping = true;

    var self = this;
    var dur = (typeof codeOrSpec === 'object' && codeOrSpec !== null && codeOrSpec.duration_ms)
      ? codeOrSpec.duration_ms
      : (durationMs || 1200);

    var runOnce = function() {
      if (!self._looping) return;
      self.play(codeOrSpec, dur).then(function() {
        if (!self._looping) return;
        self._loopTimeout = setTimeout(runOnce, dur * 2);
      });
    };

    runOnce();
  }

  /**
   * Play a sequence of animation steps in a loop.
   * Each step plays in order, then 2× total duration gap, then repeat.
   */
  playLoopSequence(steps) {
    this.stopLoop();
    this._looping = true;

    var self = this;
    var totalDur = 0;
    for (var i = 0; i < steps.length; i++) {
      totalDur += (steps[i].duration_ms || 1200);
    }

    var runSequence = function() {
      if (!self._looping) return;

      var idx = 0;
      var runStep = function() {
        if (!self._looping || idx >= steps.length) {
          // All steps done — wait 2× total duration, then restart
          if (self._looping) {
            self._loopTimeout = setTimeout(runSequence, totalDur * 2);
          }
          return;
        }
        var step = steps[idx];
        idx++;
        self.play({
          template: step.template,
          params: step.params || {},
          duration_ms: step.duration_ms || 1200,
        }).then(runStep);
      };

      runStep();
    };

    runSequence();
  }

  /**
   * Stop the loop (and any currently playing animation).
   */
  stopLoop() {
    this._looping = false;
    if (this._loopTimeout) {
      clearTimeout(this._loopTimeout);
      this._loopTimeout = null;
    }
    this.stop();
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

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { AnimationRunner };
}
